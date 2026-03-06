# Smart Coffee Grinder Controller

A Raspberry Pi-based controller for a motorized coffee grinder, featuring a circular touchscreen UI, stepper motor control via the DRV8711 driver, and systemd-managed autostart.

---

## Hardware Overview

| Component | Model / Spec |
|---|---|
| Controller | Raspberry Pi (any with GPIO 40-pin) |
| Display | 1.28" Round LCD, 240x240, GC9A01 driver (SPI) |
| Touchscreen | CST816T capacitive touch controller (I2C) |
| Motor | NEMA 23 stepper, 4.2A rated, 3.0Nm torque, 0.9Ohm, 3.8mH, 1.8°/step (200 steps/rev) |
| Motor Driver | Pololu High-Power Stepper Driver 36v4 (DRV8711) |
| Gearbox | 2:1 reduction (motor RPM displayed at half speed) |

### GPIO Pin Map (BCM numbering)

| GPIO | Pin | Signal | Description |
|---|---|---|---|
| GPIO4 | 7 | TP_INT | Touch interrupt (input, pull-up) |
| GPIO6 | 31 | TP_RST | Touch controller reset (output) |
| GPIO7 | 26 | SLEEP_PIN | DRV8711 sleep/enable |
| GPIO8 | 24 | SCS_PIN | DRV8711 SPI chip select |
| GPIO17 | 11 | DC_PIN | LCD data/command |
| GPIO22 | 15 | LCD_CS | LCD SPI chip select |
| GPIO23 | 16 | BL_PIN | LCD backlight |
| GPIO24 | 18 | DIR_PIN | Stepper motor direction |
| GPIO25 | 22 | STEP_PIN | Stepper motor step pulse |
| GPIO27 | 13 | RST_PIN | LCD hardware reset |

**SPI bus 0** is shared between the LCD (80 MHz, CS=GPIO22) and the DRV8711 driver (500 kHz, CS=GPIO8/SCS). The LCD closes its SPI connection before the motor subprocess opens it, and reopens it after the motor stops.

**I2C bus 1** is used by the CST816T touch controller at address `0x15`.

---

## Project Structure

```
grinder/
├── motor_control.py          # Main application: UI + touch + motor orchestration
├── motor_only.py             # Subprocess: standalone motor runner (DRV8711 + step pulses)
├── lcd_display.py            # LCD driver: GC9A01 display via SPI
├── touch_screen.py           # Touch driver: CST816T via I2C
├── motor_configs.json        # 88+ DRV8711 configurations (categories A-K)
├── test_motor_comprehensive.py  # Interactive tool to test all configs and rate noise
├── diagnostic.py             # Simple DRV8711 diagnostic (SPI + J6 config test)
├── full_diagnostic.py        # 8-step comprehensive hardware diagnostic
├── wifi_setup.py             # One-time boot WiFi connection script
├── install.sh                # Install script -> /opt/motor-control + systemd
├── motor-control.service     # systemd service for main application
└── wifi-setup.service        # systemd one-shot service for WiFi setup
```

---

## How It Works

### Main Application (`motor_control.py`)

The entry point for normal operation. Requires `sudo` (GPIO access).

**UI:**
- Rendered at 2x resolution (480x480) then downscaled to 240x240 via Lanczos for crisp anti-aliased output.
- Circular arc slider (135° to 405°) to set RPM.
- Center button: green (coffee bean icon) when stopped, red (ground coffee icon) when running.
- RPM is displayed at half the set value to account for the 2:1 gearbox.
- Standby mode: after 10 minutes of inactivity (motor not running), the display and backlight sleep. Any touch wakes it.

**Touch handling:**
- Touch within ~22px of center = button press (start/stop).
- Touch on the arc ring = RPM adjustment (snaps to nearest 10 RPM, range 0-300).
- Slider is locked while motor is running.

**Motor process management:**
- Motor runs in a separate subprocess (`motor_only.py`) to isolate timing-critical step pulse generation.
- Before spawning the subprocess, the LCD closes its SPI handle. After the motor stops, SPI is reopened and the display is redrawn.
- If the motor subprocess exits unexpectedly, the UI recovers automatically.

### Motor Subprocess (`motor_only.py`)

Runs independently with no display or touch logic.

1. Loads the named config from `motor_configs.json` (default: `K4`).
2. Initializes GPIO and SPI.
3. Calculates TORQUE register and ISGAIN bits from target current (mA) and sense resistor (30 mOhm).
4. Writes all DRV8711 registers: CTRL, TORQUE, OFF, BLANK, DECAY, DRIVE, STALL, STATUS.
5. Enables the driver (sets ENBL bit) and begins the step pulse loop.
6. Uses `time.perf_counter()` busy-wait for precise step timing.
7. On SIGTERM/SIGINT: sets `shutdown_requested = True`, exits the step loop, pulls SLEEP LOW.

Active config in `motor_control.py`: **K4** (8000 mA, 100 kHz PWM, 1/64 microstepping).

### LCD Driver (`lcd_display.py`)

- Implements the full GC9A01 initialization sequence.
- `show_image(pil_image)`: converts PIL RGB888 to RGB565, transfers via SPI in 4096-byte chunks with CS held low for the entire transfer.
- `sleep_display()` / `wake_display()`: sends sleep-in/sleep-out commands and controls backlight.
- `close_spi_for_motor()` / `reopen_spi_after_motor()`: cooperative SPI sharing with motor subprocess.

### Touch Driver (`touch_screen.py`)

- Reads 6 bytes from CST816T register `0x02` via I2C.
- Validates coordinates (must be within 0-239).
- Applies a 3-sample moving average filter to reduce jitter.
- 5-pixel hysteresis to suppress sub-threshold movements.
- 10 ms debounce.
- State machine: IDLE -> PRESSED -> HELD -> RELEASED.
- Touch detection: polls `GPIO.input(TP_INT) == LOW`.

---

## Motor Configuration System

`motor_configs.json` contains 88+ named configurations organized into categories A-K, each tuning different DRV8711 parameters:

| Category | Focus |
|---|---|
| A | Diagnostic baseline |
| B | Extended PWM frequency sweep |
| C | DRIVE current optimization |
| D | Motor current sweep |
| E | Decay mode deep dive |
| F | Microstepping extended |
| G | Stall detection impact |
| H | Blanking time vs microstepping |
| I | Resonance troubleshooting |
| J | High torque optimizations |
| K | Ultra-current quiet optimization (up to 8000 mA) |

Each config entry specifies:
- `current_ma`: target coil current
- `ctrl_base`: base CTRL register value (microstepping mode, gain placeholder)
- `off`: OFF time register (sets PWM frequency)
- `blank`: BLANK time register (ABT enable)
- `decay`: DECAY mode register
- `drive`: DRIVE current register (gate drive strength)
- `stall`: STALL detection register
- `microstep_divider`: steps-per-revolution multiplier
- `pwm_freq_khz`, `decay_name`, `drive_name`: human-readable annotations
- `test_speeds`: list of RPMs for automated testing

**TORQUE register formula:**
```
TORQUE = (384 * I_TRQ * R_SENSE * 2) / V_REF
```
Where `R_SENSE = 0.030 Ohm`. The function auto-selects the appropriate ISGAIN (5x/10x/20x/40x) to fit the result in 0-255.

---

## Diagnostic Tools

### `diagnostic.py` - Simple diagnostic

```bash
sudo python3 diagnostic.py
```

Tests:
1. SPI read/write verification (TORQUE register roundtrip)
2. STATUS register fault check
3. Motor movement test with J6 configuration (400 steps forward, 400 steps back)

### `full_diagnostic.py` - Comprehensive diagnostic

```bash
sudo python3 full_diagnostic.py
```

8-step test suite with PASS/FAIL reporting:
1. GPIO pin control
2. SPI bus open + read/write
3. Driver sleep/wake via SLEEP pin
4. STATUS register fault decode (UVLO, OTS, AOCP, BOCP, APDF, BPDF, STDLAT)
5. Register configuration write+verify
6. Motor coil continuity (energize and check for coil faults)
7. Step pulse test (200 steps forward + 200 back, user confirms movement)
8. Continuous 5-second run at ~60 RPM

### `test_motor_comprehensive.py` - Config sweep tool

```bash
sudo python3 test_motor_comprehensive.py
```

Interactive menu to run any combination of the 88 configurations. After each test at each RPM, the user rates noise (1-10). Results can be exported to CSV for analysis. Used during development to find the optimal DRV8711 settings for quiet, torque-adequate operation.

---

## Installation

```bash
sudo bash install.sh
```

The install script:
1. Installs Python dependencies (`spidev`, `smbus2`, `RPi.GPIO`, `Pillow`, `numpy`, `opencv-python-headless`)
2. Copies all files to `/opt/motor-control/`
3. Installs and enables two systemd services

### Systemd Services

**`wifi-setup.service`** (one-shot, runs at boot before motor-control):
- Tries to connect to the configured WiFi networks via `nmcli`
- Has a 60-second timeout; if it fails, motor-control starts anyway

**`motor-control.service`** (always-restart, runs as root):
- Starts `motor_control.py` after `wifi-setup.service` completes
- Restarts automatically on crash (5-second delay)

```bash
# Useful commands
sudo systemctl status motor-control
sudo systemctl restart motor-control
journalctl -u motor-control -f
```

---

## Running Manually

```bash
# Main application (requires root for GPIO)
sudo python3 motor_control.py

# Motor only (for testing)
sudo python3 motor_only.py 200        # 200 RPM with default J6 config
sudo python3 motor_only.py 150 K4     # 150 RPM with K4 config

# Diagnostics
sudo python3 diagnostic.py
sudo python3 full_diagnostic.py
sudo python3 test_motor_comprehensive.py
```

---

## Dependencies

```bash
# System packages
sudo apt-get install -y python3-pip python3-pil python3-numpy

# Python packages
pip3 install spidev smbus2 RPi.GPIO
```

Required Python modules: `RPi.GPIO`, `spidev`, `smbus2`, `PIL` (Pillow), `numpy`

---

## Notes

- The application must run as root (`sudo`) due to GPIO/SPI/I2C access requirements.
- SPI is shared between the LCD and motor driver via cooperative open/close. Never run `motor_control.py` and `motor_only.py` as separate independent processes simultaneously.
- Motor RPM displayed on screen is always half the actual motor RPM (gearbox compensation).
- The RPM slider snaps to multiples of 10, in the range 0-300 (motor side).
- `wifi_setup.py` contains hardcoded network credentials - update before deploying on a new network.
