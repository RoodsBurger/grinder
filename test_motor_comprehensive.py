#!/usr/bin/env python3
"""
Comprehensive Motor Noise & Torque Assessment Script
Tests 88 different DRV8711 configurations to diagnose motor issues
and find optimal settings for quiet operation with adequate torque.

PROBLEM: Motor is very noisy, skips steps, lacks torque (especially at LOW speeds),
         and stalls MORE at lower speeds - indicating wrong decay mode or drive current.

SOLUTION: Systematically test all register configurations to find:
          1. Root cause of low-speed stalling
          2. Quietest configuration with adequate torque
          3. Resonance frequencies causing issues

Hardware:
- Motor: NEMA 23 Stepper (4.2A, 3.0Nm, 0.9Ω, 3.8mH)
- Driver: Pololu High-Power Stepper Motor Driver 36v4 (DRV8711)
- Sense Resistors: 30mΩ (0.030Ω)
"""

import RPi.GPIO as GPIO
import spidev
import time
import sys
import signal
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
import csv
import os

# ============================================================================
# MOTOR SPECIFICATIONS
# ============================================================================
MOTOR_RATED_CURRENT = 4200  # mA
MOTOR_RESISTANCE = 0.9      # ohms
MOTOR_INDUCTANCE = 3.8      # mH
MOTOR_TORQUE_RATED = 3.0    # Nm (425 oz.in)
MOTOR_STEPS_PER_REV = 200   # 1.8° step angle

# ============================================================================
# HARDWARE PIN CONFIGURATION
# ============================================================================
CS_PIN = 8       # SPI Chip Select (GPIO8, Pin 24)
STEP_PIN = 25    # Step pulse (GPIO25, Pin 22)
DIR_PIN = 24     # Direction (GPIO24, Pin 18)
SLEEP_PIN = 7    # Sleep/Enable (GPIO7, Pin 26)
LCD_CS_PIN = 22  # LCD CS - must be held HIGH to prevent SPI conflicts

# ============================================================================
# DRV8711 REGISTER MAP
# ============================================================================
REG_CTRL = 0x00
REG_TORQUE = 0x01
REG_OFF = 0x02
REG_BLANK = 0x03
REG_DECAY = 0x04
REG_DRIVE = 0x05
REG_STATUS = 0x06
REG_STALL = 0x07

# ============================================================================
# SPI CONFIGURATION
# ============================================================================
spi = None
SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 500000  # 500kHz (matches Pololu Arduino library)

# ============================================================================
# GLOBAL STATE
# ============================================================================
emergency_stop = False
current_ctrl_value = 0x000  # Track CTRL register for blind mode

# ============================================================================
# DATA STRUCTURES FOR RESULTS COLLECTION
# ============================================================================

@dataclass
class TestResult:
    """Stores results from a single configuration test"""
    config_id: str
    config_name: str
    timestamp: str
    rpm: int
    current_ma: int

    # Register values tested
    pwm_freq_khz: float
    decay_mode: str
    drive_current: str
    microstep_mode: str

    # User rating
    noise_rating: int          # 1=very loud, 10=silent

    # Auto-detected
    thermal_warning: bool      # OTS bit set?

    def to_dict(self) -> Dict:
        """Convert to dictionary for CSV export"""
        return {
            'config_id': self.config_id,
            'config_name': self.config_name,
            'timestamp': self.timestamp,
            'rpm': self.rpm,
            'current_ma': self.current_ma,
            'pwm_freq_khz': self.pwm_freq_khz,
            'decay_mode': self.decay_mode,
            'drive_current': self.drive_current,
            'microstep_mode': self.microstep_mode,
            'noise_rating': self.noise_rating,
            'thermal_warning': self.thermal_warning
        }

@dataclass
class ResultsDatabase:
    """Manages collection and analysis of test results"""
    results: List[TestResult] = field(default_factory=list)

    def add_result(self, result: TestResult):
        """Add a test result to the database"""
        self.results.append(result)

    def export_csv(self, filename: str = None):
        """Export all results to CSV file"""
        if filename is None:
            filename = f"motor_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        if not self.results:
            print("[!] No results to export.")
            return

        filepath = os.path.join(os.path.dirname(__file__), filename)

        with open(filepath, 'w', newline='') as csvfile:
            fieldnames = list(self.results[0].to_dict().keys())
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            writer.writeheader()
            for result in self.results:
                writer.writerow(result.to_dict())

        print(f"[OK] Results exported to: {filepath}")
        print(f"     Total configurations tested: {len(self.results)}")

    def get_top_by_noise(self, n: int = 10) -> List[TestResult]:
        """Get top N quietest configurations"""
        return sorted(self.results, key=lambda r: r.noise_rating, reverse=True)[:n]

    def get_configs_with_thermal(self) -> List[TestResult]:
        """Get all configurations that triggered thermal warnings"""
        return [r for r in self.results if r.thermal_warning]

# ============================================================================
# SPI LOW-LEVEL FUNCTIONS
# ============================================================================

def init_spi():
    """Initialize SPI bus"""
    global spi
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00  # CPOL=0, CPHA=0

def close_spi():
    """Close SPI bus"""
    global spi
    if spi:
        spi.close()
        spi = None

def write_reg(reg: int, value: int):
    """Write to DRV8711 register (12-bit value)"""
    if value < 0 or value > 0xFFF:
        raise ValueError(f"Register value 0x{value:X} out of range [0x000-0xFFF]")

    msb = (reg << 4) | ((value >> 8) & 0x0F)
    lsb = value & 0xFF

    GPIO.output(CS_PIN, GPIO.HIGH)  # CS Active (HIGH for Pololu)
    spi.xfer2([msb, lsb])
    GPIO.output(CS_PIN, GPIO.LOW)  # CS Inactive (LOW)
    time.sleep(0.0001)  # 100us settling time

def read_reg(reg: int) -> int:
    """Read from DRV8711 register (12-bit value)"""
    read_cmd = 0x80 | (reg << 4)

    GPIO.output(CS_PIN, GPIO.HIGH)  # CS Active (HIGH for Pololu)
    result = spi.xfer2([read_cmd, 0x00])
    GPIO.output(CS_PIN, GPIO.LOW)  # CS Inactive (LOW)
    time.sleep(0.0001)

    value = ((result[0] & 0x0F) << 8) | result[1]
    return value

# ============================================================================
# DRIVER CONTROL FUNCTIONS
# ============================================================================

def init_gpio():
    """Initialize GPIO pins"""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # Disable LCD to prevent SPI conflicts
    GPIO.setup(LCD_CS_PIN, GPIO.OUT)
    GPIO.output(LCD_CS_PIN, GPIO.HIGH)

    # Setup motor control pins
    GPIO.setup(CS_PIN, GPIO.OUT)
    GPIO.setup(STEP_PIN, GPIO.OUT)
    GPIO.setup(DIR_PIN, GPIO.OUT)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)

    GPIO.output(CS_PIN, GPIO.LOW)  # CS inactive (LOW for Pololu active-HIGH)
    GPIO.output(STEP_PIN, GPIO.LOW)
    GPIO.output(DIR_PIN, GPIO.LOW)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

def cleanup_gpio():
    """Cleanup GPIO pins"""
    try:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        GPIO.output(STEP_PIN, GPIO.LOW)
        GPIO.cleanup()
    except:
        pass

def calculate_torque_register(current_ma: int, isgain: int) -> tuple:
    """
    Calculate TORQUE register value and return (torque_value, ctrl_isgain_bits)

    Formula: TORQUE = (384 * I_TRQ * R_SENSE * 2) / V_REF
             where V_REF = 3.3V, R_SENSE = 0.030Ω, Gain = ISGAIN setting

    ISGAIN encoding:
    - 00 (0) = Gain 5  (V_REF = 3.3V)
    - 01 (1) = Gain 10 (V_REF = 1.65V)
    - 10 (2) = Gain 20 (V_REF = 0.825V)
    - 11 (3) = Gain 40 (V_REF = 0.4125V)
    """
    # Try different gains to find best fit
    r_sense = 0.030

    gains = [(0, 3.3), (1, 1.65), (2, 0.825), (3, 0.4125)]

    for gain_bits, v_ref in gains:
        torque = int((384 * (current_ma / 1000.0) * r_sense * 2) / v_ref)
        if 0 <= torque <= 255:
            return (torque, gain_bits)

    # If we get here, current is too high
    raise ValueError(f"Current {current_ma}mA exceeds maximum supported by driver")

def setup_driver(config: Dict) -> bool:
    """
    Configure DRV8711 registers from config dictionary
    Returns True if successful, False if critical error
    """
    global current_ctrl_value

    try:
        # Calculate TORQUE register and ISGAIN for requested current
        torque_val, isgain_bits = calculate_torque_register(
            config['current_ma'],
            config.get('isgain', None)
        )

        # Build CTRL register
        ctrl = config['ctrl_base']
        ctrl = (ctrl & ~0x300) | (isgain_bits << 8)  # Set ISGAIN bits [9:8]
        ctrl = ctrl & ~0x01  # Ensure disabled initially

        # Store CTRL value for blind mode enable/disable
        current_ctrl_value = ctrl

        # Wake up driver
        GPIO.output(SLEEP_PIN, GPIO.HIGH)
        time.sleep(0.001)

        # Write all registers
        write_reg(REG_CTRL, ctrl)
        write_reg(REG_TORQUE, torque_val)
        write_reg(REG_OFF, config['off'])
        write_reg(REG_BLANK, config['blank'])
        write_reg(REG_DECAY, config['decay'])
        write_reg(REG_DRIVE, config['drive'])
        write_reg(REG_STALL, config['stall'])

        # Clear faults
        write_reg(REG_STATUS, 0x000)
        time.sleep(0.01)

        # Verify critical registers (if SPI read works)
        try:
            ctrl_readback = read_reg(REG_CTRL)
            torque_readback = read_reg(REG_TORQUE)

            if ctrl_readback == 0xFFF or ctrl_readback == 0x000:
                print("[!] WARNING: SPI read may not be working (MISO issue)")
                print("    Proceeding in BLIND mode - cannot verify registers")
            elif ctrl_readback != ctrl:
                print(f"[!] WARNING: CTRL register mismatch! Wrote 0x{ctrl:03X}, Read 0x{ctrl_readback:03X}")
            elif torque_readback != torque_val:
                print(f"[!] WARNING: TORQUE register mismatch! Wrote 0x{torque_val:03X}, Read 0x{torque_readback:03X}")
        except:
            print("[!] WARNING: Cannot read registers (MISO issue) - proceeding in BLIND mode")

        return True

    except Exception as e:
        print(f"[!] ERROR in setup_driver: {e}")
        return False

def enable_driver():
    """Enable the driver (set ENBL bit in CTRL register)"""
    global current_ctrl_value

    # Use tracked CTRL value to avoid needing to read (works in blind mode)
    ctrl_enabled = current_ctrl_value | 0x01
    write_reg(REG_CTRL, ctrl_enabled)
    current_ctrl_value = ctrl_enabled  # Update tracked value
    time.sleep(0.001)

def disable_driver():
    """Disable driver - clear ENBL bit (removes holding torque but keeps config)"""
    global current_ctrl_value

    # Use tracked CTRL value to avoid needing to read (works in blind mode)
    ctrl_disabled = current_ctrl_value & ~0x01
    write_reg(REG_CTRL, ctrl_disabled)
    current_ctrl_value = ctrl_disabled  # Update tracked value
    time.sleep(0.001)

def shutdown_driver():
    """Complete shutdown - disable and pull SLEEP low (resets all registers)"""
    disable_driver()
    GPIO.output(SLEEP_PIN, GPIO.LOW)
    time.sleep(0.1)

def check_thermal_fault() -> bool:
    """Check if thermal fault (OTS) bit is set in STATUS register"""
    try:
        status = read_reg(REG_STATUS)
        return bool(status & 0x01)  # OTS is bit 0
    except:
        return False  # Cannot read in blind mode

# ============================================================================
# MOTOR TEST FUNCTIONS
# ============================================================================

def run_motor_test(rpm: int, duration: float, config: Dict) -> bool:
    """
    Run motor at specified RPM for given duration
    Uses precise timing with perf_counter busy-wait
    Returns True if successful, False if emergency stop triggered
    """
    global emergency_stop

    # Calculate timing parameters
    microstep_divider = config.get('microstep_divider', 32)
    steps_per_rev = MOTOR_STEPS_PER_REV * microstep_divider

    steps_per_second = (rpm / 60.0) * steps_per_rev
    step_delay = 1.0 / steps_per_second
    total_steps = int(steps_per_second * duration)

    print(f"    Running: {rpm} RPM, {duration}s, {total_steps} steps, {step_delay*1000:.3f}ms/step")

    # Set direction
    GPIO.output(DIR_PIN, GPIO.HIGH)

    # Enable driver
    enable_driver()
    time.sleep(0.05)

    # Precise stepping loop
    t_next = time.perf_counter()

    for i in range(total_steps):
        if emergency_stop:
            print("\n[!] EMERGENCY STOP TRIGGERED")
            return False

        # Step pulse (2us minimum)
        GPIO.output(STEP_PIN, GPIO.HIGH)
        t_pulse = time.perf_counter()
        while time.perf_counter() - t_pulse < 0.000002:
            pass
        GPIO.output(STEP_PIN, GPIO.LOW)

        # Wait for next step with precise timing
        t_next += step_delay
        while time.perf_counter() < t_next:
            pass

    return True

# ============================================================================
# USER INTERACTION FUNCTIONS
# ============================================================================

def print_header(msg: str):
    """Print formatted section header"""
    print("\n" + "=" * 70)
    print(f"  {msg}")
    print("=" * 70)

def print_config_info(config: Dict):
    """Display configuration details before testing"""
    print(f"\n  Config ID:   {config['id']}")
    print(f"  Name:        {config['name']}")
    print(f"  Current:     {config['current_ma']}mA")
    print(f"  PWM Freq:    {config.get('pwm_freq_khz', 'N/A')} kHz")
    print(f"  Decay Mode:  {config.get('decay_name', 'N/A')}")
    print(f"  Drive:       {config.get('drive_name', 'N/A')}")
    print(f"  Microstep:   1/{config.get('microstep_divider', 32)}")
    if 'description' in config:
        print(f"  Info:        {config['description']}")

def collect_ratings(config: Dict, rpm: int) -> Optional[TestResult]:
    """
    Interactive prompt to collect noise rating after test
    Returns TestResult or None if skipped
    """
    print("\n" + "-" * 70)
    print("RATE NOISE:")
    print("-" * 70)

    try:
        noise = int(input("Noise rating (1=very loud, 10=silent): "))
        if not 1 <= noise <= 10:
            print("[!] Invalid rating, skipping...")
            return None

        thermal = check_thermal_fault()

        result = TestResult(
            config_id=config['id'],
            config_name=config['name'],
            timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            rpm=rpm,
            current_ma=config['current_ma'],
            pwm_freq_khz=config.get('pwm_freq_khz', 0),
            decay_mode=config.get('decay_name', 'Unknown'),
            drive_current=config.get('drive_name', 'Unknown'),
            microstep_mode=f"1/{config.get('microstep_divider', 32)}",
            noise_rating=noise,
            thermal_warning=thermal
        )

        return result

    except (ValueError, KeyboardInterrupt):
        print("\n[!] Rating cancelled")
        return None

# ============================================================================
# SIGNAL HANDLERS
# ============================================================================

def signal_handler(sig, frame):
    """Handle Ctrl+C for emergency stop"""
    global emergency_stop
    print("\n\n[!] EMERGENCY STOP - Ctrl+C detected")
    emergency_stop = True
    disable_driver()
    cleanup_gpio()
    close_spi()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

# ============================================================================
# TEST CONFIGURATION DICTIONARY
# ============================================================================
# 88 Total Configurations organized into 10 categories (A-J)
# Each config specifies register values and test parameters
# ============================================================================

TEST_CONFIGS = {
    # ========================================================================
    # CATEGORY A: DIAGNOSTIC BASELINE (5 configs) - START HERE
    # ========================================================================
    # Purpose: Establish baseline performance and diagnose current issues
    # Test at: 50, 100, 200 RPM to identify low-speed problems
    # ========================================================================

    'A1': {
        'id': 'A1',
        'name': 'Current Library Defaults',
        'description': 'Baseline - should match current behavior',
        'current_ma': 4200,
        'ctrl_base': 0xC28,  # 1/32 step, ISGAIN will be calculated
        'off': 0x030,        # 41.7kHz PWM
        'blank': 0x080,      # Default blanking
        'decay': 0x510,      # Auto-Mixed decay
        'drive': 0xA59,      # 150/300mA drive
        'stall': 0x040,      # Default stall detection
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50, 100, 200]
    },

    'A2': {
        'id': 'A2',
        'name': 'TI Reference Design',
        'description': 'Conservative TI recommended settings',
        'current_ma': 4200,
        'ctrl_base': 0xC28,  # 1/32 step
        'off': 0x030,        # 41.7kHz PWM
        'blank': 0x100,      # Fixed blanking time
        'decay': 0x110,      # Slow/Mixed decay
        'drive': 0xA59,      # 150/300mA drive
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '150/300mA',
        'test_speeds': [50, 100, 200]
    },

    'A3': {
        'id': 'A3',
        'name': 'High Torque Preset',
        'description': 'Maximize torque - should NOT stall at low speeds',
        'current_ma': 5900,
        'ctrl_base': 0xC20,  # 1/16 step for more torque
        'off': 0x030,        # 41.7kHz PWM
        'blank': 0x100,      # Fixed blanking
        'decay': 0x010,      # Slow decay - best low-speed torque
        'drive': 0xF59,      # 200/400mA drive - maximum
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'A4': {
        'id': 'A4',
        'name': 'Low Noise Preset',
        'description': 'Optimize for quiet operation',
        'current_ma': 4200,
        'ctrl_base': 0xC30,  # 1/64 step
        'off': 0x020,        # 62.5kHz PWM - above audible
        'blank': 0x180,      # ABT enabled for high microstepping
        'decay': 0x510,      # Auto-Mixed decay
        'drive': 0xA59,      # 150/300mA drive
        'stall': 0x000,      # Stall detection disabled
        'microstep_divider': 64,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]  # Skip 50 RPM - too weak at 1/64
    },

    'A5': {
        'id': 'A5',
        'name': 'Pololu Forum Recommended',
        'description': 'Settings from Pololu community recommendations',
        'current_ma': 4600,
        'ctrl_base': 0xC28,  # 1/32 step
        'off': 0x020,        # 62.5kHz PWM
        'blank': 0x180,      # ABT enabled
        'decay': 0x110,      # Slow/Mixed decay
        'drive': 0xA59,      # 150/300mA drive
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '150/300mA',
        'test_speeds': [50, 100, 200]
    },

    # ========================================================================
    # CATEGORY B: EXTENDED PWM FREQUENCY (12 configs)
    # ========================================================================
    # Purpose: Find optimal PWM frequency above audible range
    # Test PWM from 10kHz to 100kHz
    # Fixed: current=4200mA, step_mode=32, DRIVE=0xA59, DECAY=0x510
    # Test at: 100 RPM only
    # ========================================================================

    'B1': {
        'id': 'B1',
        'name': 'PWM 100kHz',
        'description': 'Maximum PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x018,        # 100kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 100.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B2': {
        'id': 'B2',
        'name': 'PWM 83kHz',
        'description': 'Very high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x01E,        # 83kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 83.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B3': {
        'id': 'B3',
        'name': 'PWM 62.5kHz',
        'description': 'High PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,        # 62.5kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B4': {
        'id': 'B4',
        'name': 'PWM 50kHz',
        'description': 'Industry recommended high frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x028,        # 50kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 50.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B5': {
        'id': 'B5',
        'name': 'PWM 41.7kHz',
        'description': 'Current default frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,        # 41.7kHz PWM (same as A1)
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B6': {
        'id': 'B6',
        'name': 'PWM 35kHz',
        'description': 'Medium-high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x038,        # 35kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 35.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B7': {
        'id': 'B7',
        'name': 'PWM 25kHz',
        'description': 'Medium PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x050,        # 25kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 25.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B8': {
        'id': 'B8',
        'name': 'PWM 20kHz',
        'description': 'Borderline audible frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x064,        # 20kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 20.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B9': {
        'id': 'B9',
        'name': 'PWM 16.7kHz',
        'description': 'Lower PWM frequency - likely audible',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x078,        # 16.7kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 16.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B10': {
        'id': 'B10',
        'name': 'PWM 12.8kHz',
        'description': 'Low-audible PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x09C,        # 12.8kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 12.8,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B11': {
        'id': 'B11',
        'name': 'PWM 12.5kHz',
        'description': 'Low-audible PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x0A0,        # 12.5kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 12.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'B12': {
        'id': 'B12',
        'name': 'PWM 10kHz',
        'description': 'Minimum recommended PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x0C8,        # 10kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 10.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    # ========================================================================
    # CATEGORY C: DRIVE CURRENT OPTIMIZATION (8 configs) - CRITICAL
    # ========================================================================
    # Purpose: Test if insufficient DRIVE current causes low-speed stalling
    # Hypothesis: FETs not switching hard enough = torque loss at low speeds
    # Test at: 50 RPM (where stalling occurs)
    # Expected: C1-C2 may stall, C3-C4 should NOT stall
    # ========================================================================

    'C1': {
        'id': 'C1',
        'name': 'DRIVE 50/100mA (Low PWM)',
        'description': 'Weakest drive current - likely to stall',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,      # Slow decay for low-speed torque
        'drive': 0x059,      # 50/100mA drive - VERY LOW
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '50/100mA (MIN)',
        'test_speeds': [50]
    },

    'C2': {
        'id': 'C2',
        'name': 'DRIVE 100/200mA (Low PWM)',
        'description': 'Low drive current - may stall',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0x559,      # 100/200mA drive - LOW
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '100/200mA',
        'test_speeds': [50]
    },

    'C3': {
        'id': 'C3',
        'name': 'DRIVE 150/300mA (Low PWM)',
        'description': 'Medium drive current - current default',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0xA59,      # 150/300mA drive - MEDIUM (current default)
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'C4': {
        'id': 'C4',
        'name': 'DRIVE 200/400mA (Low PWM)',
        'description': 'Maximum drive current - should NOT stall',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0xF59,      # 200/400mA drive - MAXIMUM
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50]
    },

    'C5': {
        'id': 'C5',
        'name': 'DRIVE 50/100mA (High PWM)',
        'description': 'Weakest drive at high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,        # 62.5kHz PWM
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0x059,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow (0x010)',
        'drive_name': '50/100mA (MIN)',
        'test_speeds': [50]
    },

    'C6': {
        'id': 'C6',
        'name': 'DRIVE 100/200mA (High PWM)',
        'description': 'Low drive at high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0x559,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow (0x010)',
        'drive_name': '100/200mA',
        'test_speeds': [50]
    },

    'C7': {
        'id': 'C7',
        'name': 'DRIVE 150/300mA (High PWM)',
        'description': 'Medium drive at high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow (0x010)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'C8': {
        'id': 'C8',
        'name': 'DRIVE 200/400mA (High PWM)',
        'description': 'Maximum drive at high PWM frequency',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50]
    },

    # ========================================================================
    # CATEGORY D: MOTOR CURRENT SWEEP (10 configs)
    # ========================================================================
    # Purpose: Test from 50% to 155% of rated current
    # Find optimal current for balance of torque and thermal
    # Fixed: DRIVE=0xA59, step_mode=32, PWM=0x030
    # Test at: 100 RPM
    # Watch for thermal warnings at high current
    # ========================================================================

    'D1': {
        'id': 'D1',
        'name': 'Current 2100mA (50%)',
        'description': 'Half of rated current - likely too weak',
        'current_ma': 2100,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D2': {
        'id': 'D2',
        'name': 'Current 2500mA (60%)',
        'description': '60% of rated current',
        'current_ma': 2500,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D3': {
        'id': 'D3',
        'name': 'Current 2900mA (69%)',
        'description': '~70% of rated current',
        'current_ma': 2900,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D4': {
        'id': 'D4',
        'name': 'Current 3400mA (81%)',
        'description': '~80% of rated current',
        'current_ma': 3400,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D5': {
        'id': 'D5',
        'name': 'Current 3800mA (90%)',
        'description': '90% of rated current',
        'current_ma': 3800,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D6': {
        'id': 'D6',
        'name': 'Current 4200mA (100%)',
        'description': '100% rated current - baseline',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D7': {
        'id': 'D7',
        'name': 'Current 4600mA (110%)',
        'description': '110% rated current',
        'current_ma': 4600,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D8': {
        'id': 'D8',
        'name': 'Current 5000mA (119%)',
        'description': '~120% rated current',
        'current_ma': 5000,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D9': {
        'id': 'D9',
        'name': 'Current 5900mA (140%)',
        'description': '140% rated current - watch thermal',
        'current_ma': 5900,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'D10': {
        'id': 'D10',
        'name': 'Current 6500mA (155%)',
        'description': '155% rated current - MAXIMUM, monitor thermal',
        'current_ma': 6500,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    # ========================================================================
    # CATEGORY E: DECAY MODE DEEP DIVE (12 configs) - CRITICAL
    # ========================================================================
    # Purpose: Test all decay modes at LOW and NORMAL speeds
    # Diagnose torque loss at low speeds (user reports stalling more at low RPM)
    # Test each mode at 50 RPM (stall-prone) and 200 RPM (normal)
    # Expected: Slow decay should have BEST torque at 50 RPM
    # ========================================================================

    'E1': {
        'id': 'E1',
        'name': 'Slow Decay @ 50 RPM',
        'description': 'Slow decay - best low-speed torque (theory)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,      # Slow decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E2': {
        'id': 'E2',
        'name': 'Slow/Mixed Decay @ 50 RPM',
        'description': 'Slow/Mixed decay at low speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x110,      # Slow/Mixed decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E3': {
        'id': 'E3',
        'name': 'Fast Decay @ 50 RPM',
        'description': 'Fast decay - should LOSE torque at low speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x210,      # Fast decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Fast (0x210)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E4': {
        'id': 'E4',
        'name': 'Mixed Decay @ 50 RPM',
        'description': 'Mixed decay at low speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x310,      # Mixed decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Mixed (0x310)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E5': {
        'id': 'E5',
        'name': 'Slow/Auto Decay @ 50 RPM',
        'description': 'Slow/Auto decay at low speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x410,      # Slow/Auto decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Auto (0x410)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E6': {
        'id': 'E6',
        'name': 'Auto-Mixed Decay @ 50 RPM',
        'description': 'Auto-Mixed decay (current default) at low speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,      # Auto-Mixed decay
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'E7': {
        'id': 'E7',
        'name': 'Slow Decay @ 200 RPM',
        'description': 'Slow decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x010,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    'E8': {
        'id': 'E8',
        'name': 'Slow/Mixed Decay @ 200 RPM',
        'description': 'Slow/Mixed decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x110,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    'E9': {
        'id': 'E9',
        'name': 'Fast Decay @ 200 RPM',
        'description': 'Fast decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x210,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Fast (0x210)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    'E10': {
        'id': 'E10',
        'name': 'Mixed Decay @ 200 RPM',
        'description': 'Mixed decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x310,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Mixed (0x310)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    'E11': {
        'id': 'E11',
        'name': 'Slow/Auto Decay @ 200 RPM',
        'description': 'Slow/Auto decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x410,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Auto (0x410)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    'E12': {
        'id': 'E12',
        'name': 'Auto-Mixed Decay @ 200 RPM',
        'description': 'Auto-Mixed decay at normal speed',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [200]
    },

    # ========================================================================
    # CATEGORY F: MICROSTEPPING EXTENDED (9 configs)
    # ========================================================================
    # Purpose: Test all microstepping modes from full-step to 1/256
    # Enable ABT (Adaptive Blanking Time) for modes >1/16
    # Test at 100 and 200 RPM (skip low speeds for high microstepping - too weak)
    # ========================================================================

    'F1': {
        'id': 'F1',
        'name': 'Full Step (1/1)',
        'description': 'Full stepping - loudest but strongest',
        'current_ma': 4200,
        'ctrl_base': 0xC00,  # MODE=000 = Full step
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 1,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F2': {
        'id': 'F2',
        'name': 'Half Step (1/2)',
        'description': '1/2 microstepping',
        'current_ma': 4200,
        'ctrl_base': 0xC08,  # MODE=001 = 1/2 step
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 2,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F3': {
        'id': 'F3',
        'name': 'Quarter Step (1/4)',
        'description': '1/4 microstepping',
        'current_ma': 4200,
        'ctrl_base': 0xC10,  # MODE=010 = 1/4 step
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 4,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F4': {
        'id': 'F4',
        'name': '1/8 Step',
        'description': '1/8 microstepping',
        'current_ma': 4200,
        'ctrl_base': 0xC18,  # MODE=011 = 1/8 step
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 8,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F5': {
        'id': 'F5',
        'name': '1/16 Step',
        'description': '1/16 microstepping',
        'current_ma': 4200,
        'ctrl_base': 0xC20,  # MODE=100 = 1/16 step
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F6': {
        'id': 'F6',
        'name': '1/32 Step',
        'description': '1/32 microstepping with ABT',
        'current_ma': 4200,
        'ctrl_base': 0xC28,  # MODE=101 = 1/32 step
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F7': {
        'id': 'F7',
        'name': '1/64 Step',
        'description': '1/64 microstepping with ABT',
        'current_ma': 4200,
        'ctrl_base': 0xC30,  # MODE=110 = 1/64 step
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 64,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F8': {
        'id': 'F8',
        'name': '1/128 Step',
        'description': '1/128 microstepping with ABT',
        'current_ma': 4200,
        'ctrl_base': 0xC38,  # MODE=111 = 1/128 step
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 128,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    'F9': {
        'id': 'F9',
        'name': '1/256 Step',
        'description': '1/256 microstepping with ABT - quietest but weakest',
        'current_ma': 4200,
        'ctrl_base': 0xCF8,  # MODE=111 + DTIME=11 = 1/256 step
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 256,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100, 200]
    },

    # ========================================================================
    # CATEGORY G: STALL DETECTION IMPACT (6 configs)
    # ========================================================================
    # Purpose: Test if STALL register affects noise/torque
    # Some users report better performance with stall detection disabled
    # Fixed: current=4200mA, PWM=0x030, step_mode=32, DRIVE=0xA59, DECAY=0x510
    # Test at: 50 RPM (where stalling issues occur)
    # ========================================================================

    'G1': {
        'id': 'G1',
        'name': 'STALL Disabled',
        'description': 'Stall detection disabled (0x000)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x000,      # Disabled
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'G2': {
        'id': 'G2',
        'name': 'STALL Low Threshold',
        'description': 'Very sensitive stall detection (0x010)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x010,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'G3': {
        'id': 'G3',
        'name': 'STALL Default',
        'description': 'Default stall detection (0x040)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,      # Default
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'G4': {
        'id': 'G4',
        'name': 'STALL Medium',
        'description': 'Medium stall detection (0x080)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x080,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'G5': {
        'id': 'G5',
        'name': 'STALL High',
        'description': 'High stall threshold (0x0C0)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x0C0,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'G6': {
        'id': 'G6',
        'name': 'STALL Maximum',
        'description': 'Least sensitive stall detection (0x0FF)',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x0FF,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    # ========================================================================
    # CATEGORY H: BLANKING TIME vs MICROSTEPPING (8 configs)
    # ========================================================================
    # Purpose: Test if ABT (Adaptive Blanking Time) improves high microstepping
    # TI recommends ABT for microstepping >1/16
    # Test ABT on/off for 1/16, 1/32, 1/64, 1/128 step modes
    # Fixed: current=4200mA, PWM=0x030, DRIVE=0xA59, DECAY=0x510
    # Test at: 100 RPM
    # ========================================================================

    'H1': {
        'id': 'H1',
        'name': '1/16 Step WITHOUT ABT',
        'description': '1/16 step, fixed blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC20,
        'off': 0x030,
        'blank': 0x080,      # Fixed blanking
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H2': {
        'id': 'H2',
        'name': '1/16 Step WITH ABT',
        'description': '1/16 step, adaptive blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC20,
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H3': {
        'id': 'H3',
        'name': '1/32 Step WITHOUT ABT',
        'description': '1/32 step, fixed blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,      # Fixed blanking
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H4': {
        'id': 'H4',
        'name': '1/32 Step WITH ABT',
        'description': '1/32 step, adaptive blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H5': {
        'id': 'H5',
        'name': '1/64 Step WITHOUT ABT',
        'description': '1/64 step, fixed blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC30,
        'off': 0x030,
        'blank': 0x080,      # Fixed blanking
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 64,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H6': {
        'id': 'H6',
        'name': '1/64 Step WITH ABT',
        'description': '1/64 step, adaptive blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC30,
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 64,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H7': {
        'id': 'H7',
        'name': '1/128 Step WITHOUT ABT',
        'description': '1/128 step, fixed blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC38,
        'off': 0x030,
        'blank': 0x080,      # Fixed blanking
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 128,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'H8': {
        'id': 'H8',
        'name': '1/128 Step WITH ABT',
        'description': '1/128 step, adaptive blanking time',
        'current_ma': 4200,
        'ctrl_base': 0xC38,
        'off': 0x030,
        'blank': 0x180,      # ABT enabled
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 128,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    # ========================================================================
    # CATEGORY I: RESONANCE TROUBLESHOOTING (10 configs) - CRITICAL
    # ========================================================================
    # Purpose: Map resonance frequencies causing low-speed stalling
    # Test combinations of speeds and PWM frequencies to find resonance zones
    # Identify where motor vibrates/stalls due to resonance
    # Fixed: current=4200mA, step_mode=32, DRIVE=0xA59, DECAY=0x510
    # ========================================================================

    'I1': {
        'id': 'I1',
        'name': 'Resonance Test: 30 RPM @ 62.5kHz',
        'description': 'Very low speed, high PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,        # 62.5kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [30]
    },

    'I2': {
        'id': 'I2',
        'name': 'Resonance Test: 30 RPM @ 41.7kHz',
        'description': 'Very low speed, medium PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,        # 41.7kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [30]
    },

    'I3': {
        'id': 'I3',
        'name': 'Resonance Test: 50 RPM @ 25kHz',
        'description': 'Low speed, lower PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x050,        # 25kHz PWM
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 25.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [50]
    },

    'I4': {
        'id': 'I4',
        'name': 'Resonance Test: 80 RPM @ 62.5kHz',
        'description': 'Medium-low speed, high PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [80]
    },

    'I5': {
        'id': 'I5',
        'name': 'Resonance Test: 80 RPM @ 41.7kHz',
        'description': 'Medium-low speed, medium PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [80]
    },

    'I6': {
        'id': 'I6',
        'name': 'Resonance Test: 80 RPM @ 25kHz',
        'description': 'Medium-low speed, lower PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x050,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 25.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [80]
    },

    'I7': {
        'id': 'I7',
        'name': 'Resonance Test: 100 RPM @ 25kHz',
        'description': 'Medium speed, lower PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x050,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 25.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [100]
    },

    'I8': {
        'id': 'I8',
        'name': 'Resonance Test: 150 RPM @ 62.5kHz',
        'description': 'Medium-high speed, high PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x020,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [150]
    },

    'I9': {
        'id': 'I9',
        'name': 'Resonance Test: 150 RPM @ 41.7kHz',
        'description': 'Medium-high speed, medium PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x030,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [150]
    },

    'I10': {
        'id': 'I10',
        'name': 'Resonance Test: 150 RPM @ 25kHz',
        'description': 'Medium-high speed, lower PWM',
        'current_ma': 4200,
        'ctrl_base': 0xC28,
        'off': 0x050,
        'blank': 0x080,
        'decay': 0x510,
        'drive': 0xA59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 25.0,
        'decay_name': 'Auto-Mixed (0x510)',
        'drive_name': '150/300mA',
        'test_speeds': [150]
    },

    # ========================================================================
    # CATEGORY J: HIGH TORQUE OPTIMIZATIONS (8 configs)
    # ========================================================================
    # Purpose: Configurations prioritizing maximum torque over noise
    # These should provide strong, reliable torque even at low speeds
    # Test at: 50, 100, 200 RPM to ensure NO stalling
    # ========================================================================

    'J1': {
        'id': 'J1',
        'name': 'Maximum Torque v1',
        'description': 'Max current + Slow decay + Max drive',
        'current_ma': 6500,
        'ctrl_base': 0xC20,  # 1/16 step for stronger torque
        'off': 0x030,
        'blank': 0x100,
        'decay': 0x010,      # Slow decay - best low-speed torque
        'drive': 0xF59,      # Maximum drive
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J2': {
        'id': 'J2',
        'name': 'Maximum Torque v2',
        'description': 'High current + Slow/Mixed decay + Max drive',
        'current_ma': 5900,
        'ctrl_base': 0xC20,
        'off': 0x030,
        'blank': 0x100,
        'decay': 0x110,      # Slow/Mixed decay
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J3': {
        'id': 'J3',
        'name': 'High Torque Balanced',
        'description': 'Rated current + Slow decay + Max drive + 1/32 step',
        'current_ma': 4200,
        'ctrl_base': 0xC28,  # 1/32 step for smoother
        'off': 0x030,
        'blank': 0x180,      # ABT for microstepping
        'decay': 0x010,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J4': {
        'id': 'J4',
        'name': 'High Torque + High PWM',
        'description': 'High current + Slow decay + Max drive + 62.5kHz PWM',
        'current_ma': 5900,
        'ctrl_base': 0xC20,
        'off': 0x020,        # 62.5kHz PWM
        'blank': 0x100,
        'decay': 0x010,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J5': {
        'id': 'J5',
        'name': 'High Torque + Very High PWM',
        'description': 'High current + Slow decay + Max drive + 100kHz PWM',
        'current_ma': 5900,
        'ctrl_base': 0xC20,
        'off': 0x018,        # 100kHz PWM
        'blank': 0x100,
        'decay': 0x010,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 16,
        'pwm_freq_khz': 100.0,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J6': {
        'id': 'J6',
        'name': 'Torque + Quiet Compromise v1',
        'description': 'Balanced: 5000mA + Slow/Mixed + Max drive + 62.5kHz',
        'current_ma': 5000,
        'ctrl_base': 0xC28,  # 1/32 step
        'off': 0x020,
        'blank': 0x180,
        'decay': 0x110,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 32,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },

    'J7': {
        'id': 'J7',
        'name': 'Torque + Quiet Compromise v2',
        'description': 'Balanced: 4600mA + Slow/Mixed + Max drive + 62.5kHz + 1/64',
        'current_ma': 4600,
        'ctrl_base': 0xC30,  # 1/64 step
        'off': 0x020,
        'blank': 0x180,
        'decay': 0x110,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 64,
        'pwm_freq_khz': 62.5,
        'decay_name': 'Slow/Mixed (0x110)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [100, 200]  # Skip 50 RPM - too weak at 1/64
    },

    'J8': {
        'id': 'J8',
        'name': 'Ultra Torque Extreme',
        'description': 'EXTREME: Max current + Slow + Max drive + Full step',
        'current_ma': 6500,
        'ctrl_base': 0xC00,  # Full step - maximum torque per step
        'off': 0x030,
        'blank': 0x100,
        'decay': 0x010,
        'drive': 0xF59,
        'stall': 0x040,
        'microstep_divider': 1,
        'pwm_freq_khz': 41.7,
        'decay_name': 'Slow (0x010)',
        'drive_name': '200/400mA (MAX)',
        'test_speeds': [50, 100, 200]
    },
}

# ============================================================================
# TEST EXECUTION HELPERS
# ============================================================================

def run_single_config_test(config: Dict, db: ResultsDatabase):
    """Run a single configuration through all its test speeds"""
    print_header(f"Testing Config: {config['id']} - {config['name']}")
    print_config_info(config)

    # Setup driver with this configuration
    if not setup_driver(config):
        print("[!] Failed to setup driver, skipping this config")
        return

    # Test at each specified speed
    for rpm in config.get('test_speeds', [100]):
        print(f"\n>>> Testing at {rpm} RPM...")

        success = run_motor_test(rpm, duration=2.0, config=config)

        if not success:
            print("[!] Test interrupted by emergency stop")
            break

        # Disable driver between speeds (removes torque but keeps config)
        disable_driver()
        time.sleep(0.5)

        # Collect user ratings
        result = collect_ratings(config, rpm)
        if result:
            db.add_result(result)

    # Fully shutdown driver after all speeds tested (resets chip)
    shutdown_driver()

    print("\n" + "-" * 70)
    print(f"Config {config['id']} complete")

def run_config_list(config_ids: List[str], db: ResultsDatabase):
    """Run multiple configurations"""
    print_header(f"Running {len(config_ids)} Configurations")

    for i, config_id in enumerate(config_ids, 1):
        if config_id not in TEST_CONFIGS:
            print(f"[!] Config {config_id} not found, skipping")
            continue

        print(f"\n[{i}/{len(config_ids)}] Starting config {config_id}...")

        try:
            run_single_config_test(TEST_CONFIGS[config_id], db)
        except KeyboardInterrupt:
            print("\n[!] Test interrupted by user")
            response = input("Continue to next config? (y/n): ")
            if response.lower() != 'y':
                break
        except Exception as e:
            print(f"[!] ERROR during config {config_id}: {e}")
            import traceback
            traceback.print_exc()
            response = input("Continue to next config? (y/n): ")
            if response.lower() != 'y':
                break

    print_header("Test Run Complete")
    print(f"Total configurations tested: {len(db.results)}")

# ============================================================================
# RESULTS ANALYSIS FUNCTIONS
# ============================================================================

def analyze_results(db: ResultsDatabase):
    """Display results analysis"""
    if not db.results:
        print("[!] No results to analyze")
        return

    print_header("RESULTS ANALYSIS")

    # TOP 10 QUIETEST
    print("\nTOP 10 QUIETEST CONFIGURATIONS:")
    print("-" * 70)
    top_quiet = db.get_top_by_noise(10)
    for i, result in enumerate(top_quiet, 1):
        print(f"{i:2d}. {result.config_id:4s} - {result.config_name:35s} | "
              f"Noise: {result.noise_rating:2d}/10 | {result.rpm} RPM")

    # THERMAL WARNINGS
    thermal = db.get_configs_with_thermal()
    if thermal:
        print("\n\nTHERMAL WARNINGS:")
        print("-" * 70)
        for result in thermal:
            print(f"  {result.config_id:4s} - {result.config_name:30s} | "
                  f"{result.current_ma}mA | {result.rpm} RPM")
    else:
        print("\n\n[OK] No thermal warnings")

    print("\n" + "=" * 70)

# ============================================================================
# INTERACTIVE MENU SYSTEM
# ============================================================================

def show_main_menu():
    """Display main menu and return user choice"""
    print_header("COMPREHENSIVE MOTOR TEST MENU")
    print("\n[A] Run ALL Tests (88 configs - ~7 hours estimated)")
    print("[D] Diagnostic Baseline Only (5 configs - RECOMMENDED START)")
    print("[T] Torque Focus Tests (20 configs - diagnose stalling)")
    print("[Q] Quick Recommended (10 configs)")
    print("\n[C] Run by Category:")
    print("    CA - Category A: Diagnostic Baseline (5 configs)")
    print("    CB - Category B: Extended PWM Frequency (12 configs)")
    print("    CC - Category C: DRIVE Current Optimization (8 configs)")
    print("    CD - Category D: Motor Current Sweep (10 configs)")
    print("    CE - Category E: Decay Mode Deep Dive (12 configs)")
    print("    CF - Category F: Microstepping Extended (9 configs)")
    print("    CG - Category G: Stall Detection Impact (6 configs)")
    print("    CH - Category H: Blanking Time vs Microstepping (8 configs)")
    print("    CI - Category I: Resonance Troubleshooting (10 configs)")
    print("    CJ - Category J: High Torque Optimizations (8 configs)")
    print("\n[S] Specific Configuration (enter config ID like 'A1', 'B5', etc.)")
    print("[R] View Results Summary")
    print("[E] Export Results to CSV")
    print("[X] Exit")
    print("=" * 70)

    choice = input("\nEnter your choice: ").strip().upper()
    return choice

def get_category_configs(category: str) -> List[str]:
    """Get all config IDs for a category"""
    return [cid for cid in TEST_CONFIGS.keys() if cid.startswith(category)]

def main():
    """Main program loop"""
    print("""
    ╔══════════════════════════════════════════════════════════════════╗
    ║  COMPREHENSIVE MOTOR NOISE & TORQUE ASSESSMENT SCRIPT           ║
    ║  88 Test Configurations for DRV8711 + NEMA 23 Stepper          ║
    ╚══════════════════════════════════════════════════════════════════╝
    """)

    # Initialize hardware
    try:
        print("Initializing hardware...")
        init_gpio()
        init_spi()
        print("[OK] Hardware initialized")
    except Exception as e:
        print(f"[!] Hardware initialization failed: {e}")
        return

    # Create results database
    db = ResultsDatabase()

    try:
        while True:
            choice = show_main_menu()

            if choice == 'A':
                # Run ALL 88 configs
                response = input("This will run ALL 88 configurations (~7 hours). Continue? (y/n): ")
                if response.lower() == 'y':
                    all_configs = list(TEST_CONFIGS.keys())
                    run_config_list(all_configs, db)

            elif choice == 'D':
                # Diagnostic Baseline (Category A)
                print("\n[*] Running Diagnostic Baseline (Category A)...")
                print("    This will test 5 configurations to establish baseline performance")
                response = input("Continue? (y/n): ")
                if response.lower() == 'y':
                    configs = get_category_configs('A')
                    run_config_list(configs, db)

            elif choice == 'T':
                # Torque Focus Tests
                print("\n[*] Running Torque Focus Tests...")
                print("    This includes:")
                print("    - Diagnostic Baseline (A1-A5): 5 configs")
                print("    - DRIVE Current at 50 RPM (C1-C8): 8 configs")
                print("    - Decay Mode at 50 RPM (E1-E6): 6 configs")
                print("    - High current test (D9): 1 config")
                print("    Total: 20 configurations")
                response = input("Continue? (y/n): ")
                if response.lower() == 'y':
                    torque_configs = (
                        get_category_configs('A') +
                        get_category_configs('C') +
                        ['E1', 'E2', 'E3', 'E4', 'E5', 'E6'] +
                        ['D9']
                    )
                    run_config_list(torque_configs, db)

            elif choice == 'Q':
                # Quick Recommended (10 configs)
                print("\n[*] Running Quick Recommended Tests...")
                print("    Handpicked 10 most important configs")
                response = input("Continue? (y/n): ")
                if response.lower() == 'y':
                    quick_configs = ['A1', 'A2', 'A3', 'A5', 'C4', 'C8', 'E1', 'E2', 'E6', 'J3']
                    run_config_list(quick_configs, db)

            elif choice.startswith('C') and len(choice) == 3:
                # Run specific category
                category = choice[1]
                configs = get_category_configs(category)
                if configs:
                    print(f"\n[*] Running Category {category} ({len(configs)} configs)...")
                    response = input("Continue? (y/n): ")
                    if response.lower() == 'y':
                        run_config_list(configs, db)
                else:
                    print(f"[!] Category {category} not found")

            elif choice == 'S':
                # Specific configuration
                config_id = input("Enter config ID (e.g., A1, B5, etc.): ").strip().upper()
                if config_id in TEST_CONFIGS:
                    run_single_config_test(TEST_CONFIGS[config_id], db)
                else:
                    print(f"[!] Config {config_id} not found")

            elif choice == 'R':
                # View results summary
                analyze_results(db)

            elif choice == 'E':
                # Export results to CSV
                filename = input("Enter filename (or press Enter for auto-generated): ").strip()
                if not filename:
                    filename = None
                db.export_csv(filename)

            elif choice == 'X':
                print("\n[*] Exiting...")
                break

            else:
                print("[!] Invalid choice, please try again")

            if choice in ['A', 'D', 'T', 'Q'] or (choice.startswith('C') and len(choice) == 3):
                # After running tests, ask if user wants to see results
                if db.results:
                    response = input("\nView results summary? (y/n): ")
                    if response.lower() == 'y':
                        analyze_results(db)

    except KeyboardInterrupt:
        print("\n\n[!] Program interrupted by user")
    except Exception as e:
        print(f"\n[!] EXCEPTION OCCURRED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n[*] Cleaning up...")
        disable_driver()
        cleanup_gpio()
        close_spi()
        print("[*] Cleanup complete")

        # Offer to save results if any were collected
        if db.results:
            response = input("\nSave results to CSV before exiting? (y/n): ")
            if response.lower() == 'y':
                db.export_csv()

        print("\n[*] Program terminated")

if __name__ == "__main__":
    main()
