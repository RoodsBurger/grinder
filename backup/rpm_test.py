import time
from pololu_lib import HighPowerStepperDriver

# --- WIRING (Matches your setup) ---
SCS_PIN   = 8
DIR_PIN   = 24
STEP_PIN  = 25
SLEEP_PIN = 7

def main():
    print("Starting RPM Test...")

    driver = HighPowerStepperDriver(
        spi_bus=0,
        spi_device=0,
        cs_pin=SCS_PIN,
        dir_pin=DIR_PIN,
        step_pin=STEP_PIN,
        sleep_pin=SLEEP_PIN
    )

    try:
        driver.reset_settings()
        driver.clear_faults()
        time.sleep(0.1)

        # Configure Motor
        driver.set_current_milliamps(2000)
        driver.set_step_mode(8)
        driver.enable_driver()
        print("Motor Enabled.")

        # --- TEST 1: Exact Steps (e.g., 1 full rotation) ---
        print("\n[TEST 1] Moving by STEPS")
        print(" -> 1 Full Revolution at 60 RPM")
        # 200 steps * 32 microsteps = 6400 steps total
        driver.move_steps(steps=6400, direction=1, rpm=60)
        time.sleep(0.5)

        # --- TEST 2: Duration (e.g., Run for 3 seconds) ---
        print("\n[TEST 2] Moving by TIME")
        print(" <- Running for 3 seconds at 120 RPM")
        driver.move_time(seconds=3.0, direction=0, rpm=200)
        time.sleep(0.5)

        # --- TEST 3: High Speed Time Test ---
        print("\n[TEST 3] High Speed Duration")
        print(" -> Running for 2 seconds at 200 RPM")
        driver.move_time(seconds=2.0, direction=1, rpm=600)

        print("\nDone.")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    finally:
        # --- THIS IS THE CRITICAL FIX ---
        print("Disabling Driver and Releasing Motor...")
        driver.disable_driver()
        driver.close()

if __name__ == "__main__":
    main()