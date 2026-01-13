#!/usr/bin/python3
"""
Motor Noise Testing Script - Find Optimal DRV8711 Settings
Tests different configurations to find the quietest operation
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

# Test configurations
TEST_CONFIGS = {
    "1_current_3800": {
        "name": "Lower Current (3.8A - 90%)",
        "current": 3800,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "2_current_4200": {
        "name": "Rated Current (4.2A - 100%) - BASELINE",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "3_off_short": {
        "name": "Shorter OFF Time (40µs - higher PWM freq)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x050, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "4_off_long": {
        "name": "Longer OFF Time (160µs - lower PWM freq)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x140, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "5_off_very_long": {
        "name": "Very Long OFF Time (256µs - very low PWM)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x1FF, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "6_blank_abt": {
        "name": "Adaptive Blanking Time (ABT enabled)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0xA59}  # ABT bit 8
    },
    "7_drive_low": {
        "name": "Lower DRIVE currents (100/50mA)",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA31}  # IDRIVEN=100, IDRIVEP=50
    },
    "8_step_8": {
        "name": "1/8 Microstepping (more torque)",
        "current": 4200,
        "step_mode": 8,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x510, "DRIVE": 0xA59}
    },
    "9_step_32": {
        "name": "1/32 Microstepping (smoother/quieter)",
        "current": 4200,
        "step_mode": 32,
        "regs": {"OFF": 0x0A0, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0xA59}  # ABT enabled for 1/32
    },
    "10_decay_slow": {
        "name": "Slow Decay Mode",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x010, "DRIVE": 0xA59}
    },
    "11_decay_mixed": {
        "name": "Mixed Decay Mode",
        "current": 4200,
        "step_mode": 16,
        "regs": {"OFF": 0x0A0, "BLANK": 0x080, "DECAY": 0x310, "DRIVE": 0xA59}
    },
    "12_combo_quiet": {
        "name": "QUIET COMBO (32step+ABT+low drive)",
        "current": 3800,
        "step_mode": 32,
        "regs": {"OFF": 0x0C0, "BLANK": 0x180, "DECAY": 0x510, "DRIVE": 0xA31}
    },
}

def decode_settings(config):
    """Decode register values for display"""
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

    # Write custom registers
    driver._write_reg(0x02, config["regs"]["OFF"])     # OFF register
    driver._write_reg(0x03, config["regs"]["BLANK"])   # BLANK register
    driver._write_reg(0x04, config["regs"]["DECAY"])   # DECAY register
    driver._write_reg(0x06, config["regs"]["DRIVE"])   # DRIVE register

    GPIO.output(DIR_PIN, 1)
    driver.enable_driver()
    driver.clear_faults()
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

def main():
    print("="*70)
    print("DRV8711 Motor Noise Testing Script")
    print("="*70)
    print("\nThis script will test different driver configurations.")
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

        # List all tests
        print(f"\n{'='*70}")
        print("Available tests:")
        for key, config in sorted(TEST_CONFIGS.items()):
            print(f"  {key}: {config['name']}")

        print(f"\n{'='*70}")
        choice = input("\nRun [A]ll tests, [S]pecific test, or [C]ustom? (A/S/C): ").strip().upper()

        if choice == 'A':
            # Run all tests
            for key, config in sorted(TEST_CONFIGS.items()):
                run_test(config, rpm, duration)
                input("\nPress ENTER for next test (Ctrl+C to stop)...")

        elif choice == 'S':
            # Run specific test
            test_id = input("Enter test ID (e.g., 2_current_4200): ").strip()
            if test_id in TEST_CONFIGS:
                while True:
                    run_test(TEST_CONFIGS[test_id], rpm, duration)
                    if input("\nTest again? (y/n): ").lower() != 'y':
                        break
            else:
                print(f"Invalid test ID: {test_id}")

        elif choice == 'C':
            # Custom configuration
            print("\nCustom configuration:")
            current = int(input("Current (mA, 3000-4200): "))
            step_mode = int(input("Step mode (1,2,4,8,16,32,64,128,256): "))
            off_reg = int(input("OFF register (hex, e.g., 0x0A0): "), 16)
            blank_reg = int(input("BLANK register (hex, e.g., 0x080): "), 16)
            decay_reg = int(input("DECAY register (hex, e.g., 0x510): "), 16)
            drive_reg = int(input("DRIVE register (hex, e.g., 0xA59): "), 16)

            custom_config = {
                "name": "Custom Configuration",
                "current": current,
                "step_mode": step_mode,
                "regs": {
                    "OFF": off_reg,
                    "BLANK": blank_reg,
                    "DECAY": decay_reg,
                    "DRIVE": drive_reg
                }
            }

            while True:
                run_test(custom_config, rpm, duration)
                if input("\nTest again? (y/n): ").lower() != 'y':
                    break

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    finally:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        print("\n" + "="*70)
        print("Testing complete!")
        print("="*70)

if __name__ == "__main__":
    main()
