# Budget App

Monthly YNAB budget tracker for the MagTag e-ink display. Shows spending pace and top category breakdown at a glance.

## Display Layout

- **Status bar**: current time + battery %
- **Pace section**: total spent vs budgeted, progress bar with day-of-month marker, ON PACE / AHEAD / OVER label
- **Category rows**: top 4 categories by % spent, each with remaining balance and color-coded bar (light gray < 70%, dark gray 70-90%, black > 90%)

## Setup

Add to `settings.toml` on the device:

```
YNAB_API_TOKEN = "your_personal_access_token"
YNAB_BUDGET_ID = "your_budget_uuid"
```

Get your API token at https://app.ynab.com/settings/developer. Find your budget ID by opening your budget in YNAB and copying the UUID from the URL.

Existing device variables (`CIRCUITPY_WIFI_SSID`, `CIRCUITPY_WIFI_PASSWORD`, `ADAFRUIT_AIO_USERNAME`, `ADAFRUIT_AIO_KEY`, `TIMEZONE`) must also be set.

## API Usage

Single call per refresh: `GET /v1/budgets/{id}/months/current`. At 4-hour refresh intervals that's ~6 calls/day, well within YNAB's 200/hour limit.

## Deploy

1. Hold Button A during reset to enter dev mode (USB writable)
2. Copy `boot.py` and `code.py` to the CIRCUITPY root
3. Reset without holding Button A to run normally

## Refresh

Wakes every 4 hours automatically. Press any button for a manual refresh.
