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
import neopixel
import terminalio
from adafruit_display_text import label
from adafruit_display_shapes.line import Line

# --- Button-to-pin mapping ---
BUTTON_PINS = {
    "A": board.D15,
    "B": board.D14,
    "C": board.D12,
    "D": board.D11,
}

BUTTON_LABELS = {"A": "-", "B": "Mark Seen", "C": "Refresh", "D": "-"}

# --- NeoPixel setup ---
NUM_PIXELS = 4
pixels = neopixel.NeoPixel(board.NEOPIXEL, NUM_PIXELS, brightness=0.3, auto_write=False)
pixels.fill(0)
pixels.show()


def flash_blue():
    """Brief blue pulse — hint that there's an unacked message."""
    pixels.fill((0, 0, 80))
    pixels.show()
    time.sleep(0.3)
    pixels.fill(0)
    pixels.show()


def flash_green():
    pixels.fill((0, 120, 0))
    pixels.show()
    time.sleep(0.25)
    pixels.fill(0)
    pixels.show()


# --- Display setup ---
display = board.DISPLAY
DISPLAY_Y_OFFSET = 5
USABLE_HEIGHT = display.height - DISPLAY_Y_OFFSET - 5  # ~118 rows

# Layout
STATUS_BAR_HEIGHT = 14
CONTENT_TOP = STATUS_BAR_HEIGHT + 2
BUTTON_LABEL_H = 12
BUTTON_LABEL_Y = USABLE_HEIGHT - 2  # bottom-anchor
HEADER_H = 12
BODY_TOP = CONTENT_TOP + HEADER_H + 4
BODY_BOTTOM = USABLE_HEIGHT - BUTTON_LABEL_H - 4
BODY_HEIGHT = BODY_BOTTOM - BODY_TOP
BODY_LEFT = 2
BODY_RIGHT = display.width - 2
BODY_WIDTH = BODY_RIGHT - BODY_LEFT

# --- Persistent state ---
DATA_PATH = "/data.json"

def db_read():
    try:
        with open(DATA_PATH, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_seen_ts": "", "current_ts": ""}

db_write_error = None  # populated if last db_write failed

def db_write(data):
    global db_write_error
    try:
        with open(DATA_PATH, "w") as f:
            json.dump(data, f)
        db_write_error = None
        return True
    except OSError as e:
        db_write_error = str(e)
        print(f"db_write failed: {e}")
        return False


def get_wake_button():
    wake_alarm = alarm.wake_alarm
    if wake_alarm is None or not isinstance(wake_alarm, alarm.pin.PinAlarm):
        return None
    for name, pin in BUTTON_PINS.items():
        if wake_alarm.pin == pin:
            return name
    return None


# --- Time helpers ---
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_iso(s):
    """Parse 'YYYY-MM-DDTHH:MM:SS[.fff][Z]' or 'YYYY-MM-DD HH:MM:SS' → tuple."""
    s = s.replace("T", " ").replace("Z", "")
    if "." in s:
        s = s.split(".")[0]
    date_part, time_part = s.split(" ")
    y, mo, d = [int(x) for x in date_part.split("-")]
    h, mi, sec = [int(x) for x in time_part.split(":")]
    return (y, mo, d, h, mi, sec)


def to_epoch(t):
    """time.mktime treats input as local; we use it consistently to compute deltas."""
    return time.mktime((t[0], t[1], t[2], t[3], t[4], t[5], 0, 0, -1))


def format_readable(t):
    """(y,m,d,h,m,s) → 'May 23, 3:25 PM' (matches adafruit IO %b %e, %l:%M %p style)."""
    h = t[3]
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{MONTHS[t[1]-1]} {t[2]}, {h12}:{t[4]:02d} {ampm}"


# --- Battery ---
LIPO_CURVE = [
    (4.20, 100), (4.15, 95), (4.10, 90), (4.05, 85),
    (4.00, 80),  (3.90, 70), (3.80, 60), (3.70, 50),
    (3.60, 40),  (3.50, 30), (3.40, 20), (3.30, 10),
    (3.20, 5),   (3.00, 0),
]

def voltage_to_percent(v):
    if v >= LIPO_CURVE[0][0]:
        return 100
    if v <= LIPO_CURVE[-1][0]:
        return 0
    for i in range(len(LIPO_CURVE) - 1):
        v_hi, p_hi = LIPO_CURVE[i]
        v_lo, p_lo = LIPO_CURVE[i + 1]
        if v >= v_lo:
            return p_lo + (p_hi - p_lo) * (v - v_lo) / (v_hi - v_lo)
    return 0

try:
    vbat = analogio.AnalogIn(board.VOLTAGE_MONITOR)
    battery_voltage = (vbat.value / 65535.0) * 3.3 * 2
    vbat.deinit()
    battery_percent = voltage_to_percent(battery_voltage)
except Exception:
    battery_voltage = 0.0
    battery_percent = 0


# --- Word wrap and dynamic scale ---
def wrap_text(text, max_chars):
    if max_chars < 1:
        return [text]
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            # Word longer than max_chars on its own — hard-break
            while len(word) > max_chars:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:max_chars])
                word = word[max_chars:]
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars:
                current = current + " " + word
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def choose_scale(body, max_width, max_height):
    for scale in (4, 3, 2, 1):
        chars_per_line = max_width // (6 * scale)
        if chars_per_line < 1:
            continue
        lines = wrap_text(body, chars_per_line)
        total_h = len(lines) * 12 * scale
        if total_h <= max_height:
            return scale, lines
    # Fallback: scale 1, truncate to fit
    chars_per_line = max(1, max_width // 6)
    max_lines = max(1, max_height // 12)
    lines = wrap_text(body, chars_per_line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, chars_per_line - 1)] + "..."
    return 1, lines


# --- Wake handling: identify button early for fast feedback ---
wake_button = get_wake_button()
if wake_button:
    print(f"Button {wake_button} pressed")


# --- WiFi ---
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")
timezone = os.getenv("TIMEZONE")

MSG_API_URL = os.getenv("MSG_API_URL")
MSG_ACK_URL = os.getenv("MSG_ACK_URL")
MSG_API_TOKEN = os.getenv("MSG_API_TOKEN")

TIME_URL = (
    f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime"
    f"?x-aio-key={aio_key}&tz={timezone}"
    "&fmt=%25Y-%25m-%25d+%25H%3A%25M%3A%25S"
)
TIME_URL_READABLE = (
    f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime"
    f"?x-aio-key={aio_key}&tz={timezone}"
    "&fmt=%25b+%25e,+%25l:%25M+%25p"
)

print("Connecting to", ssid)
wifi.radio.connect(ssid, password)
pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

current_date_time = requests.get(TIME_URL).text.strip()
current_readable_time = requests.get(TIME_URL_READABLE).text.strip()
print("Local now:", current_date_time)


# --- API helpers ---
auth_headers = {"Authorization": f"Bearer {MSG_API_TOKEN}"} if MSG_API_TOKEN else {}


def fetch_messages(since_ts):
    if not MSG_API_URL:
        return None, None
    url = MSG_API_URL
    sep = "&" if "?" in url else "?"
    if since_ts:
        url = f"{url}{sep}since={since_ts}&limit=10"
    else:
        url = f"{url}{sep}limit=10"
    try:
        r = requests.get(url, headers=auth_headers)
        if r.status_code != 200:
            print(f"GET /messages: HTTP {r.status_code}")
            r.close()
            return None, None
        data = r.json()
        r.close()
        return data.get("messages", []), data.get("now")
    except Exception as e:
        print(f"GET /messages failed: {e}")
        return None, None


def ack_messages(up_to_ts):
    """Returns (status_str, acked_count). status: 'ok'|'noop'|'http<NNN>'|'noconfig'|'err'."""
    if not MSG_ACK_URL:
        return ("noconfig", 0)
    if not up_to_ts:
        return ("noconfig", 0)
    try:
        r = requests.post(
            MSG_ACK_URL,
            json={"up_to_ts": up_to_ts},
            headers=auth_headers,
        )
        code = r.status_code
        acked = 0
        try:
            body = r.json()
            acked = int(body.get("acked", 0))
            print(f"Ack response: {body}")
        except Exception:
            pass
        r.close()
        if code != 200:
            return (f"http{code}", acked)
        if acked == 0:
            return ("noop", 0)
        return ("ok", acked)
    except Exception as e:
        print(f"POST ack failed: {e}")
        return ("err", 0)


# --- State + button action ---
state = db_read()
last_seen_ts = state.get("last_seen_ts", "") or ""
current_ts = state.get("current_ts", "") or ""

ack_status = None  # None | (code, n)  e.g. ('ok',1) ('noop',0) ('http404',0) ('err',0)

if wake_button == "B":
    if current_ts:
        print(f"Acking up to {current_ts}")
        result = ack_messages(current_ts)
        ack_status = result
        if result[0] == "ok":
            last_seen_ts = current_ts
            current_ts = ""
            state["last_seen_ts"] = last_seen_ts
            state["current_ts"] = current_ts
            db_write(state)
    else:
        ack_status = ("noconfig", 0)

# Always poll on every wake
messages, server_now = fetch_messages(last_seen_ts)
if messages is None:
    messages = []

current_msg = messages[0] if messages else None
new_current_ts = current_msg["ts"] if current_msg else ""
if new_current_ts != current_ts:
    current_ts = new_current_ts
    state["current_ts"] = current_ts
    db_write(state)


# --- Compute local-time offset for formatting message ts ---
offset_sec = 0
try:
    local_t = parse_iso(current_date_time)
    if server_now:
        utc_t = parse_iso(server_now)
        offset_sec = to_epoch(local_t) - to_epoch(utc_t)
except Exception as e:
    print(f"Time offset calc failed: {e}")


def format_msg_when(iso_ts):
    try:
        utc_t = parse_iso(iso_ts)
        local_epoch = to_epoch(utc_t) + offset_sec
        lt = time.localtime(local_epoch)
        return format_readable((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                lt.tm_hour, lt.tm_min, lt.tm_sec))
    except Exception:
        return iso_ts


# --- Build display ---
main_group = displayio.Group()
bg_bitmap = displayio.Bitmap(display.width, display.height, 1)
bg_palette = displayio.Palette(1)
bg_palette[0] = 0xFFFFFF
main_group.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette, x=0, y=0))

content_group = displayio.Group(y=DISPLAY_Y_OFFSET)
main_group.append(content_group)

# Status bar: refresh time (left), battery (right), separator below
content_group.append(label.Label(
    terminalio.FONT,
    text=f"Refreshed: {current_readable_time}",
    color=0x000000,
    anchor_point=(0.0, 0.5),
    anchored_position=(2, STATUS_BAR_HEIGHT // 2),
    scale=1,
))
content_group.append(label.Label(
    terminalio.FONT,
    text=f"{battery_percent:.0f}%",
    color=0x000000,
    anchor_point=(1.0, 0.5),
    anchored_position=(display.width - 2, STATUS_BAR_HEIGHT // 2),
    scale=1,
))
content_group.append(Line(0, STATUS_BAR_HEIGHT, display.width - 1, STATUS_BAR_HEIGHT, 0x000000))

# Debug line (only shown when ack happened or persistence failed).
debug_bits = []
if ack_status is not None:
    code, n = ack_status
    debug_bits.append(f"Ack {code} n={n}")
if db_write_error is not None:
    debug_bits.append("WRITE-FAIL")
debug_height = 0
if debug_bits:
    ls_tail = (last_seen_ts or "")[-8:] or "-"
    ct_tail = (current_ts or "")[-8:] or "-"
    debug_bits.append(f"ls={ls_tail} ct={ct_tail}")
    content_group.append(label.Label(
        terminalio.FONT,
        text="  ".join(debug_bits),
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(2, STATUS_BAR_HEIGHT + 1),
        scale=1,
    ))
    debug_height = 12  # shift body content down to avoid overlap

# Shift the body region down by the debug-line height when present.
header_y = CONTENT_TOP + debug_height
body_top_eff = BODY_TOP + debug_height
body_height_eff = BODY_HEIGHT - debug_height

# Body: header line + dynamically-scaled message body, OR empty-state.
if current_msg:
    sender = current_msg.get("from", "")
    when = format_msg_when(current_msg.get("ts", ""))
    body_text = current_msg.get("body", "")

    header_text = f"from {sender} - {when}"
    # Truncate header if it overflows the screen width at scale=1.
    max_header_chars = display.width // 6
    if len(header_text) > max_header_chars:
        header_text = header_text[: max_header_chars - 1] + "..."

    content_group.append(label.Label(
        terminalio.FONT,
        text=header_text,
        color=0x000000,
        anchor_point=(0.0, 0.0),
        anchored_position=(BODY_LEFT, CONTENT_TOP),
        scale=1,
    ))

    scale, lines = choose_scale(body_text, BODY_WIDTH, BODY_HEIGHT)
    glyph_h = 12 * scale
    block_h = len(lines) * glyph_h
    start_y = BODY_TOP + max(0, (BODY_HEIGHT - block_h) // 2)
    for i, line in enumerate(lines):
        content_group.append(label.Label(
            terminalio.FONT,
            text=line,
            color=0x000000,
            anchor_point=(0.5, 0.0),
            anchored_position=(display.width // 2, start_y + i * glyph_h),
            scale=scale,
        ))
else:
    msg = "No new messages"
    if ack_status is not None and ack_status[0] not in ("ok",):
        msg = f"Ack: {ack_status[0]} (n={ack_status[1]})"
    content_group.append(label.Label(
        terminalio.FONT,
        text=msg,
        color=0x000000,
        anchor_point=(0.5, 0.5),
        anchored_position=(display.width // 2, (BODY_TOP + BODY_BOTTOM) // 2),
        scale=2,
    ))

# Button labels along the bottom — one per physical button.
btn_order = ["A", "B", "C", "D"]
col_w = display.width // 4
content_group.append(Line(0, BUTTON_LABEL_Y - BUTTON_LABEL_H - 2,
                          display.width - 1, BUTTON_LABEL_Y - BUTTON_LABEL_H - 2,
                          0x999999))
for i, name in enumerate(btn_order):
    text = BUTTON_LABELS[name]
    if name == "B" and not current_msg:
        text = "-"  # nothing to ack
    content_group.append(label.Label(
        terminalio.FONT,
        text=text,
        color=0x000000,
        anchor_point=(0.5, 1.0),
        anchored_position=(i * col_w + col_w // 2, BUTTON_LABEL_Y),
        scale=1,
    ))

# Refresh
display.root_group = main_group
time.sleep(display.time_to_refresh)
display.refresh()

# NeoPixel feedback while the panel refreshes.
if ack_status is not None:
    code = ack_status[0]
    if code == "ok":
        flash_green()
    elif code == "noop":
        pixels.fill((120, 100, 0))  # amber: HTTP 200 but acked=0 → backend matched nothing
        pixels.show()
        time.sleep(0.4)
        pixels.fill(0); pixels.show()
    else:
        pixels.fill((150, 0, 0))    # red: HTTP error / network / no config
        pixels.show()
        time.sleep(0.4)
        pixels.fill(0); pixels.show()
elif current_msg:
    flash_blue()

while display.busy:
    pass


# --- Dev mode escape hatch ---
# Only treat held Button A as "stay in REPL" on a true reset, not on a
# deep-sleep wake (otherwise pressing A to wake would always drop into REPL).
btn_a = digitalio.DigitalInOut(board.D15)
btn_a.direction = digitalio.Direction.INPUT
btn_a.pull = digitalio.Pull.UP
dev_skip_sleep = (not btn_a.value) and (alarm.wake_alarm is None)
btn_a.deinit()
if dev_skip_sleep:
    print("Dev mode — skipping deep sleep. REPL active.")
else:
    wifi.radio.enabled = False
    pixels.deinit()

    SLEEP_MINS = 30
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + (SLEEP_MINS * 60))
    button_a_alarm = alarm.pin.PinAlarm(pin=board.D15, value=False, pull=True)
    button_b_alarm = alarm.pin.PinAlarm(pin=board.D14, value=False, pull=True)
    button_c_alarm = alarm.pin.PinAlarm(pin=board.D12, value=False, pull=True)
    button_d_alarm = alarm.pin.PinAlarm(pin=board.D11, value=False, pull=True)

    print("Entering deep sleep...")
    alarm.exit_and_deep_sleep_until_alarms(
        time_alarm, button_a_alarm, button_b_alarm, button_c_alarm, button_d_alarm
    )
