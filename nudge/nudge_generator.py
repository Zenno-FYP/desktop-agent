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

# Cache the API key once at import time so the missing-key warning fires at most
# once per process instead of on every nudge attempt.
_GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
if not _GEMINI_API_KEY:
    logger.warning(
        "[NudgeGenerator] GEMINI_API_KEY not set — nudges will use local fallback templates"
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


# ── Gemini API Call ────────────────────────────────────────────────────────────

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

def _build_system_prompt(nudge_type: str, persona: str = "") -> str:
    """Build an imperative system prompt that binds the model to a specific nudge type.

    Design 7 fix: the nudge type is a directive, not just context, so the model
    cannot slide into a different type when the context contains mixed signals.
    """
    # Human-readable label and an explicit anti-example to anchor the boundary
    _type_guidance: dict[str, str] = {
        "BREAK_REMINDER":   "Tell the developer to take a break. Do NOT celebrate focus or productivity.",
        "FLOW_CELEBRATION": "Celebrate unbroken focus. Do NOT suggest breaks or mention fatigue.",
        "FATIGUE_WARNING":  "Warn about signs of mental fatigue. Do NOT praise output volume.",
        "REENGAGEMENT":     "Gently redirect a distracted developer back to their work.",
        "MOTIVATION":       "Give a short motivating observation. Do NOT lecture or warn.",
        "ACHIEVEMENT":      "Celebrate a specific accomplishment for today. Keep it brief and genuine.",
        "LATE_NIGHT":       "Acknowledge the late hour with warmth. Do NOT be preachy about sleep.",
    }
    type_instruction = _type_guidance.get(
        nudge_type,
        f"Generate a nudge of type {nudge_type}.",
    )
    persona_line = f"\nContext about this developer: {persona}" if persona else ""
    return (
        f"You are a friendly engineering coach inside Zenno, a developer productivity tool.\n"
        f"Generate exactly ONE nudge of type {nudge_type}.\n"
        f"{type_instruction}{persona_line}\n"
        f"≤ 2 sentences, ≤ 25 words total. Sound like a smart colleague, not a wellness bot.\n"
        f"Use specific numbers when they add weight (e.g. '47 minutes of Flow').\n"
        f"Output ONLY the nudge text, nothing else."
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


def _call_gemini(ctx: NudgeContext, timeout_sec: float = 4.0, persona: str = "") -> str | None:
    """Call Gemini Flash and return nudge text, or None on error."""
    if not _GEMINI_API_KEY:
        return None  # Warning already emitted once at import time

    payload = {
        "system_instruction": {"parts": [{"text": _build_system_prompt(ctx.recommended_nudge_type, persona)}]},
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
            params={"key": _GEMINI_API_KEY},
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

    def generate(self, ctx: NudgeContext, persona: str = "") -> tuple[str, bool]:
        """Return (nudge_text, llm_used).

        persona: optional extra instruction appended to the Gemini system prompt
                 (set from UserPreferences.llm_persona_instruction).
        Tries Gemini first; falls back to template on any failure.
        """
        if self.llm_enabled:
            text = _call_gemini(ctx, timeout_sec=self.llm_timeout_sec, persona=persona)
            if text:
                return text, True

        return _fallback(ctx), False
