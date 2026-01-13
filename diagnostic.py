#!/usr/bin/env python3
"""
DRV8711 Motor Driver Diagnostic Tool
Simple test using direct register writes (matches motor_only.py and comprehensive test)
"""
import time
import RPi.GPIO as GPIO
import spidev

# --- PIN CONFIGURATION ---
SCS_PIN   = 8    # GPIO8  (Pin 24)
DIR_PIN   = 24   # GPIO24 (Pin 18)
STEP_PIN  = 25   # GPIO25 (Pin 22)
SLEEP_PIN = 7    # GPIO7  (Pin 26)
LCD_CS_PIN = 22  # GPIO22 (Pin 15) - disable LCD

# --- DRV8711 REGISTER MAP ---
REG_CTRL = 0x00
REG_TORQUE = 0x01
REG_OFF = 0x02
REG_BLANK = 0x03
REG_DECAY = 0x04
REG_DRIVE = 0x05
REG_STATUS = 0x06
REG_STALL = 0x07

# SPI Configuration
SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 500000  # 500kHz
spi = None

# J6 Configuration (from comprehensive test)
J6_CONFIG = {
    'ctrl': 0xC28,      # Gain 5, 1/32 step, disabled
    'torque': 0x18B,    # 5000mA (calculated for Gain 5)
    'off': 0x020,       # 62.5kHz PWM
    'blank': 0x180,     # ABT enabled
    'decay': 0x110,     # Slow/Mixed decay
    'drive': 0xF59,     # 200/400mA MAX drive
    'stall': 0x040,     # Stall detection
}

def print_header(msg):
    print("\n" + "="*60)
    print(f"  {msg}")
    print("="*60)

def init_spi():
    """Initialize SPI bus"""
    global spi
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    print(f"[OK] SPI opened at {spi.max_speed_hz}Hz")

def close_spi():
    """Close SPI bus"""
    global spi
    if spi:
        spi.close()
        spi = None

def write_reg(reg: int, value: int):
    """Write to DRV8711 register"""
    msb = (reg << 4) | ((value >> 8) & 0x0F)
    lsb = value & 0xFF
    GPIO.output(SCS_PIN, GPIO.HIGH)  # CS Active (HIGH for Pololu)
    spi.xfer2([msb, lsb])
    GPIO.output(SCS_PIN, GPIO.LOW)  # CS Inactive
    time.sleep(0.0001)

def read_reg(reg: int) -> int:
    """Read from DRV8711 register"""
    read_cmd = 0x80 | (reg << 4)
    GPIO.output(SCS_PIN, GPIO.HIGH)  # CS Active
    result = spi.xfer2([read_cmd, 0x00])
    GPIO.output(SCS_PIN, GPIO.LOW)  # CS Inactive
    time.sleep(0.0001)
    return ((result[0] & 0x0F) << 8) | result[1]

def test_spi_communication():
    """Test SPI read/write"""
    print_header("TEST 1: SPI COMMUNICATION")

    try:
        # Try reading TORQUE register
        val = read_reg(REG_TORQUE)
        print(f"[*] Read TORQUE register: 0x{val:03X}")

        # Write/read test
        test_val = 0x1AA
        print(f"[*] Writing test value 0x{test_val:03X}...")
        write_reg(REG_TORQUE, test_val)
        time.sleep(0.01)

        readback = read_reg(REG_TORQUE)
        print(f"[*] Read back: 0x{readback:03X}")

        if readback == test_val:
            print("[OK] SPI Communication Working!")
            return True
        else:
            print("[!] WARNING: Readback mismatch (MISO may not work)")
            return False
    except Exception as e:
        print(f"[!] ERROR: {e}")
        return False

def check_status():
    """Check STATUS register"""
    print_header("TEST 2: STATUS CHECK")

    try:
        status = read_reg(REG_STATUS)
        print(f"[*] STATUS: 0x{status:03X} (binary: {status:012b})")

        faults = []
        if status & (1 << 5): faults.append("UVLO (Under Voltage)")
        if status & (1 << 4): faults.append("BPDF (Ch B Predriver Fault)")
        if status & (1 << 3): faults.append("APDF (Ch A Predriver Fault)")
        if status & (1 << 2): faults.append("BOCP (Ch B Over Current)")
        if status & (1 << 1): faults.append("AOCP (Ch A Over Current)")
        if status & (1 << 0): faults.append("OTS (Over Temperature)")

        if faults:
            print(f"[!] FAULTS: {', '.join(faults)}")
            return False

        print("[OK] No faults detected")
        return True
    except Exception as e:
        print(f"[!] ERROR: {e}")
        return False

def test_motor_movement():
    """Test motor with J6 configuration"""
    print_header("TEST 3: MOTOR MOVEMENT (J6 Config)")

    print("[*] Waking up driver...")
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.001)

    # Write J6 configuration
    print("[*] Writing J6 registers...")
    write_reg(REG_TORQUE, J6_CONFIG['torque'])
    write_reg(REG_OFF, J6_CONFIG['off'])
    write_reg(REG_BLANK, J6_CONFIG['blank'])
    write_reg(REG_DECAY, J6_CONFIG['decay'])
    write_reg(REG_DRIVE, J6_CONFIG['drive'])
    write_reg(REG_STALL, J6_CONFIG['stall'])
    write_reg(REG_CTRL, J6_CONFIG['ctrl'])

    # Clear faults
    print("[*] Clearing faults...")
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)

    # Verify registers
    print("[*] Verifying registers...")
    ctrl_read = read_reg(REG_CTRL)
    off_read = read_reg(REG_OFF)
    decay_read = read_reg(REG_DECAY)
    drive_read = read_reg(REG_DRIVE)

    print(f"    CTRL:  0x{ctrl_read:03X} (expected 0x{J6_CONFIG['ctrl']:03X})")
    print(f"    OFF:   0x{off_read:03X} (expected 0x{J6_CONFIG['off']:03X})")
    print(f"    DECAY: 0x{decay_read:03X} (expected 0x{J6_CONFIG['decay']:03X})")
    print(f"    DRIVE: 0x{drive_read:03X} (expected 0x{J6_CONFIG['drive']:03X})")

    if (ctrl_read == J6_CONFIG['ctrl'] and off_read == J6_CONFIG['off'] and
        decay_read == J6_CONFIG['decay'] and drive_read == J6_CONFIG['drive']):
        print("    [OK] All registers verified")
    else:
        print("    [!] WARNING: Register mismatch")

    # Check status
    check_status()

    # Enable and step
    print("\n[*] Enabling driver and stepping motor...")
    ctrl_enabled = J6_CONFIG['ctrl'] | 0x01  # Set ENBL bit
    write_reg(REG_CTRL, ctrl_enabled)
    time.sleep(0.05)

    # Step forward 400 steps
    print("[*] Stepping forward...")
    GPIO.output(DIR_PIN, GPIO.HIGH)
    for _ in range(400):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.001)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.001)

    # Step backward 400 steps
    print("[*] Stepping backward...")
    GPIO.output(DIR_PIN, GPIO.LOW)
    for _ in range(400):
        GPIO.output(STEP_PIN, GPIO.HIGH)
        time.sleep(0.001)
        GPIO.output(STEP_PIN, GPIO.LOW)
        time.sleep(0.001)

    # Disable
    ctrl_disabled = J6_CONFIG['ctrl'] & ~0x01
    write_reg(REG_CTRL, ctrl_disabled)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    print("[OK] Movement test complete")

def main():
    try:
        print("\nDRV8711 Diagnostic Tool - J6 Configuration")
        print("=" * 60)

        # Initialize GPIO
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Disable LCD
        GPIO.setup(LCD_CS_PIN, GPIO.OUT)
        GPIO.output(LCD_CS_PIN, GPIO.HIGH)
        print("[*] LCD disabled")

        # Setup pins
        GPIO.setup(SCS_PIN, GPIO.OUT)
        GPIO.setup(STEP_PIN, GPIO.OUT)
        GPIO.setup(DIR_PIN, GPIO.OUT)
        GPIO.setup(SLEEP_PIN, GPIO.OUT)

        GPIO.output(SCS_PIN, GPIO.LOW)
        GPIO.output(STEP_PIN, GPIO.LOW)
        GPIO.output(DIR_PIN, GPIO.LOW)
        GPIO.output(SLEEP_PIN, GPIO.LOW)

        # Initialize SPI
        init_spi()

        # Run tests
        spi_ok = test_spi_communication()
        if spi_ok:
            check_status()
            test_motor_movement()
        else:
            print("\n[!] SPI communication failed")
            print("[!] Check MISO connection (GPIO9/Pin 21)")

    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
    except Exception as e:
        print(f"\n[!] ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n[*] Cleaning up...")
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        close_spi()
        GPIO.cleanup()
        print("[*] Done")

if __name__ == "__main__":
    main()
