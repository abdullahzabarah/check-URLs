# URL Monitor

A small desktop URL monitoring tool built with Tkinter. It checks a URL list or a single endpoint, shows a live health dashboard, and can notify Slack when checks find issues.

## Features

- URL list monitoring with duplicate and invalid-entry detection
- Single URL checks with host-only input support
- Live progress, elapsed time, current clock, health rate, and status cards
- Clickable result URLs that copy into the single-check field
- Stop a running check without closing the app
- Export the current result log as a timestamped text report
- Animated branded dashboard header with a custom application icon
- Separate check history page with health summaries and purge controls
- Optional Slack alerts configured from Settings

## Run

```text
python check_urls_app.py
```

Put one URL per line in `urls.txt`. Lines that are invalid or duplicated are reported and skipped before network requests are made.

Completed runs are stored locally in `check_history.json` beside the application. The History page can remove all stored runs with its purge action.

## macOS Build

The repository includes a GitHub Actions workflow that builds both Windows and macOS artifacts. Push a version tag such as `v1.2.0`, or run the workflow manually from the Actions tab. The macOS artifact is a zipped `.app` bundle.

When running on macOS, settings, history, and the copied default URL list are stored in `~/Library/Application Support/URL Monitor` so the app does not need write access inside its `.app` bundle.