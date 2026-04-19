"""PreferencesPoller — polls GET /api/v1/agent/preferences and applies
any changes made on the website to the local SQLite user_preferences table.

When the backend returns preferences that differ from what is stored locally,
the poller writes the update and triggers an optional callback so the running
NudgeScheduler can reload its settings without restarting.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from typing import Callable, Optional

import requests
from dotenv import load_dotenv

from auth.tokens import get_valid_id_token, TokenError
from nudge.user_preferences import UserPreferences

load_dotenv()

logger = logging.getLogger(__name__)

_BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_POLL_INTERVAL_SEC = int(os.getenv("PREFS_POLL_INTERVAL_SEC", "300"))  # 5 minutes
_TIMEOUT = 15


class PreferencesPoller:
    """Background thread that syncs agent preferences from the backend."""

    def __init__(
        self,
        db_path: str,
        on_change: Optional[Callable[[UserPreferences], None]] = None,
    ):
        self.db_path = db_path
        self._on_change = on_change
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="PreferencesPoller", daemon=True
        )
        self._thread.start()
        logger.info("[PreferencesPoller] Started (interval=%ds)", _POLL_INTERVAL_SEC)

    def stop(self) -> None:
        self._stop.set()

    def poll_now(self) -> Optional[UserPreferences]:
        """Trigger an immediate poll (blocking). Returns updated prefs or None."""
        return self._do_poll()

    # ── Background loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Initial delay to let auth complete
        time.sleep(60)
        while not self._stop.is_set():
            self._do_poll()
            self._stop.wait(_POLL_INTERVAL_SEC)

    # ── Core poll ──────────────────────────────────────────────────────────────

    def _do_poll(self) -> Optional[UserPreferences]:
        try:
            token = self._get_token()
            if not token:
                return None

            resp = requests.get(
                f"{_BACKEND_BASE_URL}/agent/preferences",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

            data: dict = resp.json().get("data", {})
            remote = UserPreferences(
                work_schedule      = data.get("work_schedule", "standard"),
                focus_style        = data.get("focus_style", "moderate"),
                wellbeing_goal     = data.get("wellbeing_goal", "focused"),
                nudge_enabled      = bool(data.get("nudge_enabled", True)),
                notification_sound = bool(data.get("notification_sound", False)),
            )

            local = self._load_local()
            if self._differs(remote, local):
                self._save_local(remote)
                logger.info(
                    "[PreferencesPoller] Preferences updated from backend: "
                    "schedule=%s focus=%s goal=%s nudge_enabled=%s sound=%s",
                    remote.work_schedule, remote.focus_style,
                    remote.wellbeing_goal, remote.nudge_enabled,
                    remote.notification_sound,
                )
                if self._on_change:
                    try:
                        self._on_change(remote)
                    except Exception:
                        logger.exception("[PreferencesPoller] on_change callback failed")
            else:
                logger.debug("[PreferencesPoller] No preference changes detected")

            return remote

        except TokenError as e:
            logger.warning("[PreferencesPoller] Token error: %s", e)
        except requests.HTTPError as e:
            logger.warning("[PreferencesPoller] HTTP error: %s", e)
        except requests.RequestException as e:
            logger.warning("[PreferencesPoller] Network error: %s", e)
        except Exception:
            logger.exception("[PreferencesPoller] Unexpected error")
        return None

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_token(self) -> Optional[str]:
        try:
            return get_valid_id_token()
        except Exception as e:
            logger.warning("[PreferencesPoller] Could not obtain token: %s", e)
            return None

    def _load_local(self) -> Optional[UserPreferences]:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute(
                "SELECT work_schedule, focus_style, wellbeing_goal, nudge_enabled, notification_sound "
                "FROM user_preferences WHERE id = 1"
            )
            row = cur.fetchone()
            conn.close()
            if row:
                return UserPreferences.from_row(row)
        except Exception:
            # Don't silently swallow — when the local DB is unreadable we want
            # operators to see WHY (locked, schema drift, missing column …).
            # Returning None falls back to "treat remote as new", which is the
            # safe default.
            logger.exception("[PreferencesPoller] Failed to read local preferences")
        return None

    def _save_local(self, prefs: UserPreferences) -> None:
        """Write only the preference columns — never touches onboarding_completed_at."""
        try:
            conn = sqlite3.connect(self.db_path)
            # Ensure the row exists first (INSERT OR IGNORE leaves onboarding data intact)
            conn.execute(
                "INSERT OR IGNORE INTO user_preferences (id) VALUES (1)"
            )
            # Update only the synced columns — onboarding_completed_at is untouched
            conn.execute(
                """
                UPDATE user_preferences
                SET work_schedule      = ?,
                    focus_style        = ?,
                    wellbeing_goal     = ?,
                    nudge_enabled      = ?,
                    notification_sound = ?
                WHERE id = 1
                """,
                (
                    prefs.work_schedule,
                    prefs.focus_style,
                    prefs.wellbeing_goal,
                    int(prefs.nudge_enabled),
                    int(prefs.notification_sound),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("[PreferencesPoller] Failed to write local preferences")

    @staticmethod
    def _differs(a: UserPreferences, b: Optional[UserPreferences]) -> bool:
        if b is None:
            return True
        return (
            a.work_schedule      != b.work_schedule      or
            a.focus_style        != b.focus_style        or
            a.wellbeing_goal     != b.wellbeing_goal     or
            a.nudge_enabled      != b.nudge_enabled      or
            a.notification_sound != b.notification_sound
        )
