"""
Zenno Desktop Agent — Main Entry Point

1. Opens the authentication WebView (sign-in)
2. On success, starts the desktop activity agent
"""
import sys
import logging
import os
import requests
from pathlib import Path

# Ensure project root is on the path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.config import Config
from database.db import Database
from auth.webview_app import run_auth_window
from auth.tokens import get_valid_id_token, TokenError
from nudge.user_preferences import UserPreferences, load_from_db, run_onboarding

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env")

_BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_TIMEOUT = 15

logger = logging.getLogger("main")


# ── Backend preference helpers ────────────────────────────────────────────────

def _fetch_preferences_from_backend() -> dict | None:
    """GET /agent/preferences from the backend. Returns the data dict or None."""
    try:
        token = get_valid_id_token()
        resp = requests.get(
            f"{_BACKEND_BASE_URL}/agent/preferences",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return resp.json().get("data", {})
    except Exception as exc:
        logger.warning("[Main] Could not fetch backend preferences: %s", exc)
    return None


def _push_preferences_to_backend(prefs: UserPreferences) -> bool:
    """PUT /agent/preferences — returns True on success."""
    try:
        token = get_valid_id_token()
        resp = requests.put(
            f"{_BACKEND_BASE_URL}/agent/preferences",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "work_schedule":      prefs.work_schedule,
                "focus_style":        prefs.focus_style,
                "wellbeing_goal":     prefs.wellbeing_goal,
                "nudge_enabled":      prefs.nudge_enabled,
                "notification_sound": prefs.notification_sound,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            logger.info("[Main] Preferences pushed to backend successfully")
            return True
        logger.warning("[Main] Push preferences HTTP %s", resp.status_code)
    except (TokenError, Exception) as exc:
        logger.warning("[Main] Could not push preferences to backend: %s", exc)
    return False


def _prefs_from_backend_data(data: dict) -> UserPreferences:
    """Build a UserPreferences from a backend /agent/preferences response."""
    return UserPreferences(
        work_schedule      = data.get("work_schedule", "standard"),
        focus_style        = data.get("focus_style", "moderate"),
        wellbeing_goal     = data.get("wellbeing_goal", "focused"),
        nudge_enabled      = bool(data.get("nudge_enabled", True)),
        notification_sound = bool(data.get("notification_sound", False)),
    )


def _mark_onboarding_done(db_path: str, prefs: UserPreferences) -> None:
    """Persist preferences and stamp onboarding_completed_at in the local DB."""
    import sqlite3
    from datetime import datetime
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO user_preferences
                (id, work_schedule, focus_style, wellbeing_goal, nudge_enabled,
                 notification_sound, onboarding_completed_at, onboarding_version)
            VALUES (1, ?, ?, ?, ?, ?, ?, 1)
            """,
            prefs.to_db_tuple() + (datetime.now().isoformat(),),
        )
        conn.commit()
        conn.close()
        logger.info("[Main] Local user_preferences saved (onboarding complete)")
    except Exception:
        logger.exception("[Main] Failed to save preferences locally")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # ── 1) Load config & set up logging ──────────────────────
    config = Config()

    cfg = config.get("logging", {}) or {}
    level_name = str(cfg.get("level", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    log_format = cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_file = cfg.get("file")

    handlers = [logging.StreamHandler()]
    if log_file:
        try:
            log_path = Path(str(log_file))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        except Exception:
            pass

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    # ── 2) Connect DB (light — just for user table) ──────────
    db_path = config.get("db.path", "./data/db/zenno.db")
    db = Database(
        db_path,
        check_same_thread=config.get("db.check_same_thread", False),
        timeout=config.get("db.timeout", 10.0),
        journal_mode=config.get("db.journal_mode", "WAL"),
        config=config,
    )
    db.connect()
    db.create_tables()

    # ── 3) Show auth window (blocks until user signs in or closes) ──
    logger.info("[Main] Opening authentication window…")
    user_data = run_auth_window(db)

    if not user_data:
        logger.info("[Main] Auth window closed without sign-in — exiting.")
        db.close()
        sys.exit(0)

    logger.info("[Main] Authenticated as: %s (%s)", user_data.get("name"), user_data.get("email"))

    # ── 4) Resolve preferences ───────────────────────────────
    # Priority order:
    #   a) If local DB marks onboarding completed → use local prefs (may be updated by poller)
    #   b) Else try backend → if it has preferences, import them locally and skip onboarding
    #   c) Else run the onboarding wizard then push to backend

    if db.has_onboarding_completed():
        # Returning user: load what the poller last wrote (or original onboarding answers)
        prefs = load_from_db(db_path)
        logger.info("[Main] Loaded existing preferences (onboarding already done): schedule=%s", prefs.work_schedule)

    else:
        # First run on this machine — check if the user already configured things on the website
        logger.info("[Main] No local onboarding record. Checking backend for existing preferences…")
        backend_data = _fetch_preferences_from_backend()

        if backend_data:
            # User has used the website (or another device) — import those preferences
            prefs = _prefs_from_backend_data(backend_data)
            _mark_onboarding_done(db_path, prefs)
            logger.info(
                "[Main] Imported preferences from backend — skipping onboarding: schedule=%s",
                prefs.work_schedule,
            )
        else:
            # Truly first run: show the personalisation wizard
            logger.info("[Main] First run — showing personalisation wizard…")
            prefs = run_onboarding(db_path)
            logger.info(
                "[Main] Onboarding complete: schedule=%s focus=%s goal=%s",
                prefs.work_schedule, prefs.focus_style, prefs.wellbeing_goal,
            )

            # Push to backend so the website reflects the user's choices immediately
            pushed = _push_preferences_to_backend(prefs)
            if not pushed:
                logger.warning(
                    "[Main] Preferences could not be pushed to backend now — "
                    "PreferencesPoller will retry within 5 minutes."
                )

    # Close the lightweight DB connection; DesktopAgent will open its own.
    db.close()

    # ── 5) Start the agent ───────────────────────────────────
    logger.info("[Main] Launching desktop agent…")
    from agent import DesktopAgent
    agent = DesktopAgent(user_preferences=prefs)
    agent.start()


if __name__ == "__main__":
    main()
