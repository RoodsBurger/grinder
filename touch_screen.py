"""
CST816T Touch Controller Driver
I2C interface for touchscreen
"""
import time
import math
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

        # Touch filtering and debouncing
        self.debounce_time = 0.01  # 10ms debounce for fast response
        self.last_touch_time = 0
        self.hysteresis = 5  # pixels - ignore moves smaller than this
        self.last_x = 0
        self.last_y = 0

        # Touch state machine
        self.STATE_IDLE = 0
        self.STATE_PRESSED = 1
        self.STATE_HELD = 2
        self.STATE_RELEASED = 3
        self.touch_state = self.STATE_IDLE
        self.press_start_time = 0

        # Coordinate filtering (simple moving average)
        self.filter_size = 3
        self.x_history = []
        self.y_history = []

        # I2C retry configuration
        self.max_retries = 3
        self.retry_delay = 0.001  # 1ms between retries

    def init(self):
        """Initialize touch controller with robust error handling."""
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            # Setup pins
            GPIO.setup(self.TP_RST, GPIO.OUT)
            GPIO.setup(self.TP_INT, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            # Reset touch controller
            self.reset()

            # Initialize I2C (wait a bit after reset)
            time.sleep(0.1)
            try:
                self.bus = smbus2.SMBus(self.i2c_bus)
            except Exception as e:
                print(f"ERROR: Failed to open I2C bus {self.i2c_bus}: {e}")
                return False

            # Check if device is present (with more retries and longer delays)
            device_found = False
            for attempt in range(5):
                if self.who_am_i():
                    device_found = True
                    break
                if attempt < 4:  # Don't sleep on last attempt
                    time.sleep(0.2)  # Longer delay between retries

            if device_found:
                try:
                    self.read_revision()
                except:
                    pass

                self.stop_sleep()
                return True
            else:
                print("ERROR: Touch controller not detected (expected chip ID 0xB5)")
                return False

        except Exception as e:
            print(f"ERROR: Touch initialization failed: {e}")
            return False

    def reset(self):
        """Hardware reset of touch controller"""
        GPIO.output(self.TP_RST, GPIO.LOW)
        time.sleep(0.05)  # Longer reset pulse
        GPIO.output(self.TP_RST, GPIO.HIGH)
        time.sleep(0.2)  # More time for controller to boot

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

    def validate_coordinates(self, x, y):
        """
        Validate touch coordinates are within display bounds.
        CST816T can return invalid coordinates on noise/vibration.

        Returns: (valid, x, y) tuple. If invalid, returns (False, 0, 0)
        """
        # Display is 240x240
        if x < 0 or x >= 240 or y < 0 or y >= 240:
            return False, 0, 0

        # Check for common invalid values (all 0xFFF or 0x000)
        if (x == 0 and y == 0) or (x >= 4095 or y >= 4095):
            return False, 0, 0

        return True, x, y

    def filter_coordinates(self, x, y):
        """
        Apply moving average filter to reduce jitter.

        Args: x, y - raw coordinates
        Returns: (filtered_x, filtered_y)
        """
        # Add to history
        self.x_history.append(x)
        self.y_history.append(y)

        # Keep only last N samples
        if len(self.x_history) > self.filter_size:
            self.x_history.pop(0)
        if len(self.y_history) > self.filter_size:
            self.y_history.pop(0)

        # Calculate average
        avg_x = sum(self.x_history) // len(self.x_history)
        avg_y = sum(self.y_history) // len(self.y_history)

        return avg_x, avg_y

    def check_hysteresis(self, x, y):
        """
        Check if movement exceeds hysteresis threshold.
        Prevents jitter from reporting tiny movements.

        Returns: True if movement is significant, False if within hysteresis
        """
        dx = abs(x - self.last_x)
        dy = abs(y - self.last_y)
        dist = math.sqrt(dx*dx + dy*dy)

        return dist >= self.hysteresis

    def read_touch(self):
        """
        Read touch coordinates with debouncing, filtering, and error handling.

        Returns: True if valid touch detected, False otherwise.
        Updates self.x, self.y, self.touched with filtered coordinates.
        """
        current_time = time.time()

        # Minimal debounce - only skip if called too rapidly
        if current_time - self.last_touch_time < self.debounce_time:
            return False

        # Try reading with retry logic
        for attempt in range(self.max_retries):
            try:
                # Read 6 bytes starting from register 0x02
                data = self.bus.read_i2c_block_data(self.i2c_addr, 0x02, 6)

                # Number of touch points
                num_points = data[0] & 0x0F

                if num_points > 0:
                    # Extract X coordinate
                    raw_x = ((data[1] & 0x0F) << 8) | data[2]
                    # Extract Y coordinate
                    raw_y = ((data[3] & 0x0F) << 8) | data[4]

                    # Validate coordinates
                    valid, x, y = self.validate_coordinates(raw_x, raw_y)

                    if not valid:
                        # Invalid data, likely noise/vibration
                        self.touched = False
                        return False

                    # Apply filtering
                    filtered_x, filtered_y = self.filter_coordinates(x, y)

                    # Disable hysteresis check - we want immediate response for slider
                    # (Original code had no hysteresis and worked fine)

                    # Update state
                    self.x = filtered_x
                    self.y = filtered_y
                    self.last_x = filtered_x
                    self.last_y = filtered_y
                    self.touched = True
                    self.last_touch_time = current_time

                    # State machine transition
                    if self.touch_state == self.STATE_IDLE:
                        self.touch_state = self.STATE_PRESSED
                        self.press_start_time = current_time
                    elif self.touch_state == self.STATE_PRESSED:
                        self.touch_state = self.STATE_HELD

                    return True
                else:
                    # No touch detected
                    if self.touch_state != self.STATE_IDLE:
                        self.touch_state = self.STATE_RELEASED
                        # Clear filter history on release
                        self.x_history.clear()
                        self.y_history.clear()

                    self.touched = False
                    return False

            except OSError as e:
                # I2C error - retry
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay)
                    continue
                else:
                    # All retries failed - silently fail (normal during no-touch)
                    self.touched = False
                    return False
            except Exception as e:
                # Unexpected error - report only
                print(f"ERROR: Touch read exception: {e}")
                self.touched = False
                return False

        return False

    def get_point(self):
        """Get current touch point"""
        return self.x, self.y

    def is_touched(self):
        """Check if screen is currently touched"""
        return GPIO.input(self.TP_INT) == GPIO.LOW

    def get_touch_state(self):
        """
        Get current touch state.
        Returns: STATE_IDLE, STATE_PRESSED, STATE_HELD, or STATE_RELEASED
        """
        # Reset released state to idle after reading
        if self.touch_state == self.STATE_RELEASED:
            self.touch_state = self.STATE_IDLE
            return self.STATE_RELEASED

        return self.touch_state

    def get_touch_duration(self):
        """
        Get duration of current touch in seconds.
        Returns: 0 if not touched, otherwise duration since press.
        """
        if self.touch_state in [self.STATE_PRESSED, self.STATE_HELD]:
            return time.time() - self.press_start_time
        return 0

    def is_new_press(self):
        """
        Check if this is a new press (just transitioned from IDLE to PRESSED).
        Useful for button clicks vs. drag detection.

        Returns: True if newly pressed in this read cycle.
        """
        return self.touch_state == self.STATE_PRESSED

    def cleanup(self):
        """Clean up resources"""
        try:
            if self.bus:
                self.bus.close()
            # Don't call GPIO.cleanup() - other components (display, motor) still use GPIO
            # Just cleanup our specific pins
            GPIO.setup(self.TP_RST, GPIO.IN)
            GPIO.setup(self.TP_INT, GPIO.IN)
        except:
            pass