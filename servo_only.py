#!/usr/bin/env python3
"""
servo_only.py - Doser servo controller (continuous rotation)

Controls a 360° continuous-rotation MG90S servo on GPIO 26.
speed 0.0 = stopped, 1.0 = full speed forward.

Usage:
    python3 servo_only.py <speed>   # speed: 0.0–1.0
"""
import sys, time, signal
from gpiozero import Servo
from gpiozero.pins.lgpio import LGPIOFactory

SERVO_PIN = 26
servo = None
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def main():
    global servo
    if len(sys.argv) < 2:
        print("Usage: python3 servo_only.py <speed>")
        sys.exit(1)

    speed = max(0.0, min(1.0, float(sys.argv[1])))

    factory = LGPIOFactory()
    servo = Servo(SERVO_PIN, pin_factory=factory)
    servo.value = speed if speed > 0 else None  # None = cut PWM (no jitter at rest)

    while not shutdown_requested:
        time.sleep(0.1)

    servo.value = None   # stop on shutdown
    servo.close()
    print("Servo: shutdown complete", flush=True)

if __name__ == "__main__":
    main()
