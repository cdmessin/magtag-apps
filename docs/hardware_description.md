ESP32-S2 240MHz Tensilica processor - the next generation of ESP32, now with native USB so it can act like a keyboard/mouse, MIDI device, disk drive, etc!
WROVER module has FCC/CE certification and comes with 4 MByte of Flash and 2 MByte of PSRAM - you can have huge data buffers
2.9" grayscale display with 296x128 pixels. Each pixel can be white, light gray, dark gray or black. Compared to 'tri-color' displays with a red pigment, this display takes a lot less time to update, only about a second instead of 15 seconds!

Display quirks:
- The e-ink controller (IL0373 on original MagTag, SSD1680 on 2025 revision) has more RAM than the physical panel. IL0373 has 296x160 RAM for 296x128 pixels; SSD1680 has 296x250. The display is used at rotation=270, which transposes axes.
- The CircuitPython board definition sets colstart=0 and rowstart=0. The physical panel's active area is offset a few pixels from RAM address 0, so the top ~3 pixels of content at y=0 are clipped above the visible area. Compensate by shifting content down (e.g. displayio.Group(y=3)).
- The bottom ~3 rows show hazy static/noise. This is caused by uninitialized controller RAM beyond the 128-pixel boundary being driven to the panel during refresh. This is a hardware characteristic, not a software bug. Keep content away from the bottom edge.
- Practical usable area is approximately 296x122 pixels after accounting for both offsets.
- terminalio.FONT is 6px wide per character. At scale=2 each char is 12px. Plan text layout accordingly (e.g. 4 equal columns are 74px wide, fitting ~6 chars at scale=2).
USB C power and data connector
Four RGB side-emitting NeoPixels so you can light up the display with any color or pattern
Four buttons can be used to wake up the ESP32 from deep-sleep, or select different modes
Triple-axis accelerometer (LIS3DH) can be used to detect orientation of the display
Speaker/Buzzer with mini class D amplifier on DAC output A0 can play tones or lo-fi audio clips.
Front facing light sensor
STEMMA QT port for attaching all sorts of I2C devices
Two STEMMA 3 pin JST connectors for attaching NeoPixels, speakers, servos or relays.
On/Off switch
Boot and Reset buttons for re-programming
