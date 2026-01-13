# Comprehensive Motor Test Script Usage Guide

## Overview

`test_motor_comprehensive.py` is a complete diagnostic and optimization tool with **88 test configurations** organized into 10 categories (A-J). It will help you:

1. **Diagnose** the root cause of low-speed stalling
2. **Find** the quietest configuration with adequate torque
3. **Identify** resonance frequencies
4. **Optimize** for your specific requirements (quiet vs. torque)

## Quick Start

### Recommended First Run: Diagnostic Baseline (Option D)

```bash
sudo python3 test_motor_comprehensive.py
```

**Select Option D** from the menu to run just the 5 baseline configurations. This will:
- Establish current performance (should match existing behavior)
- Test a high-torque preset (should NOT stall)
- Test a low-noise preset
- Give you a baseline to compare against

**Estimated time:** ~15-20 minutes for 5 configs

## Test Categories

### Priority 1: Diagnose Stalling Issues
- **Option D**: Diagnostic Baseline (5 configs) - START HERE
- **Option T**: Torque Focus Tests (20 configs) - diagnose stalling
  - Tests all DRIVE currents at 50 RPM where stalling occurs
  - Tests all decay modes at 50 RPM
  - **CRITICAL** to identify root cause

### Priority 2: Noise Optimization
- **Category B** (CB): Extended PWM Frequency (12 configs)
  - Find quietest PWM frequency (10kHz to 100kHz)
- **Category F** (CF): Microstepping Extended (9 configs)
  - Test all microstepping modes (1/1 to 1/256)

### Priority 3: Full Characterization
- **Category D** (CD): Motor Current Sweep (10 configs)
- **Category E** (CE): Decay Mode Deep Dive (12 configs)
- **Category G** (CG): Stall Detection Impact (6 configs)
- **Category H** (CH): Blanking Time vs Microstepping (8 configs)
- **Category I** (CI): Resonance Troubleshooting (10 configs)
- **Category J** (CJ): High Torque Optimizations (8 configs)

## Menu Options

```
[A] Run ALL Tests (88 configs - ~7 hours estimated)
[D] Diagnostic Baseline Only (5 configs - RECOMMENDED START)
[T] Torque Focus Tests (20 configs - diagnose stalling)
[Q] Quick Recommended (10 configs)

[C] Run by Category:
    CA - Category A: Diagnostic Baseline (5 configs)
    CB - Category B: Extended PWM Frequency (12 configs)
    CC - Category C: DRIVE Current Optimization (8 configs)
    CD - Category D: Motor Current Sweep (10 configs)
    CE - Category E: Decay Mode Deep Dive (12 configs)
    CF - Category F: Microstepping Extended (9 configs)
    CG - Category G: Stall Detection Impact (6 configs)
    CH - Category H: Blanking Time vs Microstepping (8 configs)
    CI - Category I: Resonance Troubleshooting (10 configs)
    CJ - Category J: High Torque Optimizations (8 configs)

[S] Specific Configuration (e.g., 'A1', 'B5', 'C3')
[R] View Results Summary
[E] Export Results to CSV
[X] Exit
```

## What You'll Rate

For each test configuration at each speed, you'll provide:

1. **Noise rating** (1-10): 1=very loud, 10=silent
2. **Torque rating** (1-10): 1=very weak, 10=strong
3. **Did it skip steps?** (y/n)
4. **Did it stall completely?** (y/n)
5. **Unusual vibration/resonance?** (y/n)
6. **Notes** (optional)

The script automatically checks for thermal warnings (OTS bit).

## Results Analysis

After running tests, use **Option R** to view:

- **TOP 10 QUIETEST** configurations
- **TOP 10 STRONGEST TORQUE** configurations
- **TOP 10 BEST COMBINED** (60% quiet, 40% torque)
- **Configurations that STALLED** (identify problem settings)
- **Resonance zones detected** (speed/PWM combinations to avoid)
- **Thermal warnings** (overcurrent conditions)

## Export Results

Use **Option E** to export all results to CSV for:
- Excel/Google Sheets analysis
- Graphing (noise vs torque, current vs performance, etc.)
- Sharing findings

## Expected Findings

Based on research, you should discover:

### Critical Issues (likely causes of stalling):
1. **Wrong decay mode**: Fast decay (0x210) loses torque at low speeds
   - **Solution**: Slow decay (0x010) or Slow/Mixed (0x110)
2. **Insufficient DRIVE current**: 50/100mA or 100/200mA too weak
   - **Solution**: 200/400mA (0xF59) for reliable switching
3. **Motor current too low**: 4200mA might not be enough
   - **Solution**: Test 5000-6500mA (but watch thermal)

### Optimal Configuration (Predicted):
- **Best Quiet + Torque**: 5000mA + Slow/Mixed (0x110) + Max DRIVE (0xF59) + 62.5kHz PWM + 1/32 step
- **Maximum Torque**: 6500mA + Slow (0x010) + Max DRIVE (0xF59) + 1/16 step
- **Quietest (if torque adequate)**: 4200mA + Auto-Mixed (0x510) + 100kHz PWM + 1/128 step + ABT

## Safety Features

The script includes:
- **Emergency Stop**: Ctrl+C immediately disables driver and exits
- **Thermal Monitoring**: Checks OTS bit after each test
- **Driver Disable**: Fully disables driver between tests (no holding torque)
- **Register Verification**: Warns if register values don't match
- **Current Limiting**: Warns before exceeding 6500mA

## Troubleshooting

### "SPI MISO reading 0xFFF" or "Blind mode" warnings
- Your SPI read line (MISO) might not be working
- **Script will still work!** - writes (MOSI) are functional
- You just can't verify registers or read STATUS
- Motor movement and configuration will work normally

### Motor doesn't move during test
- Check connections (STEP, DIR, SLEEP pins)
- Verify power supply is connected and adequate voltage
- Try config A3 (high torque preset) - should have plenty of torque

### Script crashes or freezes
- Make sure GPIO/SPI not already in use by another program
- Ensure sudo permissions
- Check that LCD CS pin (GPIO 22) is not conflicting

## Time Estimates

- **Diagnostic Baseline (D)**: 15-20 minutes
- **Torque Focus (T)**: 60-90 minutes
- **Quick Recommended (Q)**: 30-40 minutes
- **Single Category**: 30-60 minutes (varies by category)
- **All 88 Configs (A)**: 6-8 hours

## Tips

1. **Start with Option D** to baseline performance
2. **Run Option T next** to diagnose stalling (most important)
3. **Rate consistently** - use same criteria throughout testing
4. **Take breaks** between categories to avoid rating fatigue
5. **Export regularly** - save results after each session
6. **Listen carefully** - subtle noise differences matter
7. **Feel the motor** - excessive vibration = resonance

## Next Steps After Testing

1. **Review results** with Option R
2. **Export to CSV** with Option E
3. **Identify top 3** best combined configs
4. **Update your code** with optimal register values
5. **Re-test in real grinder** to confirm performance

---

**Created**: 2026-01-13
**Script**: test_motor_comprehensive.py
**Total Configurations**: 88 across 10 categories (A-J)
**Goal**: Find quietest configuration with adequate torque to eliminate stalling
