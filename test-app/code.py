import json
import os
import ssl
import time
import alarm
import wifi
import socketpool
import adafruit_requests
import analogio
import board
import digitalio
import displayio
import terminalio
from adafruit_display_text import label
from adafruit_display_shapes.line import Line

# --- Display setup ---
# Take over the display immediately to prevent terminal output on screen
display = board.DISPLAY
main_group = displayio.Group()

# White background
bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0xFFFFFF
main_group.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette, x=0, y=0))

# The e-ink controller RAM is larger than the physical panel. With colstart=0
# in the board definition, the top ~5 pixels of RAM fall outside the visible
# area. Shift all content down to compensate. The bottom ~5 rows similarly
# show noise from uninitialized RAM, so we treat them as unusable too.
DISPLAY_Y_OFFSET =5
USABLE_HEIGHT = display.height - DISPLAY_Y_OFFSET - 5  # ~120 usable rows

content_group = displayio.Group(y=DISPLAY_Y_OFFSET)
main_group.append(content_group)
display.root_group = main_group

# --- Layout constants ---
# Physical display is 296 x 128, usable area is ~296 x 122 after offsets.
STATUS_BAR_HEIGHT = 14
CONTENT_TOP = STATUS_BAR_HEIGHT + 2  # 2px gap after status bar
CONTENT_HEIGHT = USABLE_HEIGHT - CONTENT_TOP
BLOCK_WIDTH = display.width // 4  # 4 equal vertical columns
BLOCK_HEIGHT = CONTENT_HEIGHT

# --- Local data file (persistent state) ---
DATA_PATH = "/data.json"

def db_read():
    try:
        with open(DATA_PATH, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"items": []}

def db_write(data):
    with open(DATA_PATH, "w") as f:
        json.dump(data, f)

# --- Read battery voltage & compute percentage ---
# MagTag battery voltage divider is on board.VOLTAGE_MONITOR
# 3.7V 420mAh LiPo: 4.2V = 100%, 3.0V = 0%
# Piecewise linear approximation of the typical LiPo discharge curve.
LIPO_CURVE = [
    (4.20, 100), (4.15, 95), (4.10, 90), (4.05, 85),
    (4.00, 80),  (3.90, 70), (3.80, 60), (3.70, 50),
    (3.60, 40),  (3.50, 30), (3.40, 20), (3.30, 10),
    (3.20, 5),   (3.00, 0),
]

def voltage_to_percent(voltage):
    if voltage >= LIPO_CURVE[0][0]:
        return 100
    if voltage <= LIPO_CURVE[-1][0]:
        return 0
    for i in range(len(LIPO_CURVE) - 1):
        v_high, p_high = LIPO_CURVE[i]
        v_low, p_low = LIPO_CURVE[i + 1]
        if voltage >= v_low:
            # Linear interpolation between the two points
            return p_low + (p_high - p_low) * (voltage - v_low) / (v_high - v_low)
    return 0

try:
    vbat_voltage_pin = analogio.AnalogIn(board.VOLTAGE_MONITOR)
    # Voltage divider halves the voltage; reference is 3.3V over 16-bit range
    battery_voltage = (vbat_voltage_pin.value / 65535.0) * 3.3 * 2
    vbat_voltage_pin.deinit()
    battery_percent = voltage_to_percent(battery_voltage)
except Exception:
    battery_voltage = 0.0
    battery_percent = 0

# --- Connect to WiFi & fetch time ---
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")
timezone = os.getenv("TIMEZONE")
TIME_URL = (
    f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime"
    f"?x-aio-key={aio_key}&tz={timezone}"
    "&fmt=%25Y-%25m-%25d+%25H%3A%25M%3A%25S"
)

print("Connecting to", ssid)
wifi.radio.connect(ssid, password)
print(f"Connected to {ssid}!")

pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

response = requests.get(TIME_URL)
current_time = response.text.strip()
print("Current time:", current_time)

# --- Build the display ---

# ── Status bar (top line): refresh time on the left, battery on the right ──
status_time_label = label.Label(
    terminalio.FONT,
    text=f"Refreshed: {current_time}",
    color=0x000000,
    anchor_point=(0.0, 0.5),
    anchored_position=(2, STATUS_BAR_HEIGHT // 2),
    scale=1,
)
content_group.append(status_time_label)

battery_text = f"{battery_percent:.0f}%"
battery_label = label.Label(
    terminalio.FONT,
    text=battery_text,
    color=0x000000,
    anchor_point=(1.0, 0.5),
    anchored_position=(display.width - 2, STATUS_BAR_HEIGHT // 2),
    scale=1,
)
content_group.append(battery_label)

# Horizontal separator below status bar
content_group.append(Line(0, STATUS_BAR_HEIGHT, display.width - 1, STATUS_BAR_HEIGHT, 0x000000))

# ── Four content columns with placeholder text ──
# Each column is 74px wide. terminalio.FONT is 6px/char, so at scale=2
# only ~6 chars fit per column (74 / 12 = 6.1). Use short labels.
block_labels = ["Blk 1", "Blk 2", "Blk 3", "Blk 5"]
for i, block_text in enumerate(block_labels):
    block_x = i * BLOCK_WIDTH

    # Vertical separator line between columns (skip the first — left edge)
    if i > 0:
        content_group.append(Line(block_x, CONTENT_TOP, block_x, USABLE_HEIGHT - 1, 0x999999))

    # Centered placeholder label in each column
    placeholder = label.Label(
        terminalio.FONT,
        text=block_text,
        color=0x000000,
        anchor_point=(0.5, 0.5),
        anchored_position=(block_x + BLOCK_WIDTH // 2, CONTENT_TOP + BLOCK_HEIGHT // 2),
        scale=2,
    )
    content_group.append(placeholder)

# ── Refresh the e-ink display ──
time.sleep(display.time_to_refresh)
display.refresh()

# --- Dev mode escape hatch ---
# In dev mode, Button A is held during reset. boot.py keeps USB writable
# for the host, and this check skips deep sleep so we drop into REPL.
# See boot.py for the full boot mode description.
btn_a = digitalio.DigitalInOut(board.D15)
btn_a.direction = digitalio.Direction.INPUT
btn_a.pull = digitalio.Pull.UP
if not btn_a.value:  # Button A held (active low) — dev mode
    btn_a.deinit()
    print("Dev mode — skipping deep sleep. USB writable, REPL active.")
else:
    btn_a.deinit()

    # --- Deep sleep ---
    # The e-ink display retains the image without power.
    # Wake after 1 hour or on any button press.
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + 3600)
    button_a_alarm = alarm.pin.PinAlarm(pin=board.D15, value=False, pull=True)
    button_b_alarm = alarm.pin.PinAlarm(pin=board.D14, value=False, pull=True)
    button_c_alarm = alarm.pin.PinAlarm(pin=board.D12, value=False, pull=True)
    button_d_alarm = alarm.pin.PinAlarm(pin=board.D11, value=False, pull=True)

    print("Entering deep sleep...")
    alarm.exit_and_deep_sleep_until_alarms(
        time_alarm, button_a_alarm, button_b_alarm, button_c_alarm, button_d_alarm
    )
