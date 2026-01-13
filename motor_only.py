#!/usr/bin/python3
"""
Standalone motor control - runs in separate process
No display, no touch - just motor operation

MOTOR CONFIGURATION: J6 - Torque + Quiet Compromise v1
- Current: 5000mA (119% motor rated - from comprehensive testing)
- Microstepping: 1/32 (very smooth)
- PWM Frequency: 62.5kHz (0x020 - above audible)
- Adaptive Blanking: ENABLED (for smooth 1/32 stepping)
- Decay Mode: Slow/Mixed (0x110) - better torque, still quiet
- Gate Drive: 200/400mA MAX (0xF59) - strong switching
- SPI: 500kHz (matches Pololu Arduino library)

Test Results: Noise 6-7/10, Good torque at all speeds
From 88-configuration comprehensive testing (2026-01-13)
Reference: https://github.com/pololu/high-power-stepper-driver-arduino
"""
import sys
import time
import os
import signal
import spidev
import RPi.GPIO as GPIO

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


# Hardware Pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7
LCD_CS_PIN = 22  # Must disable LCD to prevent SPI MISO conflicts

# SPI Configuration
SPI_BUS = 0
SPI_DEVICE = 0
SPI_SPEED = 500000  # 500kHz (matches Pololu Arduino library)
spi = None

# Motor Direction
MOTOR_DIRECTION = 1

# J6 Configuration (from comprehensive test)
J6_CURRENT_MA = 5000  # Target current in mA
J6_CTRL_BASE = 0xC28  # 1/32 step, Gain will be calculated
J6_CONFIG = {
    'off': 0x020,       # 62.5kHz PWM
    'blank': 0x180,     # ABT enabled
    'decay': 0x110,     # Slow/Mixed decay
    'drive': 0xF59,     # 200/400mA MAX drive
    'stall': 0x040,     # Stall detection
    'microstep': 32     # 1/32 microstepping
}

def calculate_torque_register(current_ma):
    """
    Calculate TORQUE register and ISGAIN bits for given current
    Formula: TORQUE = (384 * I_TRQ * R_SENSE * 2) / V_REF
    Returns: (torque_value, isgain_bits)
    """
    r_sense = 0.030  # 30mΩ sense resistors on Pololu 36v4

    gains = [(0, 3.3), (1, 1.65), (2, 0.825), (3, 0.4125)]

    for gain_bits, v_ref in gains:
        torque = int((384 * (current_ma / 1000.0) * r_sense * 2) / v_ref)
        if 0 <= torque <= 255:
            print(f"    [*] Calculated: TORQUE=0x{torque:02X} ({torque}), ISGAIN={gain_bits} (Gain {[5,10,20,40][gain_bits]})")
            return (torque, gain_bits)

    raise ValueError(f"Current {current_ma}mA exceeds maximum supported")

# ============================================================================
# SPI LOW-LEVEL FUNCTIONS (from comprehensive test)
# ============================================================================

def init_spi():
    """Initialize SPI bus"""
    global spi
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00  # CPOL=0, CPHA=0
    # CRITICAL: Disable kernel CS control to allow manual CS toggling (Pololu uses active-HIGH CS)
    try:
        spi.no_cs = True
    except:
        pass  # Some kernel versions don't support this
    print(f"    [OK] SPI opened at {spi.max_speed_hz}Hz")

def close_spi():
    """Close SPI bus"""
    global spi
    if spi:
        spi.close()
        spi = None

def write_reg(reg: int, value: int):
    """Write to DRV8711 register (12-bit value)"""
    if value < 0 or value > 0xFFF:
        raise ValueError(f"Register value 0x{value:X} out of range [0x000-0xFFF]")

    msb = (reg << 4) | ((value >> 8) & 0x0F)
    lsb = value & 0xFF

    GPIO.output(SCS_PIN, GPIO.HIGH)  # CS Active (HIGH for Pololu)
    spi.xfer2([msb, lsb])
    GPIO.output(SCS_PIN, GPIO.LOW)  # CS Inactive (LOW)
    time.sleep(0.0001)  # 100us settling time

def read_reg(reg: int) -> int:
    """Read from DRV8711 register (12-bit value)"""
    read_cmd = 0x80 | (reg << 4)

    GPIO.output(SCS_PIN, GPIO.HIGH)  # CS Active (HIGH for Pololu)
    result = spi.xfer2([read_cmd, 0x00])
    GPIO.output(SCS_PIN, GPIO.LOW)  # CS Inactive (LOW)
    time.sleep(0.0001)

    value = ((result[0] & 0x0F) << 8) | result[1]
    return value

def run_motor(target_rpm):
    """Run motor at target RPM until process is killed"""
    global shutdown_requested, spi

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    print(f"\n{'='*60}")
    print(f"  Motor Control Starting - Target: {target_rpm} RPM")
    print(f"{'='*60}")

    # Initialize GPIO
    print("\n[*] Initializing GPIO...")
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)

    # CRITICAL: Disable LCD to prevent SPI MISO conflicts (like comprehensive test)
    GPIO.setup(LCD_CS_PIN, GPIO.OUT)
    GPIO.output(LCD_CS_PIN, GPIO.HIGH)
    print("    [OK] LCD disabled (CS HIGH)")

    # Setup motor pins
    GPIO.setup(SCS_PIN, GPIO.OUT)
    GPIO.setup(STEP_PIN, GPIO.OUT)
    GPIO.setup(DIR_PIN, GPIO.OUT)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)

    GPIO.output(SCS_PIN, GPIO.LOW)  # CS inactive (LOW for Pololu active-HIGH)
    GPIO.output(STEP_PIN, GPIO.LOW)
    GPIO.output(DIR_PIN, GPIO.LOW)
    GPIO.output(SLEEP_PIN, GPIO.LOW)  # Start with SLEEP LOW (like comprehensive test)

    # CRITICAL: Ensure SPI bus is closed first (LCD or previous run may have left it open)
    print("[*] Ensuring SPI bus is closed...")
    try:
        import spidev
        spi_test = spidev.SpiDev()
        try:
            spi_test.open(0, 0)
            spi_test.close()
            print("    [OK] SPI bus was open, now closed")
        except:
            print("    [OK] SPI bus was already closed")
    except Exception as e:
        print(f"    [!] Could not check SPI state: {e}")

    # Initialize SPI (before waking chip - like comprehensive test)
    print("[*] Initializing SPI at 500kHz...")
    init_spi()

    # Calculate TORQUE and ISGAIN for J6 current (like comprehensive test)
    print(f"[*] Calculating registers for {J6_CURRENT_MA}mA...")
    torque_val, isgain_bits = calculate_torque_register(J6_CURRENT_MA)

    # Build CTRL register (like comprehensive test)
    ctrl = J6_CTRL_BASE
    ctrl = (ctrl & ~0x300) | (isgain_bits << 8)  # Set ISGAIN bits [9:8]
    ctrl = ctrl & ~0x01  # Ensure disabled initially
    print(f"    [*] CTRL register: 0x{ctrl:03X} (ENBL bit cleared)")

    # CRITICAL: Wake up driver AFTER SPI init (matches comprehensive test exactly)
    print("[*] Waking up driver...")
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.001)  # 1ms like comprehensive test

    # CRITICAL: Write registers in EXACT order as comprehensive test
    # Write CTRL FIRST with disabled bit, then other registers
    print("[*] Writing J6 configuration...")
    write_reg(REG_CTRL, ctrl)                   # CTRL first (disabled, with calculated ISGAIN)
    write_reg(REG_TORQUE, torque_val)           # Calculated torque value
    write_reg(REG_OFF, J6_CONFIG['off'])        # 62.5kHz PWM
    write_reg(REG_BLANK, J6_CONFIG['blank'])    # ABT enabled
    write_reg(REG_DECAY, J6_CONFIG['decay'])    # Slow/Mixed
    write_reg(REG_DRIVE, J6_CONFIG['drive'])    # 200/400mA MAX
    write_reg(REG_STALL, J6_CONFIG['stall'])    # Stall detection
    print("    [*] All registers written")

    # Clear faults (like comprehensive test)
    print("[*] Clearing faults...")
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)
    print("    [*] Faults cleared")

    # Verify configuration by reading registers (OPTIONAL - works in BLIND mode if MISO broken)
    print("[*] Verifying configuration...")
    try:
        ctrl_readback = read_reg(REG_CTRL)
        if ctrl_readback == 0xFFF or ctrl_readback == 0x000:
            print("    [!] WARNING: SPI MISO not working (reads 0xFFF)")
            print("    [i] Continuing in BLIND mode - motor should still work")
        else:
            torque_readback = read_reg(REG_TORQUE)
            off_readback = read_reg(REG_OFF)
            decay_readback = read_reg(REG_DECAY)
            drive_readback = read_reg(REG_DRIVE)

            print(f"    CTRL:   0x{ctrl_readback:03X} (expected 0x{ctrl:03X})")
            print(f"    TORQUE: 0x{torque_readback:03X} (expected 0x{torque_val:03X})")
            print(f"    OFF:    0x{off_readback:03X} (expected 0x{J6_CONFIG['off']:03X})")
            print(f"    DECAY:  0x{decay_readback:03X} (expected 0x{J6_CONFIG['decay']:03X})")
            print(f"    DRIVE:  0x{drive_readback:03X} (expected 0x{J6_CONFIG['drive']:03X})")

            if (ctrl_readback == ctrl and
                torque_readback == torque_val and
                off_readback == J6_CONFIG['off'] and
                decay_readback == J6_CONFIG['decay'] and
                drive_readback == J6_CONFIG['drive']):
                print("    [OK] All J6 registers verified")
            else:
                print("    [!] WARNING: Register mismatch - continuing anyway")
    except Exception as e:
        print(f"    [!] WARNING: Cannot read registers: {e}")
        print("    [i] Continuing in BLIND mode")

    # Set direction and enable driver (set ENBL bit in CTRL)
    print(f"\n[*] Enabling driver and starting motor at {target_rpm} RPM...")
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)
    ctrl_enabled = ctrl | 0x01  # Set ENBL bit (use calculated ctrl value)
    write_reg(REG_CTRL, ctrl_enabled)
    time.sleep(0.001)

    # Close SPI - only GPIO needed for stepping
    close_spi()
    print("[*] SPI closed, entering high-speed stepping loop...")
    print("[*] Press Ctrl+C to stop\n")

    # Calculate delays (using J6 microstepping)
    steps_rev = 200
    microsteps = J6_CONFIG['microstep']
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
        GPIO.output(SLEEP_PIN, GPIO.LOW)
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
