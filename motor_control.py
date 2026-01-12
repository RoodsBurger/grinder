import time
import math
import subprocess
import os
from PIL import Image, ImageDraw, ImageFont

# Import display and touch drivers
from lcd_display import LCD_1inch28
from touch_screen import TouchScreen

# --- CONFIGURATION ---
MIN_RPM = 0
MAX_RPM = 300

# Display Settings (back to 1x for speed)
SCALE = 1
W_REAL, H_REAL = 240, 240
W_HIGH, H_HIGH = W_REAL * SCALE, H_REAL * SCALE
CENTER = (W_HIGH // 2, H_HIGH // 2)

# Geometry
RADIUS_OUTER = 110 * SCALE
RADIUS_INNER = 85 * SCALE
BUTTON_RADIUS = 50 * SCALE

# Touch gesture thresholds
TAP_MAX_DURATION = 0.3  # Max 300ms for tap
HOLD_MIN_DURATION = 0.1  # Min 100ms for hold

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

# --- HELPER FUNCTIONS ---

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y):
    """
    Map touch coordinates to UI actions.

    Returns:
        "BUTTON" if center button pressed (45px radius)
        int RPM value if touching slider
        None otherwise
    """
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    # Button: Smaller area (45px radius)
    if dist < 45:
        return "BUTTON"

    # Slider: Only if outside button area
    angle = get_angle(x, y)
    eff_angle = angle
    if eff_angle < 135: eff_angle += 360

    start, end = 135, 405
    if start <= eff_angle <= end:
        ratio = (eff_angle - start) / (end - start)
        rpm_value = MIN_RPM + ratio * (MAX_RPM - MIN_RPM)
        # Round to nearest 5 RPM for precision
        return int(round(rpm_value / 5) * 5)

    return None

def draw_ui(disp, rpm, is_running):
    """Draws the UI at high resolution with anti-aliasing"""
    # Render at 2x resolution for smoother edges
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

    # 6. Text (scaled)
    try:
        font_size = 20 * SCALE
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)

        text = "STOP" if is_running else "GO"
        draw.text(CENTER, text, font=font, fill=COL_TEXT, anchor="mm")
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm} RPM", font=font_sm, fill=(150,150,150), anchor="mm")
    except:
        text = "STOP" if is_running else "GO"
        draw.text(CENTER, text, fill=COL_TEXT)

    # 7. Downscale to native resolution with high-quality LANCZOS filter (anti-aliasing)
    if SCALE > 1:
        img = img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS)

    disp.show_image(img)

# --- MOTOR PROCESS MANAGEMENT ---

def start_motor_process(rpm):
    """Start motor process and return process object"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    motor_script = os.path.join(script_dir, "motor_only.py")

    proc = subprocess.Popen(
        ["python3", motor_script, str(rpm)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    print(f"Started motor process PID {proc.pid} at {rpm} RPM")
    return proc

def stop_motor_process(proc):
    """Stop motor process gracefully"""
    if proc and proc.poll() is None:  # Process still running
        print(f"Stopping motor process PID {proc.pid}")
        proc.terminate()  # Send SIGTERM
        try:
            proc.wait(timeout=2)  # Wait up to 2 seconds
            print("Motor process stopped")
        except subprocess.TimeoutExpired:
            print("Motor process didn't stop, forcing kill")
            proc.kill()  # Force kill
            proc.wait()

# --- MAIN LOOP ---

def main():
    # Initialize display and touch (always active)
    disp = LCD_1inch28()
    disp.init_display()

    time.sleep(2)  # Wait for system to settle

    touch = TouchScreen()
    touch.init()

    rpm = 200
    motor_proc = None  # Motor process handle

    # Track touch gestures
    was_touched = False
    touch_start_pos = None
    touch_start_time = None

    draw_ui(disp, rpm, is_running=False)

    print("UI ready - TAP center for start/stop, HOLD+DRAG slider for RPM (5 RPM steps)")

    try:
        while True:
            try:
                currently_touched = touch.is_touched()

                if currently_touched and touch.read_touch():
                    x, y = touch.get_point()

                    # New touch started (transition from not touched to touched)
                    if not was_touched:
                        touch_start_pos = (x, y)
                        touch_start_time = time.time()
                        was_touched = True

                    # Touch continuing (HOLD + DRAG for slider)
                    else:
                        if motor_proc is None:  # Only when motor not running
                            action = map_touch(x, y)
                            if isinstance(action, int) and action != rpm:
                                rpm = action
                                draw_ui(disp, rpm, is_running=False)

                # Touch released
                elif was_touched and not currently_touched:
                    if touch_start_pos and touch_start_time:
                        duration = time.time() - touch_start_time

                        # TAP detected (quick press/release < 300ms)
                        if duration < TAP_MAX_DURATION:
                            action = map_touch(*touch_start_pos)

                            if action == "BUTTON":
                                if motor_proc is None:
                                    # START motor
                                    print(f"Starting motor at {rpm} RPM...")
                                    draw_ui(disp, rpm, is_running=True)
                                    motor_proc = start_motor_process(rpm)
                                else:
                                    # STOP motor
                                    print("Stopping motor...")
                                    stop_motor_process(motor_proc)
                                    motor_proc = None
                                    draw_ui(disp, rpm, is_running=False)

                    # Reset touch tracking
                    was_touched = False
                    touch_start_pos = None
                    touch_start_time = None

                # Check if motor process crashed
                if motor_proc and motor_proc.poll() is not None:
                    print(f"Motor process exited with code {motor_proc.returncode}")
                    motor_proc = None
                    draw_ui(disp, rpm, is_running=False)

                time.sleep(0.01)

            except Exception as e:
                print(f"Error in main loop: {e}")
                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutdown requested")
    finally:
        print("Cleaning up...")
        if motor_proc:
            stop_motor_process(motor_proc)
        disp.module_exit()
        touch.cleanup()
        print("Shutdown complete")

if __name__ == "__main__":
    main()
