#!/usr/bin/env python3
"""
servo_only.py - Doser servo controller (continuous rotation)

Controls a 360° continuous-rotation MG90S servo on GPIO 26.
speed 0.0 = stopped, 1.0 = full speed forward.

Burst mode: fixed-width full-torque pulses with variable spacing.
Every pulse is long enough for the servo to actually spin up, so each
one delivers the same kick of beans; the average feed rate then tracks
`speed` linearly. (Variable-width pulses don't: below ~50ms the servo
never reaches speed, and above ~50% duty momentum carries it through
the short gaps at nearly full speed.)

Usage:
    python3 servo_only.py <speed>   # speed: 0.0–1.0
"""
import sys, time, signal
from gpiozero import Servo
from gpiozero.pins.lgpio import LGPIOFactory

SERVO_PIN = 26
PULSE_ON = 0.15        # seconds of full-torque drive per feed pulse (enough to fully spin up)
CONTINUOUS_ABOVE = 0.95  # gaps under ~8ms are meaningless — just run continuously
servo = None
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

def _sleep_interruptible(duration):
    end = time.monotonic() + duration
    while not shutdown_requested:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        # Cap chunks at 50ms for shutdown responsiveness, but never oversleep —
        # burst on-times can be as short as ~8ms at low speeds
        time.sleep(min(0.05, remaining))

def main():
    global servo
    if len(sys.argv) < 2:
        print("Usage: python3 servo_only.py <speed>")
        sys.exit(1)

    speed = max(0.0, min(1.0, float(sys.argv[1])))

    factory = LGPIOFactory()
    servo = Servo(SERVO_PIN, pin_factory=factory)
    servo.value = None   # prevent drift during startup wait

    # Wait for grinder motor to finish acceleration before feeding
    _sleep_interruptible(3.0)

    if speed <= 0.0:
        servo.value = None
        while not shutdown_requested:
            time.sleep(0.1)
    elif speed >= CONTINUOUS_ABOVE:
        servo.value = -1.0
        while not shutdown_requested:
            time.sleep(0.1)
    else:
        # rate = PULSE_ON / period → period = PULSE_ON / speed
        off_time = PULSE_ON * (1.0 / speed - 1.0)
        while not shutdown_requested:
            servo.value = -1.0
            _sleep_interruptible(PULSE_ON)
            if shutdown_requested:
                break
            servo.value = None
            _sleep_interruptible(off_time)

    servo.value = None   # stop on shutdown
    servo.close()
    print("Servo: shutdown complete", flush=True)

if __name__ == "__main__":
    main()
