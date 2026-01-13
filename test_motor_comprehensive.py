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
import json

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
    # CRITICAL: Disable kernel CS control for manual CS toggling
    try:
        spi.no_cs = True
    except:
        pass

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
# LOAD TEST CONFIGURATIONS FROM JSON
# ============================================================================
# 88 Total Configurations organized into 10 categories (A-J)
# Loaded from motor_configs.json
# ============================================================================

def load_configs():
    """Load motor configurations from JSON file"""
    config_path = os.path.join(os.path.dirname(__file__), 'motor_configs.json')
    with open(config_path, 'r') as f:
        return json.load(f)

# Load all configurations at startup
TEST_CONFIGS = load_configs()

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
    print("\n[A] Run ALL Tests (96 configs - ~8 hours estimated)")
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
    print("    CK - Category K: Ultra Current Quiet Optimization (8 configs - 7500mA)")
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

            elif choice.startswith('C') and len(choice) == 2:
                # Run specific category (CA, CB, CC, etc.)
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
