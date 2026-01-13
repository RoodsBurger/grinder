#!/usr/bin/python3
"""
Standalone motor control - runs in separate process
No display, no touch - just motor operation

MOTOR CONFIGURATION: combo_pololu_32step (recommended quiet settings)
- Current: 4200mA (100% motor rated)
- Microstepping: 1/32 (very smooth)
- PWM Frequency: 41.7kHz (ABOVE audible, Pololu default)
- Adaptive Blanking: ENABLED (for smooth 1/32 stepping)
- Decay Mode: Auto-Mixed (TI recommended)
- Gate Drive: 150/300mA (Pololu default)

Based on official Pololu library + noise optimization testing.
Reference: https://github.com/pololu/high-power-stepper-driver-arduino
"""
import sys
import time
import os
import signal
import RPi.GPIO as GPIO
from pololu_lib import HighPowerStepperDriver

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful deceleration"""
    global shutdown_requested
    shutdown_requested = True

# Hardware Pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7

# Motor Direction
MOTOR_DIRECTION = 1

def run_motor(target_rpm):
    """Run motor at target RPM until process is killed"""
    global shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Initialize motor driver
    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )

    # CONFIGURATION: Pololu + 32step + ABT (quietest recommended settings)
    # Based on official Pololu library + noise optimization testing
    current = 4200  # Match motor rated current (4.2A)
    step_mode = 32  # 1/32 microstepping (very smooth, quiet)

    # Configure current (this calculates ISGAIN and TORQUE)
    driver.set_current_milliamps(current)

    # Custom register configuration
    # Reference: combo_pololu_32step from test_motor_noise.py
    CTRL_CUSTOM  = 0xC28  # 1/32 step mode (bits 6:3 = 0101)
    OFF_POLOLU   = 0x030  # 24µs = 41.7kHz PWM (ABOVE audible range!)
    BLANK_ABT    = 0x180  # ABT enabled (bit 8 set) for smooth 1/32 stepping
    DECAY_AUTO   = 0x510  # Auto-Mixed decay (TI recommended)
    DRIVE_POLOLU = 0xA59  # 150/300mA gate drive (Pololu default)

    # Preserve ISGAIN from set_current_milliamps, merge with custom step mode
    isgain_bits = driver.regs[0x00] & 0x0300  # Extract ISGAIN (bits 9:8)
    merged_ctrl = (CTRL_CUSTOM & ~0x0300) | isgain_bits  # Merge

    # Update cache (CRITICAL: enable_driver() reads from cache!)
    driver.regs[0x00] = merged_ctrl
    driver.regs[0x02] = OFF_POLOLU
    driver.regs[0x03] = BLANK_ABT
    driver.regs[0x04] = DECAY_AUTO
    driver.regs[0x06] = DRIVE_POLOLU

    # Update step_mode_val for correct RPM calculations
    driver.step_mode_val = 5  # 1/32 step (index 5 in step map)

    # Write all registers to hardware
    driver._write_reg(0x02, driver.regs[0x02])  # OFF
    driver._write_reg(0x03, driver.regs[0x03])  # BLANK
    driver._write_reg(0x04, driver.regs[0x04])  # DECAY
    driver._write_reg(0x06, driver.regs[0x06])  # DRIVE
    driver._write_reg(0x00, driver.regs[0x00])  # CTRL (write last)

    # Set direction
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)

    # Enable driver (uses cached CTRL value)
    driver.enable_driver()
    driver.clear_faults()

    # Close SPI - only GPIO needed now
    driver.spi.close()

    # Calculate delays
    steps_rev = 200
    microsteps = 1 << driver.step_mode_val
    steps_per_sec = (target_rpm * steps_rev * microsteps) / 60
    cruise_delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Local optimizations
    step_pin = STEP_PIN
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    t_next = time.perf_counter()

    try:
        # Run at constant speed until shutdown requested (no acceleration/deceleration)
        while not shutdown_requested:
            gpio_out(step_pin, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            t_next += cruise_delay
            while time.perf_counter() < t_next: pass

    except KeyboardInterrupt:
        pass
    finally:
        # Disable via sleep pin
        if driver.sleep_pin:
            GPIO.output(driver.sleep_pin, GPIO.LOW)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: motor_only.py <RPM>")
        sys.exit(1)

    try:
        rpm = int(sys.argv[1])
        if rpm < 0 or rpm > 300:
            print("RPM must be 0-300")
            sys.exit(1)

        run_motor(rpm)
    except ValueError:
        print("Invalid RPM value")
        sys.exit(1)
