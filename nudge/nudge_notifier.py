"""NudgeNotifier — shows a beautiful CSS notification window via a subprocess."""
import sys
import json
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_RUNNER = Path(__file__).resolve().parent / "_notification_runner.py"

NUDGE_LABELS = {
    "BREAK_REMINDER":   "Break Reminder",
    "FLOW_CELEBRATION": "Flow State",
    "REENGAGEMENT":     "Refocus",
    "MOTIVATION":       "Motivation",
    "FATIGUE_WARNING":  "Fatigue Warning",
    "LATE_NIGHT":       "Working Late",
    "ACHIEVEMENT":      "Achievement",
}


class NudgeNotifier:
    """
    Launches a pywebview notification window in a child process.

    Using a subprocess guarantees the pywebview event loop runs independently
    from the agent's main thread, and multiple notifications cannot conflict.
    """

    def __init__(self, display_sec: int = 7):
        self.display_ms = display_sec * 1000

    def show(self, nudge_type: str, nudge_text: str, play_sound: bool = False) -> bool:
        """
        Show the nudge notification. Non-blocking — returns immediately.

        Args:
            nudge_type:  One of the 7 nudge taxonomy types.
            nudge_text:  The text to display.
            play_sound:  If True, the notification plays a short chime.

        Returns:
            True if subprocess was launched, False on error.
        """
        payload = json.dumps({
            "nudge_type": nudge_type,
            "nudge_text": nudge_text,
            "display_ms": self.display_ms,
            "play_sound": play_sound,
        })

        try:
            proc = subprocess.Popen(
                [sys.executable, str(_RUNNER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            proc.stdin.write((payload + "\n").encode("utf-8"))
            proc.stdin.close()
            logger.info(
                "[NudgeNotifier] Showing %s notification: %s",
                nudge_type,
                nudge_text[:60],
            )
            return True
        except Exception:
            logger.exception("[NudgeNotifier] Failed to launch notification subprocess")
            return False
