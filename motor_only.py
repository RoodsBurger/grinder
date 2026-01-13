#!/usr/bin/python3
"""
Standalone motor control - runs in separate process
No display, no touch - just motor operation

MOTOR CONFIGURATION: Loaded from motor_configs.json
- Defaults to J6 (Torque + Quiet Compromise v1) if no config specified
- Can load any config by passing config ID as argument

Usage:
    python3 motor_only.py <RPM> [CONFIG_ID]

Examples:
    python3 motor_only.py 200          # Use J6 config at 200 RPM
    python3 motor_only.py 200 K1       # Use K1 config at 200 RPM
    python3 motor_only.py 100 A3       # Use A3 config at 100 RPM

Reference: https://github.com/pololu/high-power-stepper-driver-arduino
"""
import sys
import time
import os
import signal
import spidev
import RPi.GPIO as GPIO
import json

# DRV8711 Registers
REG_CTRL, REG_TORQUE, REG_OFF, REG_BLANK = 0x00, 0x01, 0x02, 0x03
REG_DECAY, REG_DRIVE, REG_STATUS, REG_STALL = 0x04, 0x05, 0x06, 0x07

shutdown_requested = False

def signal_handler(signum, frame):
    """Handle SIGTERM/SIGINT for graceful deceleration"""
    global shutdown_requested
    shutdown_requested = True


# Hardware
SCS_PIN, DIR_PIN, STEP_PIN, SLEEP_PIN, LCD_CS_PIN = 8, 24, 25, 7, 22
SPI_BUS, SPI_DEVICE, SPI_SPEED = 0, 0, 500000
spi, MOTOR_DIRECTION = None, 1

def load_motor_config(config_id='J6'):
    """Load motor configuration from motor_configs.json"""
    config_path = os.path.join(os.path.dirname(__file__), 'motor_configs.json')

    try:
        with open(config_path, 'r') as f:
            configs = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: motor_configs.json not found")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        sys.exit(1)

    if config_id not in configs:
        print(f"ERROR: Config '{config_id}' not found")
        print(f"Available: {', '.join(sorted(configs.keys()))}")
        sys.exit(1)

    return configs[config_id]

def calculate_torque_register(current_ma):
    """Calculate TORQUE register and ISGAIN bits for given current"""
    r_sense = 0.030
    gains = [(0, 3.3), (1, 1.65), (2, 0.825), (3, 0.4125)]

    for gain_bits, v_ref in gains:
        torque = int((384 * (current_ma / 1000.0) * r_sense * 2) / v_ref)
        if 0 <= torque <= 255:
            print(f"    Calculated: TORQUE=0x{torque:02X}, ISGAIN={gain_bits}")
            return (torque, gain_bits)

    raise ValueError(f"Current {current_ma}mA too high")

def init_spi():
    """Initialize SPI bus"""
    global spi
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEVICE)
    spi.max_speed_hz = SPI_SPEED
    spi.mode = 0b00
    try:
        spi.no_cs = True  # Manual CS control for Pololu
    except:
        pass
    print(f"    SPI opened at {spi.max_speed_hz}Hz")

def close_spi():
    global spi
    if spi:
        spi.close()
        spi = None

def write_reg(reg: int, value: int):
    """Write to DRV8711 register"""
    if value < 0 or value > 0xFFF:
        raise ValueError(f"Value 0x{value:X} out of range")

    msb = (reg << 4) | ((value >> 8) & 0x0F)
    lsb = value & 0xFF
    GPIO.output(SCS_PIN, GPIO.HIGH)
    spi.xfer2([msb, lsb])
    GPIO.output(SCS_PIN, GPIO.LOW)
    time.sleep(0.0001)

def read_reg(reg: int) -> int:
    """Read from DRV8711 register"""
    read_cmd = 0x80 | (reg << 4)
    GPIO.output(SCS_PIN, GPIO.HIGH)
    result = spi.xfer2([read_cmd, 0x00])
    GPIO.output(SCS_PIN, GPIO.LOW)
    time.sleep(0.0001)
    return ((result[0] & 0x0F) << 8) | result[1]

def run_motor(target_rpm, config_id='J6'):
    """Run motor at target RPM until process is killed"""
    global shutdown_requested, spi

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    motor_config = load_motor_config(config_id)

    print(f"\n{'='*60}")
    print(f"  Motor Control Starting")
    print(f"  Config: {config_id} - {motor_config['name']}")
    print(f"  Target: {target_rpm} RPM")
    print(f"{'='*60}")
    print(f"  Current: {motor_config['current_ma']}mA")
    print(f"  PWM: {motor_config['pwm_freq_khz']}kHz")
    print(f"  Decay: {motor_config['decay_name']}")
    print(f"  Drive: {motor_config['drive_name']}")
    print(f"  Microstepping: 1/{motor_config['microstep_divider']}")
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

    # Calculate TORQUE and ISGAIN for motor current (like comprehensive test)
    print(f"[*] Calculating registers for {motor_config['current_ma']}mA...")
    torque_val, isgain_bits = calculate_torque_register(motor_config['current_ma'])

    # Build CTRL register (like comprehensive test)
    ctrl = motor_config['ctrl_base']
    ctrl = (ctrl & ~0x300) | (isgain_bits << 8)  # Set ISGAIN bits [9:8]
    ctrl = ctrl & ~0x01  # Ensure disabled initially
    print(f"    [*] CTRL register: 0x{ctrl:03X} (ENBL bit cleared)")

    # CRITICAL: Wake up driver AFTER SPI init (matches comprehensive test exactly)
    print("[*] Waking up driver...")
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.001)  # 1ms like comprehensive test

    # CRITICAL: Write registers in EXACT order as comprehensive test
    # Write CTRL FIRST with disabled bit, then other registers
    print(f"[*] Writing {config_id} configuration...")
    write_reg(REG_CTRL, ctrl)                     # CTRL first (disabled, with calculated ISGAIN)
    write_reg(REG_TORQUE, torque_val)             # Calculated torque value
    write_reg(REG_OFF, motor_config['off'])       # PWM frequency
    write_reg(REG_BLANK, motor_config['blank'])   # Blanking time / ABT
    write_reg(REG_DECAY, motor_config['decay'])   # Decay mode
    write_reg(REG_DRIVE, motor_config['drive'])   # Gate drive current
    write_reg(REG_STALL, motor_config['stall'])   # Stall detection
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
            print(f"    OFF:    0x{off_readback:03X} (expected 0x{motor_config['off']:03X})")
            print(f"    DECAY:  0x{decay_readback:03X} (expected 0x{motor_config['decay']:03X})")
            print(f"    DRIVE:  0x{drive_readback:03X} (expected 0x{motor_config['drive']:03X})")

            if (ctrl_readback == ctrl and
                torque_readback == torque_val and
                off_readback == motor_config['off'] and
                decay_readback == motor_config['decay'] and
                drive_readback == motor_config['drive']):
                print(f"    [OK] All {config_id} registers verified")
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

    # Calculate delays (using config microstepping)
    steps_rev = 200
    microsteps = motor_config['microstep_divider']
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
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: motor_only.py <RPM> [CONFIG_ID]")
        print("")
        print("Arguments:")
        print("  RPM        - Target speed (0-300)")
        print("  CONFIG_ID  - Motor configuration ID (default: J6)")
        print("")
        print("Examples:")
        print("  python3 motor_only.py 200       # Use J6 config at 200 RPM")
        print("  python3 motor_only.py 200 K1    # Use K1 config at 200 RPM")
        print("  python3 motor_only.py 100 A3    # Use A3 config at 100 RPM")
        sys.exit(1)

    try:
        # Parse RPM
        rpm = int(sys.argv[1])
        if rpm < 0 or rpm > 300:
            print("ERROR: RPM must be 0-300")
            sys.exit(1)

        # Parse optional config ID (default to J6)
        config_id = sys.argv[2] if len(sys.argv) == 3 else 'J6'

        # Run motor with specified config
        run_motor(rpm, config_id)

    except ValueError:
        print("ERROR: Invalid RPM value (must be integer)")
        sys.exit(1)
