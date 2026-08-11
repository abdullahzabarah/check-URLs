import os
import re
import sys
import requests
import time
import json
import tkinter as tk
import tkinter.font as tkfont
from tkinter import scrolledtext, filedialog, ttk, simpledialog, messagebox
import webbrowser

# === SLACK WEBHOOK ===
SLACK_WEBHOOK_URL = "SLACK_WEBHOOK_URL"

# ✅ Handle EXE vs script path
if getattr(sys, 'frozen', False):
    base_path = os.path.dirname(sys.executable)
else:
    base_path = os.path.dirname(__file__)

# ✅ Default file
DEFAULT_FILE = os.path.join(base_path, "urls.txt")

current_file = DEFAULT_FILE
APP_VERSION = "1.0"
APP_YEAR = time.strftime('%Y')
CONFIG_FILE = os.path.join(base_path, "monitor_urls_config.json")

# Slack enabled flag (persisted)
SLACK_ENABLED = True

specific_check_button = None
progress_var = None
progress_label = None
stop_button = None
stop_requested = False


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
        label = tk.Label(tw, text=self.text, justify=tk.LEFT, background="#fff8d5", relief=tk.SOLID, borderwidth=1, font=("Segoe UI", 9))
        label.pack(ipadx=4, ipady=2)

    def hide_tip(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


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


def update_dashboard(total, healthy, warning, error, elapsed=None, last_checked=None):
    total_label.config(text=f"Total checked: {total}")
    healthy_label.config(text=f"Healthy: {healthy}")
    warning_label.config(text=f"Warnings: {warning}")
    error_label.config(text=f"Errors: {error}")

    if elapsed is not None:
        elapsed_label.config(text=f"Elapsed: {elapsed}s")
    if last_checked is not None:
        last_checked_label.config(text=f"Last checked: {last_checked}")
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
            copied_lbl = tk.Label(root, text="Copied!", fg="#008000", bg="#ffffe0", relief=tk.SOLID, borderwidth=1, font=("Segoe UI", 9))
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
            return [specific_url]
        log("⚠️ Enter a valid URL or IP address to check.", "warning")
        return []

    try:
        with open(current_file, "r") as f:
            return [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        log(f"❌ File not found:\n{current_file}", "error")
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


# =====================
# SLACK
# =====================
def send_slack_alert(message):
    if not SLACK_ENABLED:
        log("ℹ️ Slack alerts disabled; skipping Slack notification.", "info")
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


def check_urls(urls):
    global stop_requested
    output_box.delete(1.0, tk.END)
    reset_dashboard()

    if not urls:
        return

    stop_requested = False
    show_stop_button()

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

    update_dashboard(
        total=len(urls),
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
    else:
        log("✓ Stopped.", "warning")

    hide_stop_button()


def check_file_urls():
    urls = get_urls_to_check(use_file=True)
    check_urls(urls)


def check_specific_url():
    urls = get_urls_to_check(use_file=False)
    check_urls(urls)


def clear_specific_url():
    specific_url_var.set("")
    validate_specific_url()

def clear_results():
    output_box.delete(1.0, tk.END)
    reset_dashboard()
    try:
        clear_results_btn.config(state=tk.DISABLED)
    except Exception:
        pass

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
        save_config()

def show_about():
    about_win = tk.Toplevel(root)
    about_win.title("About URL Monitor")
    about_win.geometry("420x220")
    about_win.transient(root)
    about_win.resizable(False, False)

    pad = 10
    tk.Label(about_win, text="URL Monitor", font=("Segoe UI", 14, "bold")).pack(pady=(pad, 0))
    tk.Label(about_win, text=f"Version {APP_VERSION}", font=("Segoe UI", 10)).pack()
    tk.Label(about_win, text="A simple URL checking tool with Slack alerts and clickable logs.", wraplength=380, justify="left", font=("Segoe UI", 9)).pack(pady=(6, 6))

    tk.Label(about_win, text="Created by Abdullah Zabarah", font=("Segoe UI", 10, "bold")).pack()
    tk.Label(about_win, text="abdullahzabarah@gmail.com", font=("Segoe UI", 9)).pack()
    tk.Label(about_win, text=f"© {APP_YEAR} Abdullah Zabarah", font=("Segoe UI", 9)).pack(pady=(4, 0))
    tk.Label(about_win, text="Licensed under the MIT License", font=("Segoe UI", 9)).pack(pady=(2, 0))

    repo_url = "https://github.com/abdullahzabarah/check-URLs.git"
    link_font = tkfont.Font(about_win, family="Segoe UI", size=9, underline=1)
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

    tk.Button(about_win, text="Close", command=about_win.destroy, width=10).pack(pady=(8, 12))


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
        "9. About & Repo: Use Help → About to see author and repo information.\n\n"
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

    txt = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Segoe UI", 10))
    txt.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)
    txt.insert(tk.END, text)
    txt.config(state=tk.DISABLED)

    btn = tk.Button(win, text="Close", command=win.destroy, width=10)
    btn.grid(row=1, column=0, sticky="e", padx=8, pady=(0,8))

def show_version():
    messagebox.showinfo("Version", f"URL Monitor Version: {APP_VERSION}")


# =====================
# GUI
# =====================
load_config()
root = tk.Tk()
root.title("URL Monitor")
root.geometry("820x620")



specific_url_var = tk.StringVar()
progress_var = tk.DoubleVar(value=0.0)

# Use native theme for a normal OS look
style = ttk.Style(root)
# do not force a theme so the OS/native theme is used

button_font = ("Segoe UI", 10)
label_font = ("Segoe UI", 10, "bold")
text_font = ("Segoe UI", 10)
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
    tk.Button(settings_win, text="Browse...", command=browse_for_file).grid(row=0, column=2, padx=6, pady=8)

    tk.Label(settings_win, text="Slack Webhook URL:", font=label_font).grid(row=1, column=0, sticky="w", padx=10, pady=8)
    tk.Entry(settings_win, textvariable=slack_var, font=text_font).grid(row=1, column=1, columnspan=2, padx=6, pady=8, sticky="ew")

    tk.Checkbutton(settings_win, text="Enable Slack alerts", variable=slack_enabled_var).grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=8)

    btn_frame = tk.Frame(settings_win)
    btn_frame.grid(row=3, column=0, columnspan=3, sticky="e", padx=6, pady=12)
    tk.Button(btn_frame, text="Save", command=save_and_close, width=12).pack(side=tk.RIGHT, padx=(6,0))
    tk.Button(btn_frame, text="Cancel", command=settings_win.destroy, width=12).pack(side=tk.RIGHT, padx=(6,0))

settings_menu = tk.Menu(menu_bar, tearoff=0)
settings_menu.add_command(label="Settings...", command=open_settings)
menu_bar.add_cascade(label="Settings", menu=settings_menu)

help_menu = tk.Menu(menu_bar, tearoff=0)
help_menu.add_command(label="Instructions...", command=show_instructions)
help_menu.add_command(label="About", command=show_about)
help_menu.add_command(label="Version", command=show_version)
menu_bar.add_cascade(label="Help", menu=help_menu)
root.config(menu=menu_bar)
root_frame = tk.Frame(root)
root_frame.pack(fill=tk.X, padx=10)

# Left: file info + file actions
left_frame = tk.Frame(root_frame)
left_frame.pack(side=tk.LEFT, anchor="w")

file_label = tk.Label(left_frame, text=f"Using file: {current_file}", font=("Segoe UI", 12, "bold"))
file_label.grid(row=0, column=0, sticky="w", pady=(12, 4))
file_font = tkfont.Font(family="Segoe UI", size=12, weight="bold")
file_font_underline = tkfont.Font(family="Segoe UI", size=12, weight="bold", underline=1)
file_label.config(font=file_font)

def _on_file_enter(e):
    file_label.config(cursor="hand2", font=file_font_underline)

def _on_file_leave(e):
    file_label.config(cursor="", font=file_font)

file_label.bind("<Button-1>", lambda e: open_file_viewer())
file_label.bind("<Enter>", _on_file_enter)
file_label.bind("<Leave>", _on_file_leave)

file_actions = tk.Frame(left_frame)
file_actions.grid(row=1, column=0, sticky="w", pady=(2, 8))

choose_file_btn = tk.Button(file_actions, text="📁 Choose File...", command=browse_file, font=button_font)
choose_file_btn.pack(side=tk.LEFT, padx=4)
ToolTip(choose_file_btn, "Choose URL list file")

check_file_btn = tk.Button(file_actions, text="🔍 Check File", command=check_file_urls, font=button_font, fg="green")
check_file_btn.pack(side=tk.LEFT, padx=4)
ToolTip(check_file_btn, "Check all URLs in file")

clear_results_btn = tk.Button(file_actions, text="🧹 Clear Results", command=clear_results, font=button_font, fg="orange")
clear_results_btn.pack(side=tk.LEFT, padx=4)
ToolTip(clear_results_btn, "Clear results")

stop_button = tk.Button(file_actions, text="🛑 Stop", command=request_stop, font=button_font, fg="red")
ToolTip(stop_button, "Stop the running URL check")

# Specific URL field: move under file actions (new row)
url_frame = tk.Frame(left_frame)
url_frame.grid(row=2, column=0, sticky="w", pady=(6,8))

tk.Label(url_frame, text="Specific URL to check:", font=label_font).pack(side=tk.LEFT)
url_entry = tk.Entry(url_frame, textvariable=specific_url_var, width=58, font=text_font, bd=1, relief=tk.FLAT)
url_entry.pack(side=tk.LEFT, padx=6, pady=2, ipady=4)
specific_check_button = tk.Button(url_frame, text="🌐 Check URL", command=check_specific_url, font=button_font, state=tk.DISABLED, fg="green")
specific_check_button.pack(side=tk.LEFT, padx=4)
ToolTip(specific_check_button, "Check this specific URL")
clear_url_btn = tk.Button(url_frame, text="🗑️ Clear URL", command=clear_specific_url, font=button_font, state=tk.DISABLED, fg="red")
clear_url_btn.pack(side=tk.LEFT, padx=4)
ToolTip(clear_url_btn, "Clear URL input")
url_entry.bind("<KeyRelease>", validate_specific_url)
specific_url_var.trace_add('write', lambda *args: validate_specific_url())
validate_specific_url()

# Progress bar row
progress_frame = tk.Frame(root)
progress_frame.pack(pady=4, fill=tk.X, padx=10)

progress_bar = ttk.Progressbar(progress_frame, variable=progress_var, maximum=100, length=560)
progress_bar.pack(side=tk.LEFT, padx=6, pady=2)
progress_label = tk.Label(progress_frame, text="Progress: 0 / 0", font=button_font)
progress_label.pack(side=tk.LEFT, padx=10)

# Slack controls moved to Settings dialog

# Dashboard frame

dashboard_frame = tk.Frame(root, bd=0, padx=14, pady=12)
dashboard_frame.pack(padx=10, pady=6, fill=tk.X)

total_label = tk.Label(dashboard_frame, text="Total checked: 0", anchor="w", font=("Segoe UI", 11, "bold"))
healthy_label = tk.Label(dashboard_frame, text="Healthy: 0", fg="#008000", anchor="w", font=("Segoe UI", 11, "bold"))
warning_label = tk.Label(dashboard_frame, text="Warnings: 0", fg="#d2691e", anchor="w", font=("Segoe UI", 11, "bold"))
error_label = tk.Label(dashboard_frame, text="Errors: 0", fg="#ff0000", anchor="w", font=("Segoe UI", 11, "bold"))
elapsed_label = tk.Label(dashboard_frame, text="Elapsed: 0.0s", anchor="w", font=("Segoe UI", 10))
last_checked_label = tk.Label(dashboard_frame, text="Last checked: -", anchor="w", font=("Segoe UI", 10))
clock_label = tk.Label(dashboard_frame, text="Current time: -", anchor="e", font=("Segoe UI", 10, "italic"))

total_label.grid(row=0, column=0, sticky="w", padx=6, pady=3)
healthy_label.grid(row=0, column=1, sticky="w", padx=6, pady=3)
warning_label.grid(row=0, column=2, sticky="w", padx=6, pady=3)
error_label.grid(row=0, column=3, sticky="w", padx=6, pady=3)
elapsed_label.grid(row=1, column=0, sticky="w", padx=6, pady=3)
last_checked_label.grid(row=1, column=1, sticky="w", padx=6, pady=3)
clock_label.grid(row=1, column=2, columnspan=2, sticky="e", padx=6, pady=3)

# Output box
output_box = scrolledtext.ScrolledText(root, width=104, height=20, bd=1, relief=tk.SUNKEN, font=("Consolas", 10))
output_box.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)
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
    txt = scrolledtext.ScrolledText(win, width=100, height=30, font=("Consolas", 10))
    txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    txt.insert(1.0, content)
    txt.config(state=tk.DISABLED)

update_clock()
root.mainloop()