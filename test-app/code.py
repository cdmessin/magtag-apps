import ipaddress
import os
import ssl
import time
import alarm
import wifi
import socketpool
import adafruit_requests
import board
import displayio
import terminalio
from adafruit_display_text import label

# Check what woke us up from deep sleep
wake_alarm = alarm.wake_alarm
wake_reason = "Fresh boot"

if wake_alarm:
    if isinstance(wake_alarm, alarm.time.TimeAlarm):
        wake_reason = "Timer"
        print("Woke from timer")
    elif isinstance(wake_alarm, alarm.pin.PinAlarm):
        if wake_alarm.pin == board.D15:
            wake_reason = "Button A (D15)"
            print("Woke from Button A")
        elif wake_alarm.pin == board.D14:
            wake_reason = "Button B (D14)"
            print("Woke from Button B")
        elif wake_alarm.pin == board.D12:
            wake_reason = "Button C (D12)"
            print("Woke from Button C")
        elif wake_alarm.pin == board.D11:
            wake_reason = "Button D (D11)"
            print("Woke from Button D")
else:
    print("Fresh boot (power on or reset)")

# Take over the display immediately before any print statements
# Setting root_group to our own group prevents terminal output on display
# The status bar (if enabled) will still show at the top
display = board.DISPLAY
group = displayio.Group()

# Create a white background
background_bitmap = displayio.Bitmap(display.width, display.height, 1)
background_palette = displayio.Palette(1)
background_palette[0] = 0xFFFFFF  # White
background_sprite = displayio.TileGrid(background_bitmap, pixel_shader=background_palette, x=0, y=0)
group.append(background_sprite)
display.root_group = group

# Get our username, key and desired timezone
ssid = os.getenv("CIRCUITPY_WIFI_SSID")
password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
aio_username = os.getenv("ADAFRUIT_AIO_USERNAME")
aio_key = os.getenv("ADAFRUIT_AIO_KEY")
timezone = os.getenv("TIMEZONE")
TIME_URL = f"https://io.adafruit.com/api/v2/{aio_username}/integrations/time/strftime?x-aio-key={aio_key}&tz={timezone}"
TIME_URL += "&fmt=%25Y-%25m-%25d+%25H%3A%25M%3A%25S.%25L+%25j+%25u+%25z+%25Z"

print("ESP32-S2 Adafruit IO Time test")

print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

print("Available WiFi networks:")
for network in wifi.radio.start_scanning_networks():
    print("\t%s\t\tRSSI: %d\tChannel: %d" % (str(network.ssid, "utf-8"),
            network.rssi, network.channel))
wifi.radio.stop_scanning_networks()

print("Connecting to", ssid)
wifi.radio.connect(ssid, password)
print(f"Connected to {ssid}!")
print("My IP address is", wifi.radio.ipv4_address)

# Add IP address and wake reason to display
ip_text = label.Label(
    terminalio.FONT,
    text=f"IP: {wifi.radio.ipv4_address}",
    color=0x000000,
    x=10,
    y=display.height // 3,
    scale=2
)
group.append(ip_text)

wake_text = label.Label(
    terminalio.FONT,
    text=f"Wake: {wake_reason}",
    color=0x000000,
    x=10,
    y=2 * display.height // 3,
    scale=2
)
group.append(wake_text)
time.sleep(display.time_to_refresh)
display.refresh()

ipv4 = ipaddress.ip_address("8.8.4.4")
print("Ping google.com:", wifi.radio.ping(ipv4), "ms")

pool = socketpool.SocketPool(wifi.radio)
requests = adafruit_requests.Session(pool, ssl.create_default_context())

print("Fetching text from", TIME_URL)
response = requests.get(TIME_URL)
print("-" * 40)
print(response.text)
print("-" * 40)

# Enter deep sleep to save battery
# The e-ink display will retain the image without power
# Wake up after 1 hour, or press any of the 4 buttons
time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + 3600)
button_a_alarm = alarm.pin.PinAlarm(pin=board.D15, value=False, pull=True)
button_b_alarm = alarm.pin.PinAlarm(pin=board.D14, value=False, pull=True)
button_c_alarm = alarm.pin.PinAlarm(pin=board.D12, value=False, pull=True)
button_d_alarm = alarm.pin.PinAlarm(pin=board.D11, value=False, pull=True)

print("Entering deep sleep...")
alarm.exit_and_deep_sleep_until_alarms(time_alarm, button_a_alarm, button_b_alarm, button_c_alarm, button_d_alarm)

if alarm.wake_alarm:
    if isinstance(alarm.wake_alarm, alarm.time.TimeAlarm):
        print("Woke from timer")
    elif isinstance(alarm.wake_alarm, alarm.pin.PinAlarm):
        print("Woke from button press")
else:
    print("Fresh boot (power on or reset)")
