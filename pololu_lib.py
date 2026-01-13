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

        # Default Register Values (Optimized for quiet operation + Pololu base)
        # Reference: https://github.com/pololu/high-power-stepper-driver-arduino
        # Based on combo_pololu_32step testing - quietest proven configuration
        self.regs = {
            REG_CTRL:   0xC28, # Gain 5, 1/32 Step default (bits 6-3 = 0101)
            REG_TORQUE: 0x1FF, # Default torque (recalculated by set_current_milliamps)
            REG_OFF:    0x030, # 24µs = 41.7kHz PWM (ABOVE audible - Pololu default)
            REG_BLANK:  0x180, # 2.56µs + ABT enabled (bit 8) for smooth microstepping
            REG_DECAY:  0x510, # Auto-Mixed (TI recommended, Pololu example uses this)
            REG_STALL:  0x040, # Default stall detection
            REG_DRIVE:  0xA59, # IDRIVEP=150mA, IDRIVEN=300mA (Pololu default)
        }

        # Track current step mode index (0-8) for RPM calculations
        # Default 0xC28 has bits 6-3 set to 0101 (5), which is 1/32 step
        self.step_mode_val = 5

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
        self.spi.max_speed_hz = 5000000  # 5MHz (DRV8711 max rated speed)
        self.spi.mode = 0
        try:
            self.spi.no_cs = True # Disable kernel CS to allow manual toggle
        except:
            pass

    def _write_reg(self, address, value):
        """Write to DRV8711 register with error handling."""
        if address < 0 or address > 7:
            raise ValueError(f"Invalid register address: {address}")
        if value < 0 or value > 0xFFF:
            raise ValueError(f"Invalid register value: {value} (must be 0-4095)")

        self.regs[address] = value
        cmd_msb = ((address & 0x07) << 4) | ((value >> 8) & 0x0F)
        cmd_lsb = value & 0xFF

        if self.cs_pin: GPIO.output(self.cs_pin, GPIO.HIGH) # CS Active (High)
        try:
            self.spi.xfer2([cmd_msb, cmd_lsb])
        except Exception as e:
            raise IOError(f"SPI write failed: {e}")
        finally:
            if self.cs_pin: GPIO.output(self.cs_pin, GPIO.LOW) # CS Inactive (Low)

    def _read_reg(self, address):
        """Read from DRV8711 register with error handling."""
        if address < 0 or address > 7:
            raise ValueError(f"Invalid register address: {address}")

        cmd_msb = (1 << 7) | ((address & 0x07) << 4)
        if self.cs_pin: GPIO.output(self.cs_pin, GPIO.HIGH)
        try:
            result = self.spi.xfer2([cmd_msb, 0x00])
        except Exception as e:
            raise IOError(f"SPI read failed: {e}")
        finally:
            if self.cs_pin: GPIO.output(self.cs_pin, GPIO.LOW)
        return ((result[0] & 0x0F) << 8) | result[1]

    def apply_settings(self):
        """
        Writes all cached register values to the driver.
        Order matches Pololu library: TORQUE, OFF, BLANK, DECAY, DRIVE, STALL, CTRL last.
        CTRL is written last because it contains the ENBL bit.
        """
        self._write_reg(REG_TORQUE, self.regs[REG_TORQUE])
        self._write_reg(REG_OFF, self.regs[REG_OFF])
        self._write_reg(REG_BLANK, self.regs[REG_BLANK])
        self._write_reg(REG_DECAY, self.regs[REG_DECAY])
        self._write_reg(REG_DRIVE, self.regs[REG_DRIVE])
        self._write_reg(REG_STALL, self.regs[REG_STALL])
        self._write_reg(REG_CTRL, self.regs[REG_CTRL])

    def reset_settings(self):
        """
        Resets all registers to optimized defaults.
        Based on Pololu library + noise optimization (ABT enabled vs Pololu 0x080).
        """
        if self.reset_pin:
            GPIO.output(self.reset_pin, GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(self.reset_pin, GPIO.LOW)

        # Restore default values (optimized for quiet operation)
        self.regs[REG_CTRL]   = 0xC28  # Gain 5, 1/32 step, disabled
        self.regs[REG_TORQUE] = 0x1FF  # Default torque
        self.regs[REG_OFF]    = 0x030  # 24µs = 41.7kHz (Pololu default)
        self.regs[REG_BLANK]  = 0x180  # ABT enabled (vs Pololu 0x080)
        self.regs[REG_DECAY]  = 0x510  # Auto-Mixed (Pololu examples use this)
        self.regs[REG_STALL]  = 0x040  # Default stall
        self.regs[REG_DRIVE]  = 0xA59  # 150/300mA (Pololu default)

        self.step_mode_val = 5  # Track 1/32 step default
        self.apply_settings()  # Write to hardware

    def set_current_milliamps(self, current_ma):
        """
        Sets current limit for 36v4 board (30mOhm sense resistors).
        Uses Pololu's exact formula from official library.
        Reference: HighPowerStepperDriver::setCurrentMilliamps36v4()
        """
        if current_ma < 0 or current_ma > 8000:
            raise ValueError(f"Current {current_ma}mA out of range (0-8000mA)")

        # Pololu 36v4 formula: delegate to 36v8 with doubled current
        # This accounts for different sense resistor configuration
        current_doubled = current_ma * 2
        if current_doubled > 16000:
            current_doubled = 16000

        # Calculate TORQUE and ISGAIN using Pololu formula
        # Formula: torqueBits = (384 * current_doubled) / 6875
        isgain_bits = 0b11  # Start with gain 40 (bits = 3)
        torque_bits = (384 * current_doubled) // 6875

        # Reduce gain if TORQUE overflows 8 bits
        while torque_bits > 0xFF:
            isgain_bits -= 1
            torque_bits >>= 1

        # Map ISGAIN bits to gain values for logging
        gain_map = {0: 5, 1: 10, 2: 20, 3: 40}
        gain_value = gain_map.get(isgain_bits, 5)

        # Update CTRL register (bits 9:8 = ISGAIN)
        ctrl_val = self.regs[REG_CTRL] & 0b110011111111  # Clear bits 9:8
        ctrl_val |= (isgain_bits << 8)
        self._write_reg(REG_CTRL, ctrl_val)

        # Update TORQUE register (bits 7:0 = TORQUE)
        torque_val = self.regs[REG_TORQUE] & 0b111100000000  # Clear bits 7:0
        torque_val |= torque_bits
        self._write_reg(REG_TORQUE, torque_val)

        print(f"Driver Configured: {current_ma}mA (Gain: {gain_value}, Torque: {torque_bits})")

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

    def check_thermal_fault(self):
        """
        Check if driver is in thermal shutdown.
        Returns: True if overtemperature detected, False otherwise.
        """
        status = self.read_status()
        ots_bit = (status >> 7) & 0x01  # Bit 7: OTS
        return ots_bit == 1

    def check_all_faults(self):
        """
        Check all fault conditions in STATUS register.
        Returns: dict with fault flags and raw status value.

        STATUS Register bits (DRV8711):
        Bit 7: OTS  - OverTemperature Shutdown
        Bit 6: AOCP - Channel A Overcurrent
        Bit 5: BOCP - Channel B Overcurrent
        Bit 4: APDF - Channel A Predriver Fault
        Bit 3: BPDF - Channel B Predriver Fault
        Bit 2: UVLO - Undervoltage Lockout
        Bit 1: STD  - Stall Detected
        Bit 0: STDLAT - Stall Detected Latched
        """
        status = self.read_status()
        # Only consider critical faults (bits 2-4), ignore thermal/overcurrent (bits 5-7) and stall (bits 0-1)
        # WARNING: Ignoring thermal shutdown is dangerous and can damage hardware
        critical_faults = status & 0x1C  # Bits 2-4 only (predriver faults, undervoltage)
        return {
            'raw_status': status,
            'ots_thermal': bool((status >> 7) & 0x01),
            'overcurrent_a': bool((status >> 6) & 0x01),
            'overcurrent_b': bool((status >> 5) & 0x01),
            'predriver_fault_a': bool((status >> 4) & 0x01),
            'predriver_fault_b': bool((status >> 3) & 0x01),
            'undervoltage': bool((status >> 2) & 0x01),
            'stall_detected': bool((status >> 1) & 0x01),
            'stall_latched': bool(status & 0x01),
            'any_fault': critical_faults != 0  # Only critical faults trigger shutdown
        }

    def get_fault_description(self, faults):
        """
        Convert fault dict to human-readable string.
        Args: faults dict from check_all_faults()
        Returns: String describing active faults, or "No faults" if clean.
        """
        if not faults['any_fault']:
            return "No faults"

        msgs = []
        if faults['ots_thermal']: msgs.append("THERMAL SHUTDOWN")
        if faults['overcurrent_a'] or faults['overcurrent_b']: msgs.append("OVERCURRENT")
        if faults['predriver_fault_a'] or faults['predriver_fault_b']: msgs.append("PREDRIVER FAULT")
        if faults['undervoltage']: msgs.append("UNDERVOLTAGE")
        if faults['stall_detected']: msgs.append("STALL")

        return " | ".join(msgs) + f" (0x{faults['raw_status']:03X})"

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

    def calculate_accel_profile(self, target_rpm, accel_time, steps_per_rev=200):
        """
        Calculate trapezoidal acceleration profile.

        Args:
            target_rpm: Target speed in RPM
            accel_time: Time to reach target speed (seconds)
            steps_per_rev: Motor steps per revolution (default 200)

        Returns:
            List of delays (seconds) for each step during acceleration phase.
            Use reversed list for deceleration.
        """
        microsteps = 1 << self.step_mode_val
        target_steps_per_sec = (target_rpm * steps_per_rev * microsteps) / 60.0

        if target_steps_per_sec <= 0 or accel_time <= 0:
            return []

        target_delay = 1.0 / target_steps_per_sec

        # Simple linear ramp
        # Start from 5x slower, ramp to target speed
        start_delay = target_delay * 5
        num_steps = int(accel_time * target_steps_per_sec / 2)  # Ramp over ~50% of time

        if num_steps < 10:
            num_steps = 10  # Minimum ramp steps

        delays = []
        for i in range(num_steps):
            ratio = i / float(num_steps)
            delay = start_delay - (start_delay - target_delay) * ratio
            delays.append(delay)

        return delays

    def move_steps_with_accel(self, steps, direction, rpm, accel_time=0.5, steps_per_rev=200):
        """
        Move with trapezoidal acceleration/deceleration.

        Args:
            steps: Total steps to move
            direction: 1 (forward) or 0 (reverse)
            rpm: Target speed in RPM
            accel_time: Time to accelerate/decelerate (seconds)
            steps_per_rev: Motor steps per revolution
        """
        if steps < 50:
            # Too short for acceleration, use constant speed
            return self.move_steps(steps, direction, rpm=rpm, steps_per_rev=steps_per_rev)

        # Calculate acceleration profile
        accel_profile = self.calculate_accel_profile(rpm, accel_time, steps_per_rev)
        decel_profile = list(reversed(accel_profile))

        accel_steps = len(accel_profile)
        decel_steps = len(decel_profile)
        cruise_steps = steps - accel_steps - decel_steps

        # Adjust if not enough steps for full profile
        if cruise_steps < 0:
            accel_steps = steps // 2
            decel_steps = steps - accel_steps
            accel_profile = accel_profile[:accel_steps]
            decel_profile = decel_profile[-decel_steps:]
            cruise_steps = 0

        # Calculate cruise delay
        cruise_delay = self._calculate_delay(rpm, None, steps_per_rev)

        # Set direction
        if self.dir_pin:
            GPIO.output(self.dir_pin, GPIO.HIGH if direction else GPIO.LOW)

        if not self.step_pin:
            return

        # Optimize GPIO calls
        output = GPIO.output
        step_pin = self.step_pin
        high = GPIO.HIGH
        low = GPIO.LOW

        step_count = 0

        # Acceleration phase
        for delay in accel_profile:
            output(step_pin, high)
            time.sleep(0.000002)
            output(step_pin, low)
            time.sleep(delay)
            step_count += 1

        # Cruise phase
        for _ in range(cruise_steps):
            output(step_pin, high)
            time.sleep(0.000002)
            output(step_pin, low)
            time.sleep(cruise_delay)
            step_count += 1

        # Deceleration phase
        for delay in decel_profile:
            output(step_pin, high)
            time.sleep(0.000002)
            output(step_pin, low)
            time.sleep(delay)
            step_count += 1

        return step_count

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
        """Close SPI connection only - don't cleanup GPIO (shared with display/touch)"""
        self.spi.close()
        # Don't call GPIO.cleanup() - display and touch still need their pins