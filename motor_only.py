#!/usr/bin/python3
"""
Standalone motor control - runs in separate process
No display, no touch - just motor operation

MOTOR CONFIGURATION: J6 - Torque + Quiet Compromise v1 (from comprehensive testing)
- Current: 5000mA (119% motor rated - optimal torque/noise balance)
- Microstepping: 1/32 (very smooth)
- PWM Frequency: 62.5kHz (above audible, quieter than 41.7kHz)
- Adaptive Blanking: ENABLED (for smooth 1/32 stepping)
- Decay Mode: Slow/Mixed (0x110) - better torque than Auto-Mixed, quieter than Slow
- Gate Drive: 200/400mA MAX (0xF59) - strong switching for reliability
- SPI: 500kHz (matches Pololu Arduino library)

Test Results: Noise 6-7/10, Excellent torque, No stalling at low speeds
Based on 88-configuration comprehensive testing (2026-01-13)
Reference: https://github.com/pololu/high-power-stepper-driver-arduino
"""
import sys
import time
import os
import signal
import RPi.GPIO as GPIO
from pololu_lib import HighPowerStepperDriver

# DRV8711 Register addresses
REG_CTRL = 0x00
REG_TORQUE = 0x01
REG_OFF = 0x02
REG_BLANK = 0x03
REG_DECAY = 0x04
REG_DRIVE = 0x05
REG_STATUS = 0x06
REG_STALL = 0x07

# Global flag for graceful shutdown
shutdown_requested = False

def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful deceleration"""
    global shutdown_requested
    shutdown_requested = True

def verify_registers(driver):
    """Read and display all DRV8711 registers for verification"""
    print("\n[*] Verifying DRV8711 Configuration:")
    try:
        ctrl = driver._read_reg(REG_CTRL)
        torque = driver._read_reg(REG_TORQUE)
        off = driver._read_reg(REG_OFF)
        blank = driver._read_reg(REG_BLANK)
        decay = driver._read_reg(REG_DECAY)
        drive = driver._read_reg(REG_DRIVE)
        stall = driver._read_reg(REG_STALL)

        print(f"    CTRL   (0x00): 0x{ctrl:03X}   - Step mode, enable, gain")
        print(f"    TORQUE (0x01): 0x{torque:03X}   - Current setting (5000mA)")
        print(f"    OFF    (0x02): 0x{off:03X}   - PWM frequency (62.5kHz @ 0x020)")
        print(f"    BLANK  (0x03): 0x{blank:03X}   - Blanking time (ABT @ 0x180)")
        print(f"    DECAY  (0x04): 0x{decay:03X}   - Decay mode (Slow/Mixed @ 0x110)")
        print(f"    DRIVE  (0x05): 0x{drive:03X}   - Gate drive current (0xF59 = 200/400mA MAX)")
        print(f"    STALL  (0x07): 0x{stall:03X}   - Stall detection threshold")

        return True
    except Exception as e:
        print(f"    [!] WARNING: Cannot read registers (MISO issue): {e}")
        print(f"    Continuing in BLIND mode - motor should still work")
        return False

def check_status(driver):
    """Check STATUS register for faults"""
    try:
        status = driver._read_reg(REG_STATUS)

        # Check critical faults
        faults = []
        if status & (1 << 5): faults.append("UVLO (Under Voltage)")
        if status & (1 << 4): faults.append("BPDF (Ch B Predriver Fault)")
        if status & (1 << 3): faults.append("APDF (Ch A Predriver Fault)")
        if status & (1 << 2): faults.append("BOCP (Ch B Over Current)")
        if status & (1 << 1): faults.append("AOCP (Ch A Over Current)")
        if status & (1 << 0): faults.append("OTS (Over Temperature)")

        if faults:
            print(f"\n[!] CRITICAL FAULTS DETECTED: {', '.join(faults)}")
            return False

        return True
    except:
        return True  # Assume OK if can't read

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

    print(f"\n{'='*60}")
    print(f"  Motor Control Starting - Target: {target_rpm} RPM")
    print(f"{'='*60}")

    # Initialize motor driver
    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )

    # Configure driver with J6 optimized settings
    print("\n[*] Configuring DRV8711 driver with J6 settings...")
    driver.reset_settings()
    driver.set_current_milliamps(5000)  # 119% rated - optimal torque/noise balance
    driver.set_step_mode(32)  # 1/32 microstepping for smoothest, quietest operation

    # Override defaults with J6 optimizations
    driver._write_reg(REG_OFF, 0x020)     # 62.5kHz PWM (quieter than 41.7kHz)
    driver._write_reg(REG_DECAY, 0x110)   # Slow/Mixed decay (better torque, still quiet)
    driver._write_reg(REG_DRIVE, 0xF59)   # 200/400mA MAX (strong gate drive)
    # BLANK already set to 0x180 (ABT enabled) by reset_settings()
    time.sleep(0.01)  # Let settings settle

    # Verify configuration by reading registers
    can_read_spi = verify_registers(driver)

    # Clear any old faults from previous runs BEFORE checking status
    if can_read_spi:
        print("\n[*] Clearing any old faults...")
        driver.clear_faults()
        time.sleep(0.01)

    # Check status after clearing faults
    if can_read_spi:
        print("[*] Checking STATUS register...")
        if not check_status(driver):
            print("[!] CRITICAL: Faults detected even after clearing!")
            print("[!] Check wiring and power supply")
            driver.disable_driver()
            return

    # Set direction and enable
    print(f"\n[*] Enabling driver and starting motor at {target_rpm} RPM...")
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)
    driver.enable_driver()

    # Verify enabled successfully
    if can_read_spi:
        time.sleep(0.1)
        if not check_status(driver):
            print("[!] WARNING: Faults detected after enabling")
            # Continue anyway - might clear during operation

    # Close SPI - only GPIO needed for stepping
    driver.spi.close()
    print("[*] SPI closed, entering high-speed stepping loop...")
    print("[*] Press Ctrl+C to stop\n")

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
        print("\n[*] Shutdown requested...")
    finally:
        # Disable driver completely
        print("[*] Disabling driver...")
        if driver.sleep_pin:
            GPIO.output(driver.sleep_pin, GPIO.LOW)
        print("[*] Motor stopped")

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
