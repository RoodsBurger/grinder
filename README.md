# Coffee Grinder Controller

Custom coffee grinder controller with touchscreen UI, built on Raspberry Pi.

## Hardware

- **Display**: 1.28" circular LCD (240x240, GC9A01 controller)
- **Touch**: CST816T capacitive touch controller (I2C)
- **Motor Driver**: Pololu 36v4 High-Power Stepper Motor Driver (DRV8711)
- **Platform**: Raspberry Pi (tested on Pi 3/4)

## Features

- Circular touchscreen UI with radial RPM selector (0-300 RPM)
- Precision motor control with 1/32 microstepping
- Real-time touch control during grinding
- GO/STOP button control

## Installation

```bash
# Install dependencies
sudo apt-get update
sudo apt-get install python3-pip python3-pil python3-numpy
pip3 install spidev smbus2 RPi.GPIO

# Clone repository
git clone https://github.com/RoodsBurger/grinder.git
cd grinder

# Run the application
python3 motor_control.py
```

## Pin Configuration

| Component | Pin (BCM) | Function |
|-----------|-----------|----------|
| LCD Reset | 27 | Display reset |
| LCD DC | 17 | Data/Command select |
| LCD Backlight | 23 | Backlight control |
| LCD CS | 22 | SPI chip select (manual) |
| Touch Reset | 6 | Touch reset |
| Touch INT | 4 | Touch interrupt |
| Motor CS | 8 | Motor driver SPI CS |
| Motor DIR | 24 | Motor direction |
| Motor STEP | 25 | Motor step pulses |
| Motor SLEEP | 7 | Motor driver sleep/wake |

## Files

- `motor_control.py` - Main application with UI and motor control logic
- `lcd_display.py` - GC9A01 display driver (RGB565, SPI)
- `touch_screen.py` - CST816T touch controller driver (I2C)
- `pololu_lib.py` - DRV8711 stepper motor driver (SPI)

## Configuration

Edit `motor_control.py` to adjust:
- `MIN_RPM` / `MAX_RPM` - RPM range
- `MOTOR_DIRECTION` - Reverse motor direction (0 or 1)
- Pin assignments in Hardware Pins section

## License

MIT License - See LICENSE file for details
