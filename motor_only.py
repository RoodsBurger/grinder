#!/usr/bin/python3
"""
Standalone motor control - runs in separate process
No display, no touch - just motor operation
"""
import sys
import time
import os
import RPi.GPIO as GPIO
from pololu_lib import HighPowerStepperDriver

# Hardware Pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7

# Motor Direction
MOTOR_DIRECTION = 1

def run_motor(target_rpm):
    """Run motor at target RPM until process is killed"""
    print(f"Motor process starting at {target_rpm} RPM (PID: {os.getpid()})")

    # Initialize motor driver
    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )
    driver.reset_settings()
    driver.set_current_milliamps(1000)
    driver.set_step_mode(32)

    # Set direction
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)

    # Enable driver
    driver.enable_driver()
    driver.clear_faults()

    # Close SPI - only GPIO needed now
    driver.spi.close()

    # Calculate delays
    steps_rev = 200
    microsteps = 1 << driver.step_mode_val
    steps_per_sec = (target_rpm * steps_rev * microsteps) / 60
    cruise_delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Acceleration profile
    accel_time = 2.0
    accel_profile = driver.calculate_accel_profile(target_rpm, accel_time, steps_rev)

    print(f"Motor running: {len(accel_profile)} accel steps, cruise delay {cruise_delay*1000:.3f}ms")

    # Local optimizations
    step_pin = STEP_PIN
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    t_next = time.perf_counter()

    try:
        # Acceleration
        for delay in accel_profile:
            gpio_out(step_pin, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            t_next += delay
            while time.perf_counter() < t_next: pass

        # Cruise forever (until killed)
        while True:
            gpio_out(step_pin, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            t_next += cruise_delay
            while time.perf_counter() < t_next: pass

    except KeyboardInterrupt:
        print("Motor process interrupted")
    finally:
        # Disable via sleep pin
        if driver.sleep_pin:
            GPIO.output(driver.sleep_pin, GPIO.LOW)
        print("Motor disabled")

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
