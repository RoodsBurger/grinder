#!/usr/bin/env python3
"""
Interactive Motor Controller
"""
import sys
from StepperLibrary import StepperMotor, Direction, MicrostepMode

def get_input(prompt, default):
    val = input(f"{prompt} [{default}]: ").strip()
    return val if val else default

def main():
    print("="*60)
    print(" INTERACTIVE MOTOR CONTROL")
    print("="*60)

    with StepperMotor(cs_pin=4, step_pin=17, dir_pin=27) as motor:

        while True:
            print("\n--- Main Menu ---")
            print("1. Run Constant Speed")
            print("2. Configure Settings")
            print("3. Quit")

            choice = input("Select option: ").strip()

            if choice == '3' or choice.lower() == 'quit':
                break

            # --- CONFIGURATION MENU ---
            elif choice == '2':
                print("\n--- Configure ---")

                # Torque
                t_val = get_input("Set Torque % (0-100)", "current")
                if t_val != "current":
                    try:
                        motor.set_torque_percent(float(t_val))
                        print("Torque updated.")
                    except: print("Invalid torque.")

                # Resolution
                r_val = get_input("Set Resolution (1,2,4,8...)", "current")
                if r_val != "current":
                    try:
                        val = int(r_val)
                        enum_val = None
                        for mode in MicrostepMode:
                            if mode.value == val: enum_val = mode

                        if enum_val:
                            motor.set_microstep_mode(enum_val)
                            print("Resolution updated.")
                        else:
                            print("Invalid resolution.")
                    except: print("Invalid input.")

            # --- RUN MENU ---
            elif choice == '1':
                speed = int(get_input("Speed (steps/sec)", "600"))
                dur = float(get_input("Duration (sec)", "3.0"))
                d_str = get_input("Direction (cw/ccw)", "cw").lower()
                dir_enum = Direction.CW if d_str == 'cw' else Direction.CCW

                print(f"Running {speed} sps for {dur}s...")
                try:
                    steps = motor.run_for_time(dur, speed, dir_enum)
                    print(f"Done. Steps: {steps}")
                except KeyboardInterrupt:
                    motor.stop()
                    print("\nStopped.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting.")
