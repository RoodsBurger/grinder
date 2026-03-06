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
STANDBY_TIMEOUT = 600  # 10 minutes of inactivity before display sleeps

# Feed control
OPEN_TIME_MIN = 0.5   # seconds
OPEN_TIME_MAX = 8.0   # seconds
OPEN_TIME_STEP = 0.5  # seconds per snap increment
OPEN_TIME_DEFAULT = 2.0
CLOSED_TIME = 1.0     # always 1 second closed (informational, enforced in servo_only.py)

# Swipe detection
SWIPE_THRESHOLD = 75  # minimum horizontal pixels to trigger screen switch

# Display Settings - Render at 2x for crispness
SCALE = 2
W_REAL, H_REAL = 240, 240
W_HIGH, H_HIGH = W_REAL * SCALE, H_REAL * SCALE
CENTER = (W_HIGH // 2, H_HIGH // 2)

# Geometry (scaled)
RADIUS_OUTER = 110 * SCALE
RADIUS_INNER = 70 * SCALE
BUTTON_RADIUS = 40 * SCALE
BUTTON_TOUCH_RADIUS = 22
KNOB_RADIUS = 22 * SCALE
ICON_SIZE = 32 * SCALE

# Angles
START_ANGLE = 135
END_ANGLE = 405

# Colors
COL_BG = (10, 10, 15)
COL_TRACK = (40, 44, 52)
COL_ACTIVE = (0, 122, 255)        # Blue for RPM screen
COL_ACTIVE_LOCKED = (60, 70, 80)
COL_FEED_ACTIVE = (255, 140, 0)   # Amber for feed screen
COL_FEED_LOCKED = (100, 60, 10)
COL_KNOB = (255, 255, 255)
COL_BTN_GO = (46, 204, 113)
COL_BTN_STOP = (231, 76, 60)
COL_TEXT = (255, 255, 255)

# --- CACHED RESOURCES (populated at startup) ---
CACHED_FONT = None
ICON_START = None
ICON_STOP = None
ICON_FEED_CLOSED = None
ICON_FEED_OPEN = None

def preload_resources():
    """Pre-render icons and load font at startup"""
    global CACHED_FONT, ICON_START, ICON_STOP, ICON_FEED_CLOSED, ICON_FEED_OPEN

    try:
        CACHED_FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)
    except:
        CACHED_FONT = None

    cx, cy = ICON_SIZE, ICON_SIZE

    # START icon (whole coffee beans)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    bean_w, bean_h, spacing = ICON_SIZE * 0.35, ICON_SIZE * 0.5, ICON_SIZE * 0.25
    for offset in [-spacing, spacing]:
        x = cx + offset
        draw.ellipse([x - bean_w, cy - bean_h, x + bean_w, cy + bean_h], fill=COL_TEXT)
        groove_w = bean_w * 0.8
        draw.arc([x - groove_w, cy - bean_h*0.6, x + groove_w, cy + bean_h*0.6],
                start=20, end=160, fill=COL_BTN_GO, width=int(3*SCALE))
    ICON_START = icon_img

    # STOP icon (ground coffee particles)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    for _ in range(40):
        offset_x = random.uniform(-bean_w - spacing, bean_w + spacing)
        offset_y = random.uniform(-bean_h, bean_h)
        particle_size = random.uniform(1.5*SCALE, 3*SCALE)
        draw.ellipse([cx + offset_x - particle_size, cy + offset_y - particle_size,
                     cx + offset_x + particle_size, cy + offset_y + particle_size],
                    fill=COL_TEXT)
    ICON_STOP = icon_img

    # FEED CLOSED icon - two horizontal plates with a filled amber block (gate sealed)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    bar_w = int(ICON_SIZE * 1.2)
    bar_h = int(3 * SCALE)
    gap = int(0.28 * ICON_SIZE)
    draw.rectangle([cx - bar_w, cy - gap - bar_h, cx + bar_w, cy - gap], fill=COL_TEXT)
    draw.rectangle([cx - bar_w, cy + gap, cx + bar_w, cy + gap + bar_h], fill=COL_TEXT)
    block_w = int(ICON_SIZE * 0.35)
    draw.rectangle([cx - block_w, cy - gap, cx + block_w, cy + gap], fill=COL_FEED_ACTIVE)
    ICON_FEED_CLOSED = icon_img

    # FEED OPEN icon - split plates with amber dots falling through the gap
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)
    inner_gap = int(ICON_SIZE * 0.35)
    draw.rectangle([cx - bar_w, cy - gap - bar_h, cx - inner_gap, cy - gap], fill=COL_TEXT)
    draw.rectangle([cx + inner_gap, cy - gap - bar_h, cx + bar_w, cy - gap], fill=COL_TEXT)
    draw.rectangle([cx - bar_w, cy + gap, cx - inner_gap, cy + gap + bar_h], fill=COL_TEXT)
    draw.rectangle([cx + inner_gap, cy + gap, cx + bar_w, cy + gap + bar_h], fill=COL_TEXT)
    dot_r = int(2.5 * SCALE)
    for ox, oy in [(-int(0.3*ICON_SIZE), -int(0.1*ICON_SIZE)),
                   (0, int(0.05*ICON_SIZE)),
                   (int(0.3*ICON_SIZE), -int(0.1*ICON_SIZE))]:
        draw.ellipse([cx + ox - dot_r, cy + oy - dot_r,
                      cx + ox + dot_r, cy + oy + dot_r], fill=COL_FEED_ACTIVE)
    ICON_FEED_OPEN = icon_img

# --- HELPER FUNCTIONS ---

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y):
    """Map touch to RPM screen action: returns 'BUTTON', int RPM, or None"""
    dx, dy = x - (W_REAL // 2), y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    if dist < BUTTON_TOUCH_RADIUS:
        return "BUTTON"
    if dist < 45:
        return None

    angle = get_angle(x, y)
    eff_angle = angle if angle >= 135 else angle + 360

    if 135 <= eff_angle <= 405:
        ratio = (eff_angle - 135) / 270
        rpm_value = MIN_RPM + ratio * (MAX_RPM - MIN_RPM)
        return int(round(rpm_value / 10) * 10)

    return None

def map_touch_feed(x, y):
    """Map touch to feed screen action: returns 'BUTTON', float open_time, or None"""
    dx, dy = x - (W_REAL // 2), y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    if dist < BUTTON_TOUCH_RADIUS:
        return "BUTTON"
    if dist < 45:
        return None

    angle = get_angle(x, y)
    eff_angle = angle if angle >= 135 else angle + 360

    if 135 <= eff_angle <= 405:
        ratio = (eff_angle - 135) / 270
        raw_time = OPEN_TIME_MIN + ratio * (OPEN_TIME_MAX - OPEN_TIME_MIN)
        snapped = round(raw_time / OPEN_TIME_STEP) * OPEN_TIME_STEP
        return float(max(OPEN_TIME_MIN, min(OPEN_TIME_MAX, snapped)))

    return None

def draw_nav_dots(draw, current_screen):
    """Two small dots at the bottom to indicate the active screen"""
    dot_y = CENTER[1] + 100 * SCALE
    dot_r = 4 * SCALE
    spacing = 20 * SCALE
    for i in range(2):
        col = (220, 220, 220) if i == current_screen else (55, 55, 55)
        dot_x = CENTER[0] + int((i - 0.5) * spacing)
        draw.ellipse([dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r], fill=col)

def draw_ui(disp, rpm, is_running, current_screen=0):
    """RPM control screen - blue arc"""
    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    bbox = [CENTER[0]-RADIUS_OUTER, CENTER[1]-RADIUS_OUTER,
            CENTER[0]+RADIUS_OUTER, CENTER[1]+RADIUS_OUTER]
    draw.pieslice(bbox, start=START_ANGLE, end=END_ANGLE, fill=COL_TRACK)

    fill_col = COL_ACTIVE_LOCKED if is_running else COL_ACTIVE
    ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM) if MAX_RPM > MIN_RPM else 0
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    draw.pieslice(bbox, start=START_ANGLE, end=active_angle, fill=fill_col)

    mask_bbox = [CENTER[0]-RADIUS_INNER, CENTER[1]-RADIUS_INNER,
                 CENTER[0]+RADIUS_INNER, CENTER[1]+RADIUS_INNER]
    draw.ellipse(mask_bbox, fill=COL_BG)

    if not is_running:
        knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
        rad = math.radians(active_angle)
        kx = CENTER[0] + knob_dist * math.cos(rad)
        ky = CENTER[1] + knob_dist * math.sin(rad)
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=COL_KNOB)

    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS], fill=btn_col)

    icon = ICON_STOP if is_running else ICON_START
    if icon:
        img.paste(icon, (CENTER[0] - ICON_SIZE, CENTER[1] - ICON_SIZE), icon)

    if CACHED_FONT:
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm // 2} RPM",
                 font=CACHED_FONT, fill=(150, 150, 150), anchor="mm")

    draw_nav_dots(draw, current_screen)
    disp.show_image(img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS))

def draw_feed_ui(disp, open_time, is_running, current_screen=1):
    """Feed open-time control screen - amber arc"""
    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    bbox = [CENTER[0]-RADIUS_OUTER, CENTER[1]-RADIUS_OUTER,
            CENTER[0]+RADIUS_OUTER, CENTER[1]+RADIUS_OUTER]
    draw.pieslice(bbox, start=START_ANGLE, end=END_ANGLE, fill=COL_TRACK)

    fill_col = COL_FEED_LOCKED if is_running else COL_FEED_ACTIVE
    ratio = (open_time - OPEN_TIME_MIN) / (OPEN_TIME_MAX - OPEN_TIME_MIN)
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    draw.pieslice(bbox, start=START_ANGLE, end=active_angle, fill=fill_col)

    mask_bbox = [CENTER[0]-RADIUS_INNER, CENTER[1]-RADIUS_INNER,
                 CENTER[0]+RADIUS_INNER, CENTER[1]+RADIUS_INNER]
    draw.ellipse(mask_bbox, fill=COL_BG)

    if not is_running:
        knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
        rad = math.radians(active_angle)
        kx = CENTER[0] + knob_dist * math.cos(rad)
        ky = CENTER[1] + knob_dist * math.sin(rad)
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=COL_KNOB)

    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS], fill=btn_col)

    icon = ICON_FEED_OPEN if is_running else ICON_FEED_CLOSED
    if icon:
        img.paste(icon, (CENTER[0] - ICON_SIZE, CENTER[1] - ICON_SIZE), icon)

    if CACHED_FONT:
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{open_time:.1f}s open",
                 font=CACHED_FONT, fill=(150, 150, 150), anchor="mm")

    draw_nav_dots(draw, current_screen)
    disp.show_image(img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS))

def draw_current(disp, current_screen, rpm, open_time, is_running):
    """Redraw whichever screen is currently active"""
    if current_screen == 0:
        draw_ui(disp, rpm, is_running, current_screen)
    else:
        draw_feed_ui(disp, open_time, is_running, current_screen)

# --- PROCESS MANAGEMENT ---

def start_processes(rpm, open_time, disp, config_id='K4'):
    """Start motor and feeder as parallel subprocesses. Returns (motor_proc, servo_proc)."""
    disp.close_spi_for_motor()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    motor_proc = subprocess.Popen(
        ["python3", os.path.join(script_dir, "motor_only.py"), str(rpm), config_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    servo_proc = subprocess.Popen(
        ["python3", os.path.join(script_dir, "servo_only.py"), str(open_time)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    return motor_proc, servo_proc

def stop_processes(motor_proc, servo_proc, disp):
    """Terminate both motor and feeder subprocesses and reopen LCD SPI."""
    for proc in [motor_proc, servo_proc]:
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
    open_time = OPEN_TIME_DEFAULT
    current_screen = 0  # 0 = RPM control, 1 = Feed control
    motor_proc = None
    servo_proc = None
    last_activity_time = time.time()
    is_standby = False

    # Swipe tracking
    touch_start_x = None
    touch_start_y = None

    draw_ui(disp, rpm, is_running=False, current_screen=current_screen)

    try:
        while True:
            try:
                current_time = time.time()

                # Standby timeout (only when motor stopped)
                if not is_standby and motor_proc is None:
                    if current_time - last_activity_time > STANDBY_TIMEOUT:
                        disp.sleep_display()
                        is_standby = True

                if touch.is_touched():
                    if touch.read_touch():
                        # Wake from standby
                        if is_standby:
                            disp.wake_display()
                            is_standby = False
                            last_activity_time = current_time
                            draw_current(disp, current_screen, rpm, open_time,
                                        is_running=(motor_proc is not None))
                            time.sleep(0.2)
                            continue

                        last_activity_time = current_time
                        x, y = touch.get_point()

                        # Record where this touch began
                        if touch.touch_state == touch.STATE_PRESSED:
                            touch_start_x, touch_start_y = x, y

                        # Swipe detection: large horizontal displacement → switch screen
                        if touch_start_x is not None:
                            swipe_dx = x - touch_start_x
                            swipe_dy = y - touch_start_y
                            if abs(swipe_dx) >= SWIPE_THRESHOLD and abs(swipe_dx) > abs(swipe_dy) * 2:
                                current_screen = 1 - current_screen
                                touch_start_x = None  # consume the gesture
                                draw_current(disp, current_screen, rpm, open_time,
                                            is_running=(motor_proc is not None))
                                time.sleep(0.25)
                                continue

                        # Route arc slider and button to the active screen
                        if current_screen == 0:
                            action = map_touch(x, y)
                            if isinstance(action, int):
                                if motor_proc is None and action != rpm:
                                    rpm = action
                                    draw_ui(disp, rpm, is_running=False, current_screen=current_screen)
                            elif action == "BUTTON":
                                if motor_proc is None:
                                    draw_ui(disp, rpm, is_running=True, current_screen=current_screen)
                                    motor_proc, servo_proc = start_processes(rpm, open_time, disp, MOTOR_CONFIG_ID)
                                else:
                                    stop_processes(motor_proc, servo_proc, disp)
                                    motor_proc = None
                                    servo_proc = None
                                    draw_ui(disp, rpm, is_running=False, current_screen=current_screen)
                                time.sleep(0.15)

                        else:  # Feed screen
                            action = map_touch_feed(x, y)
                            if isinstance(action, float):
                                if motor_proc is None and action != open_time:
                                    open_time = action
                                    draw_feed_ui(disp, open_time, is_running=False, current_screen=current_screen)
                            elif action == "BUTTON":
                                if motor_proc is None:
                                    draw_feed_ui(disp, open_time, is_running=True, current_screen=current_screen)
                                    motor_proc, servo_proc = start_processes(rpm, open_time, disp, MOTOR_CONFIG_ID)
                                else:
                                    stop_processes(motor_proc, servo_proc, disp)
                                    motor_proc = None
                                    servo_proc = None
                                    draw_feed_ui(disp, open_time, is_running=False, current_screen=current_screen)
                                time.sleep(0.15)

                else:
                    # Touch released - reset swipe tracking
                    touch_start_x = None

                # Check if either subprocess ended unexpectedly - stop both
                motor_dead = motor_proc and motor_proc.poll() is not None
                servo_dead = servo_proc and servo_proc.poll() is not None
                if motor_dead or servo_dead:
                    if motor_dead:
                        print(f"Motor process ended unexpectedly (code {motor_proc.returncode})")
                    if servo_dead:
                        print(f"Servo process ended unexpectedly (code {servo_proc.returncode})")
                    try:
                        for proc in [motor_proc, servo_proc]:
                            if proc:
                                out = proc.stdout.read()
                                err = proc.stderr.read()
                                if out: print(f"stdout: {out}")
                                if err: print(f"stderr: {err}")
                    except:
                        pass

                    stop_processes(motor_proc, servo_proc, disp)
                    motor_proc = None
                    servo_proc = None

                    if is_standby:
                        disp.wake_display()
                        is_standby = False

                    draw_current(disp, current_screen, rpm, open_time, is_running=False)
                    last_activity_time = current_time
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
        if motor_proc or servo_proc:
            stop_processes(motor_proc, servo_proc, disp)
        if is_standby:
            disp.wake_display()
        try:
            GPIO.output(SLEEP_PIN, GPIO.LOW)
        except:
            pass
        disp.module_exit()
        touch.cleanup()

if __name__ == "__main__":
    main()
