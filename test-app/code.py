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
display.root_group = main_group

# --- Layout constants ---
# Display is 296 x 128
STATUS_BAR_HEIGHT = 14  # thin bar at the very top
CONTENT_TOP = STATUS_BAR_HEIGHT + 2  # leave a 2px gap after status bar
CONTENT_HEIGHT = display.height - CONTENT_TOP
BLOCK_HEIGHT = CONTENT_HEIGHT // 4  # 4 equal vertical blocks
BLOCK_WIDTH = display.width

# --- Read battery voltage ---
# MagTag battery voltage divider is on board.VOLTAGE_MONITOR
try:
    vbat_voltage_pin = analogio.AnalogIn(board.VOLTAGE_MONITOR)
    # Voltage divider halves the voltage; reference is 3.3V over 16-bit range
    battery_voltage = (vbat_voltage_pin.value / 65535.0) * 3.3 * 2
    vbat_voltage_pin.deinit()
except Exception:
    battery_voltage = 0.0

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
    x=2,
    y=STATUS_BAR_HEIGHT // 2,
    scale=1,
)
main_group.append(status_time_label)

battery_text = f"{battery_voltage:.2f}V"
battery_label = label.Label(
    terminalio.FONT,
    text=battery_text,
    color=0x000000,
    anchor_point=(1.0, 0.5),
    anchored_position=(display.width - 2, STATUS_BAR_HEIGHT // 2),
    scale=1,
)
main_group.append(battery_label)

# Horizontal separator below status bar
main_group.append(Line(0, STATUS_BAR_HEIGHT, display.width - 1, STATUS_BAR_HEIGHT, 0x000000))

# ── Four content blocks with placeholder text ──
block_labels = ["Block 1", "Block 2", "Block 3", "Block 4"]
for i, block_text in enumerate(block_labels):
    block_y = CONTENT_TOP + i * BLOCK_HEIGHT

    # Separator line between blocks (skip the first — the status bar line covers it)
    if i > 0:
        main_group.append(Line(0, block_y, display.width - 1, block_y, 0x999999))

    # Centered placeholder label in each block
    placeholder = label.Label(
        terminalio.FONT,
        text=block_text,
        color=0x000000,
        anchor_point=(0.5, 0.5),
        anchored_position=(display.width // 2, block_y + BLOCK_HEIGHT // 2),
        scale=2,
    )
    main_group.append(placeholder)

# ── Refresh the e-ink display ──
time.sleep(display.time_to_refresh)
display.refresh()

# --- Check for REPL escape hatch ---
# Hold button A (D15) during startup to skip deep sleep and stay in REPL.
# This gives you USB access for development/debugging.
btn_a = digitalio.DigitalInOut(board.D15)
btn_a.direction = digitalio.Direction.INPUT
btn_a.pull = digitalio.Pull.UP
if not btn_a.value:  # Button A is held (active low)
    btn_a.deinit()
    print("Button A held — skipping deep sleep. Dropping into REPL.")
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
