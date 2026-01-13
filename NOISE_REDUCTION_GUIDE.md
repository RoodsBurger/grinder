# DRV8711 Noise Reduction Guide

## Official Pololu Library Defaults

From https://github.com/pololu/high-power-stepper-driver-arduino:

| Register | Pololu Default | What We Changed | Reason |
|----------|---------------|-----------------|--------|
| OFF | 0x030 (24µs = 41.7kHz) | **NOW 0x030** ✅ | Above audible range |
| BLANK | 0x080 | Same | Standard blanking |
| DECAY | 0x110 (Slow/Mixed) | **0x510 (Auto-Mixed)** | Example recommends AutoMixed |
| DRIVE | 0xA59 (150/300mA) | **0x559 (100/200mA)** | Reduce noise while maintaining torque |

**KEY INSIGHT:** Pololu uses **SHORTER** OFF time for **HIGHER** PWM frequency (41.7kHz) to get **ABOVE** the audible range. We initially did the opposite (longer OFF = lower PWM = audible noise)!

## Critical Issues Found in Research

### 1. **DRIVE Register - NOT CONFIGURED!**
**MAJOR ISSUE:** We never set IDRIVEP/IDRIVEN in the DRIVE register!

**Default values likely TOO HIGH** causing:
- Voltage ringing and EMI
- Excessive gate drive current
- Audible noise from FET switching

**TI Recommendation:**
> "IDRIVEN / IDRIVEP should be selected to be the smallest settings that meet requirements"
>
> **Recommended starting values: IDRIVEP=50mA, IDRIVEN=100mA**

Current default in pololu_lib.py: `0xA59`
- Decodes to: IDRIVEP=150mA, IDRIVEN=300mA (TOO HIGH!)
- **FIXED to: `0x059` = IDRIVEP=50mA, IDRIVEN=100mA** ✅

### 2. **Adaptive Blanking Time (ABT) - Disabled**
ABT improves current regulation at higher microstepping (1/16, 1/32)

**Enable by setting bit 8 in BLANK register:**
- Current: `0x080` (ABT disabled)
- With ABT: `0x180` (ABT enabled)

### 3. **PWM Frequency - KEY TO NOISE REDUCTION!**
**CRITICAL: Pololu uses OFF=0x030 (24µs = 41.7 kHz) - ABOVE audible range!**

We initially had OFF=0x0A0 (80µs = 12.5 kHz) which was **IN the audible range** - that's why it was noisy!

**The fix:** Use **SHORTER** OFF time = **HIGHER** PWM frequency = above audible!

**Recommended values:**
- `0x030` = 24µs = **41.7 kHz** ✅ **Pololu default - ABOVE audible**
- `0x028` = 20µs = 50 kHz (even higher)
- `0x050` = 40µs = 25 kHz (still above audible)
- `0x0A0` = 80µs = 12.5 kHz ❌ **AUDIBLE - DO NOT USE**

**Now fixed to 0x030 (Pololu default)**

### 4. **Decay Mode Settings**
Current: Auto-Mixed Decay (0x510)
Alternative options to test:
- Slow Decay: `0x010` (smoothest but can distort)
- Mixed Decay: `0x310` (balanced)
- Fast Decay: `0x210` (fastest response but noisy)

### 5. **Microstepping vs Noise**
From research:
- **Full step (1):** LOUDEST (resonance, jerky)
- **1/8 step:** Moderate noise, good torque
- **1/16 step:** Quiet, balanced (current)
- **1/32 step:** Quieter, needs ABT enabled
- **1/64 step:** Very quiet, reduced torque

## Test Configurations to Try

### Configuration Priority Order:

1. **Fix DRIVE register** (most likely culprit) - **NOW FIXED!** ✅
   - Fixed: `DRIVE=0x059` (50/100mA instead of 150/300mA)

2. **Enable ABT for smoother microstepping**
   - Test: `BLANK=0x180` instead of `0x080`

3. **Change PWM frequency out of audible range**
   - Test: `OFF=0x050` (25 kHz) or `OFF=0x1FF` (4 kHz)

4. **Try 1/32 microstepping with ABT**
   - Smoother = quieter, but needs ABT for good current regulation

5. **Lower current if still too loud**
   - Test: 3800mA (90% of rated) or 3400mA (80%)

## How to Use test_motor_noise.py

```bash
sudo python3 test_motor_noise.py
```

**The script now has 50+ test configurations organized into 8 categories:**

1. **BASELINE** (2 tests) - Compare before/after DRIVE fix
2. **PWM FREQUENCY** (9 tests) - Sweep from 20µs to 250µs (50kHz to 4kHz)
3. **BLANKING TIME** (6 tests) - Test different blank times + ABT
4. **DRIVE CURRENT** (4 tests) - Test gate drive currents (min to max)
5. **MICROSTEPPING** (6 tests) - Test 1/4 to 1/128 step modes
6. **DECAY MODE** (6 tests) - Test all decay mode variations
7. **CURRENT LEVEL** (3 tests) - Test 80%, 90%, 100% motor current
8. **OPTIMIZED COMBOS** (5 tests) - Pre-tuned quiet configurations

**Quick recommended sequence (select [Q] option):**
1. `baseline_new` - Verify DRIVE register fix is working
2. `combo_balanced` - Good starting point (16step + 25kHz PWM)
3. `combo_silent_high_freq` - High PWM approach (32step + 42kHz)
4. `combo_silent_low_freq` - Low PWM approach (32step + 4kHz)

**The script now includes register verification:**
- Reads back all written register values
- Decodes actual microstepping from CTRL register
- Warns if register writes don't match expected values
- Confirms settings were applied correctly

## Expected Results

**Noise should significantly reduce** after fixing DRIVE register.

If still noisy, the issue might be:
- Mechanical resonance (motor/grinder coupling)
- Power supply noise
- Inadequate decoupling capacitors
- Ground loops

## Sources

- [TI DRV8711 Noise Issues](https://e2e.ti.com/support/motor-drivers-group/motor-drivers/f/motor-drivers-forum/368628/stepper-motor-noise-drv8711)
- [DRV8711 DRIVE Register Settings](https://e2e.ti.com/support/motor-drivers-group/motor-drivers/f/motor-drivers-forum/1294032/drv8711evm-calculating-the-drive-register-values)
- [DRV8711 Optimization Guide](https://e2e.ti.com/support/motor-drivers-group/motor-drivers/f/motor-drivers-forum/651987/drv8711-optimizing-drv8711)
- [Pololu Forum: Noisy DRV8711](https://forum.pololu.com/t/noisy-drv8711-stepper-driver-36v4-with-large-motors/20489)
- [DRV8711 Datasheet](https://www.ti.com/lit/ds/symlink/drv8711.pdf)
