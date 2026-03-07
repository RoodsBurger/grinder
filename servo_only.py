#!/usr/bin/env python3
"""
servo_only.py - Bean feeder servo controller

Controls a servo on GPIO 26 to cycle open/close the bean gate.
Cycle: open for <open_time> seconds, then closed for CLOSED_TIME seconds total.

Usage:
    python3 servo_only.py <open_time>

Example:
    python3 servo_only.py 2.0   # open 2s, closed 1s
"""
import sys
import time
import signal
from gpiozero import Servo
from gpiozero.pins.lgpio import LGPIOFactory

SERVO_PIN   = 26
CLOSED_TIME = 1.0   # total closed time per cycle (fixed)
SERVO_SETTLE = 0.3  # time for servo to physically reach position before cutting PWM
POLL_STEP   = 0.02  # loop resolution (50 Hz)

servo = None
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def wait(duration):
    """Sleep for duration seconds, returning early if shutdown is requested."""
    elapsed = 0.0
    while elapsed < duration and not shutdown_requested:
        time.sleep(POLL_STEP)
        elapsed += POLL_STEP

def main():
    global servo, shutdown_requested

    if len(sys.argv) < 2:
        print("Usage: python3 servo_only.py <open_time>")
        sys.exit(1)

    open_time = float(sys.argv[1])

    factory = LGPIOFactory()
    servo = Servo(SERVO_PIN, pin_factory=factory)

    always_open = (open_time <= 0)

    if always_open:
        # Gate stays open permanently until shutdown
        servo.value = 1.0
        while not shutdown_requested:
            time.sleep(0.1)
    else:
        # Start closed
        servo.value = -1.0
        time.sleep(SERVO_SETTLE)
        servo.value = None  # cut PWM to prevent jitter

        while not shutdown_requested:
            # Open gate
            servo.value = 1.0
            wait(open_time)

            if shutdown_requested:
                break

            # Close gate
            servo.value = -1.0
            time.sleep(SERVO_SETTLE)
            servo.value = None  # cut PWM

            # Wait remaining closed time
            wait(CLOSED_TIME - SERVO_SETTLE)

    # Graceful shutdown: ensure gate is closed
    servo.value = -1.0
    time.sleep(SERVO_SETTLE)
    servo.value = None
    servo.close()
    print("Servo: shutdown complete", flush=True)

if __name__ == "__main__":
    main()
