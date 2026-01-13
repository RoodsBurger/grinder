import time
import math
import subprocess
import os
import random
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import display and touch drivers
from lcd_display import LCD_1inch28
from touch_screen import TouchScreen

# Pre-seed random for consistent icon rendering
random.seed(42)

# Hardware pins
SLEEP_PIN = 7

# --- CONFIGURATION ---
MOTOR_CONFIG_ID = 'K4'  # Motor config from motor_configs.json (8000mA, 100kHz PWM, 1/64 step)
MIN_RPM = 0
MAX_RPM = 300

# Display Settings - Render at 2x for crispness
SCALE = 2
W_REAL, H_REAL = 240, 240
W_HIGH, H_HIGH = W_REAL * SCALE, H_REAL * SCALE
CENTER = (W_HIGH // 2, H_HIGH // 2)

# Geometry (scaled)
RADIUS_OUTER = 110 * SCALE
RADIUS_INNER = 70 * SCALE  # Thicker slider track
BUTTON_RADIUS = 40 * SCALE  # Button size (visual)
BUTTON_TOUCH_RADIUS = 22  # Touch detection (even smaller to avoid slider conflicts)
KNOB_RADIUS = 22 * SCALE  # Bigger slider knob
ICON_SIZE = 32 * SCALE  # Icon size (bigger)

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

# --- CACHED RESOURCES (populated at startup) ---
CACHED_FONT = None
ICON_START = None
ICON_STOP = None

def preload_resources():
    """Pre-render icons and load font at startup"""
    global CACHED_FONT, ICON_START, ICON_STOP

    try:
        CACHED_FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)
    except:
        CACHED_FONT = None

    # START icon (whole coffee beans)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    bean_w, bean_h, spacing = ICON_SIZE * 0.35, ICON_SIZE * 0.5, ICON_SIZE * 0.25
    center_x, center_y = ICON_SIZE, ICON_SIZE

    for offset in [-spacing, spacing]:
        x = center_x + offset
        draw.ellipse([x - bean_w, center_y - bean_h, x + bean_w, center_y + bean_h], fill=COL_TEXT)
        groove_w = bean_w * 0.8
        draw.arc([x - groove_w, center_y - bean_h*0.6, x + groove_w, center_y + bean_h*0.6],
                start=20, end=160, fill=COL_BTN_GO, width=int(3*SCALE))
    ICON_START = icon_img

    # STOP icon (ground coffee particles)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    for _ in range(40):
        offset_x = random.uniform(-bean_w - spacing, bean_w + spacing)
        offset_y = random.uniform(-bean_h, bean_h)
        particle_size = random.uniform(1.5*SCALE, 3*SCALE)
        draw.ellipse([center_x + offset_x - particle_size, center_y + offset_y - particle_size,
                     center_x + offset_x + particle_size, center_y + offset_y + particle_size],
                    fill=COL_TEXT)
    ICON_STOP = icon_img

# --- HELPER FUNCTIONS ---

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y, debug=False):
    """Map touch to action: returns 'BUTTON', RPM integer, or None"""
    dx, dy = x - (W_REAL // 2), y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    if dist < BUTTON_TOUCH_RADIUS:
        return "BUTTON"

    if dist < 45:  # Dead zone
        return None

    angle = get_angle(x, y)
    eff_angle = angle if angle >= 135 else angle + 360

    if 135 <= eff_angle <= 405:
        ratio = (eff_angle - 135) / 270
        rpm_value = MIN_RPM + ratio * (MAX_RPM - MIN_RPM)
        return int(round(rpm_value / 5) * 5)

    return None

def draw_ui(disp, rpm, is_running):
    """Draws the UI at 2x resolution with cached resources"""
    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    # Track and active arc
    bbox = [CENTER[0]-RADIUS_OUTER, CENTER[1]-RADIUS_OUTER,
            CENTER[0]+RADIUS_OUTER, CENTER[1]+RADIUS_OUTER]
    draw.pieslice(bbox, start=START_ANGLE, end=END_ANGLE, fill=COL_TRACK)

    fill_col = COL_ACTIVE_LOCKED if is_running else COL_ACTIVE
    ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM)
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    draw.pieslice(bbox, start=START_ANGLE, end=active_angle, fill=fill_col)

    # Center hole
    mask_bbox = [CENTER[0]-RADIUS_INNER, CENTER[1]-RADIUS_INNER,
                 CENTER[0]+RADIUS_INNER, CENTER[1]+RADIUS_INNER]
    draw.ellipse(mask_bbox, fill=COL_BG)

    # Knob (when stopped)
    if not is_running:
        knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
        rad = math.radians(active_angle)
        kx, ky = CENTER[0] + knob_dist * math.cos(rad), CENTER[1] + knob_dist * math.sin(rad)
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=COL_KNOB)

    # Button
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS], fill=btn_col)

    # Icon
    icon = ICON_STOP if is_running else ICON_START
    if icon:
        img.paste(icon, (CENTER[0] - ICON_SIZE, CENTER[1] - ICON_SIZE), icon)

    # RPM text
    if CACHED_FONT:
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm} RPM",
                 font=CACHED_FONT, fill=(150,150,150), anchor="mm")

    disp.show_image(img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS))

# --- MOTOR PROCESS MANAGEMENT ---

def start_motor_process(rpm, disp, config_id='K4'):
    """Start motor process with specified config"""
    disp.close_spi_for_motor()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    motor_script = os.path.join(script_dir, "motor_only.py")
    return subprocess.Popen(
        ["python3", motor_script, str(rpm), config_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

def stop_motor_process(proc, disp):
    """Stop motor process and reopen LCD SPI"""
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    try:
        GPIO.output(SLEEP_PIN, GPIO.LOW)
    except Exception as e:
        print(f"ERROR: Failed to disable motor: {e}")

    disp.reopen_spi_after_motor()

# --- MAIN LOOP ---

def main():
    if os.geteuid() != 0:
        print("ERROR: This script must be run with sudo!")
        print("Usage: sudo python3 motor_control.py")
        return

    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)
    GPIO.output(SLEEP_PIN, GPIO.LOW)

    preload_resources()

    disp = LCD_1inch28()
    disp.init_display()
    time.sleep(1.0)

    touch = TouchScreen()
    if not touch.init():
        time.sleep(1)
        if not touch.init():
            print("WARNING: Touch controller not responding - UI will show but touch won't work")

    rpm = 200
    motor_proc = None

    draw_ui(disp, rpm, is_running=False)

    try:
        while True:
            try:
                # Simple immediate response
                if touch.is_touched():
                    if touch.read_touch():
                        x, y = touch.get_point()
                        action = map_touch(x, y, debug=False)

                        # Slider - change RPM immediately (only when motor not running)
                        if isinstance(action, int):
                            if motor_proc is None:
                                if action != rpm:
                                    rpm = action
                                    draw_ui(disp, rpm, is_running=False)
                            # Silently ignore slider while motor running

                        # Button - toggle motor
                        elif action == "BUTTON":
                            if motor_proc is None:
                                # START - draw UI BEFORE closing SPI
                                draw_ui(disp, rpm, is_running=True)
                                motor_proc = start_motor_process(rpm, disp, MOTOR_CONFIG_ID)
                            else:
                                # STOP - reopen SPI, then draw
                                stop_motor_process(motor_proc, disp)
                                motor_proc = None
                                draw_ui(disp, rpm, is_running=False)
                            # Brief debounce
                            time.sleep(0.15)

                # Check if motor process ended unexpectedly
                if motor_proc and motor_proc.poll() is not None:
                    print(f"Motor process ended with code {motor_proc.returncode}")
                    try:
                        stdout = motor_proc.stdout.read()
                        stderr = motor_proc.stderr.read()
                        if stdout:
                            print(f"stdout: {stdout}")
                        if stderr:
                            print(f"stderr: {stderr}")
                    except:
                        pass

                    stop_motor_process(None, disp)
                    motor_proc = None
                    draw_ui(disp, rpm, is_running=False)
                    time.sleep(0.5)

                time.sleep(0.005)  # 200Hz update rate

            except Exception as e:
                print(f"ERROR: Loop exception: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.1)

    except KeyboardInterrupt:
        pass
    finally:
        if motor_proc:
            stop_motor_process(motor_proc, disp)
        try:
            GPIO.output(SLEEP_PIN, GPIO.LOW)
        except:
            pass
        disp.module_exit()
        touch.cleanup()

if __name__ == "__main__":
    main()
