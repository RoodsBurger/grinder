#!/usr/bin/python3
"""
UI test without motor control
Tests the circular RPM selector and GO/STOP button rendering
"""
import time
import math
import random
from PIL import Image, ImageDraw, ImageFont

from lcd_display import LCD_1inch28
from touch_screen import TouchScreen

# Pre-seed random for consistent icon rendering
random.seed(42)

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
BUTTON_TOUCH_RADIUS = 22  # Touch detection (even smaller to avoid slider conflicts)
KNOB_RADIUS = 22 * SCALE  # Bigger slider knob
ICON_SIZE = 32 * SCALE  # Icon size (bigger)

# Touch gesture thresholds
TAP_MAX_DURATION = 0.3  # Max 300ms for tap

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

    # Load font once
    try:
        CACHED_FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15 * SCALE)
    except:
        CACHED_FONT = None

    # Pre-render START icon (whole coffee beans)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)

    bean_w = ICON_SIZE * 0.35
    bean_h = ICON_SIZE * 0.5
    spacing = ICON_SIZE * 0.25
    center_x = ICON_SIZE
    center_y = ICON_SIZE

    # Left bean
    left_x = center_x - spacing
    draw.ellipse([left_x - bean_w, center_y - bean_h,
                 left_x + bean_w, center_y + bean_h],
                fill=COL_TEXT)
    # Bean groove (curved line on white bean)
    groove_w = bean_w * 0.8
    draw.arc([left_x - groove_w, center_y - bean_h*0.6,
             left_x + groove_w, center_y + bean_h*0.6],
            start=20, end=160, fill=COL_BTN_GO, width=int(3*SCALE))

    # Right bean
    right_x = center_x + spacing
    draw.ellipse([right_x - bean_w, center_y - bean_h,
                 right_x + bean_w, center_y + bean_h],
                fill=COL_TEXT)
    # Bean groove
    draw.arc([right_x - groove_w, center_y - bean_h*0.6,
             right_x + groove_w, center_y + bean_h*0.6],
            start=20, end=160, fill=COL_BTN_GO, width=int(3*SCALE))

    ICON_START = icon_img

    # Pre-render STOP icon (ground coffee particles)
    icon_img = Image.new('RGBA', (ICON_SIZE*2, ICON_SIZE*2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon_img)

    particle_count = 40
    for _ in range(particle_count):
        offset_x = random.uniform(-bean_w - spacing, bean_w + spacing)
        offset_y = random.uniform(-bean_h, bean_h)
        particle_size = random.uniform(1.5*SCALE, 3*SCALE)
        draw.ellipse([center_x + offset_x - particle_size,
                     center_y + offset_y - particle_size,
                     center_x + offset_x + particle_size,
                     center_y + offset_y + particle_size],
                    fill=COL_TEXT)

    ICON_STOP = icon_img

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y):
    """Map touch coordinates to UI actions"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    # Button in center
    if dist < BUTTON_TOUCH_RADIUS:
        return "BUTTON"

    # Dead zone between button and slider (prevents accidental slider when pressing button)
    DEAD_ZONE_OUTER = 45  # Small buffer zone
    if dist < DEAD_ZONE_OUTER:
        return None

    # Slider: Only if outside dead zone
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
        draw.ellipse([kx-KNOB_RADIUS, ky-KNOB_RADIUS, kx+KNOB_RADIUS, ky+KNOB_RADIUS], fill=COL_KNOB)

    # 5. Button
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS],
                 fill=btn_col)

    # 6. Icon (use pre-rendered cached icon)
    icon = ICON_STOP if is_running else ICON_START
    if icon:
        icon_x = CENTER[0] - ICON_SIZE
        icon_y = CENTER[1] - ICON_SIZE
        img.paste(icon, (icon_x, icon_y), icon)

    # 7. RPM text below button (use cached font)
    if CACHED_FONT:
        draw.text((CENTER[0], CENTER[1] + 70*SCALE), f"{rpm} RPM", font=CACHED_FONT, fill=(150,150,150), anchor="mm")

    # 8. Downscale with anti-aliasing (LANCZOS for quality)
    img = img.resize((W_REAL, H_REAL), Image.Resampling.LANCZOS)

    disp.show_image(img)

def main():
    print("Initializing UI test...")

    # Pre-load resources (icons, font)
    preload_resources()

    disp = LCD_1inch28()
    disp.init_display()
    print("Display initialized")

    touch = TouchScreen()
    touch.init()
    print("Touch initialized")

    rpm = 200
    is_running = False
    
    # Initial draw
    print("Drawing initial UI...")
    start = time.time()
    draw_ui(disp, rpm, is_running)
    elapsed = time.time() - start
    print(f"Initial UI render: {elapsed*1000:.1f}ms\n")

    print("UI test running...")
    print("Touch the dial to change RPM")
    print("Touch center button to toggle running state (no motor)")
    print("Press Ctrl+C to exit\n")

    frame_count = 0
    frame_times = []

    # Track touch gestures
    was_touched = False
    touch_start_pos = None
    touch_start_time = None

    print("TAP center button to toggle, HOLD+DRAG slider to change RPM (5 RPM steps)\n")

    try:
        while True:
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
                    action = map_touch(x, y)
                    if isinstance(action, int) and action != rpm:
                        rpm = action
                        start = time.time()
                        draw_ui(disp, rpm, is_running)
                        elapsed = time.time() - start
                        frame_times.append(elapsed)
                        frame_count += 1

                        if frame_count % 10 == 0:
                            avg = sum(frame_times[-10:]) / 10
                            print(f"Frame {frame_count}: {elapsed*1000:.1f}ms (avg: {avg*1000:.1f}ms, {1/avg:.1f} FPS)")

            # Touch released
            elif was_touched and not currently_touched:
                if touch_start_pos and touch_start_time:
                    duration = time.time() - touch_start_time

                    # TAP detected (quick press/release < 300ms)
                    if duration < TAP_MAX_DURATION:
                        action = map_touch(*touch_start_pos)

                        if action == "BUTTON":
                            is_running = not is_running
                            start = time.time()
                            draw_ui(disp, rpm, is_running)
                            elapsed = time.time() - start
                            frame_times.append(elapsed)
                            frame_count += 1
                            print(f"Button tapped: {'RUNNING' if is_running else 'STOPPED'} ({elapsed*1000:.1f}ms)")

                # Reset touch tracking
                was_touched = False
                touch_start_pos = None
                touch_start_time = None

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n\nUI test stopped")
        if frame_times:
            avg = sum(frame_times) / len(frame_times)
            print(f"\nStats: {frame_count} frames rendered")
            print(f"Average: {avg*1000:.1f}ms per frame ({1/avg:.1f} FPS)")
    finally:
        disp.module_exit()
        touch.cleanup()
        print("Cleanup complete")

if __name__ == "__main__":
    main()
