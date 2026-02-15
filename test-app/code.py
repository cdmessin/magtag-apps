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
from adafruit_display_shapes.rect import Rect

# --- Button-to-pin mapping ---
# MagTag has 4 buttons (A-D) mapped to these GPIO pins
BUTTON_PINS = {
    "A": board.D15,
    "B": board.D14,
    "C": board.D12,
    "D": board.D11,
}
# Map buttons to item indices (button A -> item 0, etc.)
BUTTON_TO_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}

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


def get_wake_button():
    """Return which button ('A'-'D') triggered the wake, or None if not a button wake."""
    wake_alarm = alarm.wake_alarm
    if wake_alarm is None or not isinstance(wake_alarm, alarm.pin.PinAlarm):
        return None
    for button_name, pin in BUTTON_PINS.items():
        if wake_alarm.pin == pin:
            return button_name
    return None


def add_days_to_date(date_str, days):
    """Add days to a YYYY-MM-DD date string. Returns new date string."""
    year, month, day = map(int, date_str.split("-"))
    # Days in each month (non-leap year base)
    days_in_month = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    # Leap year check
    if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        days_in_month[2] = 29
    
    day += days
    while day > days_in_month[month]:
        day -= days_in_month[month]
        month += 1
        if month > 12:
            month = 1
            year += 1
            # Recalculate leap year for new year
            if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
                days_in_month[2] = 29
            else:
                days_in_month[2] = 28
    return f"{year:04d}-{month:02d}-{day:02d}"


def format_due_date(due_date, today_str):
    """Format due date as 'past due', 'today', 'tomorrow', or the date."""
    if due_date < today_str:
        return "past due"
    if due_date == today_str:
        return "today"
    tomorrow = add_days_to_date(today_str, 1)
    if due_date == tomorrow:
        return "tomorrow"
    # Return just month/day for brevity
    parts = due_date.split("-")
    return f"{int(parts[1])}/{int(parts[2])}"


def days_between(date1, date2):
    """Calculate days between two YYYY-MM-DD date strings (date2 - date1)."""
    y1, m1, d1 = map(int, date1.split("-"))
    y2, m2, d2 = map(int, date2.split("-"))
    # Simple day count using a reference point
    def to_days(y, m, d):
        # Approximate days since year 0
        days = y * 365 + d
        for i in range(1, m):
            days += [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][i]
        # Add leap years
        days += y // 4 - y // 100 + y // 400
        if m <= 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
            days -= 1
        return days
    return to_days(y2, m2, d2) - to_days(y1, m1, d1)


def calculate_progress(item, today_str):
    """Calculate progress 0.0-1.0 based on days elapsed since last completion."""
    last_completed = item.get("last_completed", "")
    interval = int(item.get("day_interval", 1))
    if not last_completed or interval <= 0:
        return 0.0
    days_elapsed = days_between(last_completed, today_str)
    progress = days_elapsed / interval
    return max(0.0, min(1.0, progress))  # Clamp to 0-1


def get_fill_color(progress):
    """Return grayscale fill color based on progress (0.0-1.0)."""
    if progress >= 0.8:
        return 0x000000  # Black - urgent
    elif progress >= 0.5:
        return 0x666666  # Medium gray
    else:
        return 0xAAAAAA  # Light gray


def is_past_due(due_date, today_str):
    """Check if an item is past due."""
    return due_date < today_str


def mark_item_completed(item_index, current_date):
    """Mark an item as completed today and recalculate its due date."""
    data = db_read()
    items = data.get("items", [])
    
    if item_index < 0 or item_index >= len(items):
        return  # Invalid index, nothing to do
    
    # Sort items the same way as display to match button to correct item
    items.sort(key=lambda x: x.get("due_date", ""))
    
    item = items[item_index]
    item["last_completed"] = current_date
    interval = int(item.get("day_interval", 1))
    item["due_date"] = add_days_to_date(current_date, interval)
    
    data["items"] = items
    db_write(data)

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

# --- Handle button wake: mark corresponding item as completed ---
wake_button = get_wake_button()
if wake_button:
    item_index = BUTTON_TO_INDEX.get(wake_button)
    if item_index is not None:
        today = current_time.split(" ")[0]  # Extract YYYY-MM-DD from timestamp
        print(f"Button {wake_button} pressed — marking item {item_index} completed")
        mark_item_completed(item_index, today)

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

# ── Four content columns ──

# Get data for each
data = db_read()
items = data.get("items", [])
items.sort(key=lambda x: x.get("due_date", ""))  # Earliest due dates first

# Extract titles and due dates for display, pad to 4 items
displayed_items = items[:4]
today_str = current_time.split(" ")[0]  # Extract YYYY-MM-DD

# Each column is 74px wide. terminalio.FONT is 6px/char, so at scale=1
# only ~12 chars fit per column (74 / 6 = 12.3).
# Progress bar dimensions
BAR_WIDTH = 12
BAR_HEIGHT = 40
BAR_TOP = CONTENT_TOP + 38  # Below the title
BAR_BOTTOM = BAR_TOP + BAR_HEIGHT

for i in range(4):
    block_x = i * BLOCK_WIDTH

    # Vertical separator line between columns (skip the first — left edge)
    if i > 0:
        content_group.append(Line(block_x, CONTENT_TOP, block_x, USABLE_HEIGHT - 1, 0x999999))

    if i < len(displayed_items):
        item = displayed_items[i]
        title = item.get("title", "")
        due_date = item.get("due_date", "")
        due_text = format_due_date(due_date, today_str) if due_date else ""
        progress = calculate_progress(item, today_str)
        past_due = is_past_due(due_date, today_str) if due_date else False
    else:
        title = ""
        due_text = ""
        progress = 0.0
        past_due = False

    # Inverted background for past due items
    if past_due:
        content_group.append(Rect(block_x + 1, CONTENT_TOP, BLOCK_WIDTH - 2, USABLE_HEIGHT - CONTENT_TOP - 1, fill=0x000000))
        text_color = 0xFFFFFF
    else:
        text_color = 0x000000

    # Label at top of block centered horizontally
    placeholder = label.Label(
        terminalio.FONT,
        text=title,
        color=text_color,
        anchor_point=(0.5, 0.5),
        anchored_position=(block_x + BLOCK_WIDTH // 2, CONTENT_TOP + 14),
        scale=1,
    )
    content_group.append(placeholder)

    # Progress bar (outline + fill)
    if i < len(displayed_items):
        bar_x = block_x + (BLOCK_WIDTH - BAR_WIDTH) // 2
        # Outline - white for past due (inverted), black otherwise
        outline_color = 0xFFFFFF if past_due else 0x000000
        content_group.append(Rect(bar_x, BAR_TOP, BAR_WIDTH, BAR_HEIGHT, outline=outline_color))
        # Fill from bottom upward based on progress with urgency-based color
        fill_height = int(BAR_HEIGHT * progress)
        if fill_height > 0:
            fill_y = BAR_TOP + BAR_HEIGHT - fill_height
            fill_color = 0xFFFFFF if past_due else get_fill_color(progress)
            content_group.append(Rect(bar_x + 1, fill_y, BAR_WIDTH - 2, fill_height - 1, fill=fill_color))

    # Due date at bottom of block
    if due_text:
        due_label = label.Label(
            terminalio.FONT,
            text=due_text,
            color=text_color,
            anchor_point=(0.5, 1.0),
            anchored_position=(block_x + BLOCK_WIDTH // 2, USABLE_HEIGHT - 4),
            scale=1,
        )
        content_group.append(due_label)

# ── Refresh the e-ink display ──
time.sleep(display.time_to_refresh)
display.refresh()
while display.busy:
    pass

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
    # Disable WiFi radio before sleep to avoid drawing hundreds of mA.
    wifi.radio.enabled = False

    # Wake after designated time or on any button press.
    SLEEP_MINS = 240  # 4 Hours
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + (SLEEP_MINS * 60))
    button_a_alarm = alarm.pin.PinAlarm(pin=board.D15, value=False, pull=True)
    button_b_alarm = alarm.pin.PinAlarm(pin=board.D14, value=False, pull=True)
    button_c_alarm = alarm.pin.PinAlarm(pin=board.D12, value=False, pull=True)
    button_d_alarm = alarm.pin.PinAlarm(pin=board.D11, value=False, pull=True)

    print("Entering deep sleep...")
    alarm.exit_and_deep_sleep_until_alarms(
        time_alarm, button_a_alarm, button_b_alarm, button_c_alarm, button_d_alarm
    )
