import time
import math
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw, ImageFont

# Import your existing drivers
from lcd_display import LCD_1inch28
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

def map_touch(x, y):
    """
    Map touch coordinates to UI actions.

    Args:
        x, y: Touch coordinates

    Returns:
        "BUTTON" if center button pressed (smaller 45px area)
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


def draw_ui_fast(disp, rpm, is_running):
    """
    Fast UI rendering using pre-rendered cache.
    Falls back to draw_ui() if cache disabled.
    """
    if not disp.cache_enabled:
        return draw_ui(disp, rpm, is_running)

    try:
        # Calculate knob position
        knob_pos = None
        if not is_running:
            ratio = (rpm - MIN_RPM) / (MAX_RPM - MIN_RPM)
            active_angle = START_ANGLE + ratio * (END_ANGLE - START_ANGLE)
            knob_dist = (RADIUS_OUTER + RADIUS_INNER) / 2
            rad = math.radians(active_angle)
            kx = CENTER[0] + knob_dist * math.cos(rad)
            ky = CENTER[1] + knob_dist * math.sin(rad)
            knob_pos = (kx, ky)

        # Button color
        btn_col = COL_BTN_STOP if is_running else COL_BTN_GO

        # Text elements
        text = "STOP" if is_running else "GO"
        text_elements = []

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 20)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
            text_elements = [
                (text, CENTER, font, COL_TEXT),
                (f"{rpm} RPM", (CENTER[0], CENTER[1] + 70), font_sm, (150, 150, 150))
            ]
        except:
            text_elements = [(text, CENTER, None, COL_TEXT)]

        # Composite and display
        frame = disp.composite_cached_frame(
            rpm=rpm,
            knob_pos=knob_pos,
            button_color=btn_col,
            button_radius=BUTTON_RADIUS,
            text_elements=text_elements
        )

        disp.show_image(frame)

    except Exception as e:
        # Fallback on error
        print(f"Fast render failed, using fallback: {e}")
        draw_ui(disp, rpm, is_running)


# --- MAIN LOGIC ---

def check_stop_button(touch):
    """Check if stop button is pressed (center area only)"""
    if touch.is_touched() and touch.read_touch():
        x, y = touch.get_point()
        dx = x - (W_REAL // 2)
        dy = y - (H_REAL // 2)
        dist = math.sqrt(dx*dx + dy*dy)
        return dist < 45  # Stop button pressed
    return False

def run_motor_loop(driver, target_rpm, touch):
    """
    Blocking loop that runs the motor with acceleration/deceleration.
    Only checks for stop button - no UI updates during operation.
    """
    print(f"Starting Motor at {target_rpm} RPM")

    # Set Direction
    GPIO.output(DIR_PIN, MOTOR_DIRECTION)

    driver.enable_driver()

    # Clear any previous faults
    driver.clear_faults()

    # Calculate Step Delay (for cruise phase)
    steps_rev = 200
    microsteps = 1 << driver.step_mode_val  # Read actual microstepping from driver
    steps_per_sec = (target_rpm * steps_rev * microsteps) / 60
    cruise_delay = 1.0 / steps_per_sec if steps_per_sec > 0 else 0.01

    # Calculate acceleration profile
    accel_time = 2.0  # 2s to reach full speed
    accel_profile = driver.calculate_accel_profile(target_rpm, accel_time, steps_rev)
    decel_profile = list(reversed(accel_profile))

    print(f"Acceleration profile: {len(accel_profile)} steps over {accel_time}s")

    # Local optimizations
    step_pin = STEP_PIN
    gpio_out = GPIO.output
    gpio_high = GPIO.HIGH
    gpio_low = GPIO.LOW

    steps_count = 0
    check_every = 50  # Check stop button every N steps
    fault_check_every = 1000  # Check driver faults every N steps

    motor_phase = "ACCEL"

    # Drift-correcting timer
    t_next = time.perf_counter()

    try:
        # === ACCELERATION PHASE ===
        motor_phase = "ACCEL"
        for delay in accel_profile:
            gpio_out(step_pin, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            t_next += delay
            while time.perf_counter() < t_next: pass

            steps_count += 1

            # Check for stop during acceleration
            if steps_count % check_every == 0:
                if check_stop_button(touch):
                    print("Stop during acceleration")
                    motor_phase = "DECEL"
                    break  # Jump to deceleration

        # === CRUISE PHASE ===
        if motor_phase == "ACCEL":  # Only cruise if we didn't stop during accel
            motor_phase = "CRUISE"
            while True:
                gpio_out(step_pin, gpio_high)
                t_pulse = time.perf_counter()
                while time.perf_counter() - t_pulse < 0.000002: pass
                gpio_out(step_pin, gpio_low)

                t_next += cruise_delay
                while time.perf_counter() < t_next: pass

                steps_count += 1

                # Check for stop
                if steps_count % check_every == 0:
                    if check_stop_button(touch):
                        print("Stop button pressed")
                        motor_phase = "DECEL"
                        break  # Exit to deceleration

                # Fault check
                if steps_count % fault_check_every == 0:
                    faults = driver.check_all_faults()
                    if faults['any_fault']:
                        print(f"MOTOR FAULT: {driver.get_fault_description(faults)}")
                        motor_phase = "DECEL"
                        break  # Emergency deceleration

        # === DECELERATION PHASE ===
        motor_phase = "DECEL"
        print(f"Decelerating after {steps_count} steps...")
        for delay in decel_profile:
            gpio_out(step_pin, gpio_high)
            t_pulse = time.perf_counter()
            while time.perf_counter() - t_pulse < 0.000002: pass
            gpio_out(step_pin, gpio_low)

            t_next += delay
            while time.perf_counter() < t_next: pass

            steps_count += 1

    except Exception as e:
        print(f"Motor loop error in {motor_phase} phase: {e}")
    finally:
        driver.disable_driver()
        print(f"Motor Stopped & Disabled ({steps_count} total steps)")

        # Report any faults at shutdown
        faults = driver.check_all_faults()
        if faults['any_fault']:
            print(f"Final status: {driver.get_fault_description(faults)}")


def main():
    # Initialize display and touch (always active)
    disp = LCD_1inch28()
    disp.init_display()

    # Network is now handled by separate wifi-setup.service at boot
    # No network code here - just wait a moment for system to settle
    time.sleep(2)

    touch = TouchScreen()
    touch.init()

    # Motor driver is NOT initialized yet - only init when GO pressed
    rpm = 200
    draw_ui(disp, rpm, is_running=False)

    print("UI ready - motor driver not initialized")
    print("Touch slider to change RPM, touch center button to start")

    try:
        while True:
            try:
                if touch.is_touched():
                    if touch.read_touch():
                        x, y = touch.get_point()
                        action = map_touch(x, y)

                        if isinstance(action, int):
                            # Slider: Change RPM
                            if action != rpm:
                                rpm = action
                                draw_ui(disp, rpm, is_running=False)

                        elif action == "BUTTON":
                            # GO button: Initialize motor driver and run
                            print(f"Initializing motor driver for {rpm} RPM...")

                            driver = HighPowerStepperDriver(
                                spi_bus=0, spi_device=0,
                                cs_pin=SCS_PIN, dir_pin=DIR_PIN, step_pin=STEP_PIN, sleep_pin=SLEEP_PIN
                            )
                            driver.reset_settings()
                            driver.set_current_milliamps(1000)  # Low current for testing
                            driver.set_step_mode(32)            # Set to 1/32 Microstepping

                            # Show running UI
                            draw_ui(disp, rpm, is_running=True)

                            # Run motor (blocking until stop pressed)
                            run_motor_loop(driver, rpm, touch)

                            # Clean up motor driver completely
                            driver.close()  # Close SPI and cleanup GPIO
                            del driver
                            print("Motor driver cleaned up")

                            # Return to UI-only mode
                            draw_ui(disp, rpm, is_running=False)

                time.sleep(0.01)

            except Exception as e:
                print(f"Error in main loop iteration: {e}")
                # Continue running, just log the error
                time.sleep(0.1)  # Brief pause before retry

    except KeyboardInterrupt:
        print("\nShutdown requested")
    finally:
        print("Cleaning up...")
        disp.module_exit()
        touch.cleanup()
        print("Shutdown complete")

if __name__ == "__main__":
    main()