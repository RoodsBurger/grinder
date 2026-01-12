"""
CST816T Touch Controller Driver
I2C interface for touchscreen
"""
import time
import smbus2
import RPi.GPIO as GPIO

class TouchScreen:
    def __init__(self):
        # Pin configuration
        self.TP_RST = 6   # Pin 31 - Touch Reset
        self.TP_INT = 4   # Pin 7 - Touch Interrupt

        # I2C configuration
        self.i2c_bus = 1
        self.i2c_addr = 0x15  # CST816T default address
        self.bus = None

        # Touch data
        self.x = 0
        self.y = 0
        self.touched = False

    def init(self):
        """Initialize touch controller"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Setup pins
        GPIO.setup(self.TP_RST, GPIO.OUT)
        GPIO.setup(self.TP_INT, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        # Reset touch controller
        self.reset()

        # Initialize I2C
        try:
            self.bus = smbus2.SMBus(self.i2c_bus)

            # Check if device is present
            if self.who_am_i():
                print("Touch controller detected: CST816T")
                rev = self.read_revision()
                print(f"Revision: {rev}")
                self.stop_sleep()
                return True
            else:
                print("Warning: Touch controller not detected")
                return False
        except Exception as e:
            print(f"Error initializing touch: {e}")
            return False

    def reset(self):
        """Hardware reset of touch controller"""
        GPIO.output(self.TP_RST, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(self.TP_RST, GPIO.HIGH)
        time.sleep(0.05)

    def who_am_i(self):
        """Check if touch controller is present"""
        try:
            chip_id = self.bus.read_byte_data(self.i2c_addr, 0xA7)
            return chip_id == 0xB5
        except:
            return False

    def read_revision(self):
        """Read controller revision"""
        try:
            return self.bus.read_byte_data(self.i2c_addr, 0xA9)
        except:
            return 0

    def stop_sleep(self):
        """Wake up touch controller"""
        try:
            self.bus.write_byte_data(self.i2c_addr, 0xFE, 0x01)
        except:
            pass

    def read_touch(self):
        """Read touch coordinates"""
        try:
            # Read 6 bytes starting from register 0x02
            data = self.bus.read_i2c_block_data(self.i2c_addr, 0x02, 6)

            # Number of touch points
            num_points = data[0] & 0x0F

            if num_points > 0:
                # Extract X coordinate
                self.x = ((data[1] & 0x0F) << 8) | data[2]
                # Extract Y coordinate
                self.y = ((data[3] & 0x0F) << 8) | data[4]
                self.touched = True
                return True
            else:
                self.touched = False
                return False
        except:
            self.touched = False
            return False

    def get_point(self):
        """Get current touch point"""
        return self.x, self.y

    def is_touched(self):
        """Check if screen is currently touched"""
        return GPIO.input(self.TP_INT) == GPIO.LOW

    def cleanup(self):
        """Clean up resources"""
        try:
            if self.bus:
                self.bus.close()
            GPIO.cleanup()
        except:
            pass