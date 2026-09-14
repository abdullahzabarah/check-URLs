import os
import re
import shutil
import sys
import requests
import time
import json
import threading
import ctypes
from ctypes import wintypes
from urllib.parse import urlsplit
import tkinter as tk
import tkinter.font as tkfont
from tkinter import scrolledtext, filedialog, ttk, simpledialog, messagebox
import webbrowser

if sys.platform == "darwin":
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

# === SLACK WEBHOOK ===
SLACK_WEBHOOK_URL = "SLACK_WEBHOOK_URL"

# Handle source and packaged resource paths separately so macOS app bundles
# can keep writable settings outside the read-only .app directory.
if getattr(sys, 'frozen', False):
    resource_path = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    executable_path = os.path.dirname(sys.executable)
else:
    resource_path = os.path.dirname(__file__)
    executable_path = resource_path

if sys.platform == "darwin":
    base_path = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "URL Monitor")
    os.makedirs(base_path, exist_ok=True)
else:
    base_path = executable_path

UI_FONT_FAMILY = "Helvetica" if sys.platform == "darwin" else "Segoe UI"
MONO_FONT_FAMILY = "Menlo" if sys.platform == "darwin" else "Consolas"

DEFAULT_FILE = os.path.join(base_path, "urls.txt")
bundled_default_file = os.path.join(resource_path, "urls.txt")
if not os.path.exists(DEFAULT_FILE) and os.path.exists(bundled_default_file):
    try:
        shutil.copyfile(bundled_default_file, DEFAULT_FILE)
    except OSError:
        pass

current_file = DEFAULT_FILE
APP_VERSION = "1.2.1"
APP_YEAR = time.strftime('%Y')
CONFIG_FILE = os.path.join(base_path, "monitor_urls_config.json")
HISTORY_FILE = os.path.join(base_path, "check_history.json")
ICON_FILE = os.path.join(resource_path, "url_monitor.ico")
LOGO_FILE = os.path.join(resource_path, "url_monitor_logo.svg")

# Slack enabled flag (persisted)
SLACK_ENABLED = True
INTERVAL_CHECK_ENABLED = False
INTERVAL_MINUTES = 5
INTERVAL_UNIT = "Minutes"

specific_check_button = None
progress_var = None
progress_label = None
stop_button = None
run_status_label = None
interval_status_label = None
slack_status_label = None
stop_requested = False
interval_job = None
interval_countdown_job = None
interval_due_at = None
last_run_summary = "No checks run yet"
history_records = []
root = None
window_geometry = "1040x760"
window_state = "normal"
status_animation_id = None
tray_icon = None
tray_thread = None
tray_thread_id = None
tray_ready = threading.Event()
app_exiting = False


def save_config():
    try:
        default_file = current_file
        if default_file:
            try:
                abs_path = os.path.abspath(default_file)
                rel_path = os.path.relpath(abs_path, base_path)
                if not rel_path.startswith(os.pardir + os.sep) and rel_path != os.pardir:
                    default_file = rel_path
            except Exception:
                default_file = current_file

        current_root = globals().get("root")
        current_state = window_state
        current_geometry = window_geometry
        if current_root is not None:
            current_state = current_root.state()
            if current_state == "normal":
                current_geometry = current_root.geometry()

        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "default_file": default_file,
                "slack_webhook_url": SLACK_WEBHOOK_URL,
                "slack_enabled": SLACK_ENABLED,
                "interval_check_enabled": INTERVAL_CHECK_ENABLED,
                "interval_minutes": INTERVAL_MINUTES,
                "interval_unit": INTERVAL_UNIT,
                "window_geometry": current_geometry,
                "window_state": current_state,
            }, f, indent=2)
    except Exception:
        pass


def set_run_status(text, fg="#52606d"):
    global status_animation_id
    if status_animation_id is not None and root is not None:
        try:
            root.after_cancel(status_animation_id)
        except tk.TclError:
            pass
        status_animation_id = None

    if run_status_label is None:
        return

    display_text = f"Run status: {text}"
    run_status_label.config(text="", fg=fg)

    def reveal(index=0):
        global status_animation_id
        if not run_status_label.winfo_exists():
            status_animation_id = None
            return
        run_status_label.config(text=display_text[:index], fg=fg)
        if index < len(display_text):
            status_animation_id = root.after(28, lambda: reveal(index + 1))
        else:
            status_animation_id = None

    reveal()


def resolve_file_path(path):
    if not path:
        return DEFAULT_FILE
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base_path, path))


def read_url_file(path):
    """Read a URL list and return usable URLs plus file-quality metrics."""
    with open(path, "r", encoding="utf-8", errors="replace") as file_handle:
        raw_lines = [line.strip() for line in file_handle if line.strip()]

    unique_urls = []
    seen = set()
    duplicate_count = 0
    invalid_count = 0
    for value in raw_lines:
        if not is_valid_url(value):
            invalid_count += 1
            continue
        normalized = value if value.startswith(("http://", "https://")) else f"https://{value}"
        if normalized in seen:
            duplicate_count += 1
            continue
        seen.add(normalized)
        unique_urls.append(normalized)

    return unique_urls, {
        "lines": len(raw_lines),
        "valid": len(unique_urls),
        "invalid": invalid_count,
        "duplicates": duplicate_count,
    }


def load_config():
    global current_file, SLACK_WEBHOOK_URL, window_geometry, window_state
    global INTERVAL_CHECK_ENABLED, INTERVAL_MINUTES, INTERVAL_UNIT
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            current_file = resolve_file_path(cfg.get("default_file", DEFAULT_FILE))
            SLACK_WEBHOOK_URL = cfg.get("slack_webhook_url", SLACK_WEBHOOK_URL)
            global SLACK_ENABLED
            SLACK_ENABLED = cfg.get("slack_enabled", SLACK_ENABLED)
            INTERVAL_CHECK_ENABLED = bool(cfg.get("interval_check_enabled", INTERVAL_CHECK_ENABLED))
            try:
                INTERVAL_MINUTES = max(1, int(cfg.get("interval_minutes", INTERVAL_MINUTES)))
            except (TypeError, ValueError):
                INTERVAL_MINUTES = 5
            INTERVAL_UNIT = cfg.get("interval_unit", INTERVAL_UNIT)
            if INTERVAL_UNIT not in ("Seconds", "Minutes", "Hours"):
                INTERVAL_UNIT = "Minutes"
            window_geometry = cfg.get("window_geometry", window_geometry)
            window_state = cfg.get("window_state", window_state)
        except Exception:
            current_file = DEFAULT_FILE
    else:
        save_config()


def load_history():
    global history_records
    if not os.path.exists(HISTORY_FILE):
        history_records = []
        return
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as file_handle:
            records = json.load(file_handle)
        history_records = records if isinstance(records, list) else []
    except (OSError, json.JSONDecodeError):
        history_records = []


def save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as file_handle:
            json.dump(history_records[-100:], file_handle, indent=2)
    except OSError:
        log("⚠️ Could not save check history.", "warning")


def record_history(source, total, healthy, warning, error, elapsed, checked_at, stopped=False):
    history_records.append({
        "checked_at": checked_at,
        "source": source,
        "total": total,
        "healthy": healthy,
        "warning": warning,
        "error": error,
        "elapsed": elapsed,
        "stopped": stopped,
    })
    del history_records[:-100]
    save_history()


# =====================
# LOG FUNCTION WITH COLORS
# =====================
def update_output_box(edit):
    output_box.config(state=tk.NORMAL)
    try:
        edit()
    finally:
        output_box.config(state=tk.DISABLED)


def log(message, tag="normal"):
    update_output_box(lambda: output_box.insert(tk.END, message + "\n", (tag,)))
    output_box.see(tk.END)
    root.update()


def log_clickable(prefix, url, suffix, tag="normal"):
    def add_result():
        output_box.insert(tk.END, prefix, (tag,))
        output_box.insert(tk.END, url, ("clickable", tag))
        output_box.insert(tk.END, suffix + "\n", (tag,))

    update_output_box(add_result)
    output_box.see(tk.END)
    root.update()


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tipwindow or not self.text:
            return
        x = event.x_root + 10
        y = event.y_root + 10
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT, background="#fff8d5", relief=tk.SOLID, borderwidth=1, font=(UI_FONT_FAMILY, 9))
        label.pack(ipadx=4, ipady=2)

    def hide_tip(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


BUTTON_THEMES = {
    "primary": {
        "bg": "#176b87", "hover": "#2b8aa3", "pressed": "#0f5268", "border": "#0f5268",
        "fg": "#ffffff", "active_fg": "#ffffff",
    },
    "neutral": {
        "bg": "#ffffff", "hover": "#eef5f7", "pressed": "#dbe9ed", "border": "#b9cbd1",
        "fg": "#12343b", "active_fg": "#12343b",
    },
    "warning": {
        "bg": "#fff7e8", "hover": "#ffefd0", "pressed": "#f8d99e", "border": "#e6b85c",
        "fg": "#8a4300", "active_fg": "#6d3300",
    },
    "danger": {
        "bg": "#fff4f3", "hover": "#ffe5e3", "pressed": "#f8c5c1", "border": "#df9690",
        "fg": "#a61e1e", "active_fg": "#861818",
    },
}


def style_action_button(button, tone="neutral"):
    theme = BUTTON_THEMES[tone]
    button.configure(
        bg=theme["bg"],
        fg=theme["fg"],
        activebackground=theme["hover"],
        activeforeground=theme["active_fg"],
        disabledforeground="#9aa8ad",
        relief=tk.FLAT,
        overrelief=tk.FLAT,
        bd=1,
        borderwidth=1,
        highlightthickness=1,
        highlightbackground=theme["border"],
        highlightcolor="#2b8aa3",
        padx=12,
        pady=5,
        font=(UI_FONT_FAMILY, 10, "bold"),
        cursor="hand2",
    )

    def on_enter(event):
        if button["state"] != tk.DISABLED:
            button.configure(bg=theme["hover"])

    def on_leave(event):
        if button["state"] != tk.DISABLED:
            button.configure(bg=theme["bg"], relief=tk.FLAT)

    def on_press(event):
        if button["state"] != tk.DISABLED:
            button.configure(bg=theme["pressed"], relief=tk.FLAT, highlightbackground=theme["border"])

    def on_release(event):
        if button["state"] != tk.DISABLED:
            button.configure(bg=theme["hover"], relief=tk.FLAT)
            button.after(110, lambda: button.configure(bg=theme["bg"]) if button.winfo_exists() else None)

    button.bind("<Enter>", on_enter, add="+")
    button.bind("<Leave>", on_leave, add="+")
    button.bind("<ButtonPress-1>", on_press, add="+")
    button.bind("<ButtonRelease-1>", on_release, add="+")
    return button


class StatusLabel(tk.Canvas):
    def __init__(self, master, text, font, fg, bg, padx=10, pady=10, **kwargs):
        self._text = text
        self._font = tkfont.Font(font=font)
        self._text_color = fg
        self._fill_color = bg
        self._padx = padx
        self._pady = pady
        width = self._font.measure(text) + (padx * 2)
        height = self._font.metrics("linespace") + (pady * 2)
        super().__init__(
            master,
            width=width,
            height=height,
            bg=master.cget("bg"),
            highlightthickness=0,
            bd=0,
            **kwargs,
        )
        self._draw()

    def _draw(self):
        self.delete("all")
        width = int(self["width"])
        height = int(self["height"])
        self.create_rectangle(0, 0, width, height, fill=self._fill_color, outline="#d7e2e6")
        self.create_rectangle(0, 0, 4, height, fill=self._text_color, outline="")
        self.create_text(self._padx + 3, height // 2, text=self._text, anchor="w", font=self._font, fill="#12343b")

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        self._text = kwargs.pop("text", self._text)
        self._text_color = kwargs.pop("fg", kwargs.pop("foreground", self._text_color))
        self._fill_color = kwargs.pop("bg", kwargs.pop("background", self._fill_color))
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure


def reset_dashboard():
    total_label.config(text="Total checked: 0")
    healthy_label.config(text="Healthy: 0")
    warning_label.config(text="Warnings: 0")
    error_label.config(text="Errors: 0")
    elapsed_label.config(text="Elapsed: 0.0s")
    last_checked_label.config(text="Last checked: -")
    if progress_var is not None:
        progress_var.set(0)
    if progress_label is not None:
        progress_label.config(text="Progress: 0 / 0")
    if health_rate_label is not None:
        health_rate_label.config(text="Health rate: -")


def update_dashboard(total, healthy, warning, error, elapsed=None, last_checked=None):
    total_label.config(text=f"Total checked: {total}")
    healthy_label.config(text=f"Healthy: {healthy}")
    warning_label.config(text=f"Warnings: {warning}")
    error_label.config(text=f"Errors: {error}")

    if elapsed is not None:
        elapsed_label.config(text=f"Elapsed: {elapsed}s")
    if last_checked is not None:
        last_checked_label.config(text=f"Last checked: {last_checked}")
    if health_rate_label is not None:
        rate = round((healthy / total) * 100) if total else 0
        health_rate_label.config(text=f"Health rate: {rate}%")
    root.update()


def update_clock():
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    clock_label.config(text=f"Current time: {now}")
    root.after(1000, update_clock)


def extract_url_from_line(text):
    match = re.search(r'(https?://[A-Za-z0-9\-._~:/?#\[\]@!$&\'()*+,;=%]+|[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+(?::\d{1,5})?(?:/\S*)?)', text)
    if match:
        return match.group(0)
    return None


def animate_url_to_field(url, event):
    try:
        start_x = event.x_root - root.winfo_rootx()
        start_y = event.y_root - root.winfo_rooty()
        target_x = url_entry.winfo_rootx() - root.winfo_rootx() + 6
        target_y = url_entry.winfo_rooty() - root.winfo_rooty() + (url_entry.winfo_height() // 2)
        flight = tk.Label(
            root,
            text=url,
            anchor="w",
            bg="#ffffff",
            fg="#176b87",
            relief=tk.SOLID,
            bd=1,
            padx=7,
            pady=3,
            font=(UI_FONT_FAMILY, 9),
        )
        flight.update_idletasks()
        flight.configure(width=52, wraplength=340)
        flight.place(x=start_x, y=start_y, anchor="w")

        duration = 360
        steps = 18

        def move(step=0):
            if not flight.winfo_exists():
                return
            progress = step / steps
            eased = 1 - ((1 - progress) ** 3)
            x = start_x + ((target_x - start_x) * eased)
            y = start_y + ((target_y - start_y) * eased)
            flight.place(x=round(x), y=round(y))
            if step < steps:
                root.after(duration // steps, lambda: move(step + 1))
            else:
                flight.destroy()
                specific_url_var.set(url)
                validate_specific_url()

        move()
    except Exception:
        specific_url_var.set(url)
        validate_specific_url()


def on_output_click(event):
    index = event.widget.index(f"@{event.x},{event.y}")
    line_start = event.widget.index(f"{index} linestart")
    line_end = event.widget.index(f"{index} lineend")
    line_text = event.widget.get(line_start, line_end)
    url = extract_url_from_line(line_text)
    if url:
        animate_url_to_field(url, event)
        # copy to clipboard
        try:
            root.clipboard_clear()
            root.clipboard_append(url)
        except Exception:
            pass
        # temporary 'Copied!' tag next to URL entry
        try:
            copied_lbl = tk.Label(root, text="Copied!", fg="#008000", bg="#ffffe0", relief=tk.SOLID, borderwidth=1, font=(UI_FONT_FAMILY, 9))
            # place it near the mouse cursor
            rx = event.x_root - root.winfo_rootx()
            ry = event.y_root - root.winfo_rooty()
            copied_lbl.place(x=rx + 10, y=ry + 10)
            def _hide():
                try:
                    copied_lbl.destroy()
                except Exception:
                    pass
            root.after(1200, _hide)
        except Exception:
            pass


def is_valid_url(url):
    pattern = re.compile(
        r'^(https?://)?'
        r'('
        r'([A-Za-z0-9-]+\.)+[A-Za-z]{2,}'
        r'|'
        r'((25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}'
        r'(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)'
        r')'
        r'(:\d{1,5})?'
        r'(/.*)?$'
    )
    return bool(pattern.match(url.strip()))


def is_valid_slack_webhook_url(url):
    """Return whether a value matches Slack's HTTPS incoming webhook format."""
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url.strip())
    path_parts = parsed.path.strip("/").split("/")
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "hooks.slack.com"
        and len(path_parts) == 4
        and path_parts[0] == "services"
        and all(re.fullmatch(r"[A-Za-z0-9_-]+", part) for part in path_parts[1:])
        and not parsed.query
        and not parsed.fragment
    )


def validate_specific_url(*args):
    value = specific_url_var.get().strip()
    if value and is_valid_url(value) and not stop_requested:
        specific_check_button.config(state=tk.NORMAL)
    else:
        specific_check_button.config(state=tk.DISABLED)
    # enable/disable Clear URL button based on field content
    try:
        if value:
            clear_url_btn.config(state=tk.NORMAL)
        else:
            clear_url_btn.config(state=tk.DISABLED)
    except Exception:
        pass


def get_urls_to_check(use_file=True):
    specific_url = specific_url_var.get().strip()
    if not use_file:
        if specific_url and is_valid_url(specific_url):
            return [specific_url if specific_url.startswith(("http://", "https://")) else f"https://{specific_url}"]
        log("⚠️ Enter a valid URL or IP address to check.", "warning")
        return []

    try:
        urls, metrics = read_url_file(current_file)
        update_file_insights(metrics)
        if metrics["invalid"] or metrics["duplicates"]:
            log(
                f"ℹ️ File scan: {metrics['valid']} valid, "
                f"{metrics['invalid']} invalid, {metrics['duplicates']} duplicates skipped.",
                "info"
            )
        return urls
    except FileNotFoundError:
        log(f"❌ File not found:\n{current_file}", "error")
        update_file_insights({"lines": 0, "valid": 0, "invalid": 0, "duplicates": 0})
        return []
    except OSError as error:
        log(f"❌ Could not read URL file: {error}", "error")
        return []


# =====================
# FILE BROWSER
# =====================
def browse_file():
    global current_file
    file_path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
    if file_path:
        current_file = file_path
        file_label.config(text=f"Using file: {current_file}")
        update_file_insights()


# =====================
# SLACK
# =====================
def send_slack_alert(message):
    if not SLACK_ENABLED:
        log("ℹ️ Slack alerts disabled; skipping Slack notification.", "info")
        return
    if not is_valid_slack_webhook_url(SLACK_WEBHOOK_URL):
        log("ℹ️ Slack webhook is not configured; skipping notification.", "info")
        return

    try:
        payload = {"text": message}
        response = requests.post(
            SLACK_WEBHOOK_URL,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"}
        )

        if response.status_code == 200:
            log("📢 Slack alert sent.", "info")
        else:
            log(f"❌ Slack error: {response.status_code}", "error")

    except Exception as e:
        log(f"❌ Slack failed: {e}", "error")


# =====================
# STATUS ICON
# =====================
def get_status_icon(status_code):
    if status_code == 200:
        return "✅", "success"
    elif 200 < status_code < 300:
        return "🟢", "success"
    elif 300 <= status_code < 400:
        return "🔁", "warning"
    elif 400 <= status_code < 500:
        return "⚠️", "warning"
    elif 500 <= status_code < 600:
        return "❌", "error"
    else:
        return "❓", "normal"


# =====================
# CHECK URLs
# =====================
def request_stop():
    global stop_requested
    stop_requested = True
    if stop_button is not None:
        stop_button.config(state=tk.DISABLED)


def show_stop_button():
    if stop_button is not None:
        stop_button.config(state=tk.NORMAL)
        if not stop_button.winfo_ismapped():
            stop_button.pack(side=tk.LEFT, padx=4)
    check_file_btn.config(state=tk.DISABLED)
    specific_check_button.config(state=tk.DISABLED)
    choose_file_btn.config(state=tk.DISABLED)
    clear_results_btn.config(state=tk.DISABLED)


def hide_stop_button():
    global stop_requested
    if stop_button is not None and stop_button.winfo_ismapped():
        stop_button.pack_forget()
    stop_requested = False
    choose_file_btn.config(state=tk.NORMAL)
    check_file_btn.config(state=tk.NORMAL)
    validate_specific_url()
    clear_results_btn.config(state=tk.NORMAL if output_box.get(1.0, tk.END).strip() else tk.DISABLED)


def check_urls(urls, source="URL list"):
    global stop_requested
    update_output_box(lambda: output_box.delete(1.0, tk.END))
    reset_dashboard()

    if not urls:
        return

    stop_requested = False
    show_stop_button()
    set_run_status(f"Checking {len(urls)} targets...", "#176b87")

    start_time = time.strftime('%Y-%m-%d %H:%M:%S')
    log(f"🔍 Checking URLs...\nStart: {start_time}\n", "info")

    issues = []
    begin = time.time()
    healthy = 0
    warning = 0
    error = 0

    total_urls = len(urls)

    for index, url in enumerate(urls, start=1):
        if stop_requested:
            log("⏹️ Stop requested. Aborting checks.", "warning")
            break

        try:
            t1 = time.time()
            response = requests.get(url, timeout=5)
            elapsed = round(time.time() - t1, 2)

            icon, color = get_status_icon(response.status_code)
            log_clickable(f"{icon} {response.status_code} | ", url, f" | {elapsed}s", color)

            if 200 <= response.status_code < 300:
                healthy += 1
            elif 300 <= response.status_code < 500:
                warning += 1
            else:
                error += 1

            if not (200 <= response.status_code < 400):
                issues.append(f"{icon} {url} returned {response.status_code}")

        except requests.exceptions.RequestException:
            log_clickable("❌ DOWN | ", url, "", "error")
            error += 1
            issues.append(f"❌ {url} is DOWN")

        progress_value = (index / total_urls) * 100
        if progress_var is not None:
            progress_var.set(progress_value)
        if progress_label is not None:
            progress_label.config(text=f"Progress: {index} / {total_urls}")
        root.update_idletasks()

    end_time = time.strftime('%Y-%m-%d %H:%M:%S')
    total = round(time.time() - begin, 2)
    log(f"Elapsed: {total}s\n", "info")
    checked_count = healthy + warning + error

    update_dashboard(
        total=checked_count,
        healthy=healthy,
        warning=warning,
        error=error,
        elapsed=total,
        last_checked=end_time
    )

    if not stop_requested:
        if issues:
            message = "*🚨 URL Monitoring Alert*\n\n" + "\n".join(issues)
            message += f"\n\n⏱ Checked at: *{end_time}*"
            send_slack_alert(message)
        else:
            send_slack_alert(f"💚 OK | All URLs healthy at *{end_time}*")

        log("✓ Done.", "success")
        set_run_status("Check complete", "#087f5b")
    else:
        log("✓ Stopped.", "warning")
        set_run_status("Check stopped", "#b54708")

    last_run_label.config(text=f"Last run: {end_time}")
    record_history(
        source=source,
        total=checked_count,
        healthy=healthy,
        warning=warning,
        error=error,
        elapsed=total,
        checked_at=end_time,
        stopped=stop_requested,
    )

    hide_stop_button()


def check_file_urls():
    urls = get_urls_to_check(use_file=True)
    check_urls(urls, source=os.path.basename(current_file))


def schedule_interval_check():
    global interval_job, interval_countdown_job, interval_due_at
    if interval_job is not None and root is not None:
        try:
            root.after_cancel(interval_job)
        except tk.TclError:
            pass
        interval_job = None
    if interval_countdown_job is not None and root is not None:
        try:
            root.after_cancel(interval_countdown_job)
        except tk.TclError:
            pass
        interval_countdown_job = None
    interval_due_at = None

    if INTERVAL_CHECK_ENABLED and root is not None:
        multipliers = {"Seconds": 1000, "Minutes": 60 * 1000, "Hours": 60 * 60 * 1000}
        delay_ms = INTERVAL_MINUTES * multipliers[INTERVAL_UNIT]
        interval_due_at = time.monotonic() + (delay_ms / 1000)
        interval_job = root.after(delay_ms, run_interval_check)
    update_interval_status()


def update_interval_status():
    global interval_countdown_job
    if interval_status_label is None:
        return
    if INTERVAL_CHECK_ENABLED:
        remaining = max(0, round((interval_due_at - time.monotonic()) if interval_due_at else 0))
        minutes, seconds = divmod(remaining, 60)
        interval_status_label.config(
            text=f"Auto-check: ON ({INTERVAL_MINUTES} {INTERVAL_UNIT}) - next in {minutes}:{seconds:02d}",
            fg="#087f5b",
        )
        if root is not None and interval_due_at and remaining > 0:
            interval_countdown_job = root.after(1000, update_interval_status)
    else:
        interval_status_label.config(text="Auto-check: Off", fg="#52606d")


def update_slack_status():
    if slack_status_label is None:
        return
    if SLACK_ENABLED:
        slack_status_label.config(text="Slack: ON", fg="#087f5b")
    else:
        slack_status_label.config(text="Slack: Off", fg="#52606d")


def run_interval_check():
    global interval_job
    interval_job = None
    if not INTERVAL_CHECK_ENABLED:
        return
    set_run_status("Scheduled check starting...", "#176b87")
    check_file_urls()
    schedule_interval_check()


def check_specific_url():
    urls = get_urls_to_check(use_file=False)
    check_urls(urls, source="Specific URL")


def clear_specific_url():
    specific_url_var.set("")
    validate_specific_url()

def clear_results():
    update_output_box(lambda: output_box.delete(1.0, tk.END))
    reset_dashboard()
    set_run_status("Ready for a new check", "#52606d")
    last_run_label.config(text="Last run: -")
    update_clear_results_state()
    try:
        clear_results_btn.config(state=tk.DISABLED)
    except Exception:
        pass


def update_file_insights(metrics=None):
    if metrics is None:
        try:
            _, metrics = read_url_file(current_file)
        except (FileNotFoundError, OSError):
            metrics = {"lines": 0, "valid": 0, "invalid": 0, "duplicates": 0}

    file_stats_label.config(
        text=(
            f"{metrics['valid']} ready  |  {metrics['invalid']} invalid  |  "
            f"{metrics['duplicates']} duplicates"
        )
    )
    source_count_label.config(text=f"{metrics['valid']} targets")


def export_results():
    content = output_box.get(1.0, tk.END).strip()
    if not content:
        messagebox.showinfo("Export report", "Run a check before exporting a report.")
        return

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = filedialog.asksaveasfilename(
        title="Export URL report",
        defaultextension=".txt",
        initialfile=f"url_report_{timestamp}.txt",
        filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
    )
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as file_handle:
            file_handle.write(content + "\n")
        set_run_status(f"Report exported: {os.path.basename(path)}", "#176b87")
    except OSError as error:
        messagebox.showerror("Export failed", f"Could not save report:\n{error}")


def copy_results():
    content = output_box.get(1.0, tk.END).strip()
    if not content:
        messagebox.showinfo("Copy results", "Run a check before copying results.")
        return

    try:
        root.clipboard_clear()
        root.clipboard_append(content)
        root.update()
        set_run_status("Results copied to clipboard", "#087f5b")
        root.after(1800, lambda: set_run_status("Ready", "#52606d") if root.winfo_exists() else None)
    except tk.TclError:
        messagebox.showerror("Copy results", "Could not copy results to the clipboard.")


def show_history():
    history_win = tk.Toplevel(root)
    history_win.title("Check History")
    history_win.geometry("900x500")
    history_win.minsize(720, 380)
    history_win.transient(root)
    history_win.configure(bg="#f4f7f9")
    history_win.columnconfigure(0, weight=1)
    history_win.rowconfigure(1, weight=1)

    header = tk.Frame(history_win, bg="#f4f7f9")
    header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
    tk.Label(header, text="Check History", font=(UI_FONT_FAMILY, 18, "bold"), fg="#12343b", bg="#f4f7f9").pack(side=tk.LEFT)
    tk.Label(header, text="Your last 100 monitoring runs", font=(UI_FONT_FAMILY, 10), fg="#52606d", bg="#f4f7f9").pack(side=tk.LEFT, padx=(12, 0), pady=(6, 0))

    table_frame = tk.Frame(history_win, bg="#f4f7f9")
    table_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=4)
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)
    columns = ("checked_at", "source", "total", "healthy", "warning", "error", "health", "elapsed", "state")
    tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)
    headings = {
        "checked_at": "Checked at", "source": "Source", "total": "Total",
        "healthy": "Healthy", "warning": "Warnings", "error": "Errors",
        "health": "Health", "elapsed": "Time", "state": "State",
    }
    widths = {"checked_at": 150, "source": 150, "total": 60, "healthy": 70, "warning": 75, "error": 60, "health": 70, "elapsed": 65, "state": 90}
    for column in columns:
        tree.heading(column, text=headings[column])
        tree.column(column, width=widths[column], anchor="center")
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)

    def refresh_rows():
        for item in tree.get_children():
            tree.delete(item)
        for record in reversed(history_records):
            total = record.get("total", 0)
            healthy = record.get("healthy", 0)
            rate = f"{round((healthy / total) * 100)}%" if total else "-"
            state = "Stopped" if record.get("stopped") else "Complete"
            tree.insert("", tk.END, values=(
                record.get("checked_at", "-"), record.get("source", "-"), total,
                healthy, record.get("warning", 0), record.get("error", 0), rate,
                f"{record.get('elapsed', 0)}s", state,
            ))
        count_label.config(text=f"{len(history_records)} saved runs")

    footer = tk.Frame(history_win, bg="#f4f7f9")
    footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 16))
    count_label = tk.Label(footer, text="0 saved runs", font=(UI_FONT_FAMILY, 9), fg="#52606d", bg="#f4f7f9")
    count_label.pack(side=tk.LEFT)

    def purge_history():
        if not history_records:
            messagebox.showinfo("Purge history", "There is no saved history to purge.", parent=history_win)
            return
        if not messagebox.askyesno("Purge history", "Delete all saved check history? This cannot be undone.", parent=history_win):
            return
        history_records.clear()
        save_history()
        refresh_rows()

    purge_button = tk.Button(footer, text="Purge History", command=purge_history, font=button_font)
    purge_button.pack(side=tk.RIGHT, padx=(8, 0))
    style_action_button(purge_button, "danger")
    close_button = tk.Button(footer, text="Close", command=history_win.destroy, font=button_font)
    close_button.pack(side=tk.RIGHT)
    style_action_button(close_button)
    refresh_rows()

def configure_slack():
    global SLACK_WEBHOOK_URL
    url = simpledialog.askstring("Slack Webhook", "Enter Slack webhook URL:", initialvalue=SLACK_WEBHOOK_URL)
    if url:
        url = url.strip()
        if not is_valid_slack_webhook_url(url):
            messagebox.showerror(
                "Slack Settings",
                "Enter a valid HTTPS Slack webhook URL from hooks.slack.com/services/.",
            )
            return
        SLACK_WEBHOOK_URL = url
        save_config()
        messagebox.showinfo("Slack Settings", "Slack webhook saved successfully.")

def configure_default_file():
    global current_file
    file_path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
    if file_path:
        current_file = file_path
        file_label.config(text=f"Using file: {current_file}")
        update_file_insights()
        save_config()

def show_about():
    about_win = tk.Toplevel(root)
    about_win.title("About URL Monitor")
    about_win.geometry("420x220")
    about_win.transient(root)
    about_win.resizable(False, False)
    about_win.configure(bg=BG_COLOR)

    pad = 10
    tk.Label(about_win, text="URL Monitor", font=(UI_FONT_FAMILY, 14, "bold"), bg=BG_COLOR, fg=FG_COLOR).pack(pady=(pad, 0))
    tk.Label(about_win, text=f"Version {APP_VERSION}", font=(UI_FONT_FAMILY, 10), bg=BG_COLOR, fg=FG_COLOR).pack()
    tk.Label(about_win, text="A simple URL checking tool with Slack alerts and clickable logs.", wraplength=380, justify="left", font=(UI_FONT_FAMILY, 9), bg=BG_COLOR, fg=FG_COLOR).pack(pady=(6, 6))

    tk.Label(about_win, text="Created by Abdullah Zabarah", font=(UI_FONT_FAMILY, 10, "bold"), bg=BG_COLOR, fg=FG_COLOR).pack()
    tk.Label(about_win, text="abdullahzabarah@gmail.com", font=(UI_FONT_FAMILY, 9), bg=BG_COLOR, fg=FG_COLOR).pack()
    tk.Label(about_win, text=f"© {APP_YEAR} Abdullah Zabarah", font=(UI_FONT_FAMILY, 9), bg=BG_COLOR, fg=FG_COLOR).pack(pady=(4, 0))
    tk.Label(about_win, text="Licensed under the MIT License", font=(UI_FONT_FAMILY, 9), bg=BG_COLOR, fg=FG_COLOR).pack(pady=(2, 0))

    repo_url = "https://github.com/abdullahzabarah/check-URLs.git"
    link_font = tkfont.Font(about_win, family=UI_FONT_FAMILY, size=9, underline=1)
    repo_lbl = tk.Label(about_win, text=repo_url, fg="#0000ee", cursor="hand2", font=link_font, bg=BG_COLOR)
    repo_lbl.pack(pady=(6, 6))

    def open_repo(event=None):
        try:
            webbrowser.open(repo_url)
        except Exception:
            messagebox.showinfo("Open Link", f"Open link: {repo_url}")

    repo_lbl.bind("<Button-1>", open_repo)
    repo_lbl.bind("<Enter>", lambda e: repo_lbl.config(fg="#551A8B"))
    repo_lbl.bind("<Leave>", lambda e: repo_lbl.config(fg="#0000ee"))

    about_close_button = tk.Button(about_win, text="Close", command=about_win.destroy, width=10)
    about_close_button.pack(pady=(8, 12))
    style_action_button(about_close_button)


def show_instructions():
    text = (
        "How to use URL Monitor:\n\n"
        "1. Choose a URL list file: Click 'Choose File...' and select a text file with one URL per line.\n\n"
        "2. Check file: Click 'Check File' to run checks against all URLs in the chosen file.\n\n"
        "3. Specific URL: Enter a single URL in the 'Specific URL to check' field and click 'Check URL' to test it.\n\n"
        "4. Results: Results appear in the main output area. Click any URL in the results to copy it to the Specific URL field and clipboard.\n\n"
        "5. Clear: Use 'Clear Results' to clear the output and reset the dashboard; use 'Clear URL' to clear the specific URL field.\n\n"
        "6. Settings: Open Settings from the Settings menu to configure the default file and Slack webhook, and enable/disable Slack alerts.\n\n"
        "7. Slack: When enabled, Slack notifications will be sent to the configured webhook whenever issues are detected.\n\n"
        "8. File viewer: Click the 'Using file:' label to open and view the current URL file contents.\n\n"
        "9. Export: Use 'Export Report' above the results to save a timestamped text report.\n\n"
        "10. History: Open View → Check History or click History in the header to review previous runs. Use Purge History to remove all saved runs.\n\n"
        "11. About & Repo: Use Help → About to see author and repo information.\n\n"
        "Tips:\n- Use fully-qualified URLs (https://...) for reliable checks.\n- Timeout is 5 seconds per request; adjust the code if you need a different timeout.\n"
    )

    win = tk.Toplevel(root)
    win.title("Instructions")
    win.geometry("980x650")
    win.minsize(820, 520)
    win.transient(root)
    win.resizable(True, True)
    win.columnconfigure(0, weight=1)
    win.rowconfigure(0, weight=1)
    win.configure(bg=BG_COLOR)

    txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=(UI_FONT_FAMILY, 10), bg=TEXT_BG, fg=TEXT_FG)
    txt.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    txt.insert(tk.END, text)
    txt.config(state=tk.DISABLED)

    btn = tk.Button(win, text="Close", command=win.destroy, width=10, bg=BG_COLOR, fg=FG_COLOR)
    btn.grid(row=1, column=0, sticky="e", padx=8, pady=(0,8))
    style_action_button(btn)

def show_version():
    messagebox.showinfo("Version", f"URL Monitor Version: {APP_VERSION}")


# =====================
# GUI
# =====================
load_config()
load_history()
root = tk.Tk()
root.title("URL Monitor")
root.geometry("1040x760")
root.minsize(900, 650)
root.configure(bg="#f4f7f9")
try:
    root.geometry(window_geometry)
except tk.TclError:
    root.geometry("1040x760")
if window_state in ("normal", "zoomed", "iconic"):
    try:
        root.state(window_state)
    except tk.TclError:
        pass


def start_tray_icon():
    global tray_icon, tray_thread, tray_thread_id
    if tray_icon is not None:
        return
    tray_ready.clear()
    tray_thread = threading.Thread(target=run_native_tray, daemon=True)
    tray_thread.start()
    tray_ready.wait(2)


def run_native_tray():
    global tray_icon, tray_thread_id
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    tray_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
    WM_TRAY = 0x8001
    WM_COMMAND = 0x0111
    WM_RBUTTONUP = 0x0205
    WM_LBUTTONDBLCLK = 0x0203
    WM_DESTROY = 0x0002
    WM_QUIT = 0x0012
    WM_APP_SHOW = 1001
    WM_APP_EXIT = 1002
    NIM_ADD = 0
    NIM_DELETE = 2
    NIF_MESSAGE = 1
    NIF_ICON = 2
    NIF_TIP = 4
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x10
    LR_DEFAULTSIZE = 0x40
    HWND_MESSAGE = -3
    TPM_RIGHTBUTTON = 0x0002
    MF_STRING = 0x0000

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class NotifyIconData(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256), ("uTimeout", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
        ]

    class WindowClass(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
        ]

    class Message(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD), ("pt", Point), ("lPrivate", wintypes.DWORD),
        ]

    @ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    def window_proc(hwnd, message, wparam, lparam):
        if message == WM_TRAY:
            if lparam in (WM_LBUTTONDBLCLK,):
                root.after(0, restore_from_tray)
            elif lparam == WM_RBUTTONUP:
                menu = user32.CreatePopupMenu()
                user32.AppendMenuW(menu, MF_STRING, WM_APP_SHOW, "Show URL Monitor")
                user32.AppendMenuW(menu, MF_STRING, WM_APP_EXIT, "Exit")
                point = Point()
                user32.GetCursorPos(ctypes.byref(point))
                user32.SetForegroundWindow(hwnd)
                user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, hwnd, None)
                user32.DestroyMenu(menu)
        elif message == WM_COMMAND:
            command = wparam & 0xFFFF
            if command == WM_APP_SHOW:
                root.after(0, restore_from_tray)
            elif command == WM_APP_EXIT:
                root.after(0, close_app)
        elif message == WM_DESTROY:
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    instance = ctypes.windll.kernel32.GetModuleHandleW(None)
    class_name = f"URLMonitorTray_{os.getpid()}"
    window_class = WindowClass(0, ctypes.cast(window_proc, ctypes.c_void_p), 0, 0, instance, None, None, None, None, class_name)
    user32.RegisterClassW(ctypes.byref(window_class))
    hwnd = user32.CreateWindowExW(0, class_name, "URL Monitor", 0, 0, 0, 0, 0, HWND_MESSAGE, None, instance, None)
    hicon = user32.LoadImageW(None, ICON_FILE, IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if not hicon:
        tray_ready.set()
        user32.DestroyWindow(hwnd)
        return
    notify_data = NotifyIconData()
    notify_data.cbSize = ctypes.sizeof(NotifyIconData)
    notify_data.hWnd = hwnd
    notify_data.uID = 1
    notify_data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    notify_data.uCallbackMessage = WM_TRAY
    notify_data.hIcon = hicon
    notify_data.szTip = "URL Monitor"
    shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(notify_data))
    tray_icon = hwnd
    tray_ready.set()
    message = Message()
    while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))
    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(notify_data))
    if hicon:
        user32.DestroyIcon(hicon)
    tray_icon = None
    tray_thread_id = None


def restore_from_tray():
    if root.state() == "withdrawn":
        root.deiconify()
    root.state("normal")
    root.lift()
    root.focus_force()


def minimize_to_tray(event=None):
    if sys.platform == "win32" and not app_exiting and root.state() == "iconic":
        root.withdraw()
        start_tray_icon()


def close_app():
    global app_exiting, tray_icon, tray_thread_id
    app_exiting = True
    save_config()
    if tray_icon is not None:
        if tray_thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(tray_thread_id, 0x0012, 0, 0)
        tray_icon = None
    root.destroy()


root.protocol("WM_DELETE_WINDOW", close_app)

# Color palette for consistent macOS compatibility
BG_COLOR = "#f4f7f9"
FG_COLOR = "#12343b"
ENTRY_BG = "#ffffff"
ENTRY_FG = "#12343b"
TEXT_BG = "#fbfcfd"
TEXT_FG = "#172b4d"

if sys.platform == "win32":
    root.bind("<Unmap>", lambda event: root.after_idle(minimize_to_tray))
if sys.platform == "darwin":
    # Keep baseline colors explicit on macOS where Tk can inherit low-contrast defaults.
    root.option_add("*Label.Background", BG_COLOR)
    root.option_add("*Label.Foreground", FG_COLOR)
    root.option_add("*Frame.Background", BG_COLOR)
    root.option_add("*Toplevel.Background", BG_COLOR)
    root.option_add("*Menu.Background", BG_COLOR)
    root.option_add("*Menu.Foreground", FG_COLOR)
    root.option_add("*Entry.Background", ENTRY_BG)
    root.option_add("*Entry.Foreground", ENTRY_FG)
    root.option_add("*Text.Background", TEXT_BG)
    root.option_add("*Text.Foreground", TEXT_FG)
    try:
        root.tk_setPalette(
            background=BG_COLOR,
            foreground=FG_COLOR,
            activeBackground="#d4e3e8",
            activeForeground=FG_COLOR,
        )
    except tk.TclError:
        pass
try:
    if os.path.exists(ICON_FILE):
        root.iconbitmap(ICON_FILE)
except tk.TclError:
    pass



specific_url_var = tk.StringVar()
progress_var = tk.DoubleVar(value=0.0)

# Use a restrained native theme with a stronger information hierarchy.
style = ttk.Style(root)
if sys.platform != "darwin":
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
style.configure("Monitor.Horizontal.TProgressbar", troughcolor="#dfe8ed", background="#176b87", lightcolor="#176b87", darkcolor="#176b87", borderwidth=0)

button_font = (UI_FONT_FAMILY, 10)
label_font = (UI_FONT_FAMILY, 10, "bold")
text_font = (UI_FONT_FAMILY, 10)
menu_bar = tk.Menu(root)
file_menu = tk.Menu(menu_bar, tearoff=0)
file_menu.add_command(label="Choose Default File...", command=configure_default_file)
file_menu.add_separator()
file_menu.add_command(label="Exit", command=close_app)
menu_bar.add_cascade(label="File", menu=file_menu)

# Unified Settings dialog
def open_settings():
    def browse_for_file():
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
        if path:
            default_file_var.set(path)

    def save_and_close():
        nonlocal default_file_var, slack_var, slack_enabled_var, interval_minutes_var
        global current_file, SLACK_WEBHOOK_URL, SLACK_ENABLED
        global INTERVAL_CHECK_ENABLED, INTERVAL_MINUTES, INTERVAL_UNIT
        new_file = default_file_var.get().strip()
        new_slack = slack_var.get().strip()
        if slack_enabled_var.get() and not is_valid_slack_webhook_url(new_slack):
            messagebox.showerror(
                "Settings",
                "Enter a valid HTTPS Slack webhook URL from hooks.slack.com/services/ or disable Slack alerts.",
                parent=settings_win,
            )
            return
        if interval_enabled_var.get():
            try:
                requested_interval = int(interval_minutes_var.get().strip())
            except ValueError:
                messagebox.showerror("Settings", "Enter a whole-number check interval or disable automatic checks.", parent=settings_win)
                return
            minimum_interval = 10 if interval_unit_var.get() == "Seconds" else 1
            if requested_interval < minimum_interval:
                messagebox.showerror(
                    "Settings",
                    f"The minimum interval is {minimum_interval} {interval_unit_var.get().lower()}.",
                    parent=settings_win,
                )
                return
            new_interval_minutes = requested_interval
        else:
            new_interval_minutes = INTERVAL_MINUTES
        if new_file:
            if not os.path.isabs(new_file):
                new_file = os.path.normpath(os.path.join(base_path, new_file))
            current_file = new_file
        SLACK_WEBHOOK_URL = new_slack
        SLACK_ENABLED = bool(slack_enabled_var.get())
        INTERVAL_CHECK_ENABLED = bool(interval_enabled_var.get())
        INTERVAL_MINUTES = new_interval_minutes
        INTERVAL_UNIT = interval_unit_var.get()
        file_label.config(text=f"Using file: {current_file}")
        save_config()
        schedule_interval_check()
        update_slack_status()
        settings_win.destroy()

    settings_win = tk.Toplevel(root)
    settings_win.title("Settings")
    settings_win.geometry("700x360")
    settings_win.transient(root)
    settings_win.resizable(True, False)
    settings_win.columnconfigure(1, weight=1)
    settings_win.configure(bg=BG_COLOR)

    display_default_file = current_file
    try:
        rel_path = os.path.relpath(os.path.abspath(current_file), base_path)
        if not rel_path.startswith(os.pardir + os.sep) and rel_path != os.pardir:
            display_default_file = f".{os.sep}{rel_path}"
    except Exception:
        display_default_file = current_file

    default_file_var = tk.StringVar(value=display_default_file)
    slack_var = tk.StringVar(value=SLACK_WEBHOOK_URL)
    slack_enabled_var = tk.BooleanVar(value=SLACK_ENABLED)
    interval_enabled_var = tk.BooleanVar(value=INTERVAL_CHECK_ENABLED)
    interval_minutes_var = tk.StringVar(value=str(INTERVAL_MINUTES))
    interval_unit_var = tk.StringVar(value=INTERVAL_UNIT)

    def update_interval_input_state(*args):
        interval_entry.config(state=tk.NORMAL if interval_enabled_var.get() else tk.DISABLED)
        interval_unit_combo.config(state="readonly" if interval_enabled_var.get() else tk.DISABLED)

    def update_slack_input_state(*args):
        slack_entry.config(state=tk.NORMAL if slack_enabled_var.get() else tk.DISABLED)

    tk.Label(settings_win, text="Default URL file:", font=label_font, bg=BG_COLOR, fg=FG_COLOR).grid(row=0, column=0, sticky="w", padx=10, pady=8)
    tk.Entry(settings_win, textvariable=default_file_var, font=text_font, bg=ENTRY_BG, fg=ENTRY_FG).grid(row=0, column=1, padx=6, pady=8, sticky="ew")
    browse_button = tk.Button(settings_win, text="Browse...", command=browse_for_file, bg=BG_COLOR, fg=FG_COLOR)
    browse_button.grid(row=0, column=2, padx=6, pady=8)
    style_action_button(browse_button)

    tk.Checkbutton(settings_win, text="Enable Slack alerts", variable=slack_enabled_var, command=update_slack_input_state).grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=8)

    tk.Label(settings_win, text="Slack Webhook URL:", font=label_font).grid(row=2, column=0, sticky="w", padx=10, pady=8)
    slack_entry = tk.Entry(settings_win, textvariable=slack_var, font=text_font)
    slack_entry.grid(row=2, column=1, columnspan=2, padx=6, pady=8, sticky="ew")
    update_slack_input_state()

    tk.Label(settings_win, text="Automatic check interval", font=(UI_FONT_FAMILY, 11, "bold"), fg="#12343b").grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(14, 4))
    tk.Checkbutton(settings_win, text="Enable automatic interval checks", variable=interval_enabled_var, command=update_interval_input_state).grid(row=4, column=0, columnspan=2, sticky="w", padx=10, pady=8)
    tk.Label(settings_win, text="Check every:", font=label_font).grid(row=5, column=0, sticky="w", padx=10, pady=8)
    interval_controls = tk.Frame(settings_win)
    interval_controls.grid(row=5, column=1, columnspan=2, sticky="w", padx=6, pady=8)
    interval_entry = tk.Entry(interval_controls, textvariable=interval_minutes_var, width=8, font=text_font)
    interval_entry.pack(side=tk.LEFT)
    interval_unit_combo = ttk.Combobox(interval_controls, textvariable=interval_unit_var, values=("Seconds", "Minutes", "Hours"), state="readonly", width=10, font=text_font)
    interval_unit_combo.pack(side=tk.LEFT, padx=0)
    update_interval_input_state()

    btn_frame = tk.Frame(settings_win)
    btn_frame.grid(row=6, column=0, columnspan=3, sticky="e", padx=6, pady=12)
    save_button = tk.Button(btn_frame, text="Save", command=save_and_close, width=12)
    save_button.pack(side=tk.RIGHT, padx=(6,0))
    style_action_button(save_button, "primary")
    cancel_button = tk.Button(btn_frame, text="Cancel", command=settings_win.destroy, width=12, bg=BG_COLOR, fg=FG_COLOR)
    cancel_button.pack(side=tk.RIGHT, padx=(6,0))
    style_action_button(cancel_button)

settings_menu = tk.Menu(menu_bar, tearoff=0)
settings_menu.add_command(label="Settings...", command=open_settings)
menu_bar.add_cascade(label="Settings", menu=settings_menu)

view_menu = tk.Menu(menu_bar, tearoff=0)
view_menu.add_command(label="Check History...", command=show_history)
menu_bar.add_cascade(label="View", menu=view_menu)

help_menu = tk.Menu(menu_bar, tearoff=0)
help_menu.add_command(label="Instructions...", command=show_instructions)
help_menu.add_command(label="About", command=show_about)
help_menu.add_command(label="Version", command=show_version)
menu_bar.add_cascade(label="Help", menu=help_menu)
root.config(menu=menu_bar)
root_frame = tk.Frame(root, bg="#f4f7f9")
root_frame.pack(fill=tk.X, padx=18, pady=(14, 0))

title_frame = tk.Frame(root_frame, bg="#f4f7f9")
title_frame.pack(fill=tk.X)
logo_canvas = tk.Canvas(title_frame, width=54, height=54, highlightthickness=0, bg="#f4f7f9")
logo_canvas.pack(side=tk.LEFT, padx=(0, 10))
logo_canvas.create_oval(7, 7, 47, 47, fill="#176b87", outline="#12343b", width=2, tags="logo_ring")
logo_canvas.create_arc(14, 14, 40, 40, start=35, extent=230, style=tk.ARC, outline="#f4f7f9", width=4, tags="logo_arc")
logo_canvas.create_oval(24, 24, 30, 30, fill="#f4f7f9", outline="", tags="logo_dot")
tk.Label(title_frame, text="URL Monitor", font=(UI_FONT_FAMILY, 22, "bold"), fg="#12343b", bg="#f4f7f9").pack(side=tk.LEFT)
tk.Label(title_frame, text="Visibility for every endpoint", font=(UI_FONT_FAMILY, 10), fg="#52606d", bg="#f4f7f9").pack(side=tk.LEFT, padx=(12, 0), pady=(9, 0))

status_panel = tk.Frame(title_frame, bg="#f4f7f9")
status_panel.pack(side=tk.RIGHT, padx=(12, 0), pady=(4, 0))
run_status_label = tk.Label(status_panel, text="Run status: Ready for a new check", width=65, anchor="e", font=(UI_FONT_FAMILY, 10, "bold"), fg="#52606d", bg="#f4f7f9")
run_status_label.pack(side=tk.TOP, anchor="e")
interval_status_label = tk.Label(status_panel, text="Auto-check: Off", anchor="e", font=(UI_FONT_FAMILY, 9, "bold"), fg="#52606d", bg="#f4f7f9")
interval_status_label.pack(side=tk.TOP, anchor="e", pady=(2, 0))
slack_status_label = tk.Label(status_panel, text="Slack: Off", anchor="e", font=(UI_FONT_FAMILY, 9, "bold"), fg="#52606d", bg="#f4f7f9")
slack_status_label.pack(side=tk.TOP, anchor="e", pady=(2, 0))

logo_phase = 0


def animate_logo():
    global logo_phase
    logo_phase = (logo_phase + 1) % 24
    pulse = 2 if logo_phase < 12 else 0
    logo_canvas.coords("logo_ring", 7 - pulse, 7 - pulse, 47 + pulse, 47 + pulse)
    logo_canvas.itemconfig("logo_ring", outline="#2b8aa3" if pulse else "#12343b")
    root.after(140, animate_logo)

# Left: file info + file actions
left_frame = tk.Frame(root_frame, bg="#f4f7f9")
left_frame.pack(side=tk.LEFT, anchor="w")

file_label = tk.Label(left_frame, text=f"Using file: {current_file}", font=(UI_FONT_FAMILY, 12, "bold"), fg="#12343b", bg="#f4f7f9")
file_label.grid(row=0, column=0, sticky="w", pady=(12, 4))
file_font = tkfont.Font(family=UI_FONT_FAMILY, size=12, weight="bold")
file_font_underline = tkfont.Font(family=UI_FONT_FAMILY, size=12, weight="bold", underline=1)
file_label.config(font=file_font)

def _on_file_enter(e):
    file_label.config(cursor="hand2", font=file_font_underline)

def _on_file_leave(e):
    file_label.config(cursor="", font=file_font)

file_label.bind("<Button-1>", lambda e: open_file_viewer())
file_label.bind("<Enter>", _on_file_enter)
file_label.bind("<Leave>", _on_file_leave)

source_count_label = tk.Label(left_frame, text="0 targets", font=(UI_FONT_FAMILY, 9, "bold"), fg="#176b87", bg="#f4f7f9")
source_count_label.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(12, 4))

file_stats_label = tk.Label(left_frame, text="0 ready  |  0 invalid  |  0 duplicates", font=(UI_FONT_FAMILY, 9), fg="#52606d", bg="#f4f7f9")
file_stats_label.grid(row=1, column=0, columnspan=2, sticky="w")

file_actions = tk.Frame(left_frame, bg="#f4f7f9")
file_actions.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 8))

choose_file_btn = tk.Button(file_actions, text="Choose File...", command=browse_file, font=button_font)
choose_file_btn.pack(side=tk.LEFT, padx=4)
style_action_button(choose_file_btn)
ToolTip(choose_file_btn, "Choose URL list file")

check_file_btn = tk.Button(file_actions, text="Check File", command=check_file_urls, font=button_font, fg="green")
check_file_btn.pack(side=tk.LEFT, padx=4)
style_action_button(check_file_btn, "primary")
ToolTip(check_file_btn, "Check all URLs in file")

history_button = tk.Button(file_actions, text="History", command=show_history, font=button_font, fg="#176b87")
history_button.pack(side=tk.LEFT, padx=4)
style_action_button(history_button, "primary")

stop_button = tk.Button(file_actions, text="Stop", command=request_stop, font=button_font, fg="red")
style_action_button(stop_button, "danger")
ToolTip(stop_button, "Stop the running URL check")

# Specific URL field: move under file actions (new row)
url_frame = tk.Frame(left_frame, bg="#f4f7f9")
url_frame.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6,8))

tk.Label(url_frame, text="Specific URL to check:", font=label_font, fg="#12343b", bg="#f4f7f9").pack(side=tk.LEFT)
url_entry = tk.Entry(url_frame, textvariable=specific_url_var, width=58, font=text_font, bd=1, relief=tk.FLAT)
url_entry.pack(side=tk.LEFT, padx=6, pady=2, ipady=4)
specific_check_button = tk.Button(url_frame, text="Check URL", command=check_specific_url, font=button_font, state=tk.DISABLED, fg="green")
specific_check_button.pack(side=tk.LEFT, padx=4)
style_action_button(specific_check_button, "primary")
ToolTip(specific_check_button, "Check this specific URL")
clear_url_btn = tk.Button(url_frame, text="Clear URL", command=clear_specific_url, font=button_font, state=tk.DISABLED, fg="red")
clear_url_btn.pack(side=tk.LEFT, padx=4)
style_action_button(clear_url_btn, "danger")
ToolTip(clear_url_btn, "Clear URL input")
url_entry.bind("<KeyRelease>", validate_specific_url)
specific_url_var.trace_add('write', lambda *args: validate_specific_url())
validate_specific_url()

# Progress bar row
progress_frame = tk.Frame(root, bg="#f4f7f9")
progress_frame.pack(pady=(2, 6), fill=tk.X, padx=18)

progress_bar = ttk.Progressbar(progress_frame, variable=progress_var, maximum=100, style="Monitor.Horizontal.TProgressbar")
progress_bar.pack(side=tk.LEFT, padx=6, pady=2, fill=tk.X, expand=True)
progress_label = tk.Label(progress_frame, text="Progress: 0 / 0", font=button_font, bg="#f4f7f9", fg="#52606d")
progress_label.pack(side=tk.LEFT, padx=10)

# Slack controls moved to Settings dialog

# Dashboard frame

dashboard_frame = tk.Frame(root, bg="#f4f7f9", padx=12, pady=6)
dashboard_frame.pack(padx=18, pady=(2, 4), fill=tk.X)
for column in range(4):
    dashboard_frame.columnconfigure(column, weight=1)

total_label = StatusLabel(dashboard_frame, text="Total checked: 0", font=(UI_FONT_FAMILY, 11, "bold"), bg="#e7eef2", fg="#176b87")
healthy_label = StatusLabel(dashboard_frame, text="Healthy: 0", fg="#087f5b", font=(UI_FONT_FAMILY, 11, "bold"), bg="#e3f4ed")
warning_label = StatusLabel(dashboard_frame, text="Warnings: 0", fg="#b54708", font=(UI_FONT_FAMILY, 11, "bold"), bg="#fff0d9")
error_label = StatusLabel(dashboard_frame, text="Errors: 0", fg="#c92a2a", font=(UI_FONT_FAMILY, 11, "bold"), bg="#fde8e7")
elapsed_label = tk.Label(dashboard_frame, text="Elapsed: 0.0s", anchor="w", font=(UI_FONT_FAMILY, 10), bg="#f4f7f9", fg="#52606d")
last_checked_label = tk.Label(dashboard_frame, text="Last checked: -", anchor="w", font=(UI_FONT_FAMILY, 10), bg="#f4f7f9", fg="#52606d")
health_rate_label = tk.Label(dashboard_frame, text="Health rate: -", anchor="w", font=(UI_FONT_FAMILY, 10, "bold"), bg="#f4f7f9", fg="#176b87")
last_run_label = tk.Label(dashboard_frame, text="Last run: -", anchor="e", font=(UI_FONT_FAMILY, 10), bg="#f4f7f9", fg="#52606d")
clock_label = tk.Label(dashboard_frame, text="Current time: -", anchor="e", font=(UI_FONT_FAMILY, 10, "italic"), bg="#f4f7f9", fg="#52606d")

total_label.grid(row=0, column=0, sticky="w", padx=6, pady=3)
healthy_label.grid(row=0, column=1, sticky="w", padx=6, pady=3)
warning_label.grid(row=0, column=2, sticky="w", padx=6, pady=3)
error_label.grid(row=0, column=3, sticky="w", padx=6, pady=3)
elapsed_label.grid(row=1, column=0, sticky="w", padx=6, pady=(8, 3))
last_checked_label.grid(row=1, column=1, sticky="w", padx=6, pady=(8, 3))
health_rate_label.grid(row=1, column=2, sticky="w", padx=6, pady=(8, 3))
last_run_label.grid(row=1, column=3, sticky="e", padx=6, pady=(8, 3))
clock_label.grid(row=2, column=0, columnspan=4, sticky="e", padx=6, pady=(0, 2))

# Output box
results_header = tk.Frame(root, bg="#f4f7f9")
results_header.pack(padx=18, pady=(8, 0), fill=tk.X)
tk.Label(results_header, text="Check results", font=(UI_FONT_FAMILY, 12, "bold"), fg="#12343b", bg="#f4f7f9").pack(side=tk.LEFT)
copy_results_button = tk.Button(results_header, text="Copy Results", command=copy_results, font=button_font)
copy_results_button.pack(side=tk.RIGHT, padx=(6, 0))
style_action_button(copy_results_button)
clear_results_btn = tk.Button(results_header, text="Clear Results", command=clear_results, font=button_font, fg="orange")
clear_results_btn.pack(side=tk.RIGHT, padx=(6, 0))
style_action_button(clear_results_btn, "warning")
ToolTip(clear_results_btn, "Clear results")
export_button = tk.Button(results_header, text="Export Report", command=export_results, font=button_font, fg="#176b87")
export_button.pack(side=tk.RIGHT, padx=(6, 0))
style_action_button(export_button, "primary")

output_box = scrolledtext.ScrolledText(root, width=104, height=20, bd=1, relief=tk.SUNKEN, font=(MONO_FONT_FAMILY, 10), bg="#fbfcfd", fg="#172b4d", padx=8, pady=6)
output_box.pack(padx=18, pady=(6, 14), fill=tk.BOTH, expand=True)
output_box.config(state=tk.DISABLED)
output_box.tag_config("success", foreground="#008000")
output_box.tag_config("warning", foreground="#d2691e")
output_box.tag_config("error", foreground="#ff0000")
output_box.tag_config("info", foreground="#0000ff")
output_box.tag_config("normal", foreground="#000000")
output_box.tag_config("clickable", foreground="#0000ee", underline=1)
output_box.tag_bind("clickable", "<Button-1>", on_output_click)
output_box.tag_bind("clickable", "<Enter>", lambda e: output_box.config(cursor="hand2"))
output_box.tag_bind("clickable", "<Leave>", lambda e: output_box.config(cursor=""))

def update_clear_results_state():
    try:
        content = output_box.get(1.0, tk.END).strip()
        state = tk.NORMAL if content else tk.DISABLED
        if content:
            clear_results_btn.config(state=state)
        else:
            clear_results_btn.config(state=state)
        export_button.config(state=state)
        copy_results_button.config(state=state)
    except Exception:
        pass


def _on_output_modified(event=None):
    # Called when output_box content changes (<<Modified>>)
    update_clear_results_state()
    try:
        output_box.edit_modified(False)
    except Exception:
        pass

# Bind to modified event to update Clear Results button state
output_box.bind('<<Modified>>', _on_output_modified)

# Initialize clear results button state based on existing content
update_clear_results_state()
update_file_insights()
schedule_interval_check()
update_slack_status()


def open_file_viewer():
    path = current_file
    if not path or not os.path.exists(path):
        messagebox.showerror("File not found", f"File not found:\n{path}")
        return

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        messagebox.showerror("Error", f"Could not read file:\n{e}")
        return

    win = tk.Toplevel(root)
    win.title(f"Contents: {os.path.basename(path)}")
    win.geometry("700x500")
    win.configure(bg=BG_COLOR)
    txt = scrolledtext.ScrolledText(win, width=100, height=30, font=(MONO_FONT_FAMILY, 10), bg=TEXT_BG, fg=TEXT_FG)
    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    txt.insert(1.0, content)
    txt.config(state=tk.DISABLED)

animate_logo()
update_clock()
root.mainloop()