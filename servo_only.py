#!/usr/bin/env python3
"""
servo_only.py - Doser servo controller (continuous rotation)

Controls a 360° continuous-rotation MG90S servo on GPIO 26.
speed 0.0 = stopped, 1.0 = full speed forward.

Burst mode: always commands full torque (-1.0) and duty-cycles on/off
to control average speed. This ensures full torque even at low speeds.

Usage:
    python3 servo_only.py <speed>   # speed: 0.0–1.0
"""
import sys, time, signal
from gpiozero import Servo
from gpiozero.pins.lgpio import LGPIOFactory

SERVO_PIN = 26
BURST_PERIOD = 0.8   # seconds per on/off cycle
servo = None
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def _sleep_interruptible(duration):
    end = time.monotonic() + duration
    while time.monotonic() < end and not shutdown_requested:
        time.sleep(0.05)

def main():
    global servo
    if len(sys.argv) < 2:
        print("Usage: python3 servo_only.py <speed>")
        sys.exit(1)

    speed = max(0.0, min(1.0, float(sys.argv[1])))

    factory = LGPIOFactory()
    servo = Servo(SERVO_PIN, pin_factory=factory)

    if speed <= 0.0:
        servo.value = None
        while not shutdown_requested:
            time.sleep(0.1)
    elif speed >= 1.0:
        servo.value = -1.0
        while not shutdown_requested:
            time.sleep(0.1)
    else:
        on_time  = speed * BURST_PERIOD
        off_time = (1.0 - speed) * BURST_PERIOD
        while not shutdown_requested:
            servo.value = -1.0
            _sleep_interruptible(on_time)
            if shutdown_requested:
                break
            servo.value = None
            _sleep_interruptible(off_time)

    servo.value = None   # stop on shutdown
    servo.close()
    print("Servo: shutdown complete", flush=True)

if __name__ == "__main__":
    main()
