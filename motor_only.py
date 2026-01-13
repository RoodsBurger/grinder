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

    # Configure driver using high-level API
    # Library defaults are already optimized (ABT enabled, 41.7kHz PWM, Auto-Mixed decay)
    driver.reset_settings()
    driver.set_current_milliamps(4200)  # Match motor rated current (4.2A)
    driver.set_step_mode(32)  # 1/32 microstepping for smoothest, quietest operation

    # Set direction and enable
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)
    driver.enable_driver()
    driver.clear_faults()

    # Close SPI - only GPIO needed for stepping
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
