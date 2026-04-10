"""NudgeGenerator — Gemini LLM call with fallback template library."""
import os
import random
import logging
import requests
from pathlib import Path
from dotenv import load_dotenv

from nudge.nudge_context import NudgeContext

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

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


# ── Gemini API Call ────────────────────────────────────────────────────────────

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

_SYSTEM_PROMPT = (
    "You are a friendly, perceptive engineering coach embedded inside a developer "
    "productivity tool called Zenno. You generate short, human nudges — never more "
    "than 2 sentences — that a developer sees as a desktop notification.\n\n"
    "Rules:\n"
    "- Sound like a smart colleague, not a corporate wellness bot\n"
    "- Never lecture, never be preachy\n"
    "- Match the nudge type to the developer's situation\n"
    "- Use specific numbers when they're impressive (e.g., '3 hours of Flow today')\n"
    "- Keep it under 25 words total\n"
    "- Tone should match time of day and energy level\n"
    "- Output ONLY the nudge text, nothing else"
)


def _build_user_prompt(ctx: NudgeContext) -> str:
    dominant_window_state = (
        max(ctx.context_last_window.items(), key=lambda x: x[1])[0]
        if ctx.context_last_window
        else "Unknown"
    )
    flow_pct = ctx.context_today.get("Flow", 0.0) * 100

    return (
        f"Developer snapshot:\n"
        f"- Active today: {ctx.total_active_min_today:.0f} min\n"
        f"- Time since last break: {ctx.min_since_last_break:.0f} min\n"
        f"- Flow this session: {flow_pct:.0f}%\n"
        f"- Current state: {dominant_window_state}\n"
        f"- Fatigue level: {ctx.fatigue_level}\n"
        f"- Top project: {ctx.top_project_today or 'unknown'}\n"
        f"- Working late: {ctx.is_working_late}\n"
        f"- Nudge type needed: {ctx.recommended_nudge_type}\n"
        f"- Rationale: {ctx.nudge_rationale}\n\n"
        f"Generate the nudge."
    )


def _call_gemini(ctx: NudgeContext, timeout_sec: float = 4.0) -> str | None:
    """Call Gemini Flash and return nudge text, or None on error."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.warning("[NudgeGenerator] GEMINI_API_KEY not set — using fallback")
        return None

    payload = {
        "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": _build_user_prompt(ctx)}]}],
        "generationConfig": {
            "maxOutputTokens": 80,
            "temperature": 0.8,
            "topP": 0.95,
        },
    }

    try:
        resp = requests.post(
            _GEMINI_URL,
            params={"key": api_key},
            json=payload,
            timeout=timeout_sec,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if text:
            logger.info("[NudgeGenerator] Gemini generated: %s", text)
            return text
    except requests.exceptions.Timeout:
        logger.warning("[NudgeGenerator] Gemini timeout — using fallback")
    except Exception:
        logger.exception("[NudgeGenerator] Gemini call failed — using fallback")

    return None


# ── Public API ─────────────────────────────────────────────────────────────────

class NudgeGenerator:
    """Generate nudge text using Gemini with graceful fallback to templates."""

    def __init__(self, llm_enabled: bool = True, llm_timeout_sec: float = 4.0):
        self.llm_enabled = llm_enabled
        self.llm_timeout_sec = llm_timeout_sec

    def generate(self, ctx: NudgeContext) -> tuple[str, bool]:
        """
        Return (nudge_text, llm_used).
        Tries Gemini first; falls back to template on any failure.
        """
        if self.llm_enabled:
            text = _call_gemini(ctx, timeout_sec=self.llm_timeout_sec)
            if text:
                return text, True

        return _fallback(ctx), False
