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

## Run From Source

```bash
python check_urls_app.py
```

Put one URL per line in `urls.txt`. Lines that are invalid or duplicated are reported and skipped before network requests are made.

Completed runs are stored locally in `check_history.json` beside the application. The History page can remove all stored runs with its purge action.

## Build Artifacts

The repository includes `.github/workflows/build.yml`, which builds Windows and macOS artifacts on their native GitHub-hosted runners.

### Build From The `dev` Branch

From the project folder:

```bash
git add README.md
git commit -m "Document macOS build process"
git push origin dev
```

Then on GitHub:

1. Open the repository and select **Actions**.
2. Select **Build URL Monitor**.
3. Click **Run workflow**.
4. Select the `dev` branch.
5. Click **Run workflow** and wait for the job to finish.
6. Download the `url-monitor-macos` artifact.

After unzipping the artifact, run:

```text
check_urls_app.app
```

The Windows artifact is named `url-monitor-windows` and contains `check_urls_app.exe`.

### Version Tag Builds

To trigger a build from a release tag instead:

```bash
git tag v1.2.0
git push origin v1.2.0
```

## macOS Data Location

When running on macOS, settings, history, and the copied default URL list are stored in `~/Library/Application Support/URL Monitor` so the app does not need write access inside its `.app` bundle.
