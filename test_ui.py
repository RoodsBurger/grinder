#!/usr/bin/python3
"""
UI test without motor control
Tests the circular RPM selector and GO/STOP button rendering
"""
import time
import math
from PIL import Image, ImageDraw, ImageFont

from lcd_display import LCD_1inch28
from touch_screen import TouchScreen

# --- CONFIGURATION ---
MIN_RPM = 0
MAX_RPM = 300

# High-Res Render Settings (Native Resolution)
W_REAL, H_REAL = 240, 240
CENTER = (W_REAL // 2, H_REAL // 2)

# Geometry
RADIUS_OUTER = 110
RADIUS_INNER = 85
BUTTON_RADIUS = 50

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

def get_angle(x, y):
    """Get angle from center (0-360)"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    deg = math.degrees(math.atan2(dy, dx))
    return (deg + 360) % 360

def map_touch(x, y, is_new_press=False):
    """Map touch coordinates to UI actions"""
    dx = x - (W_REAL // 2)
    dy = y - (H_REAL // 2)
    dist = math.sqrt(dx*dx + dy*dy)

    # Button: Only trigger on NEW press (not during drag), tighter area
    if dist < 55 and is_new_press:
        return "BUTTON"

    # Slider: Only if outside button area
    if dist < 55:
        return None  # Ignore touches in button area during drag

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
    img = Image.new("RGB", (W_REAL, H_REAL), COL_BG)
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
        kr = 15
        draw.ellipse([kx-kr, ky-kr, kx+kr, ky+kr], fill=COL_KNOB)

    # 5. Button
    btn_col = COL_BTN_STOP if is_running else COL_BTN_GO
    draw.ellipse([CENTER[0]-BUTTON_RADIUS, CENTER[1]-BUTTON_RADIUS,
                  CENTER[0]+BUTTON_RADIUS, CENTER[1]+BUTTON_RADIUS],
                 fill=btn_col)

    # 6. Text
    try:
        font_size = 20
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except:
        font = None

    text = "STOP" if is_running else "GO"
    if font:
        draw.text(CENTER, text, font=font, fill=COL_TEXT, anchor="mm")
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
        draw.text((CENTER[0], CENTER[1] + 70), f"{rpm} RPM", font=font_sm, fill=(150,150,150), anchor="mm")
    else:
        draw.text(CENTER, text, fill=COL_TEXT)

    # 7. Show
    disp.show_image(img)

def main():
    print("Initializing UI test...")
    
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
    
    try:
        while True:
            if touch.is_touched():
                if touch.read_touch():
                    x, y = touch.get_point()
                    is_new_press = touch.is_new_press()
                    action = map_touch(x, y, is_new_press)

                    if isinstance(action, int):
                        if action != rpm:
                            rpm = action
                            start = time.time()
                            draw_ui(disp, rpm, is_running)
                            elapsed = time.time() - start
                            frame_times.append(elapsed)
                            frame_count += 1

                            if frame_count % 10 == 0:
                                avg = sum(frame_times[-10:]) / 10
                                print(f"Frame {frame_count}: {elapsed*1000:.1f}ms (avg: {avg*1000:.1f}ms, {1/avg:.1f} FPS)")

                    elif action == "BUTTON":
                        is_running = not is_running
                        start = time.time()
                        draw_ui(disp, rpm, is_running)
                        elapsed = time.time() - start
                        frame_times.append(elapsed)
                        frame_count += 1
                        print(f"Button pressed: {'RUNNING' if is_running else 'STOPPED'} ({elapsed*1000:.1f}ms)")

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
