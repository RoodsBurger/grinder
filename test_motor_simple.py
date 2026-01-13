#!/usr/bin/env python3
"""
SIMPLE MOTOR TEST - Minimal code to verify hardware works
Uses optimized quiet configuration (41.7kHz PWM, 1/32 step, ABT enabled)
"""
import RPi.GPIO as GPIO
import spidev
import time
import sys

# --- PIN CONFIG (Grinder hardware) ---
CS_PIN = 8       # SPI Chip Select
STEP_PIN = 25    # Step pulse
DIR_PIN = 24     # Direction
SLEEP_PIN = 7    # Sleep/Enable

# --- SETUP ---
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(CS_PIN, GPIO.OUT)
GPIO.setup(STEP_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(SLEEP_PIN, GPIO.OUT)
GPIO.output(CS_PIN, GPIO.LOW)  # Inactive (Active High for DRV8711)
GPIO.output(SLEEP_PIN, GPIO.LOW)  # Start disabled

# --- SPI ---
spi = spidev.SpiDev()
spi.open(0, 0)
spi.max_speed_hz = 5000000  # 5MHz (DRV8711 max)
spi.mode = 0

def write_reg(address, value):
    """Write to DRV8711 register"""
    cmd_msb = ((address & 0x07) << 4) | ((value >> 8) & 0x0F)
    cmd_lsb = value & 0xFF

    GPIO.output(CS_PIN, GPIO.HIGH)  # Active
    spi.xfer2([cmd_msb, cmd_lsb])
    GPIO.output(CS_PIN, GPIO.LOW)   # Inactive

def setup_driver():
    """Configure driver with optimized quiet settings"""
    print("Configuring DRV8711...")

    # Configuration: combo_pololu_32step (quietest proven settings)
    # Current: 4200mA, 1/32 step, 41.7kHz PWM, ABT enabled

    # Calculate TORQUE for 4200mA with ISGAIN=20 (bits 9:8 = 0b10)
    # Formula: torque_bits = (384 * (4200*2)) / 6875 = 234 = 0xEA
    TORQUE = 0xEA   # 4200mA at gain 20

    # CTRL: Gain 20 (bits 9:8=10), 1/32 step (bits 6:3=0101), Disabled (bit 0=0)
    CTRL   = 0xA28  # 1010 0010 1000

    # Other registers (optimized for quiet operation)
    OFF    = 0x030  # 24µs = 41.7kHz PWM (ABOVE audible!)
    BLANK  = 0x180  # ABT enabled (bit 8) for smooth 1/32 stepping
    DECAY  = 0x510  # Auto-Mixed decay (TI recommended)
    STALL  = 0x040  # Default stall detection
    DRIVE  = 0xA59  # 150/300mA gate drive (Pololu default)

    # Write registers in Pololu order (CTRL last)
    write_reg(0x01, TORQUE)  # TORQUE
    write_reg(0x02, OFF)     # OFF
    write_reg(0x03, BLANK)   # BLANK
    write_reg(0x04, DECAY)   # DECAY
    write_reg(0x06, DRIVE)   # DRIVE
    write_reg(0x05, STALL)   # STALL
    write_reg(0x00, CTRL)    # CTRL (last, still disabled)

    # Enable driver
    write_reg(0x00, CTRL | 0x01)  # Set ENBL bit
    GPIO.output(SLEEP_PIN, GPIO.HIGH)  # Wake up

    # Clear faults
    write_reg(0x07, 0x000)  # STATUS

    print("Driver configured: 4200mA, 1/32 step, 41.7kHz PWM, ABT enabled")

def run_motor(rpm=200, duration=5):
    """Run motor at specified RPM for duration seconds"""
    print(f"Running motor at {rpm} RPM for {duration} seconds...")

    # Calculate step timing for 1/32 microstepping
    steps_per_rev = 200  # Motor native steps
    microsteps = 32      # 1/32 microstepping
    steps_per_sec = (rpm * steps_per_rev * microsteps) / 60
    step_delay = 1.0 / steps_per_sec

    print(f"Step delay: {step_delay*1000:.3f}ms ({steps_per_sec:.1f} steps/sec)")

    GPIO.output(DIR_PIN, 1)  # Set direction

    total_steps = int(steps_per_sec * duration)
    print(f"Total steps: {total_steps}")

    # Stepping loop with timing
    t_start = time.perf_counter()
    for i in range(total_steps):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.000002)  # 2µs pulse
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(step_delay - 0.000002)

    elapsed = time.perf_counter() - t_start
    actual_rpm = (total_steps / microsteps / steps_per_rev) * 60 / elapsed
    print(f"Completed in {elapsed:.2f}s (actual RPM: {actual_rpm:.1f})")

try:
    setup_driver()
    time.sleep(0.2)  # Let driver stabilize

    if len(sys.argv) > 1:
        rpm = int(sys.argv[1])
    else:
        rpm = 200

    if len(sys.argv) > 2:
        duration = int(sys.argv[2])
    else:
        duration = 5

    run_motor(rpm, duration)

    print("\nMotor test complete!")

except KeyboardInterrupt:
    print("\n\nStopped by user")
except Exception as e:
    print(f"\n\nERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    # Disable driver
    write_reg(0x00, 0xA28)  # Clear ENBL bit
    GPIO.output(SLEEP_PIN, GPIO.LOW)
    GPIO.cleanup()
    spi.close()
    print("Cleanup complete")
