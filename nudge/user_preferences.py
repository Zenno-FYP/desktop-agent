"""UserPreferences — personalisation data collected during onboarding.

Stored in the `user_preferences` table (single row, id=1).
Loaded once at agent start and used to override config-file defaults for the
nudge scheduler, context aggregator, and LLM prompt persona.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Lookup tables (keep in sync with onboarding.html) ─────────────────────────

_LATE_NIGHT: dict[str, int] = {
    "morning":   15,
    "standard":  19,
    "evening":   21,
    "night_owl": 23,
}

# (quiet_from_hour, quiet_until_hour)
# Interpretation:
#   if quiet_from > quiet_until  → spans midnight: quiet if hour >= from OR hour < until
#   if quiet_from < quiet_until  → intra-day:       quiet if from <= hour < until
_QUIET_WINDOW: dict[str, tuple[int, int]] = {
    "morning":   (16, 6),
    "standard":  (20, 8),
    "evening":   (22, 11),
    "night_owl": (1,  14),
}

_BREAK_REMINDER: dict[str, int] = {
    "deep":     110,
    "moderate":  75,
    "pomodoro":  35,
}

_FLOW_STREAK: dict[str, int] = {
    "deep":   60,
    "moderate": 40,
    "pomodoro": 20,
}

_NUDGE_INTERVAL_OVERRIDE: dict[str, int | None] = {
    "focused":  None,   # use config default
    "burnout":  None,
    "habits":   None,
    "minimal":  90,     # less frequent
}

_DISABLED_TYPES: dict[str, list[str]] = {
    "focused":  [],
    "burnout":  [],
    "habits":   [],
    "minimal":  ["REENGAGEMENT", "MOTIVATION"],
}

_PERSONA: dict[str, str] = {
    "focused": (
        "The developer wants to stay in deep focus. "
        "Celebrate sustained concentration. Call out distraction directly but briefly."
    ),
    "burnout": (
        "The developer is actively watching for burnout signals. "
        "Prioritise rest and recovery cues. Keep the tone warm and non-judgemental."
    ),
    "habits": (
        "The developer is building better work habits. "
        "Reference day-level patterns when they add insight (e.g. 'third distracted stretch today'). "
        "Be encouraging, not prescriptive."
    ),
    "minimal": (
        "The developer just wants tracking, not coaching. "
        "Be extremely brief. Only mention something if it genuinely matters. No fluff."
    ),
}


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class UserPreferences:
    work_schedule:  str  = "standard"   # morning | standard | evening | night_owl
    focus_style:    str  = "moderate"   # deep | moderate | pomodoro
    wellbeing_goal: str  = "focused"    # focused | burnout | habits | minimal
    has_meetings:   bool = False

    # ── Computed overrides ─────────────────────────────────────────────────

    @property
    def late_night_hour(self) -> int:
        """Hour after which LATE_NIGHT nudge fires."""
        return _LATE_NIGHT.get(self.work_schedule, 19)

    @property
    def quiet_window(self) -> tuple[int, int]:
        """(quiet_from_hour, quiet_until_hour) — no nudges outside work window."""
        return _QUIET_WINDOW.get(self.work_schedule, (20, 8))

    def is_quiet_hour(self, hour: int) -> bool:
        """Return True if the given hour falls inside the user's quiet window."""
        qs, qe = self.quiet_window
        if qs > qe:
            # Spans midnight (e.g. morning: quiet after 16 OR before 6)
            return hour >= qs or hour < qe
        else:
            # Intra-day (e.g. night_owl: quiet 01:00–14:00)
            return qs <= hour < qe

    @property
    def break_reminder_min(self) -> int:
        """Minutes without a break before BREAK_REMINDER fires."""
        return _BREAK_REMINDER.get(self.focus_style, 75)

    @property
    def flow_streak_min(self) -> int:
        """Minimum consecutive Flow minutes to trigger FLOW_CELEBRATION."""
        return _FLOW_STREAK.get(self.focus_style, 40)

    @property
    def meeting_suppression_threshold(self) -> float:
        """Communication ratio above which nudges are suppressed during meetings."""
        # Heavy meeting schedule → lower threshold (suppress more eagerly)
        return 0.60 if self.has_meetings else 0.80

    @property
    def nudge_interval_override_min(self) -> int | None:
        """Override nudge interval (minutes). None = use config default."""
        return _NUDGE_INTERVAL_OVERRIDE.get(self.wellbeing_goal)

    @property
    def disabled_nudge_types(self) -> list[str]:
        """Nudge types suppressed entirely for this user's goal."""
        return _DISABLED_TYPES.get(self.wellbeing_goal, [])

    @property
    def llm_persona_instruction(self) -> str:
        """Extra instruction passed to the nudge NLP API to tune the voice."""
        return _PERSONA.get(self.wellbeing_goal, "")

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_db_tuple(self) -> tuple:
        return (
            self.work_schedule,
            self.focus_style,
            self.wellbeing_goal,
            1 if self.has_meetings else 0,
        )

    @staticmethod
    def from_row(row) -> "UserPreferences":
        return UserPreferences(
            work_schedule  = row[0] or "standard",
            focus_style    = row[1] or "moderate",
            wellbeing_goal = row[2] or "focused",
            has_meetings   = bool(row[3]),
        )


# ── DB helpers ─────────────────────────────────────────────────────────────────

def load_from_db(db_path: str) -> UserPreferences:
    """Return stored preferences or factory defaults if none exist."""
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT work_schedule, focus_style, wellbeing_goal, has_meetings "
            "FROM user_preferences WHERE id = 1"
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return UserPreferences.from_row(row)
    except Exception:
        logger.warning("[UserPreferences] Could not load from DB — using defaults")
    return UserPreferences()


# ── Onboarding subprocess launcher ─────────────────────────────────────────────

def run_onboarding(db_path: str) -> UserPreferences:
    """Show the onboarding UI in a blocking subprocess.

    Returns the user's choices as a UserPreferences instance (or defaults if the
    window is closed without completing the flow).  Saves the result to the DB.
    """
    runner = Path(__file__).resolve().parent / "_onboarding_runner.py"
    try:
        proc = subprocess.Popen(
            [sys.executable, str(runner)],
            stdout=subprocess.PIPE,
            text=True,
        )
        stdout, _ = proc.communicate(timeout=600)  # 10-minute timeout
        data = json.loads(stdout.strip())
    except subprocess.TimeoutExpired:
        logger.warning("[Onboarding] Timed out — using defaults")
        proc.kill()
        data = {}
    except Exception:
        logger.warning("[Onboarding] Could not complete — using defaults")
        data = {}

    prefs = UserPreferences(
        work_schedule  = data.get("work_schedule", "standard"),
        focus_style    = data.get("focus_style", "moderate"),
        wellbeing_goal = data.get("wellbeing_goal", "focused"),
        has_meetings   = bool(data.get("has_meetings", 0)),
    )

    # Persist to DB
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT OR REPLACE INTO user_preferences
                (id, work_schedule, focus_style, wellbeing_goal, has_meetings,
                 onboarding_completed_at, onboarding_version)
            VALUES (1, ?, ?, ?, ?, ?, 1)
            """,
            prefs.to_db_tuple() + (datetime.now().isoformat(),),
        )
        conn.commit()
        conn.close()
        logger.info(
            "[Onboarding] Saved preferences: schedule=%s focus=%s goal=%s meetings=%s",
            prefs.work_schedule, prefs.focus_style,
            prefs.wellbeing_goal, prefs.has_meetings,
        )
    except Exception:
        logger.exception("[Onboarding] Failed to save preferences to DB")

    return prefs
