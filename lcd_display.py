"""
LCD 1.28" Display Driver (240x240 RGB565)
Standalone implementation for Raspberry Pi
"""
import time
import numpy as np
import spidev
import RPi.GPIO as GPIO
from PIL import Image, ImageDraw

class LCD_1inch28:
    def __init__(self):
        # Pin configuration (BCM numbering)
        self.RST_PIN = 27  # Pin 13 - Reset
        self.DC_PIN = 17   # Pin 11 - Data/Command
        self.BL_PIN = 23   # Pin 16 - Backlight
        self.CS_PIN = 22   # Pin 15 - Chip Select (manual control)

        # Display dimensions
        self.width = 240
        self.height = 240

        # SPI configuration
        self.spi = spidev.SpiDev()
        self.spi_bus = 0
        self.spi_device = 0

        # Pre-rendered image cache for performance
        self.cache_enabled = False
        self.static_background = None  # Cached background (track + center hole)
        self.arc_cache = {}  # Pre-rendered arcs at RPM increments
        self.cache_rpm_step = 10  # Cache every 10 RPM

    def module_init(self):
        """Initialize GPIO and SPI"""
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        # Setup GPIO pins
        GPIO.setup(self.RST_PIN, GPIO.OUT)
        GPIO.setup(self.DC_PIN, GPIO.OUT)
        GPIO.setup(self.BL_PIN, GPIO.OUT)
        GPIO.setup(self.CS_PIN, GPIO.OUT)

        # Initialize SPI
        self.spi.open(self.spi_bus, self.spi_device)
        self.spi.max_speed_hz = 40000000  # 40MHz
        self.spi.mode = 0b00  # SPI Mode 0

        # Turn on backlight
        GPIO.output(self.BL_PIN, GPIO.HIGH)

        # CS starts high (inactive)
        GPIO.output(self.CS_PIN, GPIO.HIGH)

        return 0

    def module_exit(self):
        """Clean up GPIO and SPI"""
        try:
            self.spi.close()
            GPIO.output(self.BL_PIN, GPIO.LOW)
            GPIO.cleanup()
        except:
            pass

    def reset(self):
        """Hardware reset"""
        GPIO.output(self.RST_PIN, GPIO.HIGH)
        time.sleep(0.01)
        GPIO.output(self.RST_PIN, GPIO.LOW)
        time.sleep(0.01)
        GPIO.output(self.RST_PIN, GPIO.HIGH)
        time.sleep(0.01)

    def write_cmd(self, cmd):
        """Write command to display"""
        GPIO.output(self.DC_PIN, GPIO.LOW)  # Command mode
        GPIO.output(self.CS_PIN, GPIO.LOW)  # Select chip
        self.spi.writebytes([cmd])
        GPIO.output(self.CS_PIN, GPIO.HIGH)  # Deselect

    def write_data(self, data):
        """Write data byte to display"""
        GPIO.output(self.DC_PIN, GPIO.HIGH)  # Data mode
        GPIO.output(self.CS_PIN, GPIO.LOW)  # Select chip
        if isinstance(data, int):
            self.spi.writebytes([data])
        else:
            self.spi.writebytes(data)
        GPIO.output(self.CS_PIN, GPIO.HIGH)  # Deselect

    def init_display(self):
        """Initialize display with configuration sequence"""
        self.module_init()
        self.reset()

        # Initialization sequence for GC9A01
        commands = [
            (0xEF, []),
            (0xEB, [0x14]),
            (0xFE, []),
            (0xEF, []),
            (0xEB, [0x14]),
            (0x84, [0x40]),
            (0x85, [0xFF]),
            (0x86, [0xFF]),
            (0x87, [0xFF]),
            (0x88, [0x0A]),
            (0x89, [0x21]),
            (0x8A, [0x00]),
            (0x8B, [0x80]),
            (0x8C, [0x01]),
            (0x8D, [0x01]),
            (0x8E, [0xFF]),
            (0x8F, [0xFF]),
            (0xB6, [0x00, 0x20]),
            (0x36, [0x08]),
            (0x3A, [0x05]),
            (0x90, [0x08, 0x08, 0x08, 0x08]),
            (0xBD, [0x06]),
            (0xBC, [0x00]),
            (0xFF, [0x60, 0x01, 0x04]),
            (0xC3, [0x13]),
            (0xC4, [0x13]),
            (0xC9, [0x22]),
            (0xBE, [0x11]),
            (0xE1, [0x10, 0x0E]),
            (0xDF, [0x21, 0x0c, 0x02]),
            (0xF0, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A]),
            (0xF1, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F]),
            (0xF2, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A]),
            (0xF3, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F]),
            (0xED, [0x1B, 0x0B]),
            (0xAE, [0x77]),
            (0xCD, [0x63]),
            (0x70, [0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03]),
            (0xE8, [0x34]),
            (0x62, [0x18, 0x0D, 0x71, 0xED, 0x70, 0x70, 0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70]),
            (0x63, [0x18, 0x11, 0x71, 0xF1, 0x70, 0x70, 0x18, 0x13, 0x71, 0xF3, 0x70, 0x70]),
            (0x64, [0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07]),
            (0x66, [0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00]),
            (0x67, [0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98]),
            (0x74, [0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00]),
            (0x98, [0x3e, 0x07]),
            (0x35, []),
            (0x21, []),
        ]

        for cmd, data in commands:
            self.write_cmd(cmd)
            for d in data:
                self.write_data(d)

        self.write_cmd(0x11)  # Sleep out
        time.sleep(0.12)
        self.write_cmd(0x29)  # Display on
        time.sleep(0.02)

    def set_window(self, x_start, y_start, x_end, y_end):
        """Set the active window for drawing"""
        # Column address set
        self.write_cmd(0x2A)
        self.write_data(0x00)
        self.write_data(x_start & 0xFF)
        self.write_data(0x00)
        self.write_data((x_end - 1) & 0xFF)

        # Row address set
        self.write_cmd(0x2B)
        self.write_data(0x00)
        self.write_data(y_start & 0xFF)
        self.write_data(0x00)
        self.write_data((y_end - 1) & 0xFF)

        # Memory write
        self.write_cmd(0x2C)

    def show_image(self, image):
        """Display a PIL Image on the screen"""
        if image.mode != 'RGB':
            image = image.convert('RGB')

        if image.size != (self.width, self.height):
            raise ValueError(f'Image must be {self.width}x{self.height} pixels')

        # Convert RGB888 to RGB565
        img_array = np.array(image)

        # Extract RGB channels
        r = (img_array[:, :, 0] >> 3).astype(np.uint8)  # 5 bits
        g = (img_array[:, :, 1] >> 2).astype(np.uint8)  # 6 bits
        b = (img_array[:, :, 2] >> 3).astype(np.uint8)  # 5 bits

        # Combine into RGB565 format
        # High byte: RRRRRGGG
        # Low byte:  GGGBBBBB
        high_byte = (r << 3) | (g >> 3)
        low_byte = ((g & 0x07) << 5) | b

        # Interleave high and low bytes
        pixel_data = np.empty((self.height, self.width, 2), dtype=np.uint8)
        pixel_data[:, :, 0] = high_byte
        pixel_data[:, :, 1] = low_byte

        # CRITICAL FIX: Convert to list ONCE before loop (not 29 times!)
        pixel_bytes = pixel_data.ravel().tolist()

        # Set window and write data
        self.set_window(0, 0, self.width, self.height)

        # Write in chunks - CS stays LOW for entire transfer (efficient!)
        chunk_size = 4096
        GPIO.output(self.DC_PIN, GPIO.HIGH)  # Data mode
        GPIO.output(self.CS_PIN, GPIO.LOW)   # Select chip (once)

        for i in range(0, len(pixel_bytes), chunk_size):
            # Just slice the list (already converted)
            self.spi.writebytes(pixel_bytes[i:i + chunk_size])

        GPIO.output(self.CS_PIN, GPIO.HIGH)  # Deselect (once)

    def clear(self, color=(0, 0, 0)):
        """Clear the screen with a solid color"""
        image = Image.new('RGB', (self.width, self.height), color)
        self.show_image(image)

    def reset_spi_speed(self):
        """Reset SPI speed to 40MHz (call after motor driver closes)"""
        self.spi.max_speed_hz = 40000000
        print("Display SPI speed reset to 40MHz")

    def build_static_background(self, center, radius_outer, radius_inner,
                               start_angle, end_angle, bg_color, track_color):
        """
        Pre-render the static background elements (track + center hole).
        Call this once during initialization.

        Args:
            center: (x, y) tuple for center point
            radius_outer: Outer radius of track
            radius_inner: Inner radius (center hole)
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            bg_color: Background RGB tuple
            track_color: Track color RGB tuple

        Returns: PIL Image of background
        """
        img = Image.new('RGB', (self.width, self.height), bg_color)
        draw = ImageDraw.Draw(img)

        # Draw track
        bbox = [center[0]-radius_outer, center[1]-radius_outer,
                center[0]+radius_outer, center[1]+radius_outer]
        draw.pieslice(bbox, start=start_angle, end=end_angle, fill=track_color)

        # Draw center hole
        hole_bbox = [center[0]-radius_inner, center[1]-radius_inner,
                     center[0]+radius_inner, center[1]+radius_inner]
        draw.ellipse(hole_bbox, fill=bg_color)

        self.static_background = img
        print("Built static background cache")
        return img

    def build_arc_cache(self, center, radius_outer, start_angle, end_angle,
                       active_color, min_rpm, max_rpm):
        """
        Pre-render arc states at RPM_STEP increments.
        Call this once during initialization.

        Args:
            center: (x, y) tuple for center point
            radius_outer: Outer radius of arc
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            active_color: Arc fill color RGB tuple
            min_rpm: Minimum RPM value
            max_rpm: Maximum RPM value

        Caches arcs for each RPM value at cache_rpm_step increments.
        """
        if self.static_background is None:
            raise RuntimeError("Must build static background before arc cache")

        bbox = [center[0]-radius_outer, center[1]-radius_outer,
                center[0]+radius_outer, center[1]+radius_outer]

        # Pre-render arcs for each RPM step
        rpm_range = max_rpm - min_rpm
        steps = (rpm_range // self.cache_rpm_step) + 1

        print(f"Building arc cache: {steps} images...")

        for i in range(steps + 1):
            rpm = min_rpm + (i * self.cache_rpm_step)
            if rpm > max_rpm:
                rpm = max_rpm

            # Calculate arc angle for this RPM
            ratio = (rpm - min_rpm) / rpm_range
            active_angle = start_angle + ratio * (end_angle - start_angle)

            # Create arc on transparent background
            arc_img = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(arc_img)
            draw.pieslice(bbox, start=start_angle, end=active_angle,
                         fill=active_color + (255,))  # Add alpha

            self.arc_cache[rpm] = arc_img

        self.cache_enabled = True
        print(f"Arc cache built: {len(self.arc_cache)} images")

    def get_cached_arc(self, rpm):
        """
        Get the closest pre-rendered arc for given RPM.

        Args: rpm - Target RPM value
        Returns: PIL Image (RGBA) of arc, or None if cache disabled
        """
        if not self.cache_enabled:
            return None

        # Round to nearest cache step
        cache_rpm = round(rpm / self.cache_rpm_step) * self.cache_rpm_step

        # Clamp to available range
        available_rpms = sorted(self.arc_cache.keys())
        if cache_rpm < available_rpms[0]:
            cache_rpm = available_rpms[0]
        elif cache_rpm > available_rpms[-1]:
            cache_rpm = available_rpms[-1]

        return self.arc_cache.get(cache_rpm)

    def composite_cached_frame(self, rpm, knob_pos=None, button_color=None,
                              button_radius=None, text_elements=None):
        """
        Composite a frame from cached elements + dynamic overlays.
        This is the fast-path rendering method.

        Args:
            rpm: Current RPM value
            knob_pos: (x, y) tuple for knob position, or None to skip
            button_color: RGB tuple for button, or None to use default
            button_radius: Button radius, or None to skip button
            text_elements: List of (text, position, font, color) tuples, or None

        Returns: PIL Image ready to display
        """
        if not self.cache_enabled or self.static_background is None:
            raise RuntimeError("Cache not initialized. Call build_*_cache() first")

        # Start with background
        frame = self.static_background.copy()

        # Composite arc
        arc = self.get_cached_arc(rpm)
        if arc:
            frame.paste(arc, (0, 0), arc)  # Use arc as alpha mask

        # Draw dynamic elements
        draw = ImageDraw.Draw(frame)

        # Knob (if not locked)
        if knob_pos:
            kx, ky = knob_pos
            kr = 15
            draw.ellipse([kx-kr, ky-kr, kx+kr, ky+kr], fill=(255, 255, 255))

        # Button
        if button_color and button_radius:
            center = (self.width // 2, self.height // 2)
            draw.ellipse([center[0]-button_radius, center[1]-button_radius,
                         center[0]+button_radius, center[1]+button_radius],
                        fill=button_color)

        # Text overlays
        if text_elements:
            for text, pos, font, color in text_elements:
                if font:
                    draw.text(pos, text, font=font, fill=color, anchor="mm")
                else:
                    draw.text(pos, text, fill=color)

        return frame