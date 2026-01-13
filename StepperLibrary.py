"""
StepperLibrary - Agnostic Motor Control
FIXED: Reverted SPI Logic to Active Low (Matches your working v2 code)
"""

import os
os.environ["RPI_LGPIO_CHIP"] = "0"
import RPi.GPIO as GPIO
import spidev
import time
from enum import Enum
from typing import Optional, Callable


# ============================================================================
# Constants
# ============================================================================

class MicrostepMode(Enum):
    FULL_STEP = 1
    HALF_STEP = 2
    QUARTER_STEP = 4
    EIGHTH_STEP = 8
    SIXTEENTH_STEP = 16
    THIRTYSECOND_STEP = 32
    SIXTYFOURTH_STEP = 64
    ONETWENTYEIGHTH_STEP = 128
    TWOFIFTYSIXTH_STEP = 256

class DecayMode(Enum):
    SLOW = 0b000
    SLOW_INC_MIXED_DEC = 0b001
    FAST = 0b010
    MIXED = 0b011
    SLOW_INC_AUTO_MIXED_DEC = 0b100
    AUTO_MIXED = 0b101

class Direction(Enum):
    CLOCKWISE = 1
    COUNTERCLOCKWISE = 0
    CW = 1
    CCW = 0


# ============================================================================
# Low-Level SPI (Reverted to Active Low - Working State)
# ============================================================================

class _DRV8711:
    # Registers
    CTRL = 0x00
    TORQUE = 0x01
    OFF = 0x02
    BLANK = 0x03
    DECAY = 0x04
    STALL = 0x05
    DRIVE = 0x06
    STATUS = 0x07

    def __init__(self, cs_pin: int):
        self.cs_pin = cs_pin
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 500000
        self.spi.mode = 0

        # SETUP CS PIN (Active Low Logic)
        GPIO.setup(self.cs_pin, GPIO.OUT)
        GPIO.output(self.cs_pin, GPIO.HIGH) # Idle HIGH

    def _transfer(self, value: int) -> int:
        # Construct 16-bit command
        tx = [(value >> 8) & 0xFF, value & 0xFF]

        # --- CORRECT LOGIC (Active Low) ---
        GPIO.output(self.cs_pin, GPIO.LOW)   # Select (Low)
        rx = self.spi.xfer2(tx)              # Transfer
        GPIO.output(self.cs_pin, GPIO.HIGH)  # Deselect (High)
        # ----------------------------------

        return (rx[0] << 8) | rx[1]

    def write(self, reg: int, value: int):
        # Write bit is 0 (MSB)
        self._transfer(((reg & 0x7) << 12) | (value & 0xFFF))

    def read(self, reg: int) -> int:
        # Read bit is 1 (MSB)
        return self._transfer((0x8 | (reg & 0x7)) << 12) & 0xFFF

    def close(self):
        self.spi.close()


# ============================================================================
# Stepper Motor Controller
# ============================================================================

class StepperMotor:
    def __init__(
        self,
        cs_pin: int = 4,
        step_pin: int = 17,
        dir_pin: int = 27,
        sleep_pin: Optional[int] = None,
        max_current_ma: int = 4200
    ):
        self.cs_pin = cs_pin
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.sleep_pin = sleep_pin
        self.max_current = max_current_ma

        # GPIO Setup
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(self.step_pin, GPIO.OUT)
        GPIO.setup(self.dir_pin, GPIO.OUT)

        if self.sleep_pin is not None:
            GPIO.setup(self.sleep_pin, GPIO.OUT)
            GPIO.output(self.sleep_pin, GPIO.HIGH) # Wake up
            time.sleep(0.1)

        # Initialize Driver
        self._driver = _DRV8711(cs_pin)

        # Internal State
        self._ctrl = 0xC28     # Default Control Register (1/32 step)
        self._torque = 0x1FF   # Default Torque Register
        self._enabled = False
        self._running = False

        # Initialize Registers
        self._init_registers()

        # Apply Defaults
        self.set_microstep_mode(MicrostepMode.THIRTYSECOND_STEP)
        self.set_torque_percent(100)

    def _init_registers(self):
        """Writes constant registers"""
        self._driver.write(_DRV8711.OFF, 0x030)
        self._driver.write(_DRV8711.BLANK, 0x080)
        self._driver.write(_DRV8711.DECAY, 0x510) # Auto Mixed
        self._driver.write(_DRV8711.STALL, 0x040)
        self._driver.write(_DRV8711.DRIVE, 0xA59)
        self._driver.write(_DRV8711.STATUS, 0)

    # ========================================================================
    # Configuration Methods
    # ========================================================================

    def set_torque_percent(self, percent: float):
        """Set motor torque (0-100%)"""
        percent = max(0, min(100, percent))
        current_ma = int((percent / 100.0) * self.max_current)
        self._set_torque_ma(current_ma)

    def _set_torque_ma(self, current_ma: int):
        """Internal calculation for Torque register"""
        current_ma = max(0, min(self.max_current, current_ma))

        # For 36v4 (Rsense=0.030, Gain=20): TorqueVal = Current * 55.85
        torque_bits = int(current_ma / 1000.0 * 55.85)
        isgain_bits = 0b10 # Gain 20

        if torque_bits > 255:
            torque_bits = 255

        # Update local register copies
        self._ctrl = (self._ctrl & ~(0b11 << 8)) | (isgain_bits << 8)
        self._torque = 0x100 | torque_bits

        # Write to chip
        self._driver.write(_DRV8711.CTRL, self._ctrl)
        self._driver.write(_DRV8711.TORQUE, self._torque)

    def set_microstep_mode(self, mode: MicrostepMode):
        """Set microstepping resolution"""
        mode_map = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5, 64: 6, 128: 7, 256: 8}
        sm = mode_map.get(mode.value, 3)

        # Clear bits 3-6 and set new value
        self._ctrl = (self._ctrl & 0xF87) | (sm << 3)
        self._driver.write(_DRV8711.CTRL, self._ctrl)

    # ========================================================================
    # Motion Methods (Pure Motion, No Config Logic)
    # ========================================================================

    def enable(self):
        self._ctrl |= 0x001
        self._driver.write(_DRV8711.CTRL, self._ctrl)
        self._enabled = True
        time.sleep(0.01)

    def disable(self):
        self._ctrl &= ~0x001
        self._driver.write(_DRV8711.CTRL, self._ctrl)
        self._enabled = False

    def set_direction(self, direction: Direction):
        val = GPIO.HIGH if direction.value == 1 else GPIO.LOW
        GPIO.output(self.dir_pin, val)
        time.sleep(0.005) # Settling time

    def run_for_time(self, duration: float, speed: int, direction: Direction):
        """Run for set seconds."""
        if not self._enabled: self.enable()
        self.set_direction(direction)

        if speed <= 0: speed = 1
        delay = max(0.000001, 1.0 / speed)

        start_time = time.time()
        self._running = True
        steps = 0

        try:
            while time.time() - start_time < duration and self._running:
                GPIO.output(self.step_pin, GPIO.HIGH)
                time.sleep(0.000002)
                GPIO.output(self.step_pin, GPIO.LOW)
                time.sleep(delay)
                steps += 1
        except KeyboardInterrupt:
            self._running = False
        return steps

    def stop(self):
        self._running = False
        time.sleep(0.01)
        self.disable()

    def cleanup(self):
        self.stop()
        if self.sleep_pin:
            GPIO.output(self.sleep_pin, GPIO.LOW)
        self._driver.close()
        GPIO.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()
