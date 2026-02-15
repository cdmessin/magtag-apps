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
# the top ~5 pixels of RAM fall outside the visible area. Shift all content
# down to compensate. The bottom ~5 rows show noise from uninitialized RAM.
DISPLAY_Y_OFFSET = 5
USABLE_HEIGHT = display.height - DISPLAY_Y_OFFSET - 5  # ~118 usable rows

content_group = displayio.Group(y=DISPLAY_Y_OFFSET)
main_group.append(content_group)
display.root_group = main_group

# --- Layout constants ---
STATUS_BAR_HEIGHT = 14
CONTENT_TOP = STATUS_BAR_HEIGHT + 2

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
response.close()
print("Current time:", current_time)

# --- US Eastern timezone helpers ---


def day_of_week(y, m, d):
    """Sakamoto's algorithm. Returns 0=Sunday, 1=Mon, ..., 6=Sat."""
    t = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4]
    if m < 3:
        y -= 1
    return (y + y // 4 - y // 100 + y // 400 + t[m - 1] + d) % 7


def eastern_utc_offset(year, month, day):
    """Return UTC offset for US Eastern time (-4 for EDT, -5 for EST).
    DST runs from the second Sunday of March to the first Sunday of November."""
    if month < 3 or month > 11:
        return -5
    if 3 < month < 11:
        return -4
    if month == 3:
        # Second Sunday of March: first Sunday on or after the 8th
        dow_8 = day_of_week(year, 3, 8)
        second_sun = 8 + (7 - dow_8) % 7
        return -4 if day >= second_sun else -5
    # November: first Sunday on or after the 1st
    dow_1 = day_of_week(year, 11, 1)
    first_sun = 1 + (7 - dow_1) % 7
    return -5 if day >= first_sun else -4


def utc_to_eastern(y, m, d, h):
    """Shift a UTC hour to US Eastern, rolling the date if needed."""
    offset = eastern_utc_offset(y, m, d)
    h += offset
    if h < 0:
        h += 24
        d -= 1
        if d < 1:
            m -= 1
            if m < 1:
                m = 12
                y -= 1
            dim = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
            if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0):
                dim[2] = 29
            d = dim[m]
    return y, m, d, h


# --- Fetch RSVP data from wedding website GraphQL API ---
RSVP_API_URL = os.getenv("RSVP_API_URL")
RSVP_API_KEY = os.getenv("RSVP_API_KEY")

GRAPHQL_QUERY = (
    "{ "
    "listGuests(limit: 1000) { items { guestCount isVendor } } "
    "listRSVPs(limit: 1000) { items { guestName numberOfGuests attending createdAt } } "
    "}"
)

total_invited = 0
attending_count = 0
last_rsvp_name = "N/A"
last_rsvp_date = ""
api_error = False

try:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": RSVP_API_KEY,
    }
    payload = json.dumps({"query": GRAPHQL_QUERY})
    response = requests.post(RSVP_API_URL, data=payload, headers=headers)
    data = response.json()
    response.close()

    # Parse guests for total invited count (exclude vendors)
    guests = data.get("data", {}).get("listGuests", {}).get("items", [])
    for guest in guests:
        if not guest.get("isVendor", False):
            total_invited += guest.get("guestCount", 0)

    # Parse RSVPs for attending count and most recent RSVP
    rsvps = data.get("data", {}).get("listRSVPs", {}).get("items", [])
    latest_rsvp = None
    for rsvp in rsvps:
        if rsvp.get("attending", False):
            attending_count += rsvp.get("numberOfGuests", 0)
        # Track most recent RSVP by createdAt (ISO 8601 sorts lexically)
        created = rsvp.get("createdAt", "")
        if latest_rsvp is None or created > latest_rsvp.get("createdAt", ""):
            latest_rsvp = rsvp

    if latest_rsvp:
        last_rsvp_name = latest_rsvp.get("guestName", "Unknown")
        raw_date = latest_rsvp.get("createdAt", "")
        # Format ISO 8601 "2026-01-15T15:45:30.123Z" -> "1/15 3:45 PM ET"
        if raw_date and "T" in raw_date:
            date_part = raw_date.split("T")[0]
            time_part = raw_date.split("T")[1].split(".")[0].split("Z")[0]
            yi, mi_d, di = (int(x) for x in date_part.split("-"))
            last_rsvp_date = f"{mi_d}/{di}"
            if time_part:
                hi, mi_t = (int(x) for x in time_part.split(":")[:2])
                yi, mi_d, di, hi = utc_to_eastern(yi, mi_d, di, hi)
                last_rsvp_date = f"{mi_d}/{di}"
                ampm = "AM" if hi < 12 else "PM"
                if hi == 0:
                    hi = 12
                elif hi > 12:
                    hi -= 12
                last_rsvp_date += f" {hi}:{mi_t:02d} {ampm} ET"

    print(f"Attending: {attending_count}/{total_invited}")
    print(f"Last RSVP: {last_rsvp_name} on {last_rsvp_date}")
except Exception as e:
    print(f"API error: {e}")
    api_error = True

# --- Build the display ---

# ── Status bar: refresh time on left, battery on right ──
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
content_group.append(
    Line(0, STATUS_BAR_HEIGHT, display.width - 1, STATUS_BAR_HEIGHT, 0x000000)
)

# ── Main content: RSVP count (large) ──
count_text = f"{attending_count} / {total_invited}"
count_label = label.Label(
    terminalio.FONT,
    text=count_text,
    color=0x000000,
    anchor_point=(0.5, 0.5),
    anchored_position=(display.width // 2, CONTENT_TOP + 26),
    scale=3,
)
content_group.append(count_label)

# Subtitle below the count
subtitle_label = label.Label(
    terminalio.FONT,
    text="guests attending",
    color=0x000000,
    anchor_point=(0.5, 0.0),
    anchored_position=(display.width // 2, CONTENT_TOP + 42),
    scale=1,
)
content_group.append(subtitle_label)

# ── Thin separator ──
sep_y = CONTENT_TOP + 54
content_group.append(Line(40, sep_y, display.width - 41, sep_y, 0x999999))

# ── Last RSVP info ──
if api_error:
    error_label = label.Label(
        terminalio.FONT,
        text="(API error - check settings)",
        color=0x000000,
        anchor_point=(0.5, 0.0),
        anchored_position=(display.width // 2, sep_y + 8),
        scale=1,
    )
    content_group.append(error_label)
else:
    last_rsvp_text = f"Last RSVP: {last_rsvp_name}"
    # Truncate if too long (296px / 6px per char at scale=1 = ~49 chars max)
    if len(last_rsvp_text) > 49:
        last_rsvp_text = last_rsvp_text[:46] + "..."

    last_rsvp_label = label.Label(
        terminalio.FONT,
        text=last_rsvp_text,
        color=0x000000,
        anchor_point=(0.5, 0.0),
        anchored_position=(display.width // 2, sep_y + 6),
        scale=1,
    )
    content_group.append(last_rsvp_label)

    if last_rsvp_date:
        date_label = label.Label(
            terminalio.FONT,
            text=last_rsvp_date,
            color=0x000000,
            anchor_point=(0.5, 0.0),
            anchored_position=(display.width // 2, sep_y + 18),
            scale=1,
        )
        content_group.append(date_label)

# ── Refresh the e-ink display ──
time.sleep(display.time_to_refresh)
display.refresh()
while display.busy:
    pass

# --- Dev mode escape hatch ---
btn_a = digitalio.DigitalInOut(board.D15)
btn_a.direction = digitalio.Direction.INPUT
btn_a.pull = digitalio.Pull.UP
if not btn_a.value:  # Button A held (active low) — dev mode
    btn_a.deinit()
    print("Dev mode — skipping deep sleep. USB writable, REPL active.")
else:
    btn_a.deinit()

    # Disable WiFi before sleep to save power
    wifi.radio.enabled = False

    # Wake every hour or on any button press for manual refresh
    SLEEP_MINS = 60
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
