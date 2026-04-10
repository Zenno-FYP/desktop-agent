"""
Notification subprocess runner.

Launched by NudgeNotifier as a child process. Reads JSON payload from stdin,
then opens a frameless pywebview window at the bottom-right of the screen.
Auto-closes when the JS timer fires or the user clicks dismiss.
"""
import sys
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

try:
    import webview
except ImportError:
    # If pywebview is not available, exit silently (notifier handles this)
    sys.exit(0)

# ── Read payload from stdin ────────────────────────────────────────────────────
try:
    raw = sys.stdin.readline()
    data = json.loads(raw)
except Exception as e:
    logging.error("Notification runner: failed to parse stdin: %s", e)
    sys.exit(1)

nudge_type   = data.get("nudge_type", "MOTIVATION")
nudge_text   = data.get("nudge_text", "")
display_ms   = int(data.get("display_ms", 7000))

# ── Screen dimensions (Windows) ───────────────────────────────────────────────
NOTIF_W, NOTIF_H = 380, 108
screen_w, screen_h = 1920, 1080

try:
    import ctypes
    user32 = ctypes.windll.user32
    # Use virtual screen metrics to handle DPI-aware scaling
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
except Exception:
    pass

x_pos = max(0, screen_w - NOTIF_W - 24)
y_pos = 24   # top-right, just below the screen edge

# ── HTML UI path ──────────────────────────────────────────────────────────────
UI_PATH = Path(__file__).resolve().parent / "ui" / "notification.html"


class NotifBridge:
    """Exposed to JS so dismiss() can close the window."""

    def __init__(self):
        self._window = None

    def bind(self, window):
        self._window = window

    def close_window(self):
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass


bridge = NotifBridge()


def _on_loaded():
    try:
        js = (
            f'init({json.dumps(nudge_type)}, {json.dumps(nudge_text)}, {display_ms});'
        )
        window.evaluate_js(js)
    except Exception as e:
        logging.error("Notification runner: evaluate_js failed: %s", e)


window = webview.create_window(
    title="Zenno",
    url=UI_PATH.as_uri(),
    js_api=bridge,
    width=NOTIF_W,
    height=NOTIF_H,
    x=x_pos,
    y=y_pos,
    resizable=False,
    frameless=True,
    on_top=True,
    shadow=True,
    background_color="#1a1a24",
    text_select=False,
    easy_drag=False,
    zoomable=False,
)
bridge.bind(window)
window.events.loaded += _on_loaded

webview.start(debug=False)
