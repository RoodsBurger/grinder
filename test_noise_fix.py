#!/usr/bin/env python3
"""
Noise Fix Testing - Based on old quiet configuration + research
Tests combinations to find quietest settings
Using 6500mA motor current (100% of max rated current)
"""
import RPi.GPIO as GPIO
import spidev
import time
import sys

# --- PIN CONFIG ---
CS_PIN = 8
STEP_PIN = 25
DIR_PIN = 24
SLEEP_PIN = 7
LCD_CS_PIN = 22  # Disable LCD to prevent SPI interference

# --- CURRENT CALCULATION ---
# Target: 6500mA on 36v4 board
# Formula: current_doubled = 6500 * 2 = 13000
#          torque_bits = (384 * 13000) // 6875 = 726
#          726 > 255, so reduce gain:
#          ISGAIN = 1 (Gain 10), TORQUE = 181 (0xB5)
# CTRL with ISGAIN=01, 1/32 step: 0x428
# TORQUE with value 0xB5: 0x1B5
CURRENT_MA = 6500
TORQUE_VAL = 0x1B5  # 181 at Gain 10
CTRL_BASE = 0x428   # ISGAIN=01 (Gain 10), 1/32 step, disabled

# --- SETUP ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(CS_PIN, GPIO.OUT)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(SLEEP_PIN, GPIO.OUT)
GPIO.setup(LCD_CS_PIN, GPIO.OUT)
GPIO.output(CS_PIN, GPIO.LOW)
GPIO.output(SLEEP_PIN, GPIO.LOW)
GPIO.output(LCD_CS_PIN, GPIO.HIGH)  # Disable LCD

# --- SPI ---
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 500000  # REDUCED from 5MHz to minimize interference
spi.mode = 0

def write_reg(address, value):
    """Write to DRV8711 register"""
    cmd_msb = ((address & 0x07) << 4) | ((value >> 8) & 0x0F)
    cmd_lsb = value & 0xFF

    GPIO.output(CS_PIN, GPIO.HIGH)
    spi.xfer2([cmd_msb, cmd_lsb])
    GPIO.output(CS_PIN, GPIO.LOW)
    time.sleep(0.001)  # Small delay after write

def disable_driver():
    """Fully disable driver - no torque, no holding"""
    print("[*] Disabling driver...")
    write_reg(0x00, CTRL_BASE & ~0x01)  # Clear ENBL bit
    GPIO.output(SLEEP_PIN, GPIO.LOW)     # Sleep mode
    time.sleep(0.1)

def print_config(name, config):
    print(f"\n{'='*60}")
    print(f"  CONFIG: {name}")
    print(f"{'='*60}")
    for key, val in config.items():
        print(f"  {key:10s}: 0x{val:03X}")

# ==============================================================================
# TEST CONFIGURATIONS
# ==============================================================================

configs = {
    # ========== BASELINE: OLD WORKING CONFIG ==========
    "1_OLD_QUIET": {
        "description": f"Original quiet config @ {CURRENT_MA}mA",
        "TORQUE": TORQUE_VAL,  # 6500mA at Gain 10
        "CTRL":   CTRL_BASE,   # Gain 10, 1/32 step
        "OFF":    0x030,       # 41.7kHz PWM
        "BLANK":  0x080,       # NO ABT (old setting)
        "DECAY":  0x110,       # Slow/Mixed (old setting)
        "DRIVE":  0xA59,       # 150/300mA
        "STALL":  0x040,
    },

    # ========== HIGHER PWM FREQUENCY TESTS ==========
    "2_PWM_60KHZ": {
        "description": f"60kHz PWM @ {CURRENT_MA}mA",
        "TORQUE": TORQUE_VAL,
        "CTRL":   CTRL_BASE,
        "OFF":    0x020,  # 16µs = 62.5kHz (HIGHER than current!)
        "BLANK":  0x080,
        "DECAY":  0x110,
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },

    "3_PWM_80KHZ": {
        "description": f"80kHz PWM @ {CURRENT_MA}mA",
        "TORQUE": TORQUE_VAL,
        "CTRL":   CTRL_BASE,
        "OFF":    0x018,  # 12µs = 83.3kHz (VERY HIGH!)
        "BLANK":  0x040,  # Shorter blank for higher freq
        "DECAY":  0x110,
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },

    # ========== DECAY MODE TESTS ==========
    "4_FAST_DECAY": {
        "description": f"Fast decay @ {CURRENT_MA}mA",
        "TORQUE": TORQUE_VAL,
        "CTRL":   CTRL_BASE,
        "OFF":    0x030,
        "BLANK":  0x080,
        "DECAY":  0x210,  # Fast decay
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },

    "5_SLOW_DECAY": {
        "description": f"Pure slow decay @ {CURRENT_MA}mA",
        "TORQUE": TORQUE_VAL,
        "CTRL":   CTRL_BASE,
        "OFF":    0x030,
        "BLANK":  0x080,
        "DECAY":  0x010,  # Slow decay
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },

    # ========== CURRENT REDUCTION TEST ==========
    "6_LOW_CURRENT": {
        "description": "Reduce current to 4200mA (less vibration)",
        "TORQUE": 0x16A,  # 4200mA at Gain 10 (torque=117)
        "CTRL":   CTRL_BASE,
        "OFF":    0x030,
        "BLANK":  0x080,
        "DECAY":  0x110,
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },

    # ========== COMBINATION: OLD + HIGH PWM ==========
    "7_BEST_COMBO": {
        "description": "Old decay + 60kHz PWM + 5000mA",
        "TORQUE": 0x18F,  # 5000mA at Gain 10 (torque=139)
        "CTRL":   CTRL_BASE,
        "OFF":    0x020,  # 60kHz
        "BLANK":  0x080,  # Old setting
        "DECAY":  0x110,  # Old setting
        "DRIVE":  0xA59,
        "STALL":  0x040,
    },
}

def setup_driver(config):
    """Configure driver with given settings"""
    print_config(config["description"], {k:v for k,v in config.items() if k != "description"})

    # Write in correct order (CTRL last)
    write_reg(0x01, config["TORQUE"])
    write_reg(0x02, config["OFF"])
    write_reg(0x03, config["BLANK"])
    write_reg(0x04, config["DECAY"])
    write_reg(0x06, config["DRIVE"])
    write_reg(0x05, config["STALL"])
    write_reg(0x00, config["CTRL"])  # Disabled

    # Enable
    write_reg(0x00, config["CTRL"] | 0x01)
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    write_reg(0x07, 0x000)  # Clear faults
    time.sleep(0.1)

def run_test(rpm=150, duration=5):
    """Run motor at specified RPM with precise timing"""
    print(f"\n[*] Running motor at {rpm} RPM for {duration}s...")

    steps_per_rev = 200
    microsteps = 32
    steps_per_sec = (rpm * steps_per_rev * microsteps) / 60
    step_delay = 1.0 / steps_per_sec

    GPIO.output(DIR_PIN, 1)
    total_steps = int(steps_per_sec * duration)

    print(f"[*] Step delay: {step_delay*1000:.3f}ms ({steps_per_sec:.1f} steps/sec)")

    # Precise timing using perf_counter (same as motor_only.py)
    t_next = time.perf_counter()

    for i in range(total_steps):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        t_pulse = time.perf_counter()
        while time.perf_counter() - t_pulse < 0.000002: pass
        GPIO.output(STEP_PIN, GPIO.LOW)

        t_next += step_delay
        while time.perf_counter() < t_next: pass

    print("[*] Test complete")

def interactive_test():
    """Run configurations one by one with user feedback"""
    print("\n" + "="*60)
    print("  NOISE REDUCTION TEST - Interactive Mode")
    print("="*60)
    print(f"\nTesting at {CURRENT_MA}mA motor current")
    print("This will test 7 different configurations.")
    print("Listen carefully and rate each one.")
    print("\nPress ENTER to start...")
    input()

    results = {}

    for idx, (name, config) in enumerate(configs.items(), 1):
        print(f"\n\n{'#'*60}")
        print(f"  TEST {idx}/7: {name}")
        print(f"{'#'*60}")

        try:
            setup_driver(config)
            run_test(rpm=150, duration=4)

            # DISABLE DRIVER - no torque holding
            disable_driver()

            # Get user feedback
            print("\n" + "-"*60)
            rating = input(f"Rate noise level (1=quietest, 10=loudest): ")
            notes = input("Notes (or press ENTER): ")
            results[name] = {"rating": rating, "notes": notes}

            time.sleep(1)

        except KeyboardInterrupt:
            print("\n\nTest interrupted!")
            disable_driver()
            break

    # Summary
    print("\n\n" + "="*60)
    print("  TEST RESULTS SUMMARY")
    print("="*60)
    for name, result in results.items():
        print(f"{name:20s}: Rating={result['rating']:2s}  {result['notes']}")
    print("\n")

def main():
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "interactive":
            interactive_test()
        else:
            # Quick test mode - just run config 1 (old quiet)
            print("\nQuick Test Mode - Running OLD_QUIET config")
            print(f"Testing at {CURRENT_MA}mA motor current")
            print("For full interactive testing, run: sudo python3 test_noise_fix.py interactive\n")
            setup_driver(configs["1_OLD_QUIET"])
            run_test(rpm=150, duration=5)

            # DISABLE DRIVER - no torque holding
            disable_driver()
            print("\n[OK] Test complete - driver disabled")

    except KeyboardInterrupt:
        print("\n\nStopped by user")
        disable_driver()
    except Exception as e:
        print(f"\n\nERROR: {e}")
        import traceback
        traceback.print_exc()
        disable_driver()
    finally:
        # Final cleanup
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        GPIO.cleanup()
        spi.close()
        print("Cleanup complete")

if __name__ == "__main__":
    main()
