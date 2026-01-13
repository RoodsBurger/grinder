#!/usr/bin/python3
"""
Motor Noise Testing Script - Comprehensive DRV8711 Configuration Testing
Tests systematic variations of all critical parameters to find quietest operation
"""
import time
import sys
import RPi.GPIO as GPIO
from pololu_lib import HighPowerStepperDriver

# Hardware pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7

# Test configurations organized by category
TEST_CONFIGS = {
    # ========== BASELINE TESTS ==========
    "baseline_old": {
        "name": "BASELINE OLD (before DRIVE fix)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "baseline_new": {
        "name": "BASELINE NEW (with DRIVE fix)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== PWM FREQUENCY SWEEP (OFF register) ==========
    "pwm_20us": {
        "name": "PWM: 20µs OFF (50 kHz - very high, above audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x028, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_30us": {
        "name": "PWM: 30µs OFF (33 kHz - high, above audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x03C, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_40us": {
        "name": "PWM: 40µs OFF (25 kHz - above audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_60us": {
        "name": "PWM: 60µs OFF (16.7 kHz - borderline audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x078, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_80us": {
        "name": "PWM: 80µs OFF (12.5 kHz - AUDIBLE current default)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_120us": {
        "name": "PWM: 120µs OFF (8.3 kHz - audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x0F0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_160us": {
        "name": "PWM: 160µs OFF (6.25 kHz - lower audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x140, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_200us": {
        "name": "PWM: 200µs OFF (5 kHz - low audible)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x190, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "pwm_250us": {
        "name": "PWM: 250µs OFF (4 kHz - very low)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x1F4, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== BLANKING TIME SWEEP (BLANK register) ==========
    "blank_0p5us": {
        "name": "BLANK: 0.5µs (minimal blanking)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x019, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "blank_1us": {
        "name": "BLANK: 1.0µs (short blanking)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x032, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "blank_2p5us": {
        "name": "BLANK: 2.56µs (default)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "blank_3us": {
        "name": "BLANK: 3.0µs (medium blanking)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x096, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "blank_5us": {
        "name": "BLANK: 5.0µs (longer blanking)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x0FA, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "blank_abt": {
        "name": "BLANK: ABT Enabled (adaptive blanking)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== DRIVE CURRENT SWEEP (DRIVE register) ==========
    "drive_min": {
        "name": "DRIVE: Minimum (50mA / 100mA - TI recommended)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "drive_low": {
        "name": "DRIVE: Low (100mA / 200mA)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "drive_med": {
        "name": "DRIVE: Medium (150mA / 300mA)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xE59}
    },
    "drive_high": {
        "name": "DRIVE: High (200mA / 400mA - max)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xF59}
    },

    # ========== MICROSTEPPING SWEEP ==========
    "step_4": {
        "name": "STEP: 1/4 (good torque, moderate noise)",
        "current": 4200,
        "step_mode": 4,
        "regs": {"CTRL": 0xC10, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "step_8": {
        "name": "STEP: 1/8 (balanced)",
        "current": 4200,
        "step_mode": 8,
        "regs": {"CTRL": 0xC18, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "step_16": {
        "name": "STEP: 1/16 (smooth, quiet)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "step_32": {
        "name": "STEP: 1/32 (very smooth, needs ABT)",
        "current": 4200,
        "step_mode": 32,
        "regs": {"CTRL": 0xC28, "OFF": 0x050, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "step_64": {
        "name": "STEP: 1/64 (ultra smooth, reduced torque)",
        "current": 4200,
        "step_mode": 64,
        "regs": {"CTRL": 0xC30, "OFF": 0x050, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "step_128": {
        "name": "STEP: 1/128 (max smoothness, low torque)",
        "current": 4200,
        "step_mode": 128,
        "regs": {"CTRL": 0xC38, "OFF": 0x050, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== DECAY MODE SWEEP ==========
    "decay_slow": {
        "name": "DECAY: Slow (smoothest, can distort)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x010, "DRIVE": 0x059}
    },
    "decay_slow_mixed": {
        "name": "DECAY: Slow/Mixed",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x110, "DRIVE": 0x059}
    },
    "decay_fast": {
        "name": "DECAY: Fast (fastest response, noisy)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x210, "DRIVE": 0x059}
    },
    "decay_mixed": {
        "name": "DECAY: Mixed (balanced)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x310, "DRIVE": 0x059}
    },
    "decay_slow_auto": {
        "name": "DECAY: Slow/Auto",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x410, "DRIVE": 0x059}
    },
    "decay_auto_mixed": {
        "name": "DECAY: Auto-Mixed (default, adaptive)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== CURRENT VARIATIONS ==========
    "current_3400": {
        "name": "CURRENT: 3.4A (80% rated)",
        "current": 3400,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "current_3800": {
        "name": "CURRENT: 3.8A (90% rated)",
        "current": 3800,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "current_4200": {
        "name": "CURRENT: 4.2A (100% rated)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0x059}
    },

    # ========== OPTIMIZED COMBINATIONS ==========
    "combo_silent_high_freq": {
        "name": "COMBO: Silent (high PWM freq approach)",
        "current": 4200,
        "step_mode": 32,
        "regs": {"CTRL": 0xC28, "OFF": 0x030, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "combo_silent_low_freq": {
        "name": "COMBO: Silent (low PWM freq approach)",
        "current": 4200,
        "step_mode": 32,
        "regs": {"CTRL": 0xC28, "OFF": 0x1F4, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "combo_balanced": {
        "name": "COMBO: Balanced (16step + medium PWM)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"CTRL": 0xC20, "OFF": 0x050, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
    "combo_torque": {
        "name": "COMBO: Max Torque (8step + high current)",
        "current": 4200,
        "step_mode": 8,
        "regs": {"CTRL": 0xC18, "OFF": 0x050, "BLANK": 0x080, "DECAY": 0x310, "DRIVE": 0x059}
    },
    "combo_ultra_quiet": {
        "name": "COMBO: Ultra Quiet (128step + low current)",
        "current": 3400,
        "step_mode": 128,
        "regs": {"CTRL": 0xC38, "OFF": 0x030, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0x059}
    },
}

def decode_settings(config):
    """Decode register values for display"""
    ctrl = config["regs"].get("CTRL", 0xC20)
    off = config["regs"]["OFF"]
    blank = config["regs"]["BLANK"]
    decay = config["regs"]["DECAY"]
    drive = config["regs"]["DRIVE"]

    off_time = off * 0.5
    blank_time = blank * 0.020
    abt_enabled = (blank >> 8) & 0x01
    decay_mode = (decay >> 8) & 0x7
    mixed_decay = (decay & 0xFF) * 0.5

    # DRIVE register bits
    idrivep = (drive >> 8) & 0x03  # bits 9:8
    idriven = (drive >> 10) & 0x03  # bits 11:10

    idrivep_ma = [50, 100, 150, 200][idrivep]
    idriven_ma = [100, 200, 300, 400][idriven]

    # CTRL register - microstepping is bits 6:3
    step_bits = (ctrl >> 3) & 0x0F

    decay_modes = ['Slow', 'Slow/Mixed', 'Fast', 'Mixed', 'Slow/Auto', 'Auto-Mixed', 'Reserved', 'Reserved']

    return {
        "off_time_us": off_time,
        "blank_time_us": blank_time,
        "abt_enabled": abt_enabled,
        "decay_mode": decay_modes[decay_mode],
        "mixed_decay_us": mixed_decay,
        "idrivep_ma": idrivep_ma,
        "idriven_ma": idriven_ma,
        "pwm_freq_khz": 1000.0 / off_time if off_time > 0 else 0
    }

def run_test(config, rpm=200, duration=5):
    """Run motor with given configuration"""
    print(f"\n{'='*70}")
    print(f"Testing: {config['name']}")
    print(f"{'='*70}")

    # Decode settings
    settings = decode_settings(config)

    print(f"Current: {config['current']}mA")
    print(f"Microstepping: 1/{config['step_mode']}")
    print(f"OFF Time: {settings['off_time_us']:.1f}µs (PWM ~{settings['pwm_freq_khz']:.1f}kHz)")
    print(f"Blank Time: {settings['blank_time_us']:.2f}µs (ABT: {'ON' if settings['abt_enabled'] else 'OFF'})")
    print(f"Decay Mode: {settings['decay_mode']}")
    print(f"DRIVE: IDRIVEP={settings['idrivep_ma']}mA, IDRIVEN={settings['idriven_ma']}mA")
    print(f"\nRunning at {rpm} RPM for {duration} seconds...")
    print("Listen carefully to the motor noise!")

    # Initialize driver
    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )

    # Apply configuration
    driver.reset_settings()
    driver.set_current_milliamps(config["current"])
    driver.set_step_mode(config["step_mode"])

    # Write custom registers INSTEAD of using set_step_mode()
    # This ensures we have full control over all register values
    if "CTRL" in config["regs"]:
        driver._write_reg(0x00, config["regs"]["CTRL"])    # CTRL register
    driver._write_reg(0x02, config["regs"]["OFF"])     # OFF register
    driver._write_reg(0x03, config["regs"]["BLANK"])   # BLANK register
    driver._write_reg(0x04, config["regs"]["DECAY"])   # DECAY register
    driver._write_reg(0x06, config["regs"]["DRIVE"])   # DRIVE register

    GPIO.output(DIR_PIN, 1)
    driver.enable_driver()
    driver.clear_faults()

    # VERIFICATION: Read back registers BEFORE closing SPI
    print("\n--- REGISTER VERIFICATION ---")
    ctrl_readback = driver._read_reg(0x00)
    off_readback = driver._read_reg(0x02)
    blank_readback = driver._read_reg(0x03)
    decay_readback = driver._read_reg(0x04)
    drive_readback = driver._read_reg(0x06)

    # Decode CTRL register microstepping (bits 6:3)
    step_bits_readback = (ctrl_readback >> 3) & 0x0F
    step_mode_map = {0: 1, 1: 2, 2: 4, 3: 8, 4: 16, 5: 32, 6: 64, 7: 128, 8: 256}
    actual_step = step_mode_map.get(step_bits_readback, "UNKNOWN")

    # Decode DRIVE register (use readback value, not config)
    idrivep_readback = (drive_readback >> 8) & 0x03
    idriven_readback = (drive_readback >> 10) & 0x03
    idrivep_ma_readback = [50, 100, 150, 200][idrivep_readback]
    idriven_ma_readback = [100, 200, 300, 400][idriven_readback]

    print(f"CTRL:  Written=0x{config['regs'].get('CTRL', 0xC20):03X}, Read=0x{ctrl_readback:03X} (1/{actual_step} step)")
    print(f"OFF:   Written=0x{config['regs']['OFF']:03X}, Read=0x{off_readback:03X}")
    print(f"BLANK: Written=0x{config['regs']['BLANK']:03X}, Read=0x{blank_readback:03X}")
    print(f"DECAY: Written=0x{config['regs']['DECAY']:03X}, Read=0x{decay_readback:03X}")
    print(f"DRIVE: Written=0x{config['regs']['DRIVE']:03X}, Read=0x{drive_readback:03X} ({idrivep_ma_readback}/{idriven_ma_readback}mA)")

    # Check for mismatches
    ctrl_expected = config["regs"].get("CTRL", 0xC20)
    if ctrl_readback != ctrl_expected:
        print(f"⚠️  WARNING: CTRL mismatch! Expected 1/{config['step_mode']}, got 1/{actual_step}")
    if off_readback != config["regs"]["OFF"]:
        print(f"⚠️  WARNING: OFF register mismatch!")
    if drive_readback != config["regs"]["DRIVE"]:
        print(f"⚠️  WARNING: DRIVE register mismatch!")

    print("-----------------------------\n")

    # NOW close SPI after verification
    driver.spi.close()

    # Calculate step timing
    steps_rev = 200
    microsteps = 1 << driver.step_mode_val
    steps_per_sec = (rpm * steps_rev * microsteps) / 60
    delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Run motor
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    t_start = time.time()
    t_next = time.perf_counter()

    try:
        while time.time() - t_start < duration:
            gpio_out(STEP_PIN, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(STEP_PIN, gpio_low)

            t_next += delay
            while time.perf_counter() < t_next: pass
    except KeyboardInterrupt:
        pass
    finally:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        time.sleep(0.5)

def list_tests_by_category():
    """Print organized test menu"""
    categories = {
        "BASELINE": ["baseline_old", "baseline_new"],
        "PWM FREQUENCY": [k for k in TEST_CONFIGS.keys() if k.startswith("pwm_")],
        "BLANKING TIME": [k for k in TEST_CONFIGS.keys() if k.startswith("blank_")],
        "DRIVE CURRENT": [k for k in TEST_CONFIGS.keys() if k.startswith("drive_")],
        "MICROSTEPPING": [k for k in TEST_CONFIGS.keys() if k.startswith("step_")],
        "DECAY MODE": [k for k in TEST_CONFIGS.keys() if k.startswith("decay_")],
        "CURRENT LEVEL": [k for k in TEST_CONFIGS.keys() if k.startswith("current_")],
        "OPTIMIZED COMBOS": [k for k in TEST_CONFIGS.keys() if k.startswith("combo_")]
    }

    for category, tests in categories.items():
        if tests:
            print(f"\n{category}:")
            for test_id in tests:
                if test_id in TEST_CONFIGS:
                    print(f"  {test_id}: {TEST_CONFIGS[test_id]['name']}")

def main():
    print("="*70)
    print("DRV8711 Comprehensive Motor Noise Testing Script")
    print(f"Total configurations: {len(TEST_CONFIGS)}")
    print("="*70)
    print("\nThis script tests systematic variations of DRV8711 parameters.")
    print("Listen carefully to identify the QUIETEST setting.\n")

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    try:
        # Get test parameters
        print("Test parameters:")
        rpm = int(input("RPM to test (default 200): ") or "200")
        duration = int(input("Seconds per test (default 5): ") or "5")

        # List all tests by category
        print(f"\n{'='*70}")
        list_tests_by_category()

        print(f"\n{'='*70}")
        print("\nRecommended testing sequence:")
        print("  1. baseline_new (verify DRIVE fix)")
        print("  2. combo_balanced (good starting point)")
        print("  3. combo_silent_high_freq OR combo_silent_low_freq")
        print("  4. Experiment with individual parameters based on results")

        print(f"\n{'='*70}")
        choice = input("\nRun [A]ll tests, [C]ategory, [S]pecific test, or [Q]uick recommended? (A/C/S/Q): ").strip().upper()

        if choice == 'A':
            # Run all tests
            for key, config in sorted(TEST_CONFIGS.items()):
                run_test(config, rpm, duration)
                input("\nPress ENTER for next test (Ctrl+C to stop)...")

        elif choice == 'C':
            # Run category
            print("\nCategories:")
            print("  1. BASELINE")
            print("  2. PWM FREQUENCY")
            print("  3. BLANKING TIME")
            print("  4. DRIVE CURRENT")
            print("  5. MICROSTEPPING")
            print("  6. DECAY MODE")
            print("  7. CURRENT LEVEL")
            print("  8. OPTIMIZED COMBOS")
            cat_num = int(input("Select category (1-8): "))

            cat_map = {
                1: [k for k in TEST_CONFIGS.keys() if "baseline" in k],
                2: [k for k in TEST_CONFIGS.keys() if k.startswith("pwm_")],
                3: [k for k in TEST_CONFIGS.keys() if k.startswith("blank_")],
                4: [k for k in TEST_CONFIGS.keys() if k.startswith("drive_")],
                5: [k for k in TEST_CONFIGS.keys() if k.startswith("step_")],
                6: [k for k in TEST_CONFIGS.keys() if k.startswith("decay_")],
                7: [k for k in TEST_CONFIGS.keys() if k.startswith("current_")],
                8: [k for k in TEST_CONFIGS.keys() if k.startswith("combo_")]
            }

            if cat_num in cat_map:
                for test_id in cat_map[cat_num]:
                    run_test(TEST_CONFIGS[test_id], rpm, duration)
                    input("\nPress ENTER for next test (Ctrl+C to stop)...")

        elif choice == 'S':
            # Run specific test
            test_id = input("Enter test ID: ").strip()
            if test_id in TEST_CONFIGS:
                while True:
                    run_test(TEST_CONFIGS[test_id], rpm, duration)
                    if input("\nTest again? (y/n): ").lower() != 'y':
                        break
            else:
                print(f"Invalid test ID: {test_id}")

        elif choice == 'Q':
            # Quick recommended sequence
            recommended = ["baseline_new", "combo_balanced", "combo_silent_high_freq", "combo_silent_low_freq"]
            for test_id in recommended:
                run_test(TEST_CONFIGS[test_id], rpm, duration)
                input("\nPress ENTER for next test (Ctrl+C to stop)...")

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    finally:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        print("\n" + "="*70)
        print("Testing complete!")
        print("="*70)

if __name__ == "__main__":
    main()
