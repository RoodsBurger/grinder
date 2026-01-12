import time
import math
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import your existing drivers
from lcd_display_fast import LCD_1inch28
from touch_screen import TouchScreen
from pololu_lib import HighPowerStepperDriver

# --- CONFIGURATION ---
MIN_RPM = 0
MAX_RPM = 300
ACCEL_RATE = 1.0

# --- MOTOR DIRECTION SETTING ---
# Change this to 0 or 1 to reverse the motor
MOTOR_DIRECTION = 1

# High-Res Render Settings (Native Resolution)
SCALE = 1
W_REAL, H_REAL = 240, 240
W_HIGH, H_HIGH = W_REAL * SCALE, H_REAL * SCALE
CENTER = (W_HIGH // 2, H_HIGH // 2)

# Geometry
RADIUS_OUTER = 110 * SCALE
RADIUS_INNER = 85 * SCALE
BUTTON_RADIUS = 50 * SCALE

# Angles
START_ANGLE = 135
END_ANGLE = 405

# Colors
COL_BG = (10, 10, 15)
COL_TRACK = (40, 44, 52)
COL_ACTIVE = (0, 122, 255)
COL_ACTIVE_LOCKED = (60, 70, 80)
COL_KNOB = (255, 255, 255)
COL_BTN_GO = (46, 204, 113)
COL_BTN_STOP = (231, 76, 60)
COL_TEXT = (255, 255, 255)

# Hardware Pins
SCS_PIN = 8
DIR_PIN = 24
STEP_PIN = 25
SLEEP_PIN = 7

# --- HELPER FUNCTIONS ---

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y, current_rpm):
    """Map touch coordinates to UI actions"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    if dist < 60:
        return "BUTTON"

    angle = get_angle(x, y)
    eff_angle = angle
    if eff_angle < 135: eff_angle += 360

    start, end = 135, 405
    if start <= eff_angle <= end:
        ratio = (eff_angle - start) / (end - start)
        return int(MIN_RPM + ratio * (MAX_RPM - MIN_RPM))

    return None

def draw_ui(disp, rpm, is_running):
    """Draws the UI at native resolution"""
    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    # 1. Track
    bbox = [CENTER[0]-RADIUS_OUTER, CENTER[1]-RADIUS_OUTER,
            CENTER[0]+RADIUS_OUTER, CENTER[1]+RADIUS_OUTER]
    draw.pieslice(bbox, start=START_ANGLE, end=END_ANGLE, fill=COL_TRACK)

    # 2. Active Arc
    fill_col = COL_ACTIVE_LOCKED if is_running else COL_ACTIVE
    ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM)
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    draw.pieslice(bbox, start=START_ANGLE, end=active_angle, fill=fill_col)

    # 3. Center Hole
    mask_bbox = [CENTER[0]-RADIUS_INNER, CENTER[1]-RADIUS_INNER,
                 CENTER[0]+RADIUS_INNER, CENTER[1]+RADIUS_INNER]
    draw.ellipse(mask_bbox, fill=COL_BG)

    # 4. Knob
    if not is_running:
        knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
        rad = math.radians(active_angle)
        kx = CENTER[0] + knob_dist * math.cos(rad)
        ky = CENTER[1] + knob_dist * math.sin(rad)
        kr = 15 * SCALE
        draw.ellipse([kx-kr, ky-kr, kx+kr, ky+kr], fill=COL_KNOB)

    # 5. Button
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS],
                 fill=btn_col)

    # 6. Text
    try:
        font_size = 20 * SCALE
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        font = None

    text = "STOP" if is_running else "GO"
    if font:
        draw.text(CENTER, text, font=font, fill=COL_TEXT, anchor="mm")
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm} RPM", font=font_sm, fill=(150,150,150), anchor="mm")
    else:
        draw.text(CENTER, text, fill=COL_TEXT)

    # 7. Show (No resize needed if SCALE=1)
    disp.show_image(img)


# --- MAIN LOGIC ---

def run_motor_loop(driver, target_rpm, touch):
    """
    Blocking loop that runs the motor.
    """
    print(f"Starting Motor at {target_rpm} RPM")

    # 1. Set Direction (The Fix)
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)

    driver.enable_driver()

    # Calculate Step Delay
    steps_rev = 200
    microsteps = 32  # 1/32 Microstepping
    steps_per_sec = (target_rpm * steps_rev * microsteps) / 60
    delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Local optimizations
    step_pin = STEP_PIN
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    steps_count = 0
    check_every = 20  # Check touch every N steps

    # Drift-correcting timer
    t_next = time.perf_counter()

    try:
        while True:
            # Step Pulse
            gpio_out(step_pin, gpio_high)
            # Short busy wait for pulse width (approx 2us)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            # Calculate next step time and busy-wait
            t_next += delay
            while time.perf_counter() < t_next:
                pass

            steps_count += 1
            if steps_count % check_every == 0:
                # Fast GPIO Check
                if touch.is_touched():
                    # Detailed Check
                    if touch.read_touch():
                        x, y = touch.get_point()
                        action = map_touch(x, y, target_rpm)
                        if action == "BUTTON":
                            print("Stop Button Pressed")
                            return

    except Exception as e:
        print(e)
    finally:
        driver.disable_driver()
        print("Motor Stopped & Disabled")


def main():
    disp = LCD_1inch28()
    disp.init_display()

    touch = TouchScreen()
    touch.init()

    driver = HighPowerStepperDriver(
        spi_bus=0, spi_device=0,
        cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
    )
    driver.reset_settings()
    driver.set_current_milliamps(4000)
    driver.set_step_mode(32)            # Set to 1/32 Microstepping
    driver.disable_driver()

    rpm = 50
    draw_ui(disp, rpm, is_running=False)

    try:
        while True:
            if touch.is_touched():
                if touch.read_touch():
                    x, y = touch.get_point()
                    action = map_touch(x, y, rpm)

                    if isinstance(action, int):
                        if action != rpm:
                            rpm = action
                            draw_ui(disp, rpm, is_running=False)

                    elif action == "BUTTON":
                        draw_ui(disp, rpm, is_running=True)
                        # Removed sleep(0.2)
                        run_motor_loop(driver, rpm, touch)
                        draw_ui(disp, rpm, is_running=False)
                        # Removed sleep(0.5)

            time.sleep(0.01)

    except KeyboardInterrupt:
        driver.disable_driver()
        disp.module_exit()

if __name__ == "__main__":
    main()