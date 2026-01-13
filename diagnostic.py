#!/usr/bin/env python3
"""
DRV8711 Motor Driver Diagnostic Tool
Tests SPI communication, fault detection, and motor movement
"""
import time
import RPi.GPIO as GPIO
import spidev
from pololu_lib import HighPowerStepperDriver

# --- PIN CONFIGURATION ---
SCS_PIN   = 8    # GPIO8  (Pin 24)
DIR_PIN   = 24   # GPIO24 (Pin 18)
STEP_PIN  = 25   # GPIO25 (Pin 22)
SLEEP_PIN = 7    # GPIO7  (Pin 26)

# --- LCD CONFLICT FIX ---
# Since the LCD shares the SPI bus, we must explicitly disable it
# (hold CS High) to prevent it from interfering with Motor MISO data.
LCD_CS_PIN = 22  # GPIO22 (Pin 15) per your wiring guide

# --- DRV8711 REGISTER MAP ---
REG_CTRL = 0x00
REG_TORQUE = 0x01
REG_OFF = 0x02
REG_BLANK = 0x03
REG_DECAY = 0x04
REG_DRIVE = 0x05
REG_STATUS = 0x06
REG_STALL = 0x07

def print_header(msg):
    print("\n" + "="*60)
    print(f"  {msg}")
    print("="*60)

def check_spi_communication(driver):
    """Test SPI read/write using pololu_lib driver"""
    print_header("TEST 1: SPI COMMUNICATION")

    # Try multiple reads to see if consistent
    print("[*] Testing SPI MISO (Read) Line...")
    reads = []
    for i in range(5):
        val = driver._read_reg(REG_TORQUE)
        reads.append(val)
        print(f"    Read #{i+1}: 0x{val:03X}")

    val_initial = reads[0]

    # Analyze read pattern
    all_same = all(r == val_initial for r in reads)
    all_fff = all(r == 0xFFF for r in reads)
    all_000 = all(r == 0x000 for r in reads)

    print(f"\n[*] Read Pattern Analysis:")
    print(f"    All reads identical: {all_same}")
    print(f"    All reads 0xFFF: {all_fff}")
    print(f"    All reads 0x000: {all_000}")

    if all_fff:
        print("\n[!] ERROR: Consistent 0xFFF - MISO line is FLOATING")
        print("    Possible causes:")
        print("    1. MISO (GPIO9/Pin 21) not connected to DRV8711 SDATO")
        print("    2. MISO trace broken on PCB")
        print("    3. DRV8711 not outputting data (chip fault)")
        print("    4. SPI mode mismatch (CPOL/CPHA)")
        return False

    if all_000:
        print("\n[!] ERROR: Consistent 0x000 - MISO line stuck LOW")
        print("    Possible causes:")
        print("    1. LCD Display CS not disabled (interfering)")
        print("    2. MISO shorted to ground")
        print("    3. Multiple SPI devices conflicting")
        return False

    # Try write and read back test
    test_val = 0x1AA
    print(f"\n[*] Write/Read Test - Writing 0x{test_val:03X} to TORQUE register...")
    driver._write_reg(REG_TORQUE, test_val)
    time.sleep(0.01)

    val_readback = driver._read_reg(REG_TORQUE)
    print(f"[*] Readback Value: 0x{val_readback:03X}")

    if val_readback == test_val:
        print("[OK] SPI Communication Successful!")
        print("    MOSI (write) working: YES")
        print("    MISO (read) working: YES")
        driver._write_reg(REG_TORQUE, 0x1FF) # Restore default
        return True
    else:
        print("[!] ERROR: Readback mismatch!")
        print(f"    Expected: 0x{test_val:03X}")
        print(f"    Got:      0x{val_readback:03X}")
        print("\n[*] MOSI (write) working: PROBABLY YES (can't verify without reads)")
        print("[*] MISO (read) working: NO")
        print("\n[i] You can still run in BLIND MODE - writes should work")
        return False

def check_status_register(driver, ignore_stall=False, spi_verified=False):
    """Check DRV8711 STATUS register for faults"""
    print_header("TEST 2: DRIVER STATUS & FAULTS")

    status = driver._read_reg(REG_STATUS)
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

def test_motor_movement(driver, blind_mode=False):
    """Test motor movement with safety checks"""
    print_header("TEST 3: MOTOR MOVEMENT")
    print("(!) WARNING: Motor may move.")

    # Configure driver with J6 TESTED settings (5000mA worked in comprehensive test!)
    print("[*] Configuring DRV8711 driver with J6 settings...")
    driver.reset_settings()
    driver.set_current_milliamps(5000)  # 119% rated - tested in comprehensive suite
    driver.set_step_mode(32)  # 1/32 microstepping

    # Apply J6 register overrides
    print("[*] Writing J6 register overrides...")
    driver._write_reg(REG_OFF, 0x020)     # 62.5kHz PWM
    driver._write_reg(REG_DECAY, 0x110)   # Slow/Mixed decay
    driver._write_reg(REG_DRIVE, 0xF59)   # 200/400mA MAX drive
    time.sleep(0.1)  # Let settings settle

    # Verify configuration by reading registers (if not blind mode)
    if not blind_mode:
        print("[*] Verifying J6 register writes...")
        off_val = driver._read_reg(REG_OFF)
        decay_val = driver._read_reg(REG_DECAY)
        drive_val = driver._read_reg(REG_DRIVE)
        torque_val = driver._read_reg(REG_TORQUE)

        print(f"    OFF:    expected 0x020, got 0x{off_val:03X}")
        print(f"    DECAY:  expected 0x110, got 0x{decay_val:03X}")
        print(f"    DRIVE:  expected 0xF59, got 0x{drive_val:03X}")
        print(f"    TORQUE: 0x{torque_val:03X} (5000mA)")

        if off_val == 0x020 and decay_val == 0x110 and drive_val == 0xF59:
            print("    [OK] All J6 registers verified")
        else:
            print(f"    [!] WARNING: Register write mismatch!")

    # Clear faults multiple times
    print("[*] Clearing faults (multiple attempts)...")
    for attempt in range(3):
        driver._write_reg(REG_STATUS, 0)
        time.sleep(0.02)
        if not blind_mode:
            status = driver._read_reg(REG_STATUS)
            critical_faults = status & 0x3F  # Bits 0-5 are critical
            if critical_faults == 0:
                print(f"    [OK] Faults cleared on attempt {attempt+1}")
                break
            print(f"    [i] Attempt {attempt+1}: STATUS still 0x{status:03X}")

    print("[*] Enabling Driver...")
    driver.enable_driver()
    time.sleep(0.1)

    if not blind_mode:
        # Check status but don't abort on Stall
        if not check_status_register(driver, ignore_stall=True, spi_verified=True):
            print("[!] Aborting due to CRITICAL FAULT.")
            driver.disable_driver()
            return
    else:
        print("[!] BLIND MODE: Skipping fault check (SPI Read Broken).")

    # Manual stepping - 400 steps forward then backward
    print("\n[*] Stepping Forward (400 steps)...")
    GPIO.output(DIR_PIN, GPIO.HIGH)
    for _ in range(400):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.001)  # 1ms high
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.001)  # 1ms low

    print("[*] Stepping Backward (400 steps)...")
    GPIO.output(DIR_PIN, GPIO.LOW)
    for _ in range(400):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.001)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.001)

    driver.disable_driver()
    print("\n[OK] Movement sequence finished.")

def main():
    driver = None
    try:
        print("\nInitializing Driver for Diagnostics...")

        # --- FIX: SILENCE THE LCD ---
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(LCD_CS_PIN, GPIO.OUT)
        GPIO.output(LCD_CS_PIN, GPIO.HIGH) # Set High to DISABLE LCD
        print(f"[*] LCD CS (GPIO {LCD_CS_PIN}) set HIGH to prevent SPI conflict.")

        # Setup GPIO pins for manual stepping
        GPIO.setup(STEP_PIN, GPIO.OUT)
        GPIO.setup(DIR_PIN, GPIO.OUT)
        GPIO.output(STEP_PIN, GPIO.LOW)
        GPIO.output(DIR_PIN, GPIO.LOW)

        driver = HighPowerStepperDriver(
            cs_pin=SCS_PIN,
            step_pin=STEP_PIN,
            dir_pin=DIR_PIN,
            sleep_pin=SLEEP_PIN
        )

        # Clear faults
        driver._write_reg(REG_STATUS, 0)
        time.sleep(0.1)

        spi_ok = check_spi_communication(driver)

        if spi_ok:
            # Pass spi_verified=True so it doesn't warn about 0x00 status
            check_status_register(driver, spi_verified=True)
            test_motor_movement(driver)
        else:
            print("\n[!] CRITICAL: SPI Test Failed.")
            print("    However, this might just be the READ line (MISO).")
            print("    The WRITE line (MOSI) might still work.")
            response = input("    >>> Attempt Blind Motor Movement? (y/n): ")
            if response.lower() == 'y':
                test_motor_movement(driver, blind_mode=True)

    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user.")
    except Exception as e:
        print(f"\n[!] EXCEPTION OCCURRED: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if driver:
            print("\n[*] Cleaning up...")
            driver.disable_driver()
        GPIO.cleanup()
        print("[*] Diagnostic complete.")

if __name__ == "__main__":
    main()
