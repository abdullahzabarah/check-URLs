import os
import re
import shutil
import sys
import requests
import time
import json
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

specific_check_button = None
progress_var = None
progress_label = None
stop_button = None
stop_requested = False
last_run_summary = "No checks run yet"
history_records = []


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

        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "default_file": default_file,
                "slack_webhook_url": SLACK_WEBHOOK_URL,
                "slack_enabled": SLACK_ENABLED,
            }, f, indent=2)
    except Exception:
        pass


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
    global current_file, SLACK_WEBHOOK_URL
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            current_file = resolve_file_path(cfg.get("default_file", DEFAULT_FILE))
            SLACK_WEBHOOK_URL = cfg.get("slack_webhook_url", SLACK_WEBHOOK_URL)
            global SLACK_ENABLED
            SLACK_ENABLED = cfg.get("slack_enabled", SLACK_ENABLED)
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
def log(message, tag="normal"):
    output_box.insert(tk.END, message + "\n", (tag,))
    output_box.see(tk.END)
    root.update()


def log_clickable(prefix, url, suffix, tag="normal"):
    output_box.insert(tk.END, prefix, (tag,))
    output_box.insert(tk.END, url, ("clickable", tag))
    output_box.insert(tk.END, suffix + "\n", (tag,))
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
        "bg": "#176b87", "hover": "#2b8aa3", "pressed": "#0f5268",
        "fg": "#ffffff", "active_fg": "#ffffff",
    },
    "neutral": {
        "bg": "#e7eef2", "hover": "#d4e3e8", "pressed": "#bed2d9",
        "fg": "#12343b", "active_fg": "#12343b",
    },
    "warning": {
        "bg": "#fff0d9", "hover": "#ffe2b3", "pressed": "#f4c982",
        "fg": "#8a4300", "active_fg": "#6d3300",
    },
    "danger": {
        "bg": "#fde8e7", "hover": "#f9cfcd", "pressed": "#efa9a6",
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
        overrelief=tk.RAISED,
        bd=0,
        highlightthickness=2,
        highlightbackground="#d7e2e6",
        highlightcolor="#2b8aa3",
        padx=11,
        pady=6,
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
            button.configure(bg=theme["pressed"], relief=tk.SUNKEN)

    def on_release(event):
        if button["state"] != tk.DISABLED:
            button.configure(bg=theme["hover"], relief=tk.FLAT)
            button.after(110, lambda: button.configure(bg=theme["bg"]) if button.winfo_exists() else None)

    button.bind("<Enter>", on_enter, add="+")
    button.bind("<Leave>", on_leave, add="+")
    button.bind("<ButtonPress-1>", on_press, add="+")
    button.bind("<ButtonRelease-1>", on_release, add="+")
    return button


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


def on_output_click(event):
    index = event.widget.index(f"@{event.x},{event.y}")
    line_start = event.widget.index(f"{index} linestart")
    line_end = event.widget.index(f"{index} lineend")
    line_text = event.widget.get(line_start, line_end)
    url = extract_url_from_line(line_text)
    if url:
        specific_url_var.set(url)
        validate_specific_url()
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
    if not SLACK_WEBHOOK_URL or SLACK_WEBHOOK_URL == "SLACK_WEBHOOK_URL":
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
    output_box.delete(1.0, tk.END)
    reset_dashboard()

    if not urls:
        return

    stop_requested = False
    show_stop_button()
    run_status_label.config(text=f"Checking {len(urls)} targets...", fg="#176b87")

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
        run_status_label.config(text="Check complete", fg="#087f5b")
    else:
        log("✓ Stopped.", "warning")
        run_status_label.config(text="Check stopped", fg="#b54708")

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


def check_specific_url():
    urls = get_urls_to_check(use_file=False)
    check_urls(urls, source="Specific URL")


def clear_specific_url():
    specific_url_var.set("")
    validate_specific_url()

def clear_results():
    output_box.delete(1.0, tk.END)
    reset_dashboard()
    run_status_label.config(text="Ready for a new check", fg="#52606d")
    last_run_label.config(text="Last run: -")
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
        run_status_label.config(text=f"Report exported: {os.path.basename(path)}", fg="#176b87")
    except OSError as error:
        messagebox.showerror("Export failed", f"Could not save report:\n{error}")


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
        SLACK_WEBHOOK_URL = url.strip()
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

    pad = 10
    tk.Label(about_win, text="URL Monitor", font=(UI_FONT_FAMILY, 14, "bold")).pack(pady=(pad, 0))
    tk.Label(about_win, text=f"Version {APP_VERSION}", font=(UI_FONT_FAMILY, 10)).pack()
    tk.Label(about_win, text="A simple URL checking tool with Slack alerts and clickable logs.", wraplength=380, justify="left", font=(UI_FONT_FAMILY, 9)).pack(pady=(6, 6))

    tk.Label(about_win, text="Created by Abdullah Zabarah", font=(UI_FONT_FAMILY, 10, "bold")).pack()
    tk.Label(about_win, text="abdullahzabarah@gmail.com", font=(UI_FONT_FAMILY, 9)).pack()
    tk.Label(about_win, text=f"© {APP_YEAR} Abdullah Zabarah", font=(UI_FONT_FAMILY, 9)).pack(pady=(4, 0))
    tk.Label(about_win, text="Licensed under the MIT License", font=(UI_FONT_FAMILY, 9)).pack(pady=(2, 0))

    repo_url = "https://github.com/abdullahzabarah/check-URLs.git"
    link_font = tkfont.Font(about_win, family=UI_FONT_FAMILY, size=9, underline=1)
    repo_lbl = tk.Label(about_win, text=repo_url, fg="#0000ee", cursor="hand2", font=link_font)
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

    txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=(UI_FONT_FAMILY, 10))
    txt.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    txt.insert(tk.END, text)
    txt.config(state=tk.DISABLED)

    btn = tk.Button(win, text="Close", command=win.destroy, width=10)
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
if sys.platform == "darwin":
    # Keep baseline colors explicit on macOS where Tk can inherit low-contrast defaults.
    root.option_add("*Label.Background", "#f4f7f9")
    root.option_add("*Label.Foreground", "#12343b")
    root.option_add("*Frame.Background", "#f4f7f9")
    root.option_add("*Toplevel.Background", "#f4f7f9")
    root.option_add("*Menu.Background", "#f4f7f9")
    root.option_add("*Menu.Foreground", "#12343b")
    root.option_add("*Entry.Background", "#ffffff")
    root.option_add("*Entry.Foreground", "#12343b")
    root.option_add("*Text.Background", "#fbfcfd")
    root.option_add("*Text.Foreground", "#172b4d")
    try:
        root.tk_setPalette(
            background="#f4f7f9",
            foreground="#12343b",
            activeBackground="#d4e3e8",
            activeForeground="#12343b",
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
file_menu.add_command(label="Exit", command=root.quit)
menu_bar.add_cascade(label="File", menu=file_menu)

# Unified Settings dialog
def open_settings():
    def browse_for_file():
        path = filedialog.askopenfilename(filetypes=[("Text Files", "*.txt")])
        if path:
            default_file_var.set(path)

    def save_and_close():
        nonlocal default_file_var, slack_var, slack_enabled_var
        new_file = default_file_var.get().strip()
        new_slack = slack_var.get().strip()
        global current_file, SLACK_WEBHOOK_URL
        if new_file:
            if not os.path.isabs(new_file):
                new_file = os.path.normpath(os.path.join(base_path, new_file))
            current_file = new_file
        SLACK_WEBHOOK_URL = new_slack
        global SLACK_ENABLED
        SLACK_ENABLED = bool(slack_enabled_var.get())
        file_label.config(text=f"Using file: {current_file}")
        save_config()
        settings_win.destroy()

    settings_win = tk.Toplevel(root)
    settings_win.title("Settings")
    settings_win.geometry("640x220")
    settings_win.transient(root)
    settings_win.resizable(True, False)
    settings_win.columnconfigure(1, weight=1)

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

    tk.Label(settings_win, text="Default URL file:", font=label_font).grid(row=0, column=0, sticky="w", padx=10, pady=8)
    tk.Entry(settings_win, textvariable=default_file_var, font=text_font).grid(row=0, column=1, padx=6, pady=8, sticky="ew")
    browse_button = tk.Button(settings_win, text="Browse...", command=browse_for_file)
    browse_button.grid(row=0, column=2, padx=6, pady=8)
    style_action_button(browse_button)

    tk.Label(settings_win, text="Slack Webhook URL:", font=label_font).grid(row=1, column=0, sticky="w", padx=10, pady=8)
    tk.Entry(settings_win, textvariable=slack_var, font=text_font).grid(row=1, column=1, columnspan=2, padx=6, pady=8, sticky="ew")

    tk.Checkbutton(settings_win, text="Enable Slack alerts", variable=slack_enabled_var).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=8)

    btn_frame = tk.Frame(settings_win)
    btn_frame.grid(row=3, column=0, columnspan=3, sticky="e", padx=6, pady=12)
    save_button = tk.Button(btn_frame, text="Save", command=save_and_close, width=12)
    save_button.pack(side=tk.RIGHT, padx=(6,0))
    style_action_button(save_button, "primary")
    cancel_button = tk.Button(btn_frame, text="Cancel", command=settings_win.destroy, width=12)
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

run_status_label = tk.Label(title_frame, text="Ready for a new check", font=(UI_FONT_FAMILY, 10, "bold"), fg="#52606d", bg="#f4f7f9")
run_status_label.pack(side=tk.RIGHT, padx=(12, 0), pady=(8, 0))
history_button = tk.Button(title_frame, text="History", command=show_history, font=button_font, fg="#176b87")
history_button.pack(side=tk.RIGHT, pady=(8, 0))
style_action_button(history_button, "primary")

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

clear_results_btn = tk.Button(file_actions, text="Clear Results", command=clear_results, font=button_font, fg="orange")
clear_results_btn.pack(side=tk.LEFT, padx=4)
style_action_button(clear_results_btn, "warning")
ToolTip(clear_results_btn, "Clear results")

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

total_label = tk.Label(dashboard_frame, text="Total checked: 0", anchor="w", font=(UI_FONT_FAMILY, 11, "bold"), bg="#e7eef2", fg="#12343b", padx=10, pady=10)
healthy_label = tk.Label(dashboard_frame, text="Healthy: 0", fg="#087f5b", anchor="w", font=(UI_FONT_FAMILY, 11, "bold"), bg="#e3f4ed", padx=10, pady=10)
warning_label = tk.Label(dashboard_frame, text="Warnings: 0", fg="#b54708", anchor="w", font=(UI_FONT_FAMILY, 11, "bold"), bg="#fff0d9", padx=10, pady=10)
error_label = tk.Label(dashboard_frame, text="Errors: 0", fg="#c92a2a", anchor="w", font=(UI_FONT_FAMILY, 11, "bold"), bg="#fde8e7", padx=10, pady=10)
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
export_button = tk.Button(results_header, text="Export Report", command=export_results, font=button_font, fg="#176b87")
export_button.pack(side=tk.RIGHT, padx=(6, 0))
style_action_button(export_button, "primary")

output_box = scrolledtext.ScrolledText(root, width=104, height=20, bd=1, relief=tk.SUNKEN, font=(MONO_FONT_FAMILY, 10), bg="#fbfcfd", fg="#172b4d", padx=8, pady=6)
output_box.pack(padx=18, pady=(6, 14), fill=tk.BOTH, expand=True)
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
        if content:
            clear_results_btn.config(state=tk.NORMAL)
        else:
            clear_results_btn.config(state=tk.DISABLED)
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
    txt = scrolledtext.ScrolledText(win, width=100, height=30, font=(MONO_FONT_FAMILY, 10))
    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    txt.insert(1.0, content)
    txt.config(state=tk.DISABLED)

animate_logo()
update_clock()
root.mainloop()