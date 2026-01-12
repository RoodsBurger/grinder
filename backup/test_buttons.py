import time
import math
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import original drivers
from lcd_display import LCD_1inch28
from touch_screen import TouchScreen
from pololu_lib import HighPowerStepperDriver

# --- CONFIGURATION ---
MIN_RPM = 0
MAX_RPM = 300
MOTOR_DIRECTION = 1

# Display - ROUND screen!
W_REAL, H_REAL = 240, 240
CENTER = (W_REAL // 2, H_REAL // 2)

# Circular UI Layout - optimized for round display
BUTTON_RING_RADIUS = 95
BUTTON_RADIUS = 32
CENTER_BUTTON_RADIUS = 48

# Button angles (degrees from top, clockwise)
MINUS_ANGLE = 225  # Bottom left
PLUS_ANGLE = 315   # Bottom right

# Colors - Modern dark theme
COL_BG = (8, 12, 20)
COL_RING = (25, 30, 40)
COL_BUTTON = (40, 50, 65)
COL_BUTTON_ACTIVE = (55, 70, 90)
COL_GO = (52, 211, 153)
COL_STOP = (239, 68, 68)
COL_TEXT = (255, 255, 255)
COL_TEXT_DIM = (130, 145, 165)
COL_ACCENT = (59, 130, 246)

# Hardware Pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7

# Pre-load fonts once
try:
    FONT_RPM = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
    FONT_LABEL = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
    FONT_BUTTON = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
    FONT_CENTER = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
except:
    FONT_RPM = FONT_LABEL = FONT_BUTTON = FONT_CENTER = None


def get_button_position(angle_deg, distance):
    """Calculate button position on circle (0° = top, clockwise)"""
    rad = math.radians(angle_deg - 90)
    x = CENTER[0] + distance * math.cos(rad)
    y = CENTER[1] + distance * math.sin(rad)
    return (int(x), int(y))


def draw_ui(disp, rpm, is_running, pressed_button=None):
    """Draw beautiful circular UI for round display"""
    img = Image.new("RGB", (W_REAL, H_REAL), COL_BG)
    draw = ImageDraw.Draw(img)

    # Outer decorative ring
    draw.ellipse([3, 3, W_REAL-3, H_REAL-3], outline=COL_RING, width=3)
    draw.ellipse([8, 8, W_REAL-8, H_REAL-8], outline=COL_RING, width=1)

    # --- RPM Display (Center Top) ---
    if FONT_RPM:
        # Large RPM number
        draw.text((CENTER[0], CENTER[1] - 30), str(rpm),
                 font=FONT_RPM, fill=COL_TEXT, anchor="mm")
        # RPM label
        draw.text((CENTER[0], CENTER[1] + 25), "RPM",
                 font=FONT_LABEL, fill=COL_TEXT_DIM, anchor="mm")

    # --- MINUS Button (Bottom Left) ---
    minus_pos = get_button_position(MINUS_ANGLE, BUTTON_RING_RADIUS)
    minus_color = COL_BUTTON_ACTIVE if pressed_button == "MINUS" else COL_BUTTON

    draw.ellipse([minus_pos[0]-BUTTON_RADIUS, minus_pos[1]-BUTTON_RADIUS,
                  minus_pos[0]+BUTTON_RADIUS, minus_pos[1]+BUTTON_RADIUS],
                 fill=minus_color, outline=COL_ACCENT, width=2)
    if FONT_BUTTON:
        draw.text(minus_pos, "−", font=FONT_BUTTON, fill=COL_TEXT, anchor="mm")

    # --- PLUS Button (Bottom Right) ---
    plus_pos = get_button_position(PLUS_ANGLE, BUTTON_RING_RADIUS)
    plus_color = COL_BUTTON_ACTIVE if pressed_button == "PLUS" else COL_BUTTON

    draw.ellipse([plus_pos[0]-BUTTON_RADIUS, plus_pos[1]-BUTTON_RADIUS,
                  plus_pos[0]+BUTTON_RADIUS, plus_pos[1]+BUTTON_RADIUS],
                 fill=plus_color, outline=COL_ACCENT, width=2)
    if FONT_BUTTON:
        draw.text(plus_pos, "+", font=FONT_BUTTON, fill=COL_TEXT, anchor="mm")

    # --- CENTER GO/STOP Button ---
    center_color = COL_STOP if is_running else COL_GO
    center_y = CENTER[1] + 75

    # Glow effect when running
    if is_running:
        draw.ellipse([CENTER[0]-CENTER_BUTTON_RADIUS-4, center_y-CENTER_BUTTON_RADIUS-4,
                     CENTER[0]+CENTER_BUTTON_RADIUS+4, center_y+CENTER_BUTTON_RADIUS+4],
                    fill=(239, 68, 68, 128))

    draw.ellipse([CENTER[0]-CENTER_BUTTON_RADIUS, center_y-CENTER_BUTTON_RADIUS,
                  CENTER[0]+CENTER_BUTTON_RADIUS, center_y+CENTER_BUTTON_RADIUS],
                 fill=center_color)

    center_text = "STOP" if is_running else "GO"
    if FONT_CENTER:
        draw.text((CENTER[0], center_y), center_text,
                 font=FONT_CENTER, fill=COL_TEXT, anchor="mm")

    # Update display
    disp.show_image(img)


def check_button_press(x, y):
    """Determine which button was pressed"""
    # GO/STOP button (center bottom)
    center_y = CENTER[1] + 75
    dist_center = math.sqrt((x - CENTER[0])**2 + (y - center_y)**2)
    if dist_center < CENTER_BUTTON_RADIUS:
        return "GO_STOP"

    # MINUS button
    minus_pos = get_button_position(MINUS_ANGLE, BUTTON_RING_RADIUS)
    dist_minus = math.sqrt((x - minus_pos[0])**2 + (y - minus_pos[1])**2)
    if dist_minus < BUTTON_RADIUS:
        return "MINUS"

    # PLUS button
    plus_pos = get_button_position(PLUS_ANGLE, BUTTON_RING_RADIUS)
    dist_plus = math.sqrt((x - plus_pos[0])**2 + (y - plus_pos[1])**2)
    if dist_plus < BUTTON_RADIUS:
        return "PLUS"

    return None


def run_motor_loop(driver, target_rpm, touch):
    """Motor control loop"""
    print(f"Starting Motor at {target_rpm} RPM")

    GPIO.output(DIR_PIN, MOTOR_DIRECTION)
    driver.enable_driver()

    # Calculate timing
    steps_rev = 200
    microsteps = 32
    steps_per_sec = (target_rpm * steps_rev * microsteps) / 60
    delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Optimized variables
    step_pin = STEP_PIN
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    last_check = time.time()
    check_interval = 0.05  # Check every 50ms

    try:
        while True:
            # Step Pulse
            gpio_out(step_pin, gpio_high)
            t_start = time.time()
            while time.time() - t_start < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            # Wait
            t_end = time.time()
            while time.time() - t_end < (delay - 0.00001):
                pass

            # Check Stop Button
            if time.time() - last_check > check_interval:
                if touch.is_touched():
                    if touch.read_touch():
                        x, y = touch.get_point()
                        action = check_button_press(x, y)
                        if action == "GO_STOP":
                            print("Stop Button Pressed")
                            return
                last_check = time.time()

    except Exception as e:
        print(e)
    finally:
        driver.disable_driver()
        print("Motor Stopped & Disabled")


def main():
    print("Initializing Optimized Circular UI...")

    # Initialize hardware
    disp = LCD_1inch28()
    disp.init_display()

    touch = TouchScreen()
    touch.init()

    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )
    driver.reset_settings()
    driver.set_current_milliamps(1000)
    driver.set_step_mode(32)
    driver.disable_driver()

    # State
    rpm = 50
    last_rpm = -1
    is_running = False

    # Draw initial UI
    draw_ui(disp, rpm, is_running)
    print("UI Ready - Touch to interact!")

    # Touch state
    last_action = None
    hold_start_time = 0
    hold_triggered = False
    last_redraw = time.time()

    try:
        while True:
            if touch.is_touched():
                if touch.read_touch():
                    x, y = touch.get_point()
                    action = check_button_press(x, y)
                    current_time = time.time()

                    if action and action != "GO_STOP":
                        # Handle +/- buttons
                        if action != last_action:
                            # New press - immediate feedback and increment
                            last_action = action
                            hold_start_time = current_time
                            hold_triggered = False

                            # Visual feedback - show button pressed
                            draw_ui(disp, rpm, is_running, pressed_button=action)

                            if action == "PLUS":
                                rpm = min(MAX_RPM, rpm + 10)
                            elif action == "MINUS":
                                rpm = max(MIN_RPM, rpm - 10)

                            # Small delay to show button press
                            time.sleep(0.05)
                            draw_ui(disp, rpm, is_running)
                            print(f"RPM: {rpm}")
                            last_redraw = current_time

                        elif not hold_triggered:
                            # Check for hold (2 seconds)
                            hold_duration = current_time - hold_start_time
                            if hold_duration >= 2.0:
                                hold_triggered = True

                                if action == "PLUS":
                                    rpm = min(MAX_RPM, rpm + 50)
                                elif action == "MINUS":
                                    rpm = max(MIN_RPM, rpm - 50)

                                draw_ui(disp, rpm, is_running, pressed_button=action)
                                print(f"RPM (hold): {rpm}")
                                last_redraw = current_time

                    elif action == "GO_STOP":
                        # GO/STOP button
                        if last_action != "GO_STOP":
                            last_action = "GO_STOP"

                            # Start motor
                            is_running = True
                            draw_ui(disp, rpm, is_running)
                            time.sleep(0.15)
                            run_motor_loop(driver, rpm, touch)
                            is_running = False
                            draw_ui(disp, rpm, is_running)
                            time.sleep(0.3)

                            last_action = None

            else:
                # Touch released
                if last_action in ["PLUS", "MINUS"]:
                    # Redraw without pressed state
                    draw_ui(disp, rpm, is_running)
                    last_redraw = time.time()

                last_action = None
                hold_triggered = False

            # Faster polling for better responsiveness
            time.sleep(0.005)

    except KeyboardInterrupt:
        driver.disable_driver()
        disp.module_exit()
        print("\nShutdown Complete")


if __name__ == "__main__":
    main()
