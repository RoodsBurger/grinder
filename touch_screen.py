"""
CST816T Touch Controller Driver
I2C interface for touchscreen

Modes:
  0 - Gesture only  (0xFA=0x11, 0xEC=0x01)
  1 - Point only    (0xFA=0x41)
  2 - Mixed         (0xFA=0x71)  <- we use this: gestures + coordinates together

Gesture IDs (register 0x01):
  0x00 - None
  0x01 - Swipe Up
  0x02 - Swipe Down
  0x03 - Swipe Left
  0x04 - Swipe Right
  0x05 - Long Press
"""
import time
import math
import smbus2
import RPi.GPIO as GPIO

# Gesture constants
GESTURE_NONE        = 0x00
GESTURE_SWIPE_UP    = 0x01
GESTURE_SWIPE_DOWN  = 0x02
GESTURE_SWIPE_LEFT  = 0x03
GESTURE_SWIPE_RIGHT = 0x04
GESTURE_LONG_PRESS  = 0x05

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
        self.gesture = GESTURE_NONE

        # Touch filtering and debouncing
        self.debounce_time = 0.01  # 10ms debounce
        self.last_touch_time = 0
        self.last_x = 0
        self.last_y = 0

        # Touch state machine
        self.STATE_IDLE     = 0
        self.STATE_PRESSED  = 1
        self.STATE_HELD     = 2
        self.STATE_RELEASED = 3
        self.touch_state = self.STATE_IDLE
        self.press_start_time = 0

        # Coordinate filtering (simple moving average)
        self.filter_size = 3
        self.x_history = []
        self.y_history = []

        # I2C retry configuration
        self.max_retries = 3
        self.retry_delay = 0.001

    def init(self):
        """Initialize touch controller in mixed mode (gestures + coordinates)."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            GPIO.setup(self.TP_RST, GPIO.OUT)
            GPIO.setup(self.TP_INT, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            self.reset()

            time.sleep(0.1)
            try:
                self.bus = smbus2.SMBus(self.i2c_bus)
            except Exception as e:
                print(f"ERROR: Failed to open I2C bus: {e}")
                return False

            device_found = False
            for attempt in range(5):
                if self.who_am_i():
                    device_found = True
                    break
                if attempt < 4:
                    time.sleep(0.2)

            if not device_found:
                print("ERROR: Touch controller not detected")
                return False

            try:
                self.read_revision()
            except:
                pass

            self.stop_sleep()
            self.set_mode(2)  # Mixed mode: gestures + coordinates
            return True

        except Exception as e:
            print(f"ERROR: Touch init failed: {e}")
            return False

    def reset(self):
        """Hardware reset of touch controller"""
        GPIO.output(self.TP_RST, GPIO.LOW)
        time.sleep(0.05)
        GPIO.output(self.TP_RST, GPIO.HIGH)
        time.sleep(0.2)

    def who_am_i(self):
        """Check if touch controller is present"""
        try:
            return self.bus.read_byte_data(self.i2c_addr, 0xA7) == 0xB5
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

    def set_mode(self, mode):
        """
        Set touch controller mode.
          0 - Gesture only
          1 - Point only
          2 - Mixed (gestures + point coordinates)
        """
        try:
            if mode == 1:
                self.bus.write_byte_data(self.i2c_addr, 0xFA, 0x41)
            elif mode == 2:
                self.bus.write_byte_data(self.i2c_addr, 0xFA, 0x71)
            else:  # mode 0
                self.bus.write_byte_data(self.i2c_addr, 0xFA, 0x11)
                self.bus.write_byte_data(self.i2c_addr, 0xEC, 0x01)
        except Exception as e:
            print(f"ERROR: Failed to set touch mode: {e}")

    def read_touch(self):
        """
        Read gesture and touch coordinates.
        Reads 6 bytes from register 0x01:
          [0] gesture ID
          [1] finger count
          [2] x high byte
          [3] x low byte
          [4] y high byte
          [5] y low byte

        Returns True if a touch point or gesture was detected.
        Updates self.x, self.y, self.gesture.
        """
        current_time = time.time()

        if current_time - self.last_touch_time < self.debounce_time:
            return False

        for attempt in range(self.max_retries):
            try:
                # Read gesture + finger count + coordinates in one shot
                data = self.bus.read_i2c_block_data(self.i2c_addr, 0x01, 6)

                gesture_id  = data[0]
                num_points  = data[1] & 0x0F
                raw_x       = ((data[2] & 0x0F) << 8) | data[3]
                raw_y       = ((data[4] & 0x0F) << 8) | data[5]

                # Store gesture (caller reads via get_gesture())
                self.gesture = gesture_id

                if num_points > 0:
                    valid, x, y = self.validate_coordinates(raw_x, raw_y)
                    if not valid:
                        self.touched = False
                        # Still return True if a gesture fired without valid coords
                        return gesture_id != GESTURE_NONE

                    if self.touch_state in (self.STATE_IDLE, self.STATE_RELEASED):
                        self.x_history.clear()
                        self.y_history.clear()
                        self.touch_state = self.STATE_PRESSED
                        self.press_start_time = current_time
                    elif self.touch_state == self.STATE_PRESSED:
                        self.touch_state = self.STATE_HELD

                    filtered_x, filtered_y = self.filter_coordinates(x, y)
                    self.x = filtered_x
                    self.y = filtered_y
                    self.last_x = filtered_x
                    self.last_y = filtered_y
                    self.touched = True
                    self.last_touch_time = current_time

                    return True

                else:
                    # No touch point - but may still have a gesture
                    if self.touch_state != self.STATE_IDLE:
                        self.touch_state = self.STATE_RELEASED
                        self.x_history.clear()
                        self.y_history.clear()
                    self.touched = False

                    if gesture_id != GESTURE_NONE:
                        self.last_touch_time = current_time
                        return True  # gesture without contact point

                    return False

            except OSError:
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                    continue
                self.touched = False
                return False
            except Exception as e:
                print(f"ERROR: Touch read exception: {e}")
                self.touched = False
                return False

        return False

    def validate_coordinates(self, x, y):
        """Validate coordinates are within display bounds."""
        if x < 0 or x >= 240 or y < 0 or y >= 240:
            return False, 0, 0
        if (x == 0 and y == 0) or (x >= 4095 or y >= 4095):
            return False, 0, 0
        return True, x, y

    def filter_coordinates(self, x, y):
        """3-sample moving average + 5px hysteresis to reduce jitter."""
        self.x_history.append(x)
        self.y_history.append(y)
        if len(self.x_history) > self.filter_size:
            self.x_history.pop(0)
        if len(self.y_history) > self.filter_size:
            self.y_history.pop(0)
        fx = sum(self.x_history) // len(self.x_history)
        fy = sum(self.y_history) // len(self.y_history)
        # Suppress sub-threshold movement (hysteresis)
        if len(self.x_history) == self.filter_size and abs(fx - self.last_x) < 5:
            fx = self.last_x
        if len(self.y_history) == self.filter_size and abs(fy - self.last_y) < 5:
            fy = self.last_y
        return fx, fy

    def get_point(self):
        """Return current touch coordinates."""
        return self.x, self.y

    def get_gesture(self):
        """
        Return the current gesture ID and clear it.
        Use the GESTURE_* constants to check the value.
        """
        g = self.gesture
        self.gesture = GESTURE_NONE
        return g

    def is_touched(self):
        """Check interrupt pin — LOW means touch or gesture event pending."""
        return GPIO.input(self.TP_INT) == GPIO.LOW

    def is_new_press(self):
        """True only on the first frame of a touch (IDLE → PRESSED transition)."""
        return self.touch_state == self.STATE_PRESSED

    def cleanup(self):
        """Clean up resources."""
        try:
            if self.bus:
                self.bus.close()
            GPIO.setup(self.TP_RST, GPIO.IN)
            GPIO.setup(self.TP_INT, GPIO.IN)
        except:
            pass
