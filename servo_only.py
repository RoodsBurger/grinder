#!/usr/bin/python3
"""
Standalone bean feeder servo control - runs as a separate process alongside motor_only.py.
Cycles the trap-door gate: open for <open_time> seconds, closed for 1.0 second. Repeats until killed.

Usage:
    python3 servo_only.py <open_time>

Examples:
    python3 servo_only.py 2.0   # open 2s / closed 1s
    python3 servo_only.py 0.5   # open 0.5s / closed 1s
"""
import sys
import time
import signal

# Servo configuration
SERVO_PIN = 26
SERVO_OPEN = 1.0
SERVO_CLOSED = -1.0
CLOSED_TIME = 1.0  # always 1 second closed

shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    shutdown_requested = True

def run_feeder(open_time):
    global shutdown_requested

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        from gpiozero import Servo
        from gpiozero.pins.lgpio import LGPIOFactory
        factory = LGPIOFactory()
        servo = Servo(SERVO_PIN, pin_factory=factory)
    except Exception as e:
        print(f"ERROR: Servo init failed: {e}")
        sys.exit(1)

    print(f"Feeder started: {open_time}s open / {CLOSED_TIME}s closed")

    # Start in closed position
    servo.value = SERVO_CLOSED
    time.sleep(0.3)
    servo.value = None  # cut signal to stop jitter

    try:
        while not shutdown_requested:
            # Open gate
            servo.value = SERVO_OPEN
            t_end = time.time() + open_time
            while time.time() < t_end and not shutdown_requested:
                time.sleep(0.02)

            if shutdown_requested:
                break

            # Close gate
            servo.value = SERVO_CLOSED
            time.sleep(0.3)    # time for servo to physically move
            servo.value = None  # cut signal

            # Remaining closed time
            t_end = time.time() + (CLOSED_TIME - 0.3)
            while time.time() < t_end and not shutdown_requested:
                time.sleep(0.02)

    finally:
        try:
            servo.value = SERVO_CLOSED
            time.sleep(0.3)
            servo.value = None
            servo.close()
        except:
            pass
        print("Feeder stopped")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: servo_only.py <open_time_seconds>")
        print("")
        print("Arguments:")
        print("  open_time - How long the gate stays open per cycle (seconds, e.g. 2.0)")
        sys.exit(1)

    try:
        open_time = float(sys.argv[1])
        if open_time <= 0:
            print("ERROR: open_time must be positive")
            sys.exit(1)
    except ValueError:
        print("ERROR: Invalid open_time value (must be a number)")
        sys.exit(1)

    run_feeder(open_time)
