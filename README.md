# Smart Coffee Grinder Controller

A Raspberry Pi–based controller for a motorized espresso grinder. It replaces the grinder's
original switch with a **1.28" circular touchscreen**, drives the grinding burr motor through a
high-power **DRV8711 stepper driver**, meters beans with a **continuous-rotation auger (doser)
servo**, and boots straight into the UI as a **systemd-managed appliance** — no keyboard, mouse,
or login required.

This document explains *what the system is*, *how it is built*, and *how every part actually
works* — down to the register math on the motor driver and the cooperative SPI-bus arbitration
between the screen and the motor.

---

## Table of Contents

1. [Concept & Behaviour](#concept--behaviour)
2. [Hardware](#hardware)
3. [Wiring / GPIO Map](#wiring--gpio-map)
4. [Software Architecture](#software-architecture)
5. [The Shared-SPI Problem (and the solution)](#the-shared-spi-problem-and-the-solution)
6. [Module Deep-Dive](#module-deep-dive)
   - [motor_control.py — the application](#motor_controlpy--the-application)
   - [motor_only.py — the stepper subprocess](#motor_onlypy--the-stepper-subprocess)
   - [servo_only.py — the doser subprocess](#servo_onlypy--the-doser-subprocess)
   - [lcd_display.py — GC9A01 driver](#lcd_displaypy--gc9a01-driver)
   - [touch_screen.py — CST816T driver](#touch_screenpy--cst816t-driver)
7. [The DRV8711 Motor Configuration System](#the-drv8711-motor-configuration-system)
8. [Diagnostics & Tuning Tools](#diagnostics--tuning-tools)
9. [Installation & Deployment](#installation--deployment)
10. [Running Manually](#running-manually)
11. [Dependencies](#dependencies)
12. [Troubleshooting](#troubleshooting)
13. [Design Notes & Gotchas](#design-notes--gotchas)

---

## Concept & Behaviour

The grinder is operated entirely through one round screen mounted where the original control
knob used to be. There are **two screens**, switched by a horizontal swipe:

| Screen | Theme | What it controls | On-screen label |
|---|---|---|---|
| **0 — Grind speed** | Blue arc | Burr motor speed, 60–300 RPM (motor side), snapping to 20 RPM | `X RPM` (shown at half — see gearbox note) |
| **1 — Feed rate** | Amber arc | Doser auger duty (bean feed rate), 0–100 % in 5 % steps | `X%` |

Two small navigation dots at the bottom show which screen is active.

A single round **center button** starts and stops everything:

- **Press once** → the burr motor spins up *and*, 3 seconds later, the doser auger begins feeding
  beans (the delay lets the heavy burr motor reach full speed before load is applied).
- **Press again** → both stop together, the screen returns to the idle state.

While running, the arc and knob are *locked* (dimmed, non-interactive) so a stray touch can't
change speed mid-grind. When idle, dragging the knob around the arc sets the value.

After **10 minutes** of inactivity (only while stopped) the display sleeps to protect the panel;
any touch wakes it.

> **Gearbox note:** there is a **2:1 reduction** between the stepper and the burr. The UI always
> shows *half* the commanded motor RPM, so the displayed range is **30–150 RPM at the burr** even
> though the motor is commanded at 60–300 RPM.

---

## Hardware

| Component | Model / Spec |
|---|---|
| Controller | Raspberry Pi (any 40-pin GPIO model) |
| Display | 1.28" round IPS LCD, 240×240, **GC9A01** driver, SPI |
| Touch | **CST816T** capacitive controller, I²C @ `0x15` |
| Burr motor | NEMA 23 stepper — 4.2 A rated, 3.0 N·m, 0.9 Ω, 3.8 mH, 1.8°/step (200 steps/rev) |
| Motor driver | Pololu **High-Power Stepper Driver 36v4** (TI **DRV8711** + MOSFET stage, 8 A capable) |
| Sense resistor | 30 mΩ (on the 36v4 board — drives the TORQUE math) |
| Reduction | 2:1 gearbox between motor and burr |
| Doser | 360° continuous-rotation servo (MG90S-class) driving a bean auger |

---

## Wiring / GPIO Map

All numbers are **BCM** GPIO numbers.

| GPIO | Hdr pin | Signal | Direction | Used by |
|---|---|---|---|---|
| GPIO4 | 7 | `TP_INT` touch interrupt | in (pull-up) | touch_screen.py |
| GPIO6 | 31 | `TP_RST` touch reset | out | touch_screen.py |
| GPIO7 | 26 | `SLEEP` DRV8711 sleep/enable | out | motor_control.py / motor_only.py |
| GPIO8 | 24 | `SCS` DRV8711 SPI chip-select | out | motor_only.py |
| GPIO17 | 11 | `DC` LCD data/command | out | lcd_display.py |
| GPIO22 | 15 | `LCD_CS` LCD SPI chip-select | out | lcd_display.py |
| GPIO23 | 16 | `BL` LCD backlight | out | lcd_display.py |
| GPIO24 | 18 | `DIR` stepper direction | out | motor_only.py |
| GPIO25 | 22 | `STEP` stepper step pulse | out | motor_only.py |
| GPIO26 | 37 | Doser servo PWM | out | servo_only.py |
| GPIO27 | 13 | `RST` LCD hardware reset | out | lcd_display.py |

**Buses**

- **SPI bus 0** is *shared* by two devices with different requirements:
  - LCD (GC9A01) — 40 MHz, chip-select on GPIO22, manual CS.
  - DRV8711 — 500 kHz, chip-select on GPIO8/`SCS`, manual CS, SPI mode 0.
  Because both sit on bus 0 they are never driven at the same time — see
  [The Shared-SPI Problem](#the-shared-spi-problem-and-the-solution).
- **I²C bus 1** — CST816T touch controller at address `0x15`.

---

## Software Architecture

```
                       ┌──────────────────────────────────────────┐
                       │            motor_control.py               │
                       │  (root process — the appliance UI)        │
                       │                                           │
                       │  • LCD render loop (Pillow → GC9A01)       │
                       │  • Touch state machine (CST816T)           │
                       │  • Two-screen UI + swipe nav               │
                       │  • Standby / wake                          │
                       │  • Spawns + reaps the two subprocesses     │
                       └───────────────┬───────────────────────────┘
                                       │ subprocess.Popen (on START)
                          ┌────────────┴─────────────┐
                          ▼                           ▼
              ┌───────────────────────┐   ┌───────────────────────────┐
              │     motor_only.py     │   │       servo_only.py       │
              │  DRV8711 over SPI0    │   │  gpiozero/lgpio PWM        │
              │  precise STEP pulses  │   │  burst-mode auger feed    │
              │  S-curve accel ramp   │   │  3 s start delay          │
              └───────────────────────┘   └───────────────────────────┘
```

**Why three processes instead of threads?**

1. **Timing isolation.** The stepper step train must be jitter-free. Running it in its own OS
   process keeps the Python GIL, the LCD SPI transfers, and the touch I²C polling from stalling
   the step loop.
2. **Crash containment.** If the motor or servo process dies, the UI process notices
   (`proc.poll()`), tears *both* down cleanly, disables the driver, and resets the screen — the
   appliance never ends up in a half-running state.
3. **Clean resource hand-off.** The motor subprocess owns SPI bus 0 while it runs; the UI
   process releases the bus before spawning it and reclaims it afterwards (next section).

Supporting modules (`lcd_display.py`, `touch_screen.py`) are imported by the UI process;
`motor_configs.json` is the data file the motor subprocess reads.

---

## The Shared-SPI Problem (and the solution)

Both the LCD and the DRV8711 live on **SPI bus 0**. Linux exposes that as a single device that
two processes cannot safely open simultaneously. The system solves this with a **cooperative
hand-off**, not a lock:

1. UI is running normally → `lcd_display` holds SPI0 open at 40 MHz to push frames.
2. User presses START → before spawning `motor_only.py`, the UI calls
   `disp.close_spi_for_motor()`, which closes the LCD's `spidev` handle.
3. `motor_only.py` opens SPI0 at 500 kHz, writes the DRV8711 registers, **then closes SPI
   immediately** (`close_spi()` at line ~188). The driver only needs SPI for configuration; the
   actual motion is pure GPIO `STEP`/`DIR` toggling, so the bus is free again within milliseconds.
4. User presses STOP (or a subprocess exits) → `stop_all_processes()` terminates the children,
   pulls `SLEEP` low to de-energize the driver, then calls `disp.reopen_spi_after_motor()` which
   re-opens SPI0 at 40 MHz and the UI resumes drawing.

This is why the README repeatedly warns: **never run `motor_control.py` and `motor_only.py` as
independent simultaneous processes** — they would both grab SPI0 and fault.

---

## Module Deep-Dive

### `motor_control.py` — the application

The root process and the only thing systemd launches. ~600 lines, single-threaded, ~200 Hz main
loop (`time.sleep(0.005)`).

**Rendering.** Everything is drawn with Pillow into a **2× supersampled** (480×480) RGB image,
then Lanczos-downscaled to 240×240 before being sent to the panel. This gives clean anti-aliased
arcs and text on a tiny round screen. The arc is built from two `pieslice` calls (track + active
fill) with a center circle punched out to make a ring; a knob circle is positioned on the ring by
mapping the value to an angle in the 135°→405° sweep. Fonts and the two bean icons (whole beans
for START, scattered grounds for STOP — generated procedurally with a fixed `random.seed(42)`
so they render identically every boot) are pre-rendered once at startup by `preload_resources()`.

**Touch interaction state machine.** Raw touches are noisy and the CST816T can go silent for
200 ms+ while a finger is held still, so the loop runs an explicit state machine:

```
INTERACT_IDLE
   ├─ touch on center button ──────────────► INTERACT_BUTTON
   │      (released within BUTTON_MAX_TAP, total travel ≤
   │       BUTTON_SWIPE_CANCEL px, release point still on the
   │       button, and no swipe gesture seen → toggle start/stop)
   │
   └─ touch on knob (only when stopped) ───► INTERACT_KNOB_WAITING
          (held ≥ KNOB_HOLD_TIME 0.25 s) ──► INTERACT_KNOB_ACTIVE
                                                (drag maps arc → value)
```

Release is *inferred*, not reported: if no touch event arrives for a state-dependent timeout
(150 ms for buttons — long enough for the CST816T's at-lift gesture report to arrive and veto a
false tap; 500 ms for the knob → tolerates the controller's silence) the touch is treated as
released. A horizontal hardware swipe gesture longer than `MIN_SWIPE_DISTANCE` (40 px) toggles
the screen, with a 0.4 s cooldown afterwards to swallow trailing reads so one swipe doesn't
bounce screens. **Swipes always win over the button**: a swipe gesture seen at *any* point
during a contact (except an active knob drag) vetoes the pending button tap — fast swipes
across the screen center deliver too few coordinate events for travel checks alone, so without
the veto they would start the grinder instead of switching screens.

**Process orchestration.** START draws the running state first (instant feedback), then
`start_motor_process()` (closes LCD SPI, spawns `motor_only.py <rpm> M1`) and
`start_servo_process()` (spawns `servo_only.py <0.00–1.00>`). STOP sends `SIGTERM` to **both at
once** so they decelerate in parallel, waits up to 2 s each (then `SIGKILL`), forces `SLEEP` low,
and reopens LCD SPI. Every loop iteration also polls both children; an unexpected exit tears
both down and resets the appliance (child output streams directly to the journal).

Key constants live at the top of the file: `MOTOR_CONFIG_ID = 'M1'`, `MIN_RPM/MAX_RPM = 60/300`,
`STANDBY_TIMEOUT = 600 s`, and the geometry/colour/touch-tuning values.

### `motor_only.py` — the stepper subprocess

Standalone, no display/touch. `python3 motor_only.py <RPM> [CONFIG_ID]` (default config `M1`).

1. Installs `SIGTERM`/`SIGINT` handlers that just set `shutdown_requested = True` (graceful loop
   exit, not a hard kill).
2. Loads the named entry from `motor_configs.json`.
3. Sets up GPIO, parks `LCD_CS` high (so LCD doesn't respond on the shared bus), opens SPI0 at
   500 kHz mode 0.
4. **Computes the TORQUE register and ISGAIN** from the config's `current_ma` (see
   [formula below](#the-drv8711-motor-configuration-system)).
5. Writes all DRV8711 registers — `CTRL` (microstep mode + ISGAIN, ENBL cleared), `TORQUE`,
   `OFF` (PWM off-time), `BLANK`, `DECAY`, `DRIVE`, `STALL`, and clears `STATUS`.
6. Sets `DIR`, asserts `SLEEP` high, then writes `CTRL` again with the **ENBL** bit set to
   energize the coils, and **closes SPI** (motion needs no further bus traffic).
7. Runs the step loop using `time.perf_counter()` busy-waits for sub-microsecond pulse accuracy:
   each step raises `STEP` for ~2 µs, drops it, then spins until the next scheduled edge.

**Acceleration ramp.** A NEMA 23 burr motor cannot start instantly at 300 RPM. The first
`RAMP_TIME` (1.5 s) worth of steps linearly ramp the step rate from `RAMP_START_FRACTION` (15 %)
of target up to full speed, eliminating the stall/screech of a hard start while keeping spin-up
short. On shutdown the loop exits and `SLEEP` is pulled low to de-energize the motor.

### `servo_only.py` — the doser subprocess

Drives a **continuous-rotation** auger servo on GPIO26 via `gpiozero` with the `lgpio` pin
factory. Argument is a feed rate `0.0`–`1.0`.

The trick here is **fixed-width full-torque pulsing**. A cheap CR servo has almost no torque at
low commanded speeds — it stalls against bean resistance. So this driver *always commands full
torque* (`servo.value = -1.0`, the direction that turns the auger forward) in fixed `PULSE_ON`
(0.15 s) bursts, and sets the feed rate by varying the *spacing* between bursts:

```
on_time  = 0.15 s (fixed — long enough for the servo to fully spin up)
off_time = 0.15 s × (1/speed − 1)   (servo.value = None → coast)
```

Every pulse delivers the same kick of beans, so the average feed rate tracks `speed` linearly.
(The earlier variable-width scheme — `on_time = speed × period` — was badly nonlinear: below
~50 ms the servo never reached speed, and above ~50 % duty momentum carried it through the short
gaps at nearly full speed.) Edge cases short-circuit: `speed ≤ 0` idles (`value = None`),
`speed ≥ 0.95` drives continuously. A 3-second start delay (`_sleep_interruptible(3.0)`) holds feeding until the burr
motor has finished its acceleration ramp, so beans are never dumped onto a not-yet-spinning burr.
`SIGTERM` breaks every sleep immediately for a fast, drift-free stop.

### `lcd_display.py` — GC9A01 driver

A self-contained GC9A01 driver (no Waveshare lib dependency).

- `init_display()` runs the full GC9A01 power-on register sequence, then sleep-out (`0x11`) and
  display-on (`0x29`).
- `show_image(pil)` converts an RGB888 PIL image to **RGB565** with vectorized NumPy bit-shifts
  (`R>>3`, `G>>2`, `B>>3`, packed into two interleaved bytes), sets the 240×240 window, and
  streams the NumPy buffer over SPI via `writebytes2` **with CS held low for the entire frame**
  — no Python-list conversion, the single biggest throughput win on this panel.
- `sleep_display()` / `wake_display()` issue display-off/sleep-in (and the reverse) and toggle
  the backlight GPIO — used by the 10-minute standby.
- `close_spi_for_motor()` / `reopen_spi_after_motor()` implement the cooperative SPI hand-off
  described above, tracking state with an `spi_open` flag so double-close/open is safe.

### `touch_screen.py` — CST816T driver

I²C driver for the CST816T in **mixed mode** (`0xFA = 0x71`) so it reports gesture IDs *and*
point coordinates in one transaction.

- `is_touched()` is a cheap GPIO read of `TP_INT` (LOW = event pending) — the main loop only does
  a (more expensive) I²C read when the interrupt line says there's something to read.
- `read_touch()` pulls 6 bytes from register `0x01`: gesture id, finger count, and 12-bit X/Y.
  It validates bounds (0–239, rejects `(0,0)` garbage), with up to 3 retries on
  `OSError` (I²C glitches).
- **Jitter suppression:** a 3-sample moving average plus 5 px hysteresis (sub-threshold movement
  is pinned to the last value), a 10 ms debounce, and an internal
  IDLE→PRESSED→HELD→RELEASED state machine.
- `get_gesture()` returns and *clears* the latched gesture so each swipe is consumed once.

---

## The DRV8711 Motor Configuration System

`motor_configs.json` holds **~90 named configurations** (plus a `_WARNING` meta-entry),
organized into lettered categories that were swept during development to find quiet,
torque-adequate settings:

| Category | Focus |
|---|---|
| A | Diagnostic baseline |
| B | PWM frequency sweep |
| C | DRIVE (gate-drive strength) optimization |
| D | Motor current sweep |
| E | Decay-mode deep dive |
| F | Microstepping sweep |
| G | Stall-detection impact |
| H | Blanking time vs microstepping |
| I | Resonance troubleshooting |
| J | High-torque optimizations |
| K | Ultra-current quiet optimization (up to 8000 mA) |
| **M** | **Production configs — `M1` is the one shipped** |

Each entry:

```jsonc
{
  "name": "NEMA 23 - 1/8 step, auto-mixed decay, 4200mA",
  "current_ma": 4200,        // target coil current
  "ctrl_base": 3096,         // base CTRL reg (microstep mode; ISGAIN patched in at runtime)
  "off": 48,                 // OFF time  → PWM chopping frequency
  "blank": 384,              // BLANK time + ABT
  "decay": 1296,             // decay mode (mixed/auto-mixed/slow)
  "drive": 2649,             // gate-drive currents / dead-time
  "stall": 64,               // stall-detect threshold
  "microstep_divider": 8     // microsteps per full step (→ steps/rev = 200 × divider)
}
```

**Active production config: `M1`** — NEMA 23, 1/8 microstepping, auto-mixed decay,
**4200 mA** (100 % of the motor's rated current; the 36v4 board can deliver up to 8 A).

**The TORQUE / ISGAIN math.** The DRV8711 sets the per-coil current trip point from the `TORQUE`
register and the current-sense amplifier gain `ISGAIN`:

```
I_TRIP = (TORQUE / 256) × (V_REF / (ISGAIN × R_SENSE))

⇒  TORQUE = (I_mA / 1000) × 256 × ISGAIN × R_SENSE / V_REF
```

with `R_SENSE = 0.030 Ω` and `V_REF = 2.75 V` on the 36v4. `calculate_torque_register()` tries
ISGAIN values **highest first** — 40×, 20×, 10×, 5× — and keeps the first that yields a `TORQUE`
in the valid `0–255` range. Highest gain first maximizes register resolution for a given target
current. The selected gain bits are then patched into `ctrl_base` at runtime (the JSON only
stores a placeholder), and the ENBL bit is cleared until all registers are written.

---

## Diagnostics & Tuning Tools

These are not part of the running appliance — they were used to bring up and tune the hardware.

| Tool | Purpose |
|---|---|
| `sudo python3 diagnostic.py` | Quick check: SPI register round-trip, STATUS fault read, 400-step there-and-back motion test. |
| `sudo python3 full_diagnostic.py` | 8-stage PASS/FAIL suite: GPIO control, SPI open + R/W, SLEEP wake, STATUS fault decode (UVLO/OTS/AOCP/BOCP/APDF/BPDF/STDLAT), register write+verify, coil-continuity energize, 200-step there-and-back (user confirms), and a 5-second continuous run at ~60 RPM. |
| `sudo python3 test_motor_comprehensive.py` | Interactive sweep: run any subset of the ~90 configs at their listed test speeds, rate audible noise 1–10 after each, export results to CSV. This is how `M1` was chosen. |
| `motor_only.py` / `servo_only.py` | Can be run directly (see below) to bench-test the motor or doser in isolation. |

---

## Installation & Deployment

The target is a headless Raspberry Pi that boots straight into the grinder UI.

```bash
sudo bash install.sh
```

Full install:
1. Installs system + Python deps (`python3-pip python3-pil python3-numpy python3-lgpio`, then
   `spidev smbus2 RPi.GPIO gpiozero` via pip with `--break-system-packages`).
2. Copies the runtime files to **`/opt/motor-control/`**.
3. Installs and enables two systemd units, then starts them.

Fast redeploy (copy changed files + restart, no dependency reinstall):

```bash
sudo bash install.sh -simple
```

### systemd units

**`wifi-setup.service`** — `Type=oneshot`, `RemainAfterExit=yes`, 90 s start timeout. Runs
`wifi_setup.py` once at boot to bring up WiFi via `nmcli` (tries a primary then a fallback SSID,
verifies with an IP + gateway ping). If it can't connect within `MAX_WAIT` (60 s) it gives up and
**lets the appliance start anyway** — network is convenience, not a hard requirement.

**`motor-control.service`** — `Type=simple`, `After/Wants=wifi-setup.service`, runs
`motor_control.py` as **root** (GPIO/SPI/I²C need it), `Restart=always` with `RestartSec=5` so a
crash self-heals, `PYTHONUNBUFFERED=1` so logs stream live to the journal.

```bash
sudo systemctl status  motor-control
sudo systemctl restart motor-control
journalctl -u motor-control -f
```

> ⚠️ WiFi credentials live in **`wifi_config.json`** next to `wifi_setup.py` (untracked by git;
> see the format documented at the top of `wifi_setup.py`). Without that file, the script only
> verifies existing connectivity (NetworkManager saved profiles) and cannot join new networks.

---

## Running Manually

```bash
# Full appliance (needs root for GPIO)
sudo python3 motor_control.py

# Motor only, for bench testing
sudo python3 motor_only.py 200          # 200 RPM, default M1 config
sudo python3 motor_only.py 150 K4       # 150 RPM, K4 config

# Doser servo only
python3 servo_only.py 0.5               # 50 % feed rate

# Hardware diagnostics
sudo python3 diagnostic.py
sudo python3 full_diagnostic.py
sudo python3 test_motor_comprehensive.py
```

---

## Dependencies

```bash
# System packages
sudo apt-get install -y python3-pip python3-pil python3-numpy python3-lgpio

# Python packages
pip3 install spidev smbus2 RPi.GPIO gpiozero
```

Required Python modules: `RPi.GPIO`, `spidev`, `smbus2`, `PIL` (Pillow), `numpy`, `gpiozero`,
`lgpio`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Screen blank, motor works | LCD SPI not reopened — check `reopen_spi_after_motor()` path; look for SPI errors in the journal. |
| `ERROR: This script must be run with sudo!` | GPIO needs root. Run with `sudo` (the service already does). |
| Touch dead, UI renders | CST816T not detected on I²C — check wiring/`TP_RST`; `i2cdetect -y 1` should show `0x15`. |
| Motor screeches / stalls on start | Wrong config current or decay; the accel ramp expects a sane `M*` config. Re-tune with `test_motor_comprehensive.py`. |
| Both subprocesses exit immediately | Usually SPI contention — confirm nothing else has SPI0 open; check the journal for the dumped child stdout/stderr. |
| `STATUS` faults (UVLO/OTS/OCP) | Power-supply sag, over-temp, or over-current — run `full_diagnostic.py` step 4 for a decoded readout. |
| Doser doesn't feed for a few seconds | Expected — 3 s start delay so the burr is up to speed first. |
| Swipe jumps two screens | Trailing reads — covered by the 0.4 s gesture cooldown; if persistent, raise `MIN_SWIPE_DISTANCE`. |

---

## Design Notes & Gotchas

- The app **must** run as root (GPIO/SPI/I²C).
- **Never** run `motor_control.py` and `motor_only.py` as independent simultaneous processes —
  they both want SPI bus 0 and will fault.
- Displayed RPM is always **half** the commanded motor RPM (2:1 gearbox). The slider snaps to
  multiples of 20 on the motor side (60–300), shown as 30–150 at the burr.
- The stepper step train is a `perf_counter()` busy-wait, not `time.sleep()` — that's why it
  lives in its own process where it can monopolize a core without starving the UI.
- Doser low-speed torque comes from **burst-mode full-torque pulsing**, not proportional servo
  speed — a deliberate workaround for weak CR-servo low-end torque.
- Icons are procedurally generated with a fixed RNG seed so the "ground coffee" STOP icon looks
  identical on every boot.
- `motor_configs.json` keeps the full A–K research sweep on purpose: it's the lab notebook for
  why `M1` is what it is, and the input data for `test_motor_comprehensive.py`.
