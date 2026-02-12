# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CircuitPython applications for the Adafruit MagTag 2.9" grayscale e-ink display (ESP32-S2). Code is deployed by copying files directly to the CIRCUITPY USB drive — there is no build system, package manager, or test framework.

## Deployment

Each app directory (e.g. `test-app/`) contains files that map to the root of the CIRCUITPY filesystem:
- `boot.py` → runs once at power-on/reset (before code.py)
- `code.py` → main application, runs after boot.py
- Environment variables (`CIRCUITPY_WIFI_SSID`, `CIRCUITPY_WIFI_PASSWORD`, `ADAFRUIT_AIO_USERNAME`, `ADAFRUIT_AIO_KEY`, `TIMEZONE`) are set in `settings.toml` on the device, not tracked in this repo.

## Display Constraints

The e-ink display is 296x128 pixels but the controller RAM is larger than the physical panel. Key constraints documented in `docs/hardware_description.md`:
- Top ~5 pixels of content at y=0 are clipped. All content must be placed inside a `displayio.Group(y=DISPLAY_Y_OFFSET)`.
- Bottom ~5 rows show noise from uninitialized controller RAM. Treat as unusable.
- Practical usable area: ~296x118 pixels (depends on `DISPLAY_Y_OFFSET` tuning).
- `terminalio.FONT` is 6px wide per character. At `scale=2` each char is 12px. Column widths must account for this (e.g. 74px column fits ~6 chars at scale=2).

## CircuitPython Specifics

- Target: CircuitPython 9.x on ESP32-S2 (WROVER, 4MB flash, 2MB PSRAM)
- No pip/requirements.txt — libraries are `.mpy` files copied to the device's `/lib/` folder
- Available libraries in use: `adafruit_requests`, `adafruit_display_text`, `adafruit_display_shapes`
- Deep sleep with `alarm.exit_and_deep_sleep_until_alarms()` fully powers down the CPU; the e-ink display retains its image without power
- Battery monitoring uses `board.VOLTAGE_MONITOR` with a voltage divider (multiply ADC reading by 2); LiPo curve is 4.2V=100% to 3.0V=0%

## App Specifics

### test-app

- `data.json` → persistent state file written by code.py at runtime

#### Persistent State

`data.json` is read/written via `db_read()` and `db_write()` helpers in code.py using the built-in `json` module. This only works in normal boot mode (filesystem writable by code). In dev mode, writes will raise `OSError`.

#### Boot Modes

Button A (D15) controls boot behavior (see `boot.py` comments for full description):
- **Normal** (button not held): filesystem is writable by code, USB drive is read-only to host. App runs and enters deep sleep.
- **Dev mode** (hold Button A during reset): USB drive stays writable for host (drag-and-drop editing). Code.py skips deep sleep and drops into REPL.
