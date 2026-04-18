"""NudgeGenerator — custom NLP API (Zenno nudge service) with fallback template library."""
import os
import random
import logging
import requests
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from nudge.nudge_context import NudgeContext

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

_NUDGE_API_URL: str = os.getenv("NUDGE_API_URL", "http://localhost:8000").rstrip("/")
_NUDGE_API_SECRET: str = os.getenv("NUDGE_API_SECRET", "")

_warned_once: bool = False

if not _NUDGE_API_URL:
    logger.warning(
        "[NudgeGenerator] NUDGE_API_URL is empty — nudges will use local fallback templates"
    )

# ── Fallback Template Library ──────────────────────────────────────────────────

FALLBACK_TEMPLATES: dict[str, list[str]] = {
    "BREAK_REMINDER": [
        "You've been at it for {min_since_break:.0f} minutes. Step away, even for 5.",
        "No breaks in {min_since_break:.0f} min — your brain needs a reset.",
        "{min_since_break:.0f} minutes straight. A short walk goes a long way.",
    ],
    "FLOW_CELEBRATION": [
        "You've been in Flow for {consecutive_flow_min:.0f} minutes. Don't stop.",
        "{consecutive_flow_min:.0f} minutes of pure focus — that's rare. Keep it up.",
        "Deep work streak: {consecutive_flow_min:.0f} min. You're in it.",
    ],
    "REENGAGEMENT": [
        "Scattered last hour. Pick one thing and start there.",
        "Hard to focus? Close everything except {top_project}.",
        "You've been distracted lately. Reset with one clear task.",
    ],
    "MOTIVATION": [
        "{active_min:.0f} minutes in. Good day on {top_project}.",
        "Solid work on {top_project} today. Keep the momentum.",
        "Nice session. {active_min:.0f} focused minutes and counting.",
    ],
    "FATIGUE_WARNING": [
        "Your pace has dropped. That's your body's cue — take 10.",
        "Longer sessions ≠ better sessions. A break now helps tomorrow.",
        "Signs of fatigue. Step back before you make mistakes.",
    ],
    "LATE_NIGHT": [
        "Still here? Respect. Wrap up in 30 if you can.",
        "Late session on {top_project}. Make sure you sleep.",
        "Great dedication — but rest is part of the process.",
    ],
    "ACHIEVEMENT": [
        "{flow_pct:.0f}% Flow today. That's a great day by any measure.",
        "{active_min:.0f} focused minutes on {top_project}. Ship it.",
        "Flow ratio today: {flow_pct:.0f}%. You're doing something right.",
    ],
}


def _render_template(template: str, ctx: NudgeContext) -> str:
    """Fill in placeholders from NudgeContext, ignoring missing keys gracefully."""
    try:
        return template.format(
            min_since_break=ctx.min_since_last_break,
            consecutive_flow_min=ctx.consecutive_flow_min,
            top_project=ctx.top_project_today or "your project",
            active_min=ctx.total_active_min_today,
            flow_pct=ctx.context_today.get("Flow", 0.0) * 100,
        )
    except (KeyError, ValueError):
        return template


def _fallback(ctx: NudgeContext) -> str:
    """Pick a random fallback template for the recommended nudge type."""
    templates = FALLBACK_TEMPLATES.get(ctx.recommended_nudge_type, FALLBACK_TEMPLATES["MOTIVATION"])
    return _render_template(random.choice(templates), ctx)


# ── Zenno NLP API ─────────────────────────────────────────────────────────────

def _call_nudge_api(ctx: NudgeContext, persona: str, timeout_sec: float) -> Optional[str]:
    """POST /generate on the nudge service; return text or None on failure."""
    global _warned_once

    if not _NUDGE_API_URL:
        return None

    context_last_window: dict = ctx.context_last_window or {}
    if context_last_window:
        current_state = max(context_last_window, key=context_last_window.get)
    else:
        current_state = "Unknown"

    payload = {
        "nudge_type": ctx.recommended_nudge_type,
        "fatigue_level": ctx.fatigue_level,
        "min_since_last_break": float(ctx.min_since_last_break),
        "consecutive_flow_min": float(ctx.consecutive_flow_min),
        "flow_pct": float((ctx.context_today or {}).get("Flow", 0.0) * 100),
        "current_state": current_state,
        "active_min": float(ctx.total_active_min_today),
        "top_project": ctx.top_project_today or "your project",
        "persona": persona or "",
    }

    headers: dict[str, str] = {}
    if _NUDGE_API_SECRET:
        headers["x-api-key"] = _NUDGE_API_SECRET

    try:
        resp = requests.post(
            f"{_NUDGE_API_URL}/generate",
            json=payload,
            headers=headers,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data.get("nudge_text", "").strip()
        if not text:
            logger.warning("[NudgeGenerator] Nudge API returned empty nudge_text")
            return None
        logger.info("[NudgeGenerator] NLP API generated: %s", text)
        return text
    except requests.exceptions.Timeout:
        if not _warned_once:
            logger.warning(
                "[NudgeGenerator] Nudge API timed out after %.1fs — using fallback templates. "
                "(This warning will not repeat.)",
                timeout_sec,
            )
            _warned_once = True
        return None
    except requests.exceptions.ConnectionError:
        if not _warned_once:
            logger.warning(
                "[NudgeGenerator] Nudge API unreachable at %s — using fallback templates. "
                "(This warning will not repeat.)",
                _NUDGE_API_URL,
            )
            _warned_once = True
        return None
    except Exception:
        logger.exception("[NudgeGenerator] Nudge API call failed — using fallback")
        return None


# ── Public API ─────────────────────────────────────────────────────────────────

class NudgeGenerator:
    """Generate nudge text via the Zenno NLP service, with graceful template fallback."""

    def __init__(self, llm_enabled: bool = True, llm_timeout_sec: float = 8.0):
        self.llm_enabled = llm_enabled
        self.llm_timeout_sec = llm_timeout_sec

    def generate(self, ctx: NudgeContext, persona: str = "") -> tuple[str, bool]:
        """Return (nudge_text, llm_used).

        persona: optional instruction from UserPreferences (wellbeing goal voice).
        Tries the nudge API first; falls back to templates on any failure.
        """
        if self.llm_enabled:
            text = _call_nudge_api(ctx, persona, timeout_sec=self.llm_timeout_sec)
            if text:
                return text, True

        return _fallback(ctx), False
