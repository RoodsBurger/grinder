import time
import math
import subprocess
import os
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import display and touch drivers
from lcd_display import LCD_1inch28
from touch_screen import TouchScreen

# Hardware pins
SLEEP_PIN = 7  # Motor driver sleep pin

# --- CONFIGURATION ---
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
BUTTON_TOUCH_RADIUS = 28  # Touch detection (smaller to avoid slider conflicts)
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

# --- HELPER FUNCTIONS ---

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y, debug=False):
    """
    Map touch to action - SIMPLE AND IMMEDIATE like original
    Returns: "BUTTON" or RPM integer or None
    """
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    if debug:
        print(f"  map_touch: ({x},{y}) -> dist={dist:.1f}px from center")

    # Button in center (smaller detection to avoid slider conflicts)
    if dist < BUTTON_TOUCH_RADIUS:
        if debug:
            print(f"    → BUTTON (dist < {BUTTON_TOUCH_RADIUS}px)")
        return "BUTTON"

    # Slider - calculate RPM from angle
    angle = get_angle(x, y)
    eff_angle = angle
    if eff_angle < 135:
        eff_angle += 360

    if debug:
        print(f"    angle={angle:.1f}°, eff_angle={eff_angle:.1f}°")

    start, end = 135, 405
    if start <= eff_angle <= end:
        ratio = (eff_angle - start) / (end - start)
        rpm_value = MIN_RPM + ratio * (MAX_RPM - MIN_RPM)
        rpm_rounded = int(round(rpm_value / 5) * 5)
        if debug:
            print(f"    → SLIDER RPM={rpm_rounded} (angle in range)")
        return rpm_rounded

    if debug:
        print(f"    → None (angle out of range)")
    return None

def draw_ui(disp, rpm, is_running):
    """Draws the UI at 2x resolution"""
    start_time = time.time()

    # Render at high resolution (2x)
    img = Image.new("RGB", (W_HIGH, H_HIGH), COL_BG)
    draw = ImageDraw.Draw(img)

    # 1. Track (outer ring)
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

    # 4. Knob (only when not running)
    if not is_running:
        knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
        rad = math.radians(active_angle)
        kx = CENTER[0] + knob_dist * math.cos(rad)
        ky = CENTER[1] + knob_dist * math.sin(rad)
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=COL_KNOB)

    # 5. Button (center)
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS],
                 fill=btn_col)

    # 6. Icon (coffee beans theme - whole vs ground)
    if is_running:
        # Stop icon - Ground coffee (fine particles)
        r = ICON_SIZE // 2
        import random
        random.seed(42)  # Consistent pattern
        # Draw many small dots/particles to represent ground coffee
        particle_count = 35
        for _ in range(particle_count):
            # Random position within icon area
            offset_x = random.uniform(-r*0.7, r*0.7)
            offset_y = random.uniform(-r*0.7, r*0.7)
            particle_size = random.uniform(1*SCALE, 2.5*SCALE)
            draw.ellipse([CENTER[0] + offset_x - particle_size,
                         CENTER[1] + offset_y - particle_size,
                         CENTER[0] + offset_x + particle_size,
                         CENTER[1] + offset_y + particle_size],
                        fill=COL_TEXT)
    else:
        # Start icon - Whole coffee beans (unground)
        bean_w = ICON_SIZE * 0.35
        bean_h = ICON_SIZE * 0.5
        spacing = ICON_SIZE * 0.25

        # Left bean
        left_x = CENTER[0] - spacing
        draw.ellipse([left_x - bean_w, CENTER[1] - bean_h,
                     left_x + bean_w, CENTER[1] + bean_h],
                    fill=COL_TEXT)
        # Bean groove (darker line on white bean)
        groove_w = bean_w * 0.8
        draw.arc([left_x - groove_w, CENTER[1] - bean_h*0.6,
                 left_x + groove_w, CENTER[1] + bean_h*0.6],
                start=20, end=160, fill=btn_col, width=int(3*SCALE))

        # Right bean
        right_x = CENTER[0] + spacing
        draw.ellipse([right_x - bean_w, CENTER[1] - bean_h,
                     right_x + bean_w, CENTER[1] + bean_h],
                    fill=COL_TEXT)
        # Bean groove
        draw.arc([right_x - groove_w, CENTER[1] - bean_h*0.6,
                 right_x + groove_w, CENTER[1] + bean_h*0.6],
                start=20, end=160, fill=btn_col, width=int(3*SCALE))

    # 7. RPM text below button
    try:
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm} RPM", font=font_sm, fill=(150,150,150), anchor="mm")
    except:
        pass

    # 7. Downscale with anti-aliasing (LANCZOS for quality)
    img = img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS)

    disp.show_image(img)
    elapsed = (time.time() - start_time) * 1000
    print(f"UI render: {elapsed:.1f}ms")

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
    print(f"Started motor PID {proc.pid}")
    return proc

def stop_motor_process(proc, disp):
    """Stop motor process and ensure motor driver is disabled"""
    if proc and proc.poll() is None:
        print(f"Killing motor PID {proc.pid}")
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        print(f"Motor PID {proc.pid} killed")

    # CRITICAL: Disable motor driver via sleep pin after killing subprocess
    # The subprocess leaves GPIO pins in their last state, motor stays enabled
    try:
        GPIO.output(SLEEP_PIN, GPIO.LOW)  # Disable motor driver
        print("Motor driver disabled via sleep pin")
    except Exception as e:
        print(f"Error disabling motor: {e}")

    # Mark SPI as corrupted - motor subprocess changed it to 5MHz
    disp.spi_corrupted = True

# --- MAIN LOOP ---

def main():
    # Initialize GPIO for motor sleep pin control
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(SLEEP_PIN, GPIO.OUT)
    GPIO.output(SLEEP_PIN, GPIO.LOW)  # Start with motor disabled

    # Initialize display and touch
    disp = LCD_1inch28()
    disp.init_display()

    time.sleep(2)

    touch = TouchScreen()
    touch.init()

    rpm = 200
    motor_proc = None

    draw_ui(disp, rpm, is_running=False)

    print("CONTROLS:")
    print("- Touch center = start/stop motor")
    print("- Touch outer ring = set RPM (5 RPM steps)")

    try:
        while True:
            try:
                # Simple immediate response like original
                if touch.is_touched():
                    if touch.read_touch():
                        x, y = touch.get_point()

                        # Disable debug output (too verbose)
                        action = map_touch(x, y, debug=False)

                        # Slider - change RPM immediately (only when motor not running)
                        if isinstance(action, int):
                            if motor_proc is None:
                                if action != rpm:
                                    # No throttling - immediate response like original
                                    rpm = action
                                    print(f"RPM: {rpm}")
                                    draw_ui(disp, rpm, is_running=False)
                            # Silently ignore slider while motor running (no spam)

                        # Button - toggle motor
                        elif action == "BUTTON":
                            if motor_proc is None:
                                # START
                                print(f"→ START {rpm} RPM")
                                motor_proc = start_motor_process(rpm)
                                draw_ui(disp, rpm, is_running=True)
                            else:
                                # STOP
                                print("→ STOP")
                                stop_motor_process(motor_proc, disp)
                                motor_proc = None
                                draw_ui(disp, rpm, is_running=False)
                            # Brief debounce
                            time.sleep(0.15)

                # Check if motor crashed
                if motor_proc and motor_proc.poll() is not None:
                    print(f"Motor crashed with code {motor_proc.returncode}")
                    motor_proc = None
                    draw_ui(disp, rpm, is_running=False)

                time.sleep(0.005)  # 200Hz update rate

            except Exception as e:
                print(f"Loop error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(0.1)

    except KeyboardInterrupt:
        print("\nShutdown")
    finally:
        if motor_proc:
            stop_motor_process(motor_proc, disp)
        # Ensure motor is disabled
        try:
            GPIO.output(SLEEP_PIN, GPIO.LOW)
        except:
            pass
        disp.module_exit()
        touch.cleanup()
        print("Done")

if __name__ == "__main__":
    main()
