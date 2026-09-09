# URL Monitor

A small desktop URL monitoring tool built with Tkinter. It checks a URL list or a single endpoint, shows a live health dashboard, and can notify Slack when checks find issues.

## Features

- URL list monitoring with duplicate and invalid-entry detection
- Single URL checks with host-only input support
- Live progress, elapsed time, current clock, health rate, and status cards
- Clickable result URLs that copy into the single-check field
- Stop a running check without closing the app
- Export the current result log as a timestamped text report
- Optional Slack alerts configured from Settings

## Run

```text
python check_urls_app.py
```

Put one URL per line in `urls.txt`. Lines that are invalid or duplicated are reported and skipped before network requests are made.