import spidev
import RPi.GPIO as GPIO
import time
import math

# --- Constants & Registers ---
REG_CTRL    = 0x00
REG_TORQUE  = 0x01
REG_OFF     = 0x02
REG_BLANK   = 0x03
REG_DECAY   = 0x04
REG_STALL   = 0x05
REG_DRIVE   = 0x06
REG_STATUS  = 0x07

# Internal Mapping for Step Modes
_STEP_MAP = {
    1:   0, # Full step
    2:   1, # 1/2 step
    4:   2, # 1/4 step
    8:   3, # 1/8 step
    16:  4, # 1/16 step
    32:  5, # 1/32 step
    64:  6, # 1/64 step
    128: 7, # 1/128 step
    256: 8  # 1/256 step
}

# Decay Modes
DECAY_MODE_AUTO_MIXED = 0b101

class HighPowerStepperDriver:
    def __init__(self, spi_bus=0, spi_device=0, dir_pin=None, step_pin=None, sleep_pin=None, cs_pin=None, reset_pin=None):
        """
        Final Pololu 36v4 Driver for Raspberry Pi.
        Uses Manual Chip Select (Active High) to ensure reliability.
        """
        self.spi_bus = spi_bus
        self.spi_device = spi_device
        self.dir_pin = dir_pin
        self.step_pin = step_pin
        self.sleep_pin = sleep_pin
        self.reset_pin = reset_pin
        self.cs_pin = cs_pin

        # Default Register Values (Safe Defaults)
        self.regs = {
            REG_CTRL:   0xC10, # Gain 5, 1/4 Step
            REG_TORQUE: 0x1FF,
            REG_OFF:    0x030,
            REG_BLANK:  0x080,
            REG_DECAY:  0x110, # Auto Mixed Decay
            REG_STALL:  0x040,
            REG_DRIVE:  0xA59,
        }

        # Track current step mode index (0-8) for RPM calculations
        # Default 0xC10 has bits 6-3 set to 0010 (2), which is 1/4 step
        self.step_mode_val = 2

        self._setup_gpio()
        self._setup_spi()

    def _setup_gpio(self):
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        if self.dir_pin: GPIO.setup(self.dir_pin, GPIO.OUT)

        if self.step_pin:
            GPIO.setup(self.step_pin, GPIO.OUT)
            GPIO.output(self.step_pin, GPIO.LOW)

        if self.reset_pin:
            GPIO.setup(self.reset_pin, GPIO.OUT)
            GPIO.output(self.reset_pin, GPIO.LOW)

        if self.sleep_pin:
            GPIO.setup(self.sleep_pin, GPIO.OUT)
            GPIO.output(self.sleep_pin, GPIO.HIGH) # Awake
            time.sleep(0.001)

        if self.cs_pin:
            GPIO.setup(self.cs_pin, GPIO.OUT)
            GPIO.output(self.cs_pin, GPIO.LOW) # Default Low (Inactive)

    def _setup_spi(self):
        self.spi = spidev.SpiDev()
        self.spi.open(self.spi_bus, self.spi_device)
        self.spi.max_speed_hz = 500000
        self.spi.mode = 0
        try:
            self.spi.no_cs = True # Disable kernel CS to allow manual toggle
        except:
            pass

    def _write_reg(self, address, value):
        self.regs[address] = value
        cmd_msb = ((address & 0x07) << 4) | ((value >> 8) & 0x0F)
        cmd_lsb = value & 0xFF

        if self.cs_pin: GPIO.output(self.cs_pin, GPIO.HIGH) # CS Active (High)
        try:
            self.spi.xfer2([cmd_msb, cmd_lsb])
        finally:
            if self.cs_pin: GPIO.output(self.cs_pin, GPIO.LOW) # CS Inactive (Low)

    def _read_reg(self, address):
        cmd_msb = (1 << 7) | ((address & 0x07) << 4)
        if self.cs_pin: GPIO.output(self.cs_pin, GPIO.HIGH)
        try:
            result = self.spi.xfer2([cmd_msb, 0x00])
        finally:
            if self.cs_pin: GPIO.output(self.cs_pin, GPIO.LOW)
        return ((result[0] & 0x0F) << 8) | result[1]

    def reset_settings(self):
        """Resets driver to safe defaults."""
        if self.reset_pin:
            GPIO.output(self.reset_pin, GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(self.reset_pin, GPIO.LOW)

        # Reload defaults
        for reg, val in self.regs.items():
            if reg != REG_STATUS: # Don't write to status
                self._write_reg(reg, val)
        self.step_mode_val = 2 # Reset tracker to 1/4 step

    def set_current_milliamps(self, current_ma):
        """Sets current limit based on 36v4 30mOhm resistors."""
        r_sense = 0.030
        gains = [(5, 0), (10, 1), (20, 2), (40, 3)]

        best_gain, best_gain_bits, best_torque = 5, 0, 0

        for g, bits in gains:
            max_current = (2.75 * 255) / (256 * g * r_sense)
            if max_current >= (current_ma / 1000.0):
                torque_val = int(((current_ma / 1000.0) * 256 * g * r_sense) / 2.75)
                if torque_val > 255: torque_val = 255
                best_gain, best_gain_bits, best_torque = g, bits, torque_val

        # Apply Gain
        ctrl_val = self.regs[REG_CTRL] & ~(0x3 << 8)
        self._write_reg(REG_CTRL, ctrl_val | (best_gain_bits << 8))

        # Apply Torque
        torque_reg_val = (self.regs[REG_TORQUE] & ~0xFF) | best_torque
        self._write_reg(REG_TORQUE, torque_reg_val)
        print(f"Driver Configured: {current_ma}mA (Gain: {best_gain}, Torque: {best_torque})")

    def set_step_mode(self, step_div):
        """
        Sets microstepping mode.
        Usage: driver.set_step_mode(32) for 1/32 step.
        Valid values: 1, 2, 4, 8, 16, 32, 64, 128, 256.
        """
        if step_div in _STEP_MAP:
            mode_val = _STEP_MAP[step_div]
        else:
            print(f"Warning: Invalid step mode '{step_div}'. Defaulting to 1/4.")
            mode_val = 2 # Default to 1/4

        self.step_mode_val = mode_val # Track this for RPM calc

        # Write to Register (Bits 6-3)
        ctrl_val = (self.regs[REG_CTRL] & ~(0xF << 3)) | (mode_val << 3)
        self._write_reg(REG_CTRL, ctrl_val)

    def enable_driver(self):
        self._write_reg(REG_CTRL, self.regs[REG_CTRL] | 0x01)
        if self.sleep_pin: GPIO.output(self.sleep_pin, GPIO.HIGH)

    def disable_driver(self):
        self._write_reg(REG_CTRL, self.regs[REG_CTRL] & ~0x01)
        if self.sleep_pin: GPIO.output(self.sleep_pin, GPIO.LOW)

    def clear_faults(self):
        self._write_reg(REG_STATUS, 0)

    def read_status(self):
        return self._read_reg(REG_STATUS)

    def _calculate_delay(self, rpm, step_delay, steps_per_rev):
        """Internal helper to calculate delay from RPM."""
        delay = 0.001 # Default safe delay

        if rpm is not None:
             microsteps = 1 << self.step_mode_val
             steps_per_sec = (rpm * steps_per_rev * microsteps) / 60.0
             if steps_per_sec > 0:
                 delay = 1.0 / steps_per_sec
        elif step_delay is not None:
             delay = step_delay

        return delay

    def move_steps(self, steps, direction, rpm=None, step_delay=None, steps_per_rev=200):
        """
        Moves the motor by a specific number of steps.
        steps: Total steps to take
        direction: 1 (Forward) or 0 (Reverse)
        rpm: Speed in RPM
        """
        delay = self._calculate_delay(rpm, step_delay, steps_per_rev)

        if self.dir_pin:
            GPIO.output(self.dir_pin, GPIO.HIGH if direction else GPIO.LOW)

        if self.step_pin:
            # Performance optimization: Localize variable access for tighter loop
            output = GPIO.output
            step_pin = self.step_pin
            high = GPIO.HIGH
            low = GPIO.LOW

            for _ in range(steps):
                output(step_pin, high)
                time.sleep(0.000002) # Min pulse width
                output(step_pin, low)
                time.sleep(delay)

    def move_time(self, seconds, direction, rpm=None, step_delay=None, steps_per_rev=200):
        """
        Moves the motor for a specific duration of time.
        seconds: How long to run (e.g. 5.0)
        direction: 1 (Forward) or 0 (Reverse)
        rpm: Speed in RPM
        """
        delay = self._calculate_delay(rpm, step_delay, steps_per_rev)

        # Calculate how many steps fit in the requested time
        # This ensures smooth motion (better than checking time.time() in a loop)
        total_steps = int(seconds / delay)

        print(f"Running for {seconds}s ({total_steps} steps calculated)")

        # Reuse move_steps logic with pre-calculated delay
        self.move_steps(total_steps, direction, step_delay=delay)

    def close(self):
        self.spi.close()
        GPIO.cleanup()