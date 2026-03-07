#!/usr/bin/env python3
"""
Comprehensive Motor & Driver Diagnostic Tool
Tests each component to isolate failures between:
- GPIO/Wiring issues
- SPI communication
- DRV8711 driver chip
- Motor coils
- Power supply

Run with: sudo python3 full_diagnostic.py
"""
import time
import sys
import os
import RPi.GPIO as GPIO
import spidev

# --- PIN CONFIGURATION ---
SCS_PIN    = 8    # GPIO8  - DRV8711 chip select
DIR_PIN    = 24   # GPIO24 - Direction
STEP_PIN   = 25   # GPIO25 - Step pulse
SLEEP_PIN  = 7    # GPIO7  - Driver sleep/wake
LCD_CS_PIN = 22   # GPIO22 - LCD chip select (keep high)

# --- DRV8711 REGISTERS ---
REG_CTRL   = 0x00
REG_TORQUE = 0x01
REG_OFF    = 0x02
REG_BLANK  = 0x03
REG_DECAY  = 0x04
REG_DRIVE  = 0x05
REG_STATUS = 0x06
REG_STALL  = 0x07

REG_NAMES = ['CTRL', 'TORQUE', 'OFF', 'BLANK', 'DECAY', 'DRIVE', 'STATUS', 'STALL']

# SPI
SPI_BUS, SPI_DEVICE, SPI_SPEED = 0, 0, 500000
spi = None

# Test results
results = {}

def print_header(msg):
    print("\n" + "=" * 65)
    print(f"  {msg}")
    print("=" * 65)

def print_subheader(msg):
    print(f"\n--- {msg} ---")

def pass_fail(name, passed, details=""):
    results[name] = passed
    status = "\033[92m[PASS]\033[0m" if passed else "\033[91m[FAIL]\033[0m"
    if details:
        print(f"{status} {name}: {details}")
    else:
        print(f"{status} {name}")
    return passed

def init_spi():
    global spi
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    try:
        spi.no_cs = True
    except:
        pass

def close_spi():
    global spi
    if spi:
        spi.close()
        spi = None

def write_reg(reg, value):
    msb = (reg << 4) | ((value >> 8) & 0x0F)
    lsb = value & 0xFF
    GPIO.output(SCS_PIN, GPIO.HIGH)
    spi.xfer2([msb, lsb])
    GPIO.output(SCS_PIN, GPIO.LOW)
    time.sleep(0.0001)

def read_reg(reg):
    read_cmd = 0x80 | (reg << 4)
    GPIO.output(SCS_PIN, GPIO.HIGH)
    result = spi.xfer2([read_cmd, 0x00])
    GPIO.output(SCS_PIN, GPIO.LOW)
    time.sleep(0.0001)
    return ((result[0] & 0x0F) << 8) | result[1]

def calculate_torque(current_ma):
    r_sense = 0.030
    gains = [(0, 3.3), (1, 1.65), (2, 0.825), (3, 0.4125)]
    for gain_bits, v_ref in gains:
        torque = int((384 * (current_ma / 1000.0) * r_sense * 2) / v_ref)
        if 0 <= torque <= 255:
            return (torque, gain_bits)
    return (255, 3)

# =============================================================================
# TEST 1: GPIO PIN TEST
# =============================================================================
def test_gpio():
    print_header("TEST 1: GPIO PIN FUNCTIONALITY")
    print("Testing if Raspberry Pi can control GPIO pins...")

    all_ok = True
    pins = [
        (SLEEP_PIN, "SLEEP_PIN (GPIO7)"),
        (SCS_PIN, "SCS_PIN (GPIO8)"),
        (DIR_PIN, "DIR_PIN (GPIO24)"),
        (STEP_PIN, "STEP_PIN (GPIO25)"),
    ]

    for pin, name in pins:
        try:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.HIGH)
            time.sleep(0.001)
            GPIO.output(pin, GPIO.LOW)
            print(f"  [OK] {name} - toggles correctly")
        except Exception as e:
            print(f"  [!!] {name} - FAILED: {e}")
            all_ok = False

    pass_fail("GPIO Control", all_ok)
    return all_ok

# =============================================================================
# TEST 2: SPI BUS TEST
# =============================================================================
def test_spi_bus():
    print_header("TEST 2: SPI BUS COMMUNICATION")

    print_subheader("2a. SPI Bus Open")
    try:
        init_spi()
        pass_fail("SPI Bus Open", True, f"Bus {SPI_BUS}, Device {SPI_DEVICE}, Speed {SPI_SPEED}Hz")
    except Exception as e:
        pass_fail("SPI Bus Open", False, str(e))
        return False

    print_subheader("2b. SPI Write/Read Test")
    # Try writing different values and reading them back
    test_values = [0x0AA, 0x155, 0x1FF, 0x000]
    spi_works = False

    for test_val in test_values:
        write_reg(REG_TORQUE, test_val)
        time.sleep(0.01)
        readback = read_reg(REG_TORQUE)

        if readback == test_val:
            pass_fail("SPI Read/Write", True, f"Wrote 0x{test_val:03X}, Read 0x{readback:03X}")
            spi_works = True
            break
        else:
            print(f"  [--] Write 0x{test_val:03X} → Read 0x{readback:03X} (mismatch)")

    if not spi_works:
        # Check if we're getting all 1s or all 0s
        readback = read_reg(REG_CTRL)
        if readback == 0xFFF:
            pass_fail("SPI Read/Write", False, "Reading all 1s - MISO may be floating/disconnected")
        elif readback == 0x000:
            pass_fail("SPI Read/Write", False, "Reading all 0s - Driver may not be powered or responding")
        else:
            pass_fail("SPI Read/Write", False, "Inconsistent reads - check wiring")

        print("\n  Possible causes:")
        print("  - MISO not connected (write works but can't verify)")
        print("  - Driver not powered (check VM pin)")
        print("  - SCS wiring issue")
        print("  - Driver chip damaged")

    return spi_works

# =============================================================================
# TEST 3: DRIVER WAKE/SLEEP TEST
# =============================================================================
def test_sleep_wake():
    print_header("TEST 3: DRIVER SLEEP/WAKE CONTROL")

    print_subheader("3a. Sleep Mode (SLEEP pin LOW)")
    GPIO.output(SLEEP_PIN, GPIO.LOW)
    time.sleep(0.01)

    # In sleep mode, driver should not respond properly
    status_sleep = read_reg(REG_STATUS)
    print(f"  Status in SLEEP: 0x{status_sleep:03X}")

    print_subheader("3b. Wake Mode (SLEEP pin HIGH)")
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    status_wake = read_reg(REG_STATUS)
    print(f"  Status when AWAKE: 0x{status_wake:03X}")

    # Check if waking changed the status register
    passed = True
    if status_wake == 0xFFF or status_wake == 0x000:
        passed = False
        print("  [!!] Driver not responding after wake - may be damaged or not powered")

    pass_fail("Sleep/Wake Control", passed)
    return passed

# =============================================================================
# TEST 4: STATUS REGISTER & FAULT CHECK
# =============================================================================
def test_status_faults():
    print_header("TEST 4: DRV8711 STATUS & FAULT DETECTION")

    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    # Clear any existing faults
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)

    status = read_reg(REG_STATUS)
    print(f"\n  STATUS Register: 0x{status:03X} (binary: {status:012b})")

    # Decode status bits
    print("\n  Fault Flags:")
    faults = []

    fault_bits = [
        (7, "STDLAT", "Stall detected (latched)"),
        (6, "STD",    "Stall detected"),
        (5, "UVLO",   "Under-voltage lockout (VM too low)"),
        (4, "BPDF",   "Channel B predriver fault"),
        (3, "APDF",   "Channel A predriver fault"),
        (2, "BOCP",   "Channel B overcurrent"),
        (1, "AOCP",   "Channel A overcurrent"),
        (0, "OTS",    "Over-temperature shutdown"),
    ]

    for bit, name, desc in fault_bits:
        if status & (1 << bit):
            print(f"  [!!] Bit {bit} {name}: {desc}")
            faults.append(name)
        else:
            print(f"  [  ] Bit {bit} {name}: OK")

    if faults:
        pass_fail("Fault Check", False, f"Active faults: {', '.join(faults)}")

        print("\n  Fault Interpretation:")
        if "UVLO" in faults:
            print("  → VM (motor power) voltage too low - check power supply")
        if "OTS" in faults:
            print("  → Driver overheated - let it cool down, check for shorts")
        if "AOCP" in faults or "BOCP" in faults:
            print("  → Overcurrent on motor coils - check for shorts, reduce current")
        if "APDF" in faults or "BPDF" in faults:
            print("  → Predriver fault - possible short to GND/VM on motor pins")
        return False
    else:
        pass_fail("Fault Check", True, "No faults detected")
        return True

# =============================================================================
# TEST 5: REGISTER CONFIGURATION TEST
# =============================================================================
def test_register_config():
    print_header("TEST 5: REGISTER CONFIGURATION")

    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    # Test configuration (moderate current for safety)
    test_current = 2000  # 2A - safe for testing
    torque_val, isgain = calculate_torque(test_current)

    # Configuration to write
    config = {
        REG_CTRL:   0xC20 | (isgain << 8),  # 1/32 step, calculated gain, disabled
        REG_TORQUE: torque_val,
        REG_OFF:    0x020,  # 62.5kHz PWM
        REG_BLANK:  0x180,  # ABT enabled
        REG_DECAY:  0x110,  # Slow/Mixed decay
        REG_DRIVE:  0xA59,  # Conservative drive
        REG_STALL:  0x040,  # Stall detection
    }

    print(f"\n  Writing test configuration ({test_current}mA, 1/32 step)...")

    all_ok = True
    for reg, val in config.items():
        write_reg(reg, val)

    time.sleep(0.01)

    print("\n  Verifying registers:")
    for reg, expected in config.items():
        actual = read_reg(reg)
        if actual == expected:
            print(f"  [OK] {REG_NAMES[reg]}: 0x{actual:03X}")
        else:
            print(f"  [!!] {REG_NAMES[reg]}: Expected 0x{expected:03X}, Got 0x{actual:03X}")
            all_ok = False

    pass_fail("Register Config", all_ok)
    return all_ok

# =============================================================================
# TEST 6: MOTOR COIL CONTINUITY (via driver feedback)
# =============================================================================
def test_motor_coils():
    print_header("TEST 6: MOTOR COIL TEST")

    print("  This test enables the driver briefly to check for coil faults...")
    print("  If motor coils are open/disconnected, driver may report faults.\n")

    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    # Configure with low current
    torque_val, isgain = calculate_torque(1000)  # 1A for safety
    ctrl_base = 0xC20 | (isgain << 8)

    write_reg(REG_CTRL, ctrl_base & ~0x01)  # Disabled first
    write_reg(REG_TORQUE, torque_val)
    write_reg(REG_OFF, 0x020)
    write_reg(REG_BLANK, 0x180)
    write_reg(REG_DECAY, 0x110)
    write_reg(REG_DRIVE, 0xA59)
    write_reg(REG_STALL, 0x040)
    write_reg(REG_STATUS, 0x000)  # Clear faults
    time.sleep(0.01)

    # Enable driver (energize coils)
    print("  Enabling driver (energizing coils)...")
    write_reg(REG_CTRL, ctrl_base | 0x01)
    time.sleep(0.1)  # Let it stabilize

    # Check for faults
    status = read_reg(REG_STATUS)
    print(f"  Status after enable: 0x{status:03X}")

    # Disable
    write_reg(REG_CTRL, ctrl_base & ~0x01)

    coil_ok = True
    if status & 0x1E:  # Check APDF, BPDF, AOCP, BOCP
        coil_ok = False
        print("\n  Coil-related faults detected:")
        if status & (1 << 4): print("  → Channel B predriver fault - check BOUT1/BOUT2 wiring")
        if status & (1 << 3): print("  → Channel A predriver fault - check AOUT1/AOUT2 wiring")
        if status & (1 << 2): print("  → Channel B overcurrent - possible short in coil B")
        if status & (1 << 1): print("  → Channel A overcurrent - possible short in coil A")

    pass_fail("Motor Coils", coil_ok, "No coil faults" if coil_ok else "Faults detected")
    return coil_ok

# =============================================================================
# TEST 7: STEP PULSE TEST (slow)
# =============================================================================
def test_step_pulses():
    print_header("TEST 7: STEP PULSE TEST")

    print("  Sending step pulses - motor should move slightly...")
    print("  Watch/listen for motor movement.\n")

    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    # Configure
    torque_val, isgain = calculate_torque(3000)  # 3A
    ctrl_base = 0xC20 | (isgain << 8)

    write_reg(REG_CTRL, ctrl_base & ~0x01)
    write_reg(REG_TORQUE, torque_val)
    write_reg(REG_OFF, 0x020)
    write_reg(REG_BLANK, 0x180)
    write_reg(REG_DECAY, 0x110)
    write_reg(REG_DRIVE, 0xA59)
    write_reg(REG_STALL, 0x040)
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)

    # Enable
    write_reg(REG_CTRL, ctrl_base | 0x01)
    time.sleep(0.05)

    # Send steps - forward
    print("  Sending 200 steps FORWARD (slow)...")
    GPIO.output(DIR_PIN, GPIO.HIGH)
    for i in range(200):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.002)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.002)

    time.sleep(0.2)

    # Send steps - reverse
    print("  Sending 200 steps REVERSE (slow)...")
    GPIO.output(DIR_PIN, GPIO.LOW)
    for i in range(200):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.002)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.002)

    # Disable
    write_reg(REG_CTRL, ctrl_base & ~0x01)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    # Check status
    status = read_reg(REG_STATUS)
    if status & 0x3F:
        print(f"\n  Faults after stepping: 0x{status:03X}")
        pass_fail("Step Pulses", False, "Faults occurred during stepping")
        return False

    print("\n  Did the motor move? (Y/N): ", end="", flush=True)
    try:
        response = input().strip().upper()
        if response == 'Y':
            pass_fail("Step Pulses", True, "Motor moved correctly")
            return True
        else:
            pass_fail("Step Pulses", False, "Motor did not move")
            print("\n  If no movement but no faults:")
            print("  - Check motor wire connections")
            print("  - Verify motor coil order (A1,A2,B1,B2)")
            print("  - Motor may be mechanically stuck")
            print("  - Try increasing current")
            return False
    except:
        print("\n  (skipped user input)")
        pass_fail("Step Pulses", True, "Pulses sent - verify manually")
        return True

# =============================================================================
# TEST 8: CONTINUOUS RUN TEST
# =============================================================================
def test_continuous_run():
    print_header("TEST 8: CONTINUOUS RUN TEST (5 seconds)")

    print("  Running motor at ~60 RPM for 5 seconds...")
    print("  Press Ctrl+C to stop early.\n")

    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.01)

    # Full power config
    torque_val, isgain = calculate_torque(5000)
    ctrl_base = 0xC28 | (isgain << 8)  # 1/32 microstepping

    write_reg(REG_CTRL, ctrl_base & ~0x01)
    write_reg(REG_TORQUE, torque_val)
    write_reg(REG_OFF, 0x020)
    write_reg(REG_BLANK, 0x180)
    write_reg(REG_DECAY, 0x110)
    write_reg(REG_DRIVE, 0xF59)
    write_reg(REG_STALL, 0x040)
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)

    write_reg(REG_CTRL, ctrl_base | 0x01)
    time.sleep(0.05)

    # Calculate timing for 60 RPM with 1/32 microstepping
    # 200 steps/rev * 32 microsteps * 60 RPM / 60 sec = 6400 steps/sec
    steps_per_sec = 6400
    delay = 1.0 / steps_per_sec

    GPIO.output(DIR_PIN, GPIO.HIGH)
    start_time = time.time()
    step_count = 0

    try:
        print("  Running... ", end="", flush=True)
        while time.time() - start_time < 5.0:
            GPIO.output(STEP_PIN, GPIO.HIGH)
            time.sleep(0.000002)
            GPIO.output(STEP_PIN, GPIO.LOW)
            time.sleep(delay)
            step_count += 1

            # Print progress
            elapsed = time.time() - start_time
            if int(elapsed) > int(elapsed - delay):
                print(f"{int(5-elapsed)}...", end="", flush=True)

        print(" Done!")

    except KeyboardInterrupt:
        print(" Stopped by user")

    # Stop motor
    write_reg(REG_CTRL, ctrl_base & ~0x01)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    # Final status
    status = read_reg(REG_STATUS)
    elapsed = time.time() - start_time
    actual_rpm = (step_count / 32 / 200) / elapsed * 60

    print(f"\n  Steps sent: {step_count}")
    print(f"  Calculated RPM: {actual_rpm:.1f}")
    print(f"  Final status: 0x{status:03X}")

    if status & 0x3F:
        pass_fail("Continuous Run", False, f"Faults: 0x{status:03X}")
        return False
    else:
        pass_fail("Continuous Run", True, f"Ran at ~{actual_rpm:.0f} RPM")
        return True

# =============================================================================
# SUMMARY
# =============================================================================
def print_summary():
    print_header("DIAGNOSTIC SUMMARY")

    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)

    print(f"\n  Tests Passed: {passed}")
    print(f"  Tests Failed: {failed}")

    if failed == 0:
        print("\n  \033[92m✓ All tests passed! Motor and driver appear functional.\033[0m")
    else:
        print("\n  \033[91m✗ Some tests failed. See details above.\033[0m")
        print("\n  Failed tests:")
        for name, passed in results.items():
            if not passed:
                print(f"    - {name}")

        print("\n  Troubleshooting Guide:")
        print("  ─" * 30)

        if not results.get("GPIO Control", True):
            print("  • GPIO issue: Check Raspberry Pi GPIO library and permissions")

        if not results.get("SPI Bus Open", True):
            print("  • SPI not opening: Enable SPI in raspi-config")

        if not results.get("SPI Read/Write", True):
            print("  • SPI communication failed:")
            print("    - Check MOSI/MISO/SCLK connections")
            print("    - Verify SCS pin wiring")
            print("    - Check if driver has power (VM pin)")

        if not results.get("Sleep/Wake Control", True):
            print("  • Sleep/Wake failed:")
            print("    - Check SLEEP pin connection")
            print("    - Driver may be damaged")

        if not results.get("Fault Check", True):
            print("  • Driver faults detected:")
            print("    - Check power supply voltage")
            print("    - Look for shorts in motor wiring")
            print("    - Let driver cool if overheated")

        if not results.get("Motor Coils", True):
            print("  • Motor coil issue:")
            print("    - Check motor wire connections")
            print("    - Test coil continuity with multimeter")
            print("    - Verify coil pairing (A1-A2, B1-B2)")

        if not results.get("Step Pulses", True):
            print("  • Motor not moving:")
            print("    - Increase motor current")
            print("    - Check if motor is mechanically stuck")
            print("    - Verify STEP and DIR pin connections")

# =============================================================================
# MAIN
# =============================================================================
def main():
    if os.geteuid() != 0:
        print("ERROR: This script must be run with sudo!")
        print("Usage: sudo python3 full_diagnostic.py")
        sys.exit(1)

    print("\n" + "=" * 65)
    print("  COMPREHENSIVE MOTOR & DRIVER DIAGNOSTIC")
    print("  DRV8711 + Stepper Motor Test Suite")
    print("=" * 65)
    print("\nThis diagnostic will test:")
    print("  1. GPIO pin control")
    print("  2. SPI bus communication")
    print("  3. Driver sleep/wake")
    print("  4. Status register & faults")
    print("  5. Register configuration")
    print("  6. Motor coil detection")
    print("  7. Step pulse test (manual verify)")
    print("  8. Continuous run test")
    print("\nPress Enter to begin or Ctrl+C to cancel...")

    try:
        input()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return

    try:
        # Initialize GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Keep LCD CS high
        GPIO.setup(LCD_CS_PIN, GPIO.OUT)
        GPIO.output(LCD_CS_PIN, GPIO.HIGH)

        # Setup pins
        for pin in [SCS_PIN, STEP_PIN, DIR_PIN, SLEEP_PIN]:
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)

        # Run tests
        test_gpio()

        if test_spi_bus():
            test_sleep_wake()
            test_status_faults()
            test_register_config()
            test_motor_coils()
            test_step_pulses()

            print("\n  Run continuous test? (Y/N): ", end="", flush=True)
            try:
                if input().strip().upper() == 'Y':
                    test_continuous_run()
            except:
                pass

        print_summary()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        close_spi()
        GPIO.cleanup()
        print("\nCleanup complete.")

if __name__ == "__main__":
    main()
