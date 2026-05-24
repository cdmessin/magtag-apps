import os
import alarm
import board
import digitalio
import storage
import supervisor

#   BOOT FLOW:
#
#   1. Check Button A → dev_mode flag
#   2. If not dev_mode: remount filesystem writable for CircuitPython code
#      (USB drive becomes read-only to host computer)
#   3. If hard boot (wake_alarm is None): run OTA update from GitHub
#   4. If deep sleep wake (wake_alarm is set): skip OTA for speed/battery
#   5. If dev_mode: skip everything (USB stays writable for host, no OTA)
#
#   OTA SETTINGS (add to settings.toml on device):
#     OTA_URL = "https://raw.githubusercontent.com/<user>/<repo>/main/test-app/code.py"
#     OTA_TOKEN = ""   # GitHub PAT for private repos, leave empty for public


def ota_update():
    """Fetch latest code.py from GitHub and write it if changed."""
    import ssl
    import wifi
    import socketpool
    import adafruit_requests

    ota_url = os.getenv("OTA_URL")
    if not ota_url:
        print("OTA: No OTA_URL set, skipping")
        return

    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    password = os.getenv("CIRCUITPY_WIFI_PASSWORD")
    if not ssid:
        print("OTA: No WiFi credentials, skipping")
        return

    print("OTA: Connecting to WiFi...")
    wifi.radio.connect(ssid, password)

    pool = socketpool.SocketPool(wifi.radio)
    session = adafruit_requests.Session(pool, ssl.create_default_context())

    headers = {}
    ota_token = os.getenv("OTA_TOKEN")
    if ota_token:
        headers["Authorization"] = f"token {ota_token}"

    print(f"OTA: Fetching {ota_url}")
    response = session.get(ota_url, headers=headers)

    if response.status_code != 200:
        print(f"OTA: HTTP {response.status_code}, skipping")
        response.close()
        return

    new_code = response.text
    response.close()

    if len(new_code) <= 10:
        print("OTA: Response too small, skipping")
        return

    # Compare with existing code.py
    try:
        with open("/code.py", "r") as f:
            existing_code = f.read()
    except OSError:
        existing_code = ""

    if new_code == existing_code:
        print("OTA: Already up to date")
        return

    with open("/code.py", "w") as f:
        f.write(new_code)
    print("OTA: Done, code.py updated")


# --- Button A check ---
# Only treat button A as a dev-mode signal on a HARD boot (reset/power-on).
# On a deep-sleep wake, button A is the wake source for the first item in
# code.py, and a long press is the "mark yesterday" gesture. We must not
# confuse that with dev mode, otherwise the filesystem stays read-only and
# code.py crashes when it tries to persist state.
print(f"boot.py running. wake_alarm={alarm.wake_alarm!r}")
if alarm.wake_alarm is None:
    btn = digitalio.DigitalInOut(board.D15)
    btn.direction = digitalio.Direction.INPUT
    btn.pull = digitalio.Pull.UP
    dev_mode = not btn.value  # Active low: pressed = False
    btn.deinit()
    print(f"Hard boot. Button A held={dev_mode}")
else:
    dev_mode = False
    print("Wake from sleep — dev mode skipped")

if dev_mode:
    print("Dev mode — USB writable, OTA skipped, filesystem read-only to code")
else:
    storage.remount("/", readonly=False)
    print("Filesystem remounted writable to code")

    if alarm.wake_alarm is None:
        # Hard boot (power-on or reset button) — try OTA
        try:
            ota_update()
        except Exception as e:
            print(f"OTA: Failed ({e})")
    else:
        # Deep sleep wake — skip OTA for speed/battery
        print("Deep sleep wake — skipping OTA")
