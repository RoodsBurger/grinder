#!/usr/bin/env python3
"""
servo_only.py - Bean feeder servo controller

Controls a servo on GPIO 26 to burst-feed beans.
Cycle: open for OPEN_TIME (0.15s fixed), closed for <closed_time> seconds.

Usage:
    python3 servo_only.py <closed_time>

Examples:
    python3 servo_only.py 5.0   # open 0.15s, closed 5s
    python3 servo_only.py 0     # always open
"""
import sys
import time
import signal
from gpiozero import Servo
from gpiozero.pins.lgpio import LGPIOFactory

SERVO_PIN    = 26
OPEN_TIME    = 0.15  # fixed gate open duration per burst (seconds)
SERVO_SETTLE = 0.3   # time for servo to physically reach position before cutting PWM
POLL_STEP    = 0.02  # loop resolution (50 Hz)

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

    closed_time = float(sys.argv[1])

    factory = LGPIOFactory()
    servo = Servo(SERVO_PIN, pin_factory=factory)

    always_open = (closed_time <= 0)

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
            # Open gate for fixed burst
            servo.value = 1.0
            wait(OPEN_TIME)

            if shutdown_requested:
                break

            # Close gate
            servo.value = -1.0
            time.sleep(SERVO_SETTLE)
            servo.value = None  # cut PWM

            # Wait closed time (pause between bursts)
            wait(closed_time - SERVO_SETTLE)

    # Graceful shutdown: ensure gate is closed
    servo.value = -1.0
    time.sleep(SERVO_SETTLE)
    servo.value = None
    servo.close()
    print("Servo: shutdown complete", flush=True)

if __name__ == "__main__":
    main()
