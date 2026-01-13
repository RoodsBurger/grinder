#!/usr/bin/env python3
"""
DRV8711 Motor Driver Diagnostic Tool
Tests SPI communication, fault detection, and motor movement
"""
import time
import RPi.GPIO as GPIO
import spidev
from StepperLibrary import StepperMotor, Direction, MicrostepMode, _DRV8711

# --- PIN CONFIGURATION ---
SCS_PIN   = 8    # GPIO8  (Pin 24)
DIR_PIN   = 24   # GPIO24 (Pin 18)
STEP_PIN  = 25   # GPIO25 (Pin 22)
SLEEP_PIN = 7    # GPIO7  (Pin 26)

# --- LCD CONFLICT FIX ---
# Since the LCD shares the SPI bus, we must explicitly disable it
# (hold CS High) to prevent it from interfering with Motor MISO data.
LCD_CS_PIN = 22  # GPIO22 (Pin 15) per your wiring guide

def print_header(msg):
    print("\n" + "="*60)
    print(f"  {msg}")
    print("="*60)

def check_spi_communication(driver_low):
    """Test SPI read/write using low-level _DRV8711 interface"""
    print_header("TEST 1: SPI COMMUNICATION")

    val_initial = driver_low.read(_DRV8711.TORQUE)
    print(f"[*] Initial TORQUE Register Value: 0x{val_initial:03X}")

    if val_initial == 0x000:
        print("[!] ERROR: Read 0x000. SPI MISO (SDATO) might be disconnected.")
        print("    OR: The LCD Display might be hogging the bus.")
        return False
    if val_initial == 0xFFF:
        print("[!] ERROR: Read 0xFFF. SPI MISO (SDATO) might be floating.")
        return False

    test_val = 0x1AA
    print(f"[*] Writing Test Value: 0x{test_val:03X}...")
    driver_low.write(_DRV8711.TORQUE, test_val)
    time.sleep(0.01)

    val_readback = driver_low.read(_DRV8711.TORQUE)
    print(f"[*] Readback Value:    0x{val_readback:03X}")

    if val_readback == test_val:
        print("[OK] SPI Communication Successful!")
        driver_low.write(_DRV8711.TORQUE, 0x1FF) # Restore default
        return True
    else:
        print("[!] ERROR: Readback did not match write.")
        return False

def check_status_register(driver_low, ignore_stall=False, spi_verified=False):
    """Check DRV8711 STATUS register for faults"""
    print_header("TEST 2: DRIVER STATUS & FAULTS")

    status = driver_low.read(_DRV8711.STATUS)
    print(f"[*] STATUS Register: 0x{status:03X} (Binary: {status:012b})")

    # Only warn about 0x00 if we haven't verified SPI works yet
    if status == 0x00 and not spi_verified:
        print("[!] WARNING: Status is 0x00. If SPI MISO is broken, this")
        print("    might not be a real 'Success', just a failure to read errors.")

    faults = []
    warnings = []

    # Critical Faults (bits 0-5)
    if status & (1 << 5): faults.append("UVLO (Under Voltage) - Check Power Supply!")
    if status & (1 << 4): faults.append("BPDF (Channel B Predriver Fault)")
    if status & (1 << 3): faults.append("APDF (Channel A Predriver Fault)")
    if status & (1 << 2): faults.append("BOCP (Channel B Over Current)")
    if status & (1 << 1): faults.append("AOCP (Channel A Over Current)")
    if status & (1 << 0): faults.append("OTS  (Over Temperature)")

    # Warnings (Non-Critical for startup - bits 6-7)
    if status & (1 << 7): warnings.append("STDLAT (Latched Stall Detected)")
    if status & (1 << 6): warnings.append("STD    (Stall Detected)")

    if faults:
        print("[!] CRITICAL FAULTS DETECTED:")
        for f in faults:
            print(f"    - {f}")
        return False

    if warnings:
        if not ignore_stall:
            print("[?] WARNINGS DETECTED:")
            for w in warnings:
                print(f"    - {w}")
        else:
            print("[?] WARNINGS DETECTED (Safe to proceed testing):")
            for w in warnings:
                print(f"    - {w}")

    if not faults and not warnings:
        print("[OK] No Faults Detected.")

    return True

def test_motor_movement(motor, driver_low, blind_mode=False):
    """Test motor movement with safety checks"""
    print_header("TEST 3: MOTOR MOVEMENT")
    print("(!) WARNING: Motor may move.")

    # Clear faults
    driver_low.write(_DRV8711.STATUS, 0)

    # 1.0 Amps is safer for bench testing than 4.2 Amps
    print("[*] Setting Test Current to 1000mA (24% torque)...")
    motor.set_torque_percent(24)  # 24% of 4200mA = ~1000mA
    motor.set_microstep_mode(MicrostepMode.SIXTEENTH_STEP)

    print("[*] Enabling Driver...")
    motor.enable()
    time.sleep(0.1)

    if not blind_mode:
        # Check status but don't abort on Stall
        if not check_status_register(driver_low, ignore_stall=True, spi_verified=True):
            print("[!] Aborting due to CRITICAL FAULT.")
            motor.disable()
            return
    else:
        print("[!] BLIND MODE: Skipping fault check (SPI Read Broken).")

    print("\n[*] Stepping Forward (400 steps)...")
    motor.run_for_time(duration=0.4, speed=1000, direction=Direction.CW)

    print("[*] Stepping Backward (400 steps)...")
    motor.run_for_time(duration=0.4, speed=1000, direction=Direction.CCW)

    motor.disable()
    print("\n[OK] Movement sequence finished.")

def main():
    motor = None
    try:
        print("\nInitializing Driver for Diagnostics...")

        # --- FIX: SILENCE THE LCD ---
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(LCD_CS_PIN, GPIO.OUT)
        GPIO.output(LCD_CS_PIN, GPIO.HIGH) # Set High to DISABLE LCD
        print(f"[*] LCD CS (GPIO {LCD_CS_PIN}) set HIGH to prevent SPI conflict.")

        motor = StepperMotor(
            cs_pin=SCS_PIN,
            step_pin=STEP_PIN,
            dir_pin=DIR_PIN,
            sleep_pin=SLEEP_PIN,
            max_current_ma=4200
        )

        # Clear faults
        motor._driver.write(_DRV8711.STATUS, 0)
        time.sleep(0.1)

        spi_ok = check_spi_communication(motor._driver)

        if spi_ok:
            # Pass spi_verified=True so it doesn't warn about 0x00 status
            check_status_register(motor._driver, spi_verified=True)
            test_motor_movement(motor, motor._driver)
        else:
            print("\n[!] CRITICAL: SPI Test Failed.")
            print("    However, this might just be the READ line (MISO).")
            print("    The WRITE line (MOSI) might still work.")
            response = input("    >>> Attempt Blind Motor Movement? (y/n): ")
            if response.lower() == 'y':
                test_motor_movement(motor, motor._driver, blind_mode=True)

    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user.")
    except Exception as e:
        print(f"\n[!] EXCEPTION OCCURRED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if motor:
            print("\n[*] Cleaning up...")
            motor.cleanup()
        print("[*] Diagnostic complete.")

if __name__ == "__main__":
    main()
