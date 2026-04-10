"""NudgeContext — structured snapshot of developer state used to generate nudges."""
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class NudgeContext:
    # ── Time & Session ──────────────────────────────────────────────────────
    generated_at: datetime
    current_hour: int
    is_working_late: bool

    # ── Today's Work Summary ────────────────────────────────────────────────
    total_active_sec_today: int
    total_active_min_today: float
    session_start_time: Optional[str]

    # ── Last Window (30-min look-back slice) ────────────────────────────────
    window_minutes: int
    active_sec_in_window: int
    idle_sec_in_window: int
    idle_ratio_in_window: float

    # ── Break Tracking ──────────────────────────────────────────────────────
    min_since_last_break: float
    has_taken_break_today: bool
    longest_break_min_today: float

    # ── Mental State Distributions ──────────────────────────────────────────
    context_today: dict           # {"Flow": 0.65, "Debugging": 0.20, ...} ratios
    context_last_window: dict

    # ── Behavioural Trend Signals ───────────────────────────────────────────
    avg_kpm_today: float
    avg_kpm_last_window: float
    kpm_trend: str                # "rising" | "stable" | "declining"
    correction_ratio_today: float
    correction_ratio_last_window: float
    correction_trend: str         # "improving" | "stable" | "worsening"

    # ── Focus Streak ────────────────────────────────────────────────────────
    consecutive_flow_min: float
    peak_flow_streak_today_min: float

    # ── Distraction Signal ──────────────────────────────────────────────────
    distraction_ratio_today: float
    distraction_ratio_last_window: float
    app_switch_rate_last_window: float

    # ── Project Context ─────────────────────────────────────────────────────
    top_project_today: Optional[str]
    top_language_today: Optional[str]
    projects_touched_today: int

    # ── Fatigue Composite ───────────────────────────────────────────────────
    fatigue_score: float          # 0.0–1.0
    fatigue_level: str            # "low" | "moderate" | "high" | "critical"

    # ── Nudge Suggestion ────────────────────────────────────────────────────
    recommended_nudge_type: str   # One of the 7 taxonomy types
    nudge_rationale: str

    def to_dict(self) -> dict:
        """Serialise to a plain dict (for JSON logging)."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "current_hour": self.current_hour,
            "is_working_late": self.is_working_late,
            "total_active_sec_today": self.total_active_sec_today,
            "total_active_min_today": round(self.total_active_min_today, 1),
            "session_start_time": self.session_start_time,
            "window_minutes": self.window_minutes,
            "active_sec_in_window": self.active_sec_in_window,
            "idle_sec_in_window": self.idle_sec_in_window,
            "idle_ratio_in_window": round(self.idle_ratio_in_window, 3),
            "min_since_last_break": round(self.min_since_last_break, 1),
            "has_taken_break_today": self.has_taken_break_today,
            "longest_break_min_today": round(self.longest_break_min_today, 1),
            "context_today": {k: round(v, 3) for k, v in self.context_today.items()},
            "context_last_window": {k: round(v, 3) for k, v in self.context_last_window.items()},
            "avg_kpm_today": round(self.avg_kpm_today, 1),
            "avg_kpm_last_window": round(self.avg_kpm_last_window, 1),
            "kpm_trend": self.kpm_trend,
            "correction_ratio_today": round(self.correction_ratio_today, 3),
            "correction_ratio_last_window": round(self.correction_ratio_last_window, 3),
            "correction_trend": self.correction_trend,
            "consecutive_flow_min": round(self.consecutive_flow_min, 1),
            "peak_flow_streak_today_min": round(self.peak_flow_streak_today_min, 1),
            "distraction_ratio_today": round(self.distraction_ratio_today, 3),
            "distraction_ratio_last_window": round(self.distraction_ratio_last_window, 3),
            "app_switch_rate_last_window": round(self.app_switch_rate_last_window, 2),
            "top_project_today": self.top_project_today,
            "top_language_today": self.top_language_today,
            "projects_touched_today": self.projects_touched_today,
            "fatigue_score": self.fatigue_score,
            "fatigue_level": self.fatigue_level,
            "recommended_nudge_type": self.recommended_nudge_type,
            "nudge_rationale": self.nudge_rationale,
        }
