#!/usr/bin/env python3
"""
Convert SVG icons to PNG for use on Raspberry Pi display
Requires: pip install cairosvg
"""
import os

try:
    import cairosvg

    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Convert at 2x resolution (48x48) for crisp display
    size = 48

    print("Converting SVG icons to PNG...")

    # Convert start icon
    cairosvg.svg2png(
        url=os.path.join(script_dir, "icon_start.svg"),
        write_to=os.path.join(script_dir, "icon_start.png"),
        output_width=size,
        output_height=size
    )
    print(f"✓ icon_start.png ({size}x{size})")

    # Convert stop icon
    cairosvg.svg2png(
        url=os.path.join(script_dir, "icon_stop.svg"),
        write_to=os.path.join(script_dir, "icon_stop.png"),
        output_width=size,
        output_height=size
    )
    print(f"✓ icon_stop.png ({size}x{size})")

    print("\nIcons converted successfully!")
    print("Run: python3 motor_control.py")

except ImportError:
    print("ERROR: cairosvg not installed")
    print("Install with: pip3 install cairosvg")
    print("\nAlternatively, convert SVG to PNG manually using:")
    print("  - Inkscape: inkscape icon_start.svg --export-png=icon_start.png -w 48 -h 48")
    print("  - Online: https://convertio.co/svg-png/")
