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
    print(f"Motor: {config_id} - {motor_config['name']}")
    print(f"Target: {target_rpm} RPM | Current: {motor_config['current_ma']}mA | PWM: {motor_config['pwm_freq_khz']}kHz")
    print(f"{'='*60}")

    # Initialize GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(LCD_CS_PIN, GPIO.OUT)
    GPIO.output(LCD_CS_PIN, GPIO.HIGH)
    GPIO.setup(SCS_PIN, GPIO.OUT)
    GPIO.setup(STEP_PIN, GPIO.OUT)
    GPIO.setup(DIR_PIN, GPIO.OUT)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)
    GPIO.output(SCS_PIN, GPIO.LOW)
    GPIO.output(STEP_PIN, GPIO.LOW)
    GPIO.output(DIR_PIN, GPIO.LOW)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    # Ensure SPI is closed before opening
    try:
        spi_test = spidev.SpiDev()
        try:
            spi_test.open(0, 0)
            spi_test.close()
        except:
            pass
    except:
        pass

    # Initialize SPI
    init_spi()

    # Calculate and configure registers
    torque_val, isgain_bits = calculate_torque_register(motor_config['current_ma'])
    ctrl = motor_config['ctrl_base']
    ctrl = (ctrl & ~0x300) | (isgain_bits << 8)
    ctrl = ctrl & ~0x01

    # Wake driver and write configuration
    GPIO.output(SLEEP_PIN, GPIO.HIGH)
    time.sleep(0.001)
    write_reg(REG_CTRL, ctrl)
    write_reg(REG_TORQUE, torque_val)
    write_reg(REG_OFF, motor_config['off'])
    write_reg(REG_BLANK, motor_config['blank'])
    write_reg(REG_DECAY, motor_config['decay'])
    write_reg(REG_DRIVE, motor_config['drive'])
    write_reg(REG_STALL, motor_config['stall'])
    write_reg(REG_STATUS, 0x000)
    time.sleep(0.01)

    # Verify configuration
    try:
        ctrl_readback = read_reg(REG_CTRL)
        if ctrl_readback == 0xFFF or ctrl_readback == 0x000:
            print("BLIND mode - MISO not working")
        elif ctrl_readback != ctrl:
            print(f"Warning: Register mismatch")
    except:
        print("BLIND mode")

    # Enable driver and start motor
    print(f"\nStarting motor at {target_rpm} RPM (Ctrl+C to stop)\n")
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)
    ctrl_enabled = ctrl | 0x01
    write_reg(REG_CTRL, ctrl_enabled)
    time.sleep(0.001)
    close_spi()

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
        print("\nStopping...")
    finally:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
        print("Motor stopped")

if __name__ == "__main__":
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Usage: motor_only.py <RPM> [CONFIG_ID]")
        print("")
        print("Arguments:")
        print("  RPM        - Target speed (0-600)")
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
        if rpm < 0 or rpm > 600:
            print("ERROR: RPM must be 0-600")
            sys.exit(1)

        # Parse optional config ID (default to J6)
        config_id = sys.argv[2] if len(sys.argv) == 3 else 'J6'

        # Run motor with specified config
        run_motor(rpm, config_id)

    except ValueError:
        print("ERROR: Invalid RPM value (must be integer)")
        sys.exit(1)
