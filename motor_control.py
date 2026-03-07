import time
import math
import subprocess
import os
import random
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import display and touch drivers
from lcd_display import LCD_1inch28
from touch_screen import TouchScreen, GESTURE_SWIPE_LEFT, GESTURE_SWIPE_RIGHT

# Pre-seed random for consistent icon rendering
random.seed(42)

# Hardware pins
SLEEP_PIN = 7

# --- CONFIGURATION ---
MOTOR_CONFIG_ID = 'K4'  # Motor config from motor_configs.json (6500mA, 100kHz PWM, 1/64 step)
MIN_RPM = 0
MAX_RPM = 300
STANDBY_TIMEOUT = 600  # 10 minutes of inactivity before display sleeps

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
COL_ACTIVE_SOFT = (0, 60, 130)   # dimmed arc when idle/not grabbed
COL_ACTIVE_LOCKED = (60, 70, 80)
COL_KNOB = (255, 255, 255)
COL_KNOB_SOFT = (160, 160, 170)  # dimmed knob when idle/not grabbed
COL_KNOB_WAITING = (255, 165, 0)   # orange - press detected, keep holding
COL_KNOB_ACTIVE = (100, 220, 100)  # green - drag is live
COL_BTN_GO = (46, 204, 113)
COL_BTN_STOP = (231, 76, 60)
COL_TEXT = (255, 255, 255)

# Touch interaction
KNOB_HIT_RADIUS = 38   # px (real coords) - must press within this distance of knob
KNOB_HOLD_TIME  = 0.5  # seconds to hold on knob before drag activates
BUTTON_MAX_TAP  = 0.5  # seconds - button press longer than this is ignored
BUTTON_RELEASE_TIMEOUT = 0.08  # 80ms — fast tap response for button
KNOB_RELEASE_TIMEOUT   = 0.50  # 500ms — CST816T can go silent 200ms+ while holding still

# Interaction states
INTERACT_IDLE         = 0
INTERACT_KNOB_WAITING = 1  # pressed knob, waiting for hold
INTERACT_KNOB_ACTIVE  = 2  # hold confirmed, dragging
INTERACT_BUTTON       = 3  # pressed button, waiting for tap release

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

def get_knob_pos(rpm):
    """Return knob center in real (1x) display coordinates for the given RPM."""
    ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM) if MAX_RPM > MIN_RPM else 0
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2 / SCALE
    rad = math.radians(active_angle)
    return (W_REAL // 2 + knob_dist * math.cos(rad),
            H_REAL // 2 + knob_dist * math.sin(rad))

def is_on_knob(x, y, rpm):
    """True if touch (x,y) lands within KNOB_HIT_RADIUS of the knob."""
    kx, ky = get_knob_pos(rpm)
    return math.sqrt((x - kx)**2 + (y - ky)**2) < KNOB_HIT_RADIUS

def is_on_button(x, y):
    """True if touch lands on the centre button."""
    dx, dy = x - W_REAL // 2, y - H_REAL // 2
    return math.sqrt(dx*dx + dy*dy) < BUTTON_TOUCH_RADIUS

def arc_to_rpm(x, y):
    """Convert touch position to snapped RPM value, or None if outside arc zone."""
    dx, dy = x - W_REAL // 2, y - H_REAL // 2
    dist = math.sqrt(dx*dx + dy*dy)
    if dist < 45:
        return None
    angle = get_angle(x, y)
    eff_angle = angle if angle >= 135 else angle + 360
    if 135 <= eff_angle <= 405:
        ratio = (eff_angle - 135) / 270
        return int(round((MIN_RPM + ratio * (MAX_RPM - MIN_RPM)) / 10) * 10)
    return None

def draw_ui(disp, rpm, is_running, knob_color=None, highlight=False):
    """Draws the UI at 2x resolution.
    highlight=False → soft arc + soft knob (idle state)
    highlight=True  → bright arc + bright knob (grabbed / waiting)
    knob_color overrides the knob colour when set explicitly.
    """
    if knob_color is None:
        knob_color = COL_KNOB if highlight else COL_KNOB_SOFT

    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    # Track and active arc
    bbox = [CENTER[0]-RADIUS_OUTER, CENTER[1]-RADIUS_OUTER,
            CENTER[0]+RADIUS_OUTER, CENTER[1]+RADIUS_OUTER]
    draw.pieslice(bbox, start=START_ANGLE, end=END_ANGLE, fill=COL_TRACK)

    ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM) if MAX_RPM > MIN_RPM else 0
    active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
    if is_running:
        fill_col = COL_ACTIVE_LOCKED
    elif highlight:
        fill_col = COL_ACTIVE
    else:
        fill_col = COL_ACTIVE_SOFT
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
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=knob_color)

    # Button
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS], fill=btn_col)

    # Icon
    icon = ICON_STOP if is_running else ICON_START
    if icon:
        img.paste(icon, (CENTER[0] - ICON_SIZE, CENTER[1] - ICON_SIZE), icon)

    # RPM text (display at half - gearbox reduction)
    if CACHED_FONT:
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm // 2} RPM",
                 font=CACHED_FONT, fill=(150, 150, 150), anchor="mm")

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
    current_screen = 0   # 0 = RPM screen (more screens added progressively)
    motor_proc = None
    last_activity_time = time.time()
    is_standby = False

    # Touch interaction state machine
    interact_state = INTERACT_IDLE
    interact_start_time = 0
    was_touching = False          # True once first touch event classified
    last_touch_event_time = 0    # time of most recent touch read — used for release timeout
    gesture_cooldown_until = 0

    draw_ui(disp, rpm, is_running=False)

    try:
        while True:
            try:
                current_time = time.time()

                # Standby timeout (only when motor stopped)
                if not is_standby and motor_proc is None:
                    if current_time - last_activity_time > STANDBY_TIMEOUT:
                        disp.sleep_display()
                        is_standby = True

                # ── TOUCH EVENT (INT pin pulsed LOW) ────────────────────────
                if touch.is_touched():
                    if touch.read_touch():
                        last_touch_event_time = current_time

                        # Wake from standby
                        if is_standby:
                            disp.wake_display()
                            is_standby = False
                            last_activity_time = current_time
                            interact_state = INTERACT_IDLE
                            was_touching = False
                            draw_ui(disp, rpm, is_running=(motor_proc is not None))
                            print("TOUCH: Wake from standby")
                            time.sleep(0.2)
                            continue

                        last_activity_time = current_time

                        # Drop trailing reads after a gesture (must check BEFORE gesture handler)
                        if current_time < gesture_cooldown_until:
                            touch.get_gesture()  # clear so it doesn't fire after cooldown
                            print("TOUCH: Gesture cooldown, ignoring")
                            was_touching = True
                            continue

                        # Hardware gesture → switch screen
                        gesture = touch.get_gesture()
                        if gesture in (GESTURE_SWIPE_LEFT, GESTURE_SWIPE_RIGHT):
                            direction = "LEFT" if gesture == GESTURE_SWIPE_LEFT else "RIGHT"
                            current_screen = 1 - current_screen
                            gesture_cooldown_until = current_time + 0.4
                            interact_state = INTERACT_IDLE
                            was_touching = False
                            print(f"GESTURE: Swipe {direction} → screen {current_screen}")
                            draw_ui(disp, rpm, is_running=(motor_proc is not None))
                            continue

                        x, y = touch.get_point()

                        # ── First contact this gesture ───────────────────────
                        if not was_touching:
                            was_touching = True
                            interact_start_time = current_time

                            if is_on_button(x, y):
                                interact_state = INTERACT_BUTTON
                                print(f"TOUCH: Button pressed at ({x},{y})")

                            elif is_on_knob(x, y, rpm) and motor_proc is None:
                                interact_state = INTERACT_KNOB_WAITING
                                print(f"TOUCH: Knob pressed at ({x},{y}), hold {KNOB_HOLD_TIME}s to activate")
                                draw_ui(disp, rpm, is_running=False, highlight=True)

                            else:
                                interact_state = INTERACT_IDLE
                                print(f"TOUCH: Ignored at ({x},{y}) — not on knob or button")

                        # ── Continuing hold / drag ───────────────────────────
                        else:
                            hold_time = current_time - interact_start_time

                            if interact_state == INTERACT_KNOB_WAITING:
                                if hold_time >= KNOB_HOLD_TIME:
                                    interact_state = INTERACT_KNOB_ACTIVE
                                    print(f"TOUCH: Knob ACTIVE after {hold_time:.2f}s hold")
                                    draw_ui(disp, rpm, is_running=False, highlight=True)
                                else:
                                    print(f"TOUCH: Knob waiting ({hold_time:.2f}s / {KNOB_HOLD_TIME}s)")

                            elif interact_state == INTERACT_KNOB_ACTIVE:
                                new_rpm = arc_to_rpm(x, y)
                                if new_rpm is not None and new_rpm != rpm:
                                    print(f"TOUCH: Drag → RPM {rpm} → {new_rpm}")
                                    rpm = new_rpm
                                    draw_ui(disp, rpm, is_running=False, highlight=True)

                            elif interact_state == INTERACT_BUTTON:
                                hold_time = current_time - interact_start_time
                                print(f"TOUCH: Button held ({hold_time:.2f}s)")

                # ── RELEASE: no touch event for state-appropriate timeout ────
                release_timeout = BUTTON_RELEASE_TIMEOUT if interact_state == INTERACT_BUTTON else KNOB_RELEASE_TIMEOUT
                if was_touching and (current_time - last_touch_event_time) > release_timeout:
                    hold_time = last_touch_event_time - interact_start_time
                    print(f"TOUCH: Released — state={interact_state}, hold={hold_time:.2f}s")
                    was_touching = False

                    if interact_state == INTERACT_BUTTON:
                        if hold_time <= BUTTON_MAX_TAP:
                            print("TOUCH: Button tap → toggle motor")
                            if motor_proc is None:
                                draw_ui(disp, rpm, is_running=True)
                                motor_proc = start_motor_process(rpm, disp, MOTOR_CONFIG_ID)
                            else:
                                stop_motor_process(motor_proc, disp)
                                motor_proc = None
                                draw_ui(disp, rpm, is_running=False)
                        else:
                            print(f"TOUCH: Button long-press ({hold_time:.2f}s) — ignored")

                    elif interact_state in (INTERACT_KNOB_WAITING, INTERACT_KNOB_ACTIVE):
                        print("TOUCH: Knob released — restoring normal colour")
                        draw_ui(disp, rpm, is_running=(motor_proc is not None))

                    interact_state = INTERACT_IDLE

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

                    # Wake display if in standby
                    if is_standby:
                        disp.wake_display()
                        is_standby = False

                    draw_ui(disp, rpm, is_running=False)
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
        if motor_proc:
            stop_motor_process(motor_proc, disp)
        # Wake display on exit so user can see it stopped
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
