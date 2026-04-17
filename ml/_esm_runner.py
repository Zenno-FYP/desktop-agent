"""
ESM popup subprocess runner.

Launched by ESMPopup as a child process. Reads JSON payload from stdin,
shows a pywebview window, and writes the user's choice (or null) to stdout.
"""
import sys
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

try:
    import webview
except ImportError:
    sys.exit(0)

# ── Read payload from stdin ────────────────────────────────────────────────────
try:
    raw = sys.stdin.readline()
    data = json.loads(raw)
except Exception as e:
    logging.error("ESM runner: failed to parse stdin: %s", e)
    sys.exit(1)

predicted_state = data.get("predicted_state", "")
confidence      = float(data.get("confidence", 0.0))
signal_chips    = data.get("signal_chips", [])
dismiss_ms      = int(data.get("dismiss_ms", 30000))

# ── Screen position (top-right) ───────────────────────────────────────────────
WIN_W, WIN_H = 520, 310
screen_w, screen_h = 1920, 1080

try:
    import ctypes
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
except Exception:
    pass

x_pos = max(0, screen_w - WIN_W - 24)
y_pos = 24

# ── HTML path ─────────────────────────────────────────────────────────────────
UI_PATH = Path(__file__).resolve().parent / "ui" / "esm.html"

# ── Result holder ─────────────────────────────────────────────────────────────
_result = {"label": None}


class ESMBridge:
    """Exposed to JS as window.pywebview.api."""

    def __init__(self):
        self._window = None

    def bind(self, window):
        self._window = window

    def submit(self, label):
        _result["label"] = label
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass


bridge = ESMBridge()


def _on_loaded():
    try:
        conf_pct = round(confidence * 100)
        js = (
            f"init("
            f"{json.dumps(predicted_state)}, "
            f"{json.dumps(conf_pct)}, "
            f"{json.dumps(signal_chips)}, "
            f"{dismiss_ms}"
            f");"
        )
        window.evaluate_js(js)
    except Exception as e:
        logging.error("ESM runner: evaluate_js failed: %s", e)


window = webview.create_window(
    title="Zenno — Context Check",
    url=UI_PATH.as_uri(),
    js_api=bridge,
    width=WIN_W,
    height=WIN_H,
    x=x_pos,
    y=y_pos,
    resizable=False,
    frameless=False,
    on_top=True,
    shadow=True,
    background_color="#0f0f13",
    text_select=False,
    zoomable=False,
)
bridge.bind(window)
window.events.loaded += _on_loaded

webview.start(debug=False)

# ── Write result to stdout so parent can read it ──────────────────────────────
sys.stdout.write(json.dumps({"label": _result["label"]}) + "\n")
sys.stdout.flush()
