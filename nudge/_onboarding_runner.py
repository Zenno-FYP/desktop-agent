"""
Onboarding subprocess runner.

Launched by user_preferences.run_onboarding() as a blocking child process.
Shows a 4-step personalisation wizard using pywebview, then writes the user's
choices as a JSON line to stdout so the parent can read them.
"""
import sys
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

try:
    import webview
except ImportError:
    # pywebview not available — emit defaults and exit
    sys.stdout.write(json.dumps({}) + "\n")
    sys.stdout.flush()
    sys.exit(0)

WIN_W, WIN_H = 500, 600

# ── Centre on screen (Windows) ────────────────────────────────────────────────
screen_w, screen_h = 1920, 1080
try:
    import ctypes
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
except Exception:
    pass

x_pos = max(0, (screen_w - WIN_W) // 2)
y_pos = max(0, (screen_h - WIN_H) // 2)

UI_PATH = Path(__file__).resolve().parent / "ui" / "onboarding.html"

# ── Result holder ─────────────────────────────────────────────────────────────
_result: dict = {}


class OnboardingBridge:
    """Exposed to JS as window.pywebview.api."""

    def __init__(self):
        self._window = None

    def bind(self, window):
        self._window = window

    def submit(self, work_schedule: str, focus_style: str,
               wellbeing_goal: str, has_meetings: int) -> None:
        """Called by JS when the user completes the last step."""
        _result.update({
            "work_schedule":  work_schedule,
            "focus_style":    focus_style,
            "wellbeing_goal": wellbeing_goal,
            "has_meetings":   int(has_meetings),
        })
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

    def skip(self) -> None:
        """Called by JS if the user closes the wizard early — use defaults."""
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass


bridge = OnboardingBridge()

window = webview.create_window(
    title="Zenno — Quick Setup",
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

webview.start(debug=False)

# ── Write result to stdout ────────────────────────────────────────────────────
sys.stdout.write(json.dumps(_result) + "\n")
sys.stdout.flush()
