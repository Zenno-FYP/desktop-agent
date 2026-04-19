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

    def __init__(self, display_sec: int = 7, nudge_log=None):
        """
        Args:
            display_sec: How long the notification window stays visible.
            nudge_log:   Optional NudgeLog instance — when provided, failures
                         to launch the subprocess are recorded as
                         `display_failed` suppressions so the website can
                         surface them in the Zenno Agent stats page.
        """
        self.display_ms = display_sec * 1000
        self._nudge_log = nudge_log

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
            # We deliberately keep the child's stderr connected to ours so any
            # crash (missing dependency, pywebview init failure, etc.) surfaces
            # in the agent log instead of being silently dropped to DEVNULL.
            proc = subprocess.Popen(
                [sys.executable, str(_RUNNER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None,
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
            # Use WARNING (not exception) so noisy retry storms don't fill the
            # log with full tracebacks; the underlying error is still recorded
            # via `logger.exception` in the inner subprocess if it crashes.
            logger.warning(
                "[NudgeNotifier] Failed to launch notification subprocess for %s",
                nudge_type,
                exc_info=True,
            )
            if self._nudge_log is not None:
                try:
                    self._nudge_log.record_suppressed("display_failed")
                except Exception:
                    logger.exception(
                        "[NudgeNotifier] Failed to record display_failed suppression"
                    )
            return False
