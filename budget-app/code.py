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
# the top ~5 pixels of RAM fall outside the visible area. Shift all content
# down to compensate. The bottom ~5 rows show noise from uninitialized RAM.
DISPLAY_Y_OFFSET = 5
USABLE_HEIGHT = display.height - DISPLAY_Y_OFFSET - 5  # ~118 usable rows

content_group = displayio.Group(y=DISPLAY_Y_OFFSET)
main_group.append(content_group)
display.root_group = main_group

# --- Layout constants ---
STATUS_BAR_HEIGHT = 14
BAR_LEFT = 4  # Left margin for progress bars
BAR_RIGHT = 4  # Right margin for progress bars
BAR_WIDTH = display.width - BAR_LEFT - BAR_RIGHT  # Full width minus margins

# --- Battery monitoring ---
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
            return p_low + (p_high - p_low) * (voltage - v_low) / (v_high - v_low)
    return 0


try:
    vbat_voltage_pin = analogio.AnalogIn(board.VOLTAGE_MONITOR)
    battery_voltage = (vbat_voltage_pin.value / 65535.0) * 3.3 * 2
    vbat_voltage_pin.deinit()
    battery_percent = voltage_to_percent(battery_voltage)
except Exception:
    battery_voltage = 0.0
    battery_percent = 0


# --- Helpers ---
def format_dollars(cents_or_dollars, is_milliunits=False):
    """Format a dollar amount compactly. Input is dollars (float/int).
    Returns e.g. '$1,234' or '$12k' for large amounts."""
    val = int(cents_or_dollars)
    if val > 9999:
        return "$" + str(val // 1000) + "k"
    # Manual comma formatting since CircuitPython may not support f"{val:,}"
    s = str(val)
    if len(s) > 3:
        s = s[:-3] + "," + s[-3:]
    return "$" + s


def days_in_month(year, month):
    """Return number of days in the given month."""
    dim = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
        return 29
    return dim[month]


# --- Connect to WiFi & fetch time ---
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")
timezone = os.getenv("TIMEZONE")

# Machine-readable time for pace calculation
TIME_URL = (
    f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime"
    f"?x-aio-key={aio_key}&tz={timezone}"
    "&fmt=%25Y-%25m-%25d"
)
# Human-readable time for status bar
TIME_URL_READABLE = (
    f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime"
    f"?x-aio-key={aio_key}&tz={timezone}"
    "&fmt=%25b+%25e,+%25l:%25M+%25p"
)

print("Connecting to", ssid)
wifi.radio.connect(ssid, password)
print(f"Connected to {ssid}!")

pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

response = requests.get(TIME_URL)
current_date = response.text.strip()  # "YYYY-MM-DD"
response.close()
print("Current date:", current_date)

response = requests.get(TIME_URL_READABLE)
current_time = response.text.strip()  # "Feb 15,  3:30 PM"
response.close()
print("Current time:", current_time)

# Parse date components for pace calculation
date_parts = current_date.split("-")
cur_year = int(date_parts[0])
cur_month = int(date_parts[1])
cur_day = int(date_parts[2])
total_days = days_in_month(cur_year, cur_month)
month_pct = cur_day / total_days  # 0.0 to 1.0

# --- Fetch YNAB budget data ---
YNAB_API_TOKEN = os.getenv("YNAB_API_TOKEN")
YNAB_BUDGET_ID = os.getenv("YNAB_BUDGET_ID")

EXCLUDED_GROUPS = ["Internal Master Category", "Credit Card Payments", "Reimbursable/Refund", "Brokerage - Transfer"]

# Specific categories to display (in order)
DISPLAY_CATEGORY_NAMES = [
    "Home Goods 🏠",
    "Eating Out 🌯",
    "Dates 👩‍❤️‍👨, Fun 🎉, and Wants",
    "Pet Supplies 🦴",
]

total_budgeted = 0
total_spent = 0
display_categories = []
api_error = False

try:
    ynab_url = f"https://api.ynab.com/v1/budgets/{YNAB_BUDGET_ID}/months/current"
    headers = {"Authorization": f"Bearer {YNAB_API_TOKEN}"}
    print(f"Fetching YNAB data...")
    response = requests.get(ynab_url, headers=headers)

    if response.status_code != 200:
        print(f"YNAB API error: HTTP {response.status_code}")
        api_error = True
        response.close()
    else:
        data = response.json()
        response.close()

        month_data = data.get("data", {}).get("month", {})
        categories = month_data.get("categories", [])

        # Process categories
        for cat in categories:
            # Skip hidden, deleted, excluded groups
            if cat.get("hidden", False) or cat.get("deleted", False):
                continue
            if cat.get("category_group_name", "") in EXCLUDED_GROUPS:
                continue

            budgeted = cat.get("budgeted", 0)  # milliunits
            activity = cat.get("activity", 0)  # milliunits (negative = spending)
            balance = cat.get("balance", 0)  # milliunits

            # Skip categories with no budget
            if budgeted == 0:
                continue

            spent = abs(activity)

            # Add to totals (include zero-activity categories for pace)
            total_budgeted += budgeted
            total_spent += spent

            # Only include in display list if it's one of our target categories
            if cat.get("name", "") not in DISPLAY_CATEGORY_NAMES:
                continue

            pct_spent = spent / budgeted if budgeted > 0 else 1.0
            display_categories.append({
                "name": cat.get("name", "Unknown"),
                "budgeted": budgeted,
                "spent": spent,
                "balance": balance,
                "pct_spent": pct_spent,
            })

        # Sort by the order in DISPLAY_CATEGORY_NAMES
        name_order = {name: i for i, name in enumerate(DISPLAY_CATEGORY_NAMES)}
        display_categories.sort(key=lambda c: name_order.get(c["name"], 999))

        # Convert totals to dollars
        total_budgeted_dollars = total_budgeted / 1000
        total_spent_dollars = total_spent / 1000

        print(f"Budget: spent ${total_spent_dollars:.0f} of ${total_budgeted_dollars:.0f}")
        print(f"Categories to display: {len(display_categories)}")

except Exception as e:
    print(f"API error: {e}")
    api_error = True

# --- Calculate pace ---
if not api_error and total_budgeted > 0:
    spent_pct = total_spent / total_budgeted
    if total_spent > total_budgeted:
        pace_label_text = "OVER"
    elif spent_pct > month_pct + 0.05:
        pace_label_text = "AHEAD"
    else:
        pace_label_text = "ON PACE"
else:
    spent_pct = 0.0
    pace_label_text = ""

# --- Build the display ---

# -- Status bar: time (left), battery (right) --
status_time_label = label.Label(
    terminalio.FONT,
    text=current_time,
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
content_group.append(
    Line(0, STATUS_BAR_HEIGHT, display.width - 1, STATUS_BAR_HEIGHT, 0x000000)
)

if api_error:
    # -- Error fallback --
    error_label = label.Label(
        terminalio.FONT,
        text="(API error - check settings)",
        color=0x000000,
        anchor_point=(0.5, 0.5),
        anchored_position=(display.width // 2, 70),
        scale=1,
    )
    content_group.append(error_label)

elif total_budgeted == 0:
    # -- No budget data fallback --
    no_data_label = label.Label(
        terminalio.FONT,
        text="No budget data",
        color=0x000000,
        anchor_point=(0.5, 0.5),
        anchored_position=(display.width // 2, 70),
        scale=1,
    )
    content_group.append(no_data_label)

else:
    # -- Summary line (y=16): "Spent $X of $Y" left, pace label right --
    spent_str = format_dollars(total_spent_dollars)
    budget_str = format_dollars(total_budgeted_dollars)
    summary_text = f"Spent {spent_str} of {budget_str}"

    summary_label = label.Label(
        terminalio.FONT,
        text=summary_text,
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(4, 16),
        scale=1,
    )
    content_group.append(summary_label)

    pace_label = label.Label(
        terminalio.FONT,
        text=pace_label_text,
        color=0x000000,
        anchor_point=(1.0, 0.0),
        anchored_position=(display.width - 4, 16),
        scale=1,
    )
    content_group.append(pace_label)

    # -- Pace bar (y=26, 10px tall) --
    PACE_BAR_Y = 26
    PACE_BAR_HEIGHT = 10

    # Bar outline
    content_group.append(
        Rect(BAR_LEFT, PACE_BAR_Y, BAR_WIDTH, PACE_BAR_HEIGHT, outline=0x000000)
    )

    # Fill based on spending percentage (clamped to 100%)
    fill_pct = min(spent_pct, 1.0)
    fill_width = int(BAR_WIDTH * fill_pct)
    if fill_width >= 3:
        # Color based on pace status
        if total_spent > total_budgeted:
            fill_color = 0x000000  # Black when over budget
        elif spent_pct > month_pct + 0.05:
            fill_color = 0x666666  # Dark gray when ahead
        else:
            fill_color = 0xAAAAAA  # Light gray when on pace
        content_group.append(
            Rect(BAR_LEFT + 1, PACE_BAR_Y + 1, fill_width - 2, PACE_BAR_HEIGHT - 2, fill=fill_color)
        )

    # Day marker line (vertical line at current day position)
    marker_x = BAR_LEFT + int(BAR_WIDTH * month_pct)
    # Clamp marker within bar bounds
    marker_x = max(BAR_LEFT + 1, min(marker_x, BAR_LEFT + BAR_WIDTH - 2))
    content_group.append(
        Line(marker_x, PACE_BAR_Y, marker_x, PACE_BAR_Y + PACE_BAR_HEIGHT - 1, 0x000000)
    )

    # -- Pace detail text (y=38): "53% spent, 50% of month" --
    spent_pct_display = int(spent_pct * 100)
    month_pct_display = int(month_pct * 100)
    detail_text = f"{spent_pct_display}% spent, {month_pct_display}% of month"

    detail_label = label.Label(
        terminalio.FONT,
        text=detail_text,
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(4, 38),
        scale=1,
    )
    content_group.append(detail_label)

    # Separator before categories
    content_group.append(
        Line(0, 48, display.width - 1, 48, 0x000000)
    )

    # -- Category rows (up to 4) --
    # Each row: 17px total = 1px pad + name/amount text (8px) + 1px gap + bar (5px) + 2px pad
    CAT_ROW_HEIGHT = 17
    CAT_BAR_HEIGHT = 5
    CAT_START_Y = 50

    for i, cat in enumerate(display_categories):
        row_y = CAT_START_Y + i * CAT_ROW_HEIGHT

        # Category name (left)
        name = cat["name"]
        # Truncate name if needed (leave room for amount on right)
        if len(name) > 20:
            name = name[:18] + ".."

        name_label = label.Label(
            terminalio.FONT,
            text=name,
            color=0x000000,
            anchor_point=(0.0, 0.0),
            anchored_position=(4, row_y + 1),
            scale=1,
        )
        content_group.append(name_label)

        # Amount remaining (right) - use balance from YNAB
        balance_dollars = cat["balance"] / 1000
        if balance_dollars < 0:
            amt_text = format_dollars(abs(balance_dollars)) + " over"
        else:
            amt_text = format_dollars(balance_dollars) + " left"

        amt_label = label.Label(
            terminalio.FONT,
            text=amt_text,
            color=0x000000,
            anchor_point=(1.0, 0.0),
            anchored_position=(display.width - 4, row_y + 1),
            scale=1,
        )
        content_group.append(amt_label)

        # Spending bar
        bar_y = row_y + 10  # Below the text line
        pct = min(cat["pct_spent"], 1.0)  # Clamp to 100%
        bar_fill_width = int(BAR_WIDTH * pct)

        # Bar outline
        content_group.append(
            Rect(BAR_LEFT, bar_y, BAR_WIDTH, CAT_BAR_HEIGHT, outline=0x999999)
        )

        # Fill with urgency-based color
        if bar_fill_width >= 3:
            if cat["pct_spent"] > 0.9:
                cat_fill_color = 0x000000  # Black > 90%
            elif cat["pct_spent"] > 0.7:
                cat_fill_color = 0x666666  # Dark gray 70-90%
            else:
                cat_fill_color = 0xAAAAAA  # Light gray < 70%
            content_group.append(
                Rect(BAR_LEFT + 1, bar_y + 1, bar_fill_width - 2, CAT_BAR_HEIGHT - 2, fill=cat_fill_color)
            )


# --- Refresh the e-ink display ---
time.sleep(display.time_to_refresh)
display.refresh()
while display.busy:
    pass

# --- Dev mode escape hatch ---
btn_a = digitalio.DigitalInOut(board.D15)
btn_a.direction = digitalio.Direction.INPUT
btn_a.pull = digitalio.Pull.UP
if not btn_a.value:  # Button A held (active low) -- dev mode
    btn_a.deinit()
    print("Dev mode -- skipping deep sleep. USB writable, REPL active.")
else:
    btn_a.deinit()

    # Disable WiFi before sleep to save power
    wifi.radio.enabled = False

    # Wake every 4 hours or on any button press for manual refresh
    SLEEP_MINS = 240  # 4 hours
    time_alarm = alarm.time.TimeAlarm(
        monotonic_time=time.monotonic() + (SLEEP_MINS * 60)
    )
    button_a_alarm = alarm.pin.PinAlarm(pin=board.D15, value=False, pull=True)
    button_b_alarm = alarm.pin.PinAlarm(pin=board.D14, value=False, pull=True)
    button_c_alarm = alarm.pin.PinAlarm(pin=board.D12, value=False, pull=True)
    button_d_alarm = alarm.pin.PinAlarm(pin=board.D11, value=False, pull=True)

    print("Entering deep sleep...")
    alarm.exit_and_deep_sleep_until_alarms(
        time_alarm, button_a_alarm, button_b_alarm, button_c_alarm, button_d_alarm
    )
