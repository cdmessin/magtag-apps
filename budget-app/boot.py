import os
import alarm
import board
import digitalio
import storage

#   BOOT FLOW:
#
#   1. Check Button A -> dev_mode flag
#   2. If hard boot and not dev_mode: run OTA update from GitHub
#   3. If deep sleep wake: skip OTA for speed/battery
#   4. If dev_mode: skip OTA (USB stays writable for host)
#
#   This app does not write to the filesystem at runtime, so the
#   filesystem is only remounted writable inside ota_update() when
#   there is actually a new code.py to write.
#
#   OTA SETTINGS (add to settings.toml on device):
#     OTA_URL = "https://raw.githubusercontent.com/<user>/<repo>/main/budget-app/code.py"
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

    # Only remount writable when we actually need to write
    storage.remount("/", readonly=False)
    with open("/code.py", "w") as f:
        f.write(new_code)
    print("OTA: Done, code.py updated")


# --- Button A check ---
btn = digitalio.DigitalInOut(board.D15)
btn.direction = digitalio.Direction.INPUT
btn.pull = digitalio.Pull.UP

dev_mode = not btn.value  # Active low: pressed = False
btn.deinit()

if dev_mode:
    print("Dev mode — USB writable, OTA skipped")
elif alarm.wake_alarm is None:
    # Hard boot (power-on or reset button) — try OTA
    try:
        ota_update()
    except Exception as e:
        print(f"OTA: Failed ({e})")
else:
    # Deep sleep wake — skip OTA for speed/battery
    print("Deep sleep wake — skipping OTA")
