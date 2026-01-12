# Performance Analysis: Grinder vs Tobias Display

## Critical Differences Found

### 1. **Pin Configuration**
**Working (Tobias):**
- RST = 27
- DC = **25**
- BL = 18
- No manual CS control

**Current (Grinder):**
- RST = 27
- DC = **17**
- BL = 23  
- CS = 22 (manual control)
- **Conflict:** Motor STEP = 25 (same as Tobias DC pin!)

### 2. **SPI Write Performance Bug** ⚠️ CRITICAL
**Working code (tobias/robot/how_to_walk/lib/LCD_1inch28.py:333-337):**
```python
pix = pix.flatten().tolist()  # Convert to list ONCE
self.SetWindows(0, 0, self.width, self.height)
self.digital_write(self.DC_PIN, self.GPIO.HIGH)
for i in range(0, len(pix), 4096):
    self.spi_writebyte(pix[i:i+4096])  # Just slice the list
```

**Current code (Grinder/lcd_display.py:217-220):**
```python
for i in range(0, len(pixel_bytes), chunk_size):
    chunk = pixel_bytes[i:i + chunk_size]
    self.spi.writebytes(chunk.tolist())  # tolist() called 29 TIMES per frame!
```

**Impact:** Calling `.tolist()` 29 times per frame is EXTREMELY slow!
- 115,200 bytes / 4096 = 29 iterations
- At 30 FPS = 870 numpy→list conversions per second
- This is the primary bottleneck!

### 3. **CS (Chip Select) Overhead**
**Working:** No manual CS toggling
**Current:** CS toggled LOW/HIGH around every write (lines 215, 222)

### 4. **RGB Conversion Logic**
Both use same RGB565 conversion algorithm - no difference here.

## Performance Impact Estimate

**Current implementation:**
- 29 × tolist() per frame = ~50-100ms overhead
- Manual CS toggling = ~5-10ms overhead  
- **Total: 55-110ms per frame (9-18 FPS max)**

**After fix:**
- 1 × tolist() per frame = ~2-4ms
- No CS toggling = 0ms
- **Total: ~15-20ms per frame (50-66 FPS possible)**

## Recommended Fixes

1. **CRITICAL:** Move `.tolist()` outside the loop
2. Remove manual CS control (use hardware CS)
3. Consider changing DC pin to 25 if motor interference is an issue
