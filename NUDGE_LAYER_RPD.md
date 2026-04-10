# AI Nudging Layer — Research, Planning & Design Document

**Project**: Zenno Desktop Agent — Wellbeing & Motivation Nudge Engine  
**Version**: 1.0  
**Status**: Planning  
**Scope**: Phase 1 — Context Extraction & Nudge Payload Generation

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [System Context — What We Already Have](#2-system-context--what-we-already-have)
3. [High-Level Design](#3-high-level-design)
4. [Nudge Taxonomy](#4-nudge-taxonomy)
5. [Phase 1 Deep Dive — NudgeContextAggregator](#5-phase-1-deep-dive--nudgecontextaggregator)
6. [New Database Table — nudge_log](#6-new-database-table--nudge_log)
7. [Signals Reference Sheet](#7-signals-reference-sheet)
8. [Phase 2 Preview — NudgeGenerator (LLM)](#8-phase-2-preview--nudgegenerator-llm)
9. [Phase 3 Preview — NudgeNotifier (Toast)](#9-phase-3-preview--nudgenotifier-toast)
10. [Integration Plan — Where to Hook In](#10-integration-plan--where-to-hook-in)
11. [Implementation Sequence](#11-implementation-sequence)
12. [File & Folder Structure](#12-file--folder-structure)
13. [Config Additions](#13-config-additions)
14. [Edge Cases & Guard Rails](#14-edge-cases--guard-rails)

---

## 1. Problem Statement

The desktop agent already does the hard work: it watches every window, measures every keystroke, labels every mental state (Flow, Debugging, Distracted, etc.), and stores it all in a well-structured SQLite database. But **nothing looks back at the developer as a human**.

The goal of this layer is to add a lightweight, friendly, AI-driven loop that:

- Reads the aggregated evidence of what the user has been doing
- Infers their current wellbeing, fatigue level, momentum, and distraction pattern
- Synthesises that into a single concise **nudge** — a push notification that feels like a smart colleague who noticed something
- Shows it at the right moment, not constantly

The nudge is not a report card. It is not a productivity score. It is a micro-engagement: one or two sentences that make the developer feel seen, motivated, or gently reminded to take care of themselves.

---

## 2. System Context — What We Already Have

The following data sources exist and are already populated by the existing pipeline. The nudge layer is purely a **reader** of these tables — it writes nothing to them.

| Source Table | Granularity | Key Signals Available |
|---|---|---|
| `raw_activity_logs` | Per session (2–5 sec sample rate) | app, duration, KPM, CPM, deletions, idle_sec, context_state, confidence |
| `daily_project_context` | Per (date, project, context_state) | Time in Flow / Debugging / Research / Distracted per day |
| `daily_project_behavior` | Per (date, project) | Avg KPM, avg CPM, total deletions, total idle, mouse distance |
| `daily_project_apps` | Per (date, project, app) | Time spent per app per day |
| `projects` | Per project | first_seen_at, last_active_at |

The existing `session_fatigue_factor` signal in the `FeatureExtractor` (`0–1` based on hours worked) is also a reference signal — but we will recompute a richer version from raw data rather than reading it from anywhere.

---

## 3. High-Level Design

```
┌──────────────────────────────────────────────────────────┐
│  EXISTING PIPELINE  (Phases 1–4, untouched)              │
│  raw_activity_logs → BlockEvaluator → ETL → Aggregated   │
└──────────────────────────────────────────────────────────┘
                            │  (read only)
                            ▼
┌──────────────────────────────────────────────────────────┐
│  PHASE 5: NUDGE ENGINE  (New, runs every 30–60 min)      │
│                                                          │
│  NudgeScheduler                                          │
│    └─► NudgeContextAggregator  ──────────────────────┐   │
│          (query DB, compute 15 signals)              │   │
│                                                      ▼   │
│                                            NudgeContext   │
│                                            (structured    │
│                                             dict/object)  │
│                                                      │   │
│          (Phase 2, later)                            ▼   │
│    └─► NudgeGenerator  ◄── Claude / LLM API          │   │
│          (prompt + NudgeContext → nudge text)         │   │
│                                                      ▼   │
│    └─► NudgeNotifier                            nudge_log │
│          (Windows toast notification)                │   │
└──────────────────────────────────────────────────────────┘
```

### Design Principles

**Non-intrusive**: The nudge layer never writes to, alters, or re-aggregates existing tables. It is a read-only consumer of the existing pipeline.

**Lazy by default**: The scheduler is rate-limited. If the user just received a nudge, the next one is suppressed for at least 30 minutes. If the user is idle (away from desk), no nudge fires.

**Contextually smart**: The nudge text is generated with full awareness of the time of day, the mental state distribution, and the trajectory (is the user declining or peaking?).

**Graceful degradation**: If the LLM API is unavailable, the system falls back to a small library of template-based nudges. The user always gets something.

---

## 4. Nudge Taxonomy

Every nudge belongs to one of seven types. The `NudgeContextAggregator` determines which type is most relevant before the LLM call. This classification drives both the prompt framing and the notification icon/colour.

| Type | Trigger Condition | Example Intent |
|---|---|---|
| `BREAK_REMINDER` | >90 min active, no break >5 min | "You've been going for a while — stretch?" |
| `FLOW_CELEBRATION` | >45 min continuous Flow state | "You're in the zone right now" |
| `REENGAGEMENT` | >25% Distracted in last window | "Looks like it's been a scattered hour — want to reset?" |
| `MOTIVATION` | Solid work, no special flag | "Good progress today on {project}" |
| `FATIGUE_WARNING` | KPM declining + high corrections + long session | "Your typing tells a story — you might need a pause" |
| `LATE_NIGHT` | Time of day after 21:00, still active | "Working late — you earned it. Don't forget to stop." |
| `ACHIEVEMENT` | Hit milestone (e.g., 4 h Flow today, 3-day streak) | "4 hours of focused work today — that's a great day" |

---

## 5. Phase 1 Deep Dive — NudgeContextAggregator

This is the entire scope of Phase 1. The output is a structured `NudgeContext` object (a Python dataclass). Nothing else from the nudge pipeline needs to be built yet. Once this object is solid and the signals are verified, Phase 2 (LLM call) is straightforward.

### 5.1 NudgeContext Dataclass

```python
# nudge/nudge_context.py

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

@dataclass
class NudgeContext:
    # ── Time & Session ──────────────────────────────────────────
    generated_at: datetime                  # When this context was built
    current_hour: int                        # 0–23, for time-of-day nudges
    is_working_late: bool                    # True if current_hour >= 21

    # ── Today's Work Summary ───────────────────────────────────
    total_active_sec_today: int              # Total active (non-idle) time today
    total_active_min_today: float            # Convenience: sec → min
    session_start_time: Optional[str]        # First activity timestamp today (ISO)

    # ── Last Window (the recent slice, 30 or 60 min) ───────────
    window_minutes: int                      # How many minutes the window covers
    active_sec_in_window: int                # Active time in last N minutes
    idle_sec_in_window: int                  # Idle time in last N minutes
    idle_ratio_in_window: float              # idle / total window time (0.0–1.0)

    # ── Break Tracking ─────────────────────────────────────────
    min_since_last_break: float              # Minutes since last idle gap >5 min
    has_taken_break_today: bool              # Any break >5 min today at all
    longest_break_min_today: float           # Longest idle gap today

    # ── Mental State Distributions ─────────────────────────────
    context_today: dict                      # {"Flow": 0.65, "Debugging": 0.20, ...} (ratios)
    context_last_window: dict                # Same but for last window only

    # ── Behavioural Trend Signals ──────────────────────────────
    avg_kpm_today: float                     # Average KPM across whole day
    avg_kpm_last_window: float               # Average KPM in last window
    kpm_trend: str                           # "rising", "stable", "declining"
    correction_ratio_today: float            # deletions / total keystrokes today (0.0–1.0)
    correction_ratio_last_window: float      # Same, last window only
    correction_trend: str                    # "improving", "stable", "worsening"

    # ── Focus Streak ───────────────────────────────────────────
    consecutive_flow_min: float              # Unbroken Flow minutes right now
    peak_flow_streak_today_min: float        # Longest Flow streak today

    # ── Distraction Signal ─────────────────────────────────────
    distraction_ratio_today: float           # Distracted_sec / total_active_sec
    distraction_ratio_last_window: float     # Same, last window
    app_switch_rate_last_window: float       # App switches per minute (last window)

    # ── Project Context ────────────────────────────────────────
    top_project_today: Optional[str]         # Project with most active time today
    top_language_today: Optional[str]        # Language with most time today
    projects_touched_today: int             # How many distinct projects

    # ── Fatigue Composite ──────────────────────────────────────
    fatigue_score: float                     # 0.0–1.0 composite (computed, not stored)
    fatigue_level: str                       # "low", "moderate", "high", "critical"

    # ── Nudge Suggestion ───────────────────────────────────────
    recommended_nudge_type: str              # One of the 7 types from taxonomy
    nudge_rationale: str                     # Human-readable reason why this type
```

### 5.2 NudgeContextAggregator — Full Query Logic

```python
# nudge/nudge_context_aggregator.py

import sqlite3
from datetime import datetime, timedelta
from nudge.nudge_context import NudgeContext

IDLE_BREAK_THRESHOLD_MIN = 5      # Min idle gap to count as a "break"
WINDOW_MINUTES = 30               # Default look-back window

class NudgeContextAggregator:

    def __init__(self, db_path: str, window_minutes: int = WINDOW_MINUTES):
        self.db_path = db_path
        self.window_minutes = window_minutes

    def aggregate(self) -> NudgeContext:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return self._build_context(conn)
        finally:
            conn.close()

    def _build_context(self, conn) -> NudgeContext:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        window_start = now - timedelta(minutes=self.window_minutes)
        window_start_str = window_start.isoformat()

        # ── 1. Today's raw logs (all tagged, today only) ───────
        today_logs = self._query_today_logs(conn, today_str)
        window_logs = self._query_window_logs(conn, window_start_str)

        # ── 2. Session timing ──────────────────────────────────
        session_start = today_logs[0]["start_time"] if today_logs else None
        total_active_sec_today = sum(
            r["duration_sec"] - r["idle_duration_sec"] for r in today_logs
        )
        active_sec_in_window = sum(
            r["duration_sec"] - r["idle_duration_sec"] for r in window_logs
        )
        idle_sec_in_window = sum(r["idle_duration_sec"] for r in window_logs)
        window_total = self.window_minutes * 60
        idle_ratio_in_window = idle_sec_in_window / max(window_total, 1)

        # ── 3. Break detection ─────────────────────────────────
        min_since_break, has_taken_break, longest_break = \
            self._compute_break_metrics(today_logs)

        # ── 4. Context distributions ───────────────────────────
        context_today = self._compute_context_distribution(today_logs)
        context_last_window = self._compute_context_distribution(window_logs)

        # ── 5. KPM trend ───────────────────────────────────────
        avg_kpm_today = self._weighted_avg(today_logs, "typing_intensity", "duration_sec")
        avg_kpm_window = self._weighted_avg(window_logs, "typing_intensity", "duration_sec")
        kpm_trend = self._trend(avg_kpm_today, avg_kpm_window)

        # ── 6. Correction ratio ────────────────────────────────
        corr_today = self._correction_ratio(today_logs)
        corr_window = self._correction_ratio(window_logs)
        corr_trend = self._trend_inverse(corr_today, corr_window)  # lower = better

        # ── 7. Flow streak ─────────────────────────────────────
        consecutive_flow_min, peak_flow_min = \
            self._compute_flow_streaks(today_logs)

        # ── 8. Distraction ─────────────────────────────────────
        distraction_ratio_today = self._ratio_for_state(today_logs, "Distracted")
        distraction_ratio_window = self._ratio_for_state(window_logs, "Distracted")
        app_switch_rate = self._app_switch_rate(window_logs)

        # ── 9. Project context ─────────────────────────────────
        top_project, top_language, n_projects = \
            self._project_summary(conn, today_str)

        # ── 10. Fatigue composite ──────────────────────────────
        fatigue_score, fatigue_level = self._compute_fatigue(
            total_active_sec_today,
            min_since_break,
            corr_trend,
            kpm_trend,
            distraction_ratio_window,
            idle_ratio_in_window
        )

        # ── 11. Nudge type decision ────────────────────────────
        nudge_type, rationale = self._decide_nudge_type(
            now.hour, fatigue_level, min_since_break,
            consecutive_flow_min, distraction_ratio_window,
            total_active_sec_today, context_today
        )

        return NudgeContext(
            generated_at=now,
            current_hour=now.hour,
            is_working_late=(now.hour >= 21),
            total_active_sec_today=total_active_sec_today,
            total_active_min_today=total_active_sec_today / 60,
            session_start_time=session_start,
            window_minutes=self.window_minutes,
            active_sec_in_window=active_sec_in_window,
            idle_sec_in_window=idle_sec_in_window,
            idle_ratio_in_window=idle_ratio_in_window,
            min_since_last_break=min_since_break,
            has_taken_break_today=has_taken_break,
            longest_break_min_today=longest_break,
            context_today=context_today,
            context_last_window=context_last_window,
            avg_kpm_today=avg_kpm_today,
            avg_kpm_last_window=avg_kpm_window,
            kpm_trend=kpm_trend,
            correction_ratio_today=corr_today,
            correction_ratio_last_window=corr_window,
            correction_trend=corr_trend,
            consecutive_flow_min=consecutive_flow_min,
            peak_flow_streak_today_min=peak_flow_min,
            distraction_ratio_today=distraction_ratio_today,
            distraction_ratio_last_window=distraction_ratio_window,
            app_switch_rate_last_window=app_switch_rate,
            top_project_today=top_project,
            top_language_today=top_language,
            projects_touched_today=n_projects,
            fatigue_score=fatigue_score,
            fatigue_level=fatigue_level,
            recommended_nudge_type=nudge_type,
            nudge_rationale=rationale
        )
```

### 5.3 Key Helper Methods — Implementation Detail

#### Break Detection
```python
def _compute_break_metrics(self, logs):
    """
    A "break" = any idle_duration_sec gap > IDLE_BREAK_THRESHOLD_MIN * 60
    Also inferred from large time gaps between consecutive log end/start times.
    """
    BREAK_SEC = IDLE_BREAK_THRESHOLD_MIN * 60
    breaks = []

    # Method A: Explicit idle columns in raw logs
    for log in logs:
        if log["idle_duration_sec"] >= BREAK_SEC:
            breaks.append(log["idle_duration_sec"] / 60)

    # Method B: Time gap between consecutive sessions (> BREAK_SEC apart)
    sorted_logs = sorted(logs, key=lambda r: r["start_time"])
    for i in range(1, len(sorted_logs)):
        prev_end = datetime.fromisoformat(sorted_logs[i-1]["end_time"])
        curr_start = datetime.fromisoformat(sorted_logs[i]["start_time"])
        gap_sec = (curr_start - prev_end).total_seconds()
        if gap_sec >= BREAK_SEC:
            breaks.append(gap_sec / 60)

    has_taken_break = len(breaks) > 0
    longest_break = max(breaks) if breaks else 0.0

    # Time since most recent break
    now = datetime.now()
    min_since_break = float("inf")
    for log in reversed(sorted_logs):
        if log["idle_duration_sec"] >= BREAK_SEC:
            last_break_end = datetime.fromisoformat(log["end_time"])
            min_since_break = (now - last_break_end).total_seconds() / 60
            break

    # Also check inter-log gaps
    for i in range(len(sorted_logs) - 1, 0, -1):
        prev_end = datetime.fromisoformat(sorted_logs[i-1]["end_time"])
        curr_start = datetime.fromisoformat(sorted_logs[i]["start_time"])
        gap_sec = (curr_start - prev_end).total_seconds()
        if gap_sec >= BREAK_SEC:
            gap_end = curr_start
            gap_since = (now - gap_end).total_seconds() / 60
            if gap_since < min_since_break:
                min_since_break = gap_since
            break

    if min_since_break == float("inf"):
        # No break found — use total session time as proxy
        min_since_break = sum(l["duration_sec"] for l in logs) / 60

    return min_since_break, has_taken_break, longest_break
```

#### Flow Streak Computation
```python
def _compute_flow_streaks(self, logs):
    """
    Walk logs chronologically. Accumulate consecutive Flow-labeled seconds.
    Reset on any non-Flow state. Track peak.
    """
    sorted_logs = sorted(logs, key=lambda r: r["start_time"])
    current_streak = 0.0
    peak_streak = 0.0

    for log in sorted_logs:
        if log["context_state"] == "Flow":
            current_streak += (log["duration_sec"] - log["idle_duration_sec"]) / 60
            peak_streak = max(peak_streak, current_streak)
        else:
            current_streak = 0.0

    return current_streak, peak_streak
```

#### Fatigue Composite Score
```python
def _compute_fatigue(self, active_sec_today, min_since_break,
                      corr_trend, kpm_trend,
                      distraction_ratio_window, idle_ratio_window):
    """
    Weighted composite. Each factor contributes 0–1, then scaled.

    Factor weights:
    - Session length    : 0.30  (hours worked)
    - Break absence     : 0.25  (time without break)
    - Declining KPM     : 0.20  (losing speed = tiredness signal)
    - Rising corrections: 0.15  (making more errors)
    - Rising distraction: 0.10  (losing focus)
    """
    hours = active_sec_today / 3600

    length_score = min(hours / 8.0, 1.0)                         # Saturates at 8h
    break_score  = min(min_since_break / 120.0, 1.0)             # Saturates at 2h no-break
    kpm_score    = 1.0 if kpm_trend == "declining" else (0.4 if kpm_trend == "stable" else 0.0)
    corr_score   = 1.0 if corr_trend == "worsening" else (0.4 if corr_trend == "stable" else 0.0)
    dist_score   = min(distraction_ratio_window / 0.5, 1.0)      # 50% distracted = 1.0

    fatigue = (
        0.30 * length_score +
        0.25 * break_score  +
        0.20 * kpm_score    +
        0.15 * corr_score   +
        0.10 * dist_score
    )

    if   fatigue < 0.25: level = "low"
    elif fatigue < 0.50: level = "moderate"
    elif fatigue < 0.75: level = "high"
    else:                level = "critical"

    return round(fatigue, 3), level
```

#### Nudge Type Decision Tree
```python
def _decide_nudge_type(self, hour, fatigue_level, min_since_break,
                        consecutive_flow_min, distraction_ratio_window,
                        active_sec_today, context_today):

    flow_ratio_today = context_today.get("Flow", 0.0)
    active_hours = active_sec_today / 3600

    # Priority order — first match wins
    if hour >= 21:
        return "LATE_NIGHT", f"It's {hour}:xx — working late"

    if fatigue_level == "critical":
        return "FATIGUE_WARNING", f"Fatigue score critical, {min_since_break:.0f} min since break"

    if min_since_break > 90:
        return "BREAK_REMINDER", f"{min_since_break:.0f} min without a break"

    if consecutive_flow_min >= 45:
        return "FLOW_CELEBRATION", f"{consecutive_flow_min:.0f} min of unbroken Flow"

    if distraction_ratio_window > 0.30:
        return "REENGAGEMENT", f"{distraction_ratio_window*100:.0f}% distracted in last window"

    if flow_ratio_today >= 0.60 and active_hours >= 3:
        return "ACHIEVEMENT", f"{flow_ratio_today*100:.0f}% Flow, {active_hours:.1f}h worked"

    if fatigue_level == "moderate" and min_since_break > 50:
        return "BREAK_REMINDER", "Moderate fatigue + 50+ min since break"

    return "MOTIVATION", "Solid work session, no specific flag"
```

### 5.4 SQL Queries Used

```sql
-- Today's logs (all tagged)
SELECT * FROM raw_activity_logs
WHERE DATE(start_time) = :today
  AND context_state IS NOT NULL
ORDER BY start_time ASC;

-- Last N-minute window logs
SELECT * FROM raw_activity_logs
WHERE start_time >= :window_start
  AND context_state IS NOT NULL
ORDER BY start_time ASC;

-- Top project today (from aggregated table)
SELECT project_name, SUM(duration_sec) AS total
FROM daily_project_apps
WHERE date = :today
GROUP BY project_name
ORDER BY total DESC
LIMIT 1;

-- Top language today
SELECT language_name, SUM(duration_sec) AS total
FROM daily_project_languages
WHERE date = :today
GROUP BY language_name
ORDER BY total DESC
LIMIT 1;

-- Project count today
SELECT COUNT(DISTINCT project_name)
FROM daily_project_apps
WHERE date = :today;
```

---

## 6. New Database Table — nudge_log

A lightweight audit trail. Stored locally only. Never synced to the backend.

```sql
CREATE TABLE IF NOT EXISTS nudge_log (
    nudge_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at      TEXT    NOT NULL,     -- ISO timestamp
    nudge_type        TEXT    NOT NULL,     -- BREAK_REMINDER, FLOW_CELEBRATION, etc.
    nudge_text        TEXT    NOT NULL,     -- Final displayed text (LLM or fallback)
    rationale         TEXT,                -- Why this nudge type was chosen
    fatigue_score     REAL,
    fatigue_level     TEXT,
    flow_ratio_today  REAL,
    active_min_today  REAL,
    min_since_break   REAL,
    top_project       TEXT,
    was_suppressed    INTEGER DEFAULT 0,   -- 1 if suppressed (user idle/recent nudge)
    suppression_reason TEXT,
    context_snapshot  TEXT                 -- Full NudgeContext as JSON (for debugging)
);
```

---

## 7. Signals Reference Sheet

A quick reference of every signal the `NudgeContext` carries, its source, and how to interpret it.

| Signal | Source Table | How Computed | Interpretation |
|---|---|---|---|
| `total_active_min_today` | `raw_activity_logs` | SUM(duration_sec - idle_sec) | Total focused time today |
| `min_since_last_break` | `raw_activity_logs` | Scan for idle gaps >5 min | Lower = more recently rested |
| `idle_ratio_in_window` | `raw_activity_logs` | idle_sec / window_sec | >0.5 = mostly away from desk |
| `context_today["Flow"]` | `raw_activity_logs` | Flow_sec / total_active_sec | Focus quality today |
| `context_last_window["Distracted"]` | `raw_activity_logs` | Same, last window only | Current distraction level |
| `kpm_trend` | `raw_activity_logs` | avg_kpm_window vs avg_kpm_today | Declining = fatigue |
| `correction_trend` | `raw_activity_logs` | deletion_ratio trend | Worsening = stress/fatigue |
| `consecutive_flow_min` | `raw_activity_logs` | Streak walk over sorted logs | High = celebrate |
| `fatigue_score` | Computed | Weighted composite of 5 signals | 0–1 burnout risk |
| `app_switch_rate_last_window` | `raw_activity_logs` | COUNT(distinct apps) / window_min | High = scattered/distracted |
| `top_project_today` | `daily_project_apps` | MAX(SUM(duration)) | Context for personalisation |
| `top_language_today` | `daily_project_languages` | MAX(SUM(duration)) | Context for personalisation |
| `is_working_late` | System clock | current_hour >= 21 | Trigger late-night nudge |

---

## 8. Phase 2 Preview — NudgeGenerator (LLM)

> Phase 2 is not implemented in this sprint. This section captures the design intent.

### Prompt Architecture

The LLM receives a carefully shaped prompt. The `NudgeContext` is serialised into a compact, natural-language summary rather than raw JSON, so the model can focus on tone and personality rather than parsing.

```
System:
  You are a friendly, perceptive engineering coach embedded inside a 
  developer productivity tool. You generate short, human nudges — never
  more than 2 sentences — that a developer sees as a desktop notification.
  
  Rules:
  - Sound like a smart colleague, not a corporate wellness bot
  - Never lecture, never be preachy
  - Match the nudge type to the developer's situation
  - Use specific numbers when they're impressive (e.g., "3 hours of Flow today")
  - Keep it under 25 words
  - Tone should match time of day and energy level

User:
  Developer snapshot:
  - Active today: {total_active_min_today:.0f} min
  - Time since last break: {min_since_break:.0f} min
  - Flow this session: {context_today[Flow]*100:.0f}%
  - Current state: {context_last_window most dominant}
  - Fatigue level: {fatigue_level}
  - Top project: {top_project_today}
  - Working late: {is_working_late}
  - Nudge type needed: {recommended_nudge_type}
  - Rationale: {nudge_rationale}
  
  Generate the nudge.
```

### Fallback Template Library

When the LLM is unavailable, these templates activate. They use `{placeholder}` substitution from the `NudgeContext`.

```python
FALLBACK_TEMPLATES = {
    "BREAK_REMINDER": [
        "You've been at it for {min_since_break:.0f} minutes. Step away, even for 5.",
        "No breaks in {min_since_break:.0f} min — your brain needs a reset.",
    ],
    "FLOW_CELEBRATION": [
        "You've been in Flow for {consecutive_flow_min:.0f} minutes. Don't stop.",
        "{consecutive_flow_min:.0f} minutes of pure focus — that's rare. Keep it up.",
    ],
    "REENGAGEMENT": [
        "Scattered last hour. Pick one thing and start there.",
        "Hard to focus? Close everything except {top_project_today}.",
    ],
    "MOTIVATION": [
        "{total_active_min_today:.0f} minutes in. Good day on {top_project_today}.",
        "Solid work on {top_project_today} today. Keep the momentum.",
    ],
    "FATIGUE_WARNING": [
        "Your pace has dropped. That's your body's cue — take 10.",
        "Longer sessions ≠ better sessions. A break now helps tomorrow.",
    ],
    "LATE_NIGHT": [
        "Still here? Respect. Wrap up in 30 if you can.",
        "Late session on {top_project_today}. Make sure you sleep.",
    ],
    "ACHIEVEMENT": [
        "{flow_ratio:.0f}% Flow today. That's a great day by any measure.",
        "{total_active_min_today:.0f} focused minutes on {top_project_today}. Ship it.",
    ],
}
```

---

## 9. Phase 3 Preview — NudgeNotifier (Toast)

> Also deferred. Design intent only.

Uses `win11toast` (Windows 11) or `plyer` (cross-platform fallback) to show a native desktop notification.

```python
# nudge/nudge_notifier.py

from win11toast import notify   # pip install win11toast

NUDGE_ICONS = {
    "BREAK_REMINDER"  : "🧘",
    "FLOW_CELEBRATION": "🔥",
    "REENGAGEMENT"    : "🎯",
    "MOTIVATION"      : "💪",
    "FATIGUE_WARNING" : "⚠️",
    "LATE_NIGHT"      : "🌙",
    "ACHIEVEMENT"     : "🏆",
}

class NudgeNotifier:
    def show(self, nudge_type: str, nudge_text: str):
        icon = NUDGE_ICONS.get(nudge_type, "💡")
        title = f"{icon} Zenno"
        notify(title=title, body=nudge_text, app_id="Zenno")
```

---

## 10. Integration Plan — Where to Hook In

The cleanest integration point is `agent.py`. The `DesktopAgent` already manages several background threads. Add one more.

```python
# In agent.py — DesktopAgent.__init__

from nudge.nudge_scheduler import NudgeScheduler

self.nudge_scheduler = NudgeScheduler(
    db_path=self.db_path,
    interval_min=config.get("nudge.interval_min", 30),
    suppression_min=config.get("nudge.suppression_min", 25),
)

# In DesktopAgent.start() — alongside other thread launches
self.nudge_scheduler.start()
```

```python
# nudge/nudge_scheduler.py

import threading, time, logging
from nudge.nudge_context_aggregator import NudgeContextAggregator
from nudge.nudge_log import NudgeLog

logger = logging.getLogger(__name__)

class NudgeScheduler:
    def __init__(self, db_path, interval_min=30, suppression_min=25):
        self.db_path = db_path
        self.interval_sec = interval_min * 60
        self.suppression_sec = suppression_min * 60
        self._thread = None
        self._running = False
        self.nudge_log = NudgeLog(db_path)

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            time.sleep(self.interval_sec)
            try:
                self._fire_nudge()
            except Exception as e:
                logger.error(f"[NudgeScheduler] Error: {e}")

    def _fire_nudge(self):
        # Suppression: don't nudge if user is currently idle
        # (check last raw_activity_log end_time vs now)
        if self._is_user_idle():
            self.nudge_log.record_suppressed("user_idle")
            return

        # Suppression: too soon since last nudge
        if self.nudge_log.min_since_last_nudge() < self.suppression_sec / 60:
            self.nudge_log.record_suppressed("too_recent")
            return

        # Build context
        aggregator = NudgeContextAggregator(self.db_path)
        ctx = aggregator.aggregate()

        # Phase 2: generate text (LLM or fallback)
        # nudge_text = NudgeGenerator(ctx).generate()
        nudge_text = "[Phase 2 pending — LLM call here]"

        # Phase 3: show notification
        # NudgeNotifier().show(ctx.recommended_nudge_type, nudge_text)

        # Log it
        self.nudge_log.record(ctx, nudge_text)
        logger.info(f"[Nudge] {ctx.recommended_nudge_type}: {nudge_text}")
```

---

## 11. Implementation Sequence

### Sprint 1 — Foundation (this sprint, Phase 1 scope)

| Step | Task | File | Estimated Effort |
|---|---|---|---|
| 1 | Create `nudge/` package with `__init__.py` | `nudge/__init__.py` | 5 min |
| 2 | Implement `NudgeContext` dataclass | `nudge/nudge_context.py` | 20 min |
| 3 | Implement DB queries in `NudgeContextAggregator` | `nudge/nudge_context_aggregator.py` | 2–3 h |
| 4 | Implement break detection helper | Inside aggregator | 45 min |
| 5 | Implement Flow streak helper | Inside aggregator | 30 min |
| 6 | Implement fatigue composite | Inside aggregator | 30 min |
| 7 | Implement nudge type decision tree | Inside aggregator | 30 min |
| 8 | Create `nudge_log` DB table in schema | `data/db/schema.sql` | 15 min |
| 9 | Implement `NudgeLog` (log writer) | `nudge/nudge_log.py` | 30 min |
| 10 | Implement `NudgeScheduler` (timer + suppression) | `nudge/nudge_scheduler.py` | 45 min |
| 11 | Hook scheduler into `agent.py` | `agent.py` | 15 min |
| 12 | Add `nudge` section to `config.yaml` | `config.yaml` | 10 min |
| 13 | Write unit tests for aggregator helpers | `tests/test_nudge_aggregator.py` | 1.5 h |
| 14 | Manual test: print NudgeContext to console after 30 min | `agent.py` debug flag | 30 min |

### Sprint 2 — LLM Integration

| Step | Task |
|---|---|
| 1 | Implement `NudgeGenerator` with Claude API call |
| 2 | Implement fallback template system |
| 3 | Write prompt, test for each nudge type |
| 4 | Add retry/timeout logic (2 second max latency budget) |
| 5 | Add `nudge_text` to `nudge_log` table |

### Sprint 3 — Notification Delivery

| Step | Task |
|---|---|
| 1 | Integrate `win11toast` or `plyer` |
| 2 | Design notification icons per nudge type |
| 3 | Add dismiss / snooze callback |
| 4 | Test notification suppression when fullscreen app active |

---

## 12. File & Folder Structure

```
desktop-agent/
├── agent.py                          # ← Add NudgeScheduler.start() here
├── config.yaml                       # ← Add nudge section
├── nudge/                            # ← NEW package
│   ├── __init__.py
│   ├── nudge_context.py              # NudgeContext dataclass
│   ├── nudge_context_aggregator.py   # Main aggregation logic (Phase 1 target)
│   ├── nudge_log.py                  # nudge_log table writer
│   ├── nudge_scheduler.py            # Background timer + suppression
│   ├── nudge_generator.py            # LLM + fallback (Phase 2)
│   └── nudge_notifier.py             # Toast notification (Phase 3)
├── tests/
│   └── test_nudge_aggregator.py      # Unit tests for helpers
└── data/db/
    └── schema.sql                    # ← Add nudge_log table definition
```

---

## 13. Config Additions

```yaml
# config.yaml — new section

nudge:
  enabled: true
  interval_min: 30              # How often the scheduler fires (30 or 60)
  suppression_min: 25           # Minimum gap between two nudges shown
  window_min: 30                # Look-back window for "recent" signals
  idle_break_threshold_min: 5   # Idle gap that counts as a break
  late_night_hour: 21           # Hour after which LATE_NIGHT type activates
  flow_streak_min: 45           # Min unbroken Flow minutes to trigger FLOW_CELEBRATION
  break_reminder_min: 90        # Min without break to trigger BREAK_REMINDER
  distraction_threshold: 0.30   # Distracted ratio above which REENGAGEMENT fires
  
  llm:
    enabled: false              # Set true in Sprint 2
    model: "claude-sonnet-4-20250514"
    max_tokens: 60
    timeout_sec: 3
    fallback_on_error: true
  
  notification:
    enabled: false              # Set true in Sprint 3
    app_id: "Zenno"
    suppress_fullscreen: true   # Don't interrupt when user is in fullscreen app
```

---

## 14. Edge Cases & Guard Rails

| Scenario | How Handled |
|---|---|
| No logs today (just started) | `NudgeContext` defaults safely: 0 active time, `MOTIVATION` type, no fire if active_min < 10 |
| All logs have `context_state = NULL` (BlockEvaluator not run yet) | Aggregator filters `WHERE context_state IS NOT NULL`, may return empty window — suppress nudge |
| Midnight boundary (logs spanning two days) | Only query `DATE(start_time) = today`, consistent with existing ETL logic |
| User is in a meeting (Communication state) | `REENGAGEMENT` and `BREAK_REMINDER` are suppressed if last 15 min is all Communication |
| User has fullscreen app active (gaming, video call) | Phase 3 checks before showing toast; Phase 1/2 still run silently |
| LLM API timeout | Fallback template fires; nudge_log records `llm_fallback = true` |
| Repeated same nudge type | Add diversity check: if last 3 nudges were same type, rotate to next eligible type |
| Very short session (<15 min) | Suppress all nudges; not enough data for meaningful signal |
| High confidence Distracted all day | Don't pile on with `REENGAGEMENT` repeatedly — cap at 2 per day |
