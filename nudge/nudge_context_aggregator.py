"""NudgeContextAggregator — queries the SQLite DB and builds a NudgeContext snapshot."""
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Optional

from nudge.nudge_context import NudgeContext

logger = logging.getLogger(__name__)

IDLE_BREAK_THRESHOLD_MIN = 5   # Default: idle gap >= 5 min counts as a "break"
WINDOW_MINUTES = 30            # Default look-back window


class NudgeContextAggregator:
    """Read-only consumer of existing pipeline tables. Never writes to them."""

    def __init__(
        self,
        db_path: str,
        window_minutes: int = WINDOW_MINUTES,
        idle_break_threshold_min: int = IDLE_BREAK_THRESHOLD_MIN,
        late_night_hour: int = 21,
        flow_streak_min: float = 45.0,
        break_reminder_min: float = 90.0,
        distraction_threshold: float = 0.30,
    ):
        self.db_path = db_path
        self.window_minutes = window_minutes
        self.idle_break_threshold_min = idle_break_threshold_min
        self.late_night_hour = late_night_hour
        self.flow_streak_min = flow_streak_min
        self.break_reminder_min = break_reminder_min
        self.distraction_threshold = distraction_threshold

    # ── Public API ─────────────────────────────────────────────────────────

    def aggregate(self) -> NudgeContext:
        """Build and return a NudgeContext from current DB state."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return self._build_context(conn)
        except Exception:
            logger.exception("[NudgeContextAggregator] Failed to build context")
            raise
        finally:
            conn.close()

    # ── Internal orchestration ─────────────────────────────────────────────

    def _build_context(self, conn) -> NudgeContext:
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        window_start = now - timedelta(minutes=self.window_minutes)
        # The window before the last one (used for trend comparison — Bug 1 fix)
        prev_window_start = now - timedelta(minutes=self.window_minutes * 2)

        today_logs = self._query_today_logs(conn, today_str)
        window_logs = self._query_logs_between(conn, window_start, now)
        prev_window_logs = self._query_logs_between(conn, prev_window_start, window_start)

        # Session timing
        session_start = today_logs[0]["start_time"] if today_logs else None
        total_active_sec_today = sum(
            max(0, r["duration_sec"] - r["idle_duration_sec"]) for r in today_logs
        )
        active_sec_in_window = sum(
            max(0, r["duration_sec"] - r["idle_duration_sec"]) for r in window_logs
        )
        idle_sec_in_window = sum(r["idle_duration_sec"] for r in window_logs)
        window_total = self.window_minutes * 60
        idle_ratio_in_window = idle_sec_in_window / max(window_total, 1)

        # Break detection
        min_since_break, has_taken_break, longest_break = self._compute_break_metrics(today_logs)

        # Mental state distributions
        context_today = self._compute_context_distribution(today_logs)
        context_last_window = self._compute_context_distribution(window_logs)

        # KPM / typing intensity trend — last window vs prev window (Bug 1 fix)
        avg_kpm_today = self._weighted_avg(today_logs, "typing_intensity", "duration_sec")
        avg_kpm_window = self._weighted_avg(window_logs, "typing_intensity", "duration_sec")
        avg_kpm_prev_window = self._weighted_avg(prev_window_logs, "typing_intensity", "duration_sec")
        kpm_trend = self._trend(avg_kpm_prev_window, avg_kpm_window)

        # Correction (deletion) ratio trend — same window-over-window approach (Bug 1 fix)
        corr_today = self._correction_ratio(today_logs)
        corr_window = self._correction_ratio(window_logs)
        corr_prev_window = self._correction_ratio(prev_window_logs)
        corr_trend = self._trend_inverse(corr_prev_window, corr_window)

        # Flow streak
        consecutive_flow_min, peak_flow_min = self._compute_flow_streaks(today_logs)

        # Distraction
        distraction_ratio_today = self._ratio_for_state(today_logs, "Distracted")
        distraction_ratio_window = self._ratio_for_state(window_logs, "Distracted")
        app_switch_rate = self._app_switch_rate(window_logs)

        # Project context (from aggregated tables)
        top_project, top_language, n_projects = self._project_summary(conn, today_str)

        # Fatigue composite score (Design 6: longest_break fed in as a mitigating factor)
        fatigue_score, fatigue_level = self._compute_fatigue(
            total_active_sec_today,
            min_since_break,
            longest_break,
            corr_trend,
            kpm_trend,
            distraction_ratio_window,
            idle_ratio_in_window,
        )

        # Nudge type decision
        nudge_type, rationale = self._decide_nudge_type(
            now.hour,
            fatigue_level,
            min_since_break,
            consecutive_flow_min,
            distraction_ratio_window,
            total_active_sec_today,
            context_today,
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
            nudge_rationale=rationale,
        )

    # ── SQL Queries ────────────────────────────────────────────────────────

    def _query_today_logs(self, conn, today_str: str) -> list:
        cur = conn.execute(
            """
            SELECT * FROM raw_activity_logs
            WHERE DATE(start_time) = ?
              AND context_state IS NOT NULL
            ORDER BY start_time ASC
            """,
            (today_str,),
        )
        return cur.fetchall()

    def _query_logs_between(self, conn, start: datetime, end: datetime) -> list:
        """Return context-tagged logs whose start_time falls in [start, end)."""
        cur = conn.execute(
            """
            SELECT * FROM raw_activity_logs
            WHERE start_time >= ? AND start_time < ?
              AND context_state IS NOT NULL
            ORDER BY start_time ASC
            """,
            (start.isoformat(), end.isoformat()),
        )
        return cur.fetchall()

    def _project_summary(self, conn, today_str: str):
        """Return (top_project, top_language, project_count) for today."""
        # Top project
        cur = conn.execute(
            """
            SELECT project_name, SUM(duration_sec) AS total
            FROM daily_project_apps
            WHERE date = ?
            GROUP BY project_name
            ORDER BY total DESC
            LIMIT 1
            """,
            (today_str,),
        )
        row = cur.fetchone()
        top_project = row["project_name"] if row else None

        # Top language
        cur = conn.execute(
            """
            SELECT language_name, SUM(duration_sec) AS total
            FROM daily_project_languages
            WHERE date = ?
            GROUP BY language_name
            ORDER BY total DESC
            LIMIT 1
            """,
            (today_str,),
        )
        row = cur.fetchone()
        top_language = row["language_name"] if row else None

        # Project count
        cur = conn.execute(
            """
            SELECT COUNT(DISTINCT project_name)
            FROM daily_project_apps
            WHERE date = ?
            """,
            (today_str,),
        )
        row = cur.fetchone()
        n_projects = row[0] if row else 0

        return top_project, top_language, n_projects

    # ── Break Detection ────────────────────────────────────────────────────

    def _compute_break_metrics(self, logs) -> tuple:
        """Return (min_since_last_break, has_taken_break, longest_break_min).

        Collects breaks from two sources then deduplicates overlapping detections
        (Bug 2 fix: Method A idle-column and Method B inter-session gap can detect
        the same physical break; any two break events within 2 min of each other
        are treated as the same event and the longer duration wins).
        """
        BREAK_SEC = self.idle_break_threshold_min * 60
        # Each entry: (break_end_dt, duration_min)
        raw_breaks: list[tuple[datetime, float]] = []

        sorted_logs = sorted(logs, key=lambda r: r["start_time"])

        # Method A: explicit idle column — break ends at log's end_time
        for log in sorted_logs:
            if log["idle_duration_sec"] >= BREAK_SEC:
                try:
                    end_dt = datetime.fromisoformat(log["end_time"])
                    raw_breaks.append((end_dt, log["idle_duration_sec"] / 60))
                except (ValueError, TypeError):
                    continue

        # Method B: time gaps between consecutive sessions — break ends at curr_start
        for i in range(1, len(sorted_logs)):
            try:
                prev_end = datetime.fromisoformat(sorted_logs[i - 1]["end_time"])
                curr_start = datetime.fromisoformat(sorted_logs[i]["start_time"])
                gap_sec = (curr_start - prev_end).total_seconds()
                if gap_sec >= BREAK_SEC:
                    raw_breaks.append((curr_start, gap_sec / 60))
            except (ValueError, TypeError):
                continue

        # Deduplicate: sort by timestamp, merge events within 2-minute proximity
        raw_breaks.sort(key=lambda x: x[0])
        deduped: list[tuple[datetime, float]] = []
        for ts, dur in raw_breaks:
            if deduped and (ts - deduped[-1][0]).total_seconds() < 120:
                # Same physical break — keep the longer duration
                if dur > deduped[-1][1]:
                    deduped[-1] = (ts, dur)
            else:
                deduped.append((ts, dur))

        has_taken_break = len(deduped) > 0
        longest_break = max((d for _, d in deduped), default=0.0)

        # Time since most recent break
        now = datetime.now()
        if deduped:
            last_break_ts = deduped[-1][0]
            min_since_break = (now - last_break_ts).total_seconds() / 60
        else:
            # No break detected — proxy with total active time
            min_since_break = sum(l["duration_sec"] for l in logs) / 60

        return min_since_break, has_taken_break, longest_break

    # ── Flow Streak ────────────────────────────────────────────────────────

    def _compute_flow_streaks(self, logs) -> tuple:
        """Return (current_consecutive_flow_min, peak_flow_min_today).

        Bug 3 fix: a single non-Flow block (e.g. a quick Google search mid-session)
        no longer resets the streak. Two consecutive non-Flow blocks are required to
        consider the Flow session broken.
        """
        sorted_logs = sorted(logs, key=lambda r: r["start_time"])
        current_streak = 0.0
        peak_streak = 0.0
        non_flow_run = 0  # consecutive non-Flow block counter

        for log in sorted_logs:
            if log["context_state"] == "Flow":
                current_streak += max(0, log["duration_sec"] - log["idle_duration_sec"]) / 60
                peak_streak = max(peak_streak, current_streak)
                non_flow_run = 0
            else:
                non_flow_run += 1
                if non_flow_run >= 2:
                    # Two consecutive non-Flow blocks = genuine break in concentration
                    current_streak = 0.0
                    non_flow_run = 0

        return current_streak, peak_streak

    # ── Fatigue Composite ──────────────────────────────────────────────────

    def _compute_fatigue(
        self,
        active_sec_today: int,
        min_since_break: float,
        longest_break_min: float,
        corr_trend: str,
        kpm_trend: str,
        distraction_ratio_window: float,
        idle_ratio_window: float,
    ) -> tuple:
        """Weighted composite fatigue score (0–1) and level string.

        Design 6 fix: breaks are a *multiplier* on the hours component rather than
        an independent additive term. A 30-min break halves the hours contribution —
        6 hours with a proper rest is materially less fatiguing than 6 straight hours.
        The break_score additive term still contributes for the *recency* of the last
        break (min_since_break), independent of session length.
        """
        hours = active_sec_today / 3600

        # Hours component — mitigated by the longest break taken today
        hour_base = min(hours / 8.0, 1.0)
        # rest_factor: 0 = no break (full penalty), 0.5 at 30-min break, saturates at 60 min
        rest_factor = 1.0 - (0.5 * min(longest_break_min / 30.0, 1.0))
        length_score = hour_base * rest_factor

        break_score  = min(min_since_break / 120.0, 1.0)
        kpm_score    = 1.0 if kpm_trend == "declining" else (0.4 if kpm_trend == "stable" else 0.0)
        corr_score   = 1.0 if corr_trend == "worsening" else (0.4 if corr_trend == "stable" else 0.0)
        dist_score   = min(distraction_ratio_window / 0.5, 1.0)

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

    # ── Nudge Type Decision Tree ───────────────────────────────────────────

    def _decide_nudge_type(
        self,
        hour: int,
        fatigue_level: str,
        min_since_break: float,
        consecutive_flow_min: float,
        distraction_ratio_window: float,
        active_sec_today: int,
        context_today: dict,
    ) -> tuple:
        """Return (nudge_type, rationale). First matching rule wins."""
        flow_ratio_today = context_today.get("Flow", 0.0)
        active_hours = active_sec_today / 3600

        # Bug 5 fix: the scheduler's min_active_min gate (raised to 15 min) already
        # prevents nudges on very short sessions; this redundant priority is removed.

        if hour >= self.late_night_hour:
            return "LATE_NIGHT", f"It's {hour}:xx — working late"

        if fatigue_level == "critical":
            return "FATIGUE_WARNING", f"Fatigue score critical, {min_since_break:.0f} min since break"

        if min_since_break > self.break_reminder_min:
            return "BREAK_REMINDER", f"{min_since_break:.0f} min without a break"

        if consecutive_flow_min >= self.flow_streak_min:
            return "FLOW_CELEBRATION", f"{consecutive_flow_min:.0f} min of unbroken Flow"

        if distraction_ratio_window > self.distraction_threshold:
            return "REENGAGEMENT", f"{distraction_ratio_window * 100:.0f}% distracted in last window"

        if flow_ratio_today >= 0.60 and active_hours >= 3:
            return "ACHIEVEMENT", f"{flow_ratio_today * 100:.0f}% Flow, {active_hours:.1f}h worked"

        if fatigue_level == "moderate" and min_since_break > 50:
            return "BREAK_REMINDER", "Moderate fatigue + 50+ min since break"

        return "MOTIVATION", "Solid work session, no specific flag"

    # ── Stat Helpers ───────────────────────────────────────────────────────

    def _weighted_avg(self, logs, value_col: str, weight_col: str) -> float:
        total_weight = sum(r[weight_col] for r in logs if r[weight_col])
        if total_weight == 0:
            return 0.0
        return sum(r[value_col] * r[weight_col] for r in logs if r[weight_col]) / total_weight

    def _correction_ratio(self, logs) -> float:
        total_keys = sum(
            (r["typing_intensity"] * r["duration_sec"] / 60.0)
            for r in logs if r["duration_sec"]
        )
        total_del = sum(r["deletion_key_presses"] for r in logs)
        return total_del / max(total_keys, 1)

    def _compute_context_distribution(self, logs) -> dict:
        state_sec: dict = {}
        for log in logs:
            state = log["context_state"] or "Unknown"
            active = max(0, log["duration_sec"] - log["idle_duration_sec"])
            state_sec[state] = state_sec.get(state, 0) + active

        total = sum(state_sec.values())
        if total == 0:
            return {}
        return {k: v / total for k, v in state_sec.items()}

    def _ratio_for_state(self, logs, state: str) -> float:
        total_active = sum(max(0, r["duration_sec"] - r["idle_duration_sec"]) for r in logs)
        state_active = sum(
            max(0, r["duration_sec"] - r["idle_duration_sec"])
            for r in logs if r["context_state"] == state
        )
        return state_active / max(total_active, 1)

    def _app_switch_rate(self, logs) -> float:
        """App switches per minute in the window."""
        if not logs or self.window_minutes == 0:
            return 0.0
        apps = [r["app_name"] for r in sorted(logs, key=lambda r: r["start_time"])]
        switches = sum(1 for i in range(1, len(apps)) if apps[i] != apps[i - 1])
        return switches / self.window_minutes

    def _trend(self, baseline: float, recent: float, threshold: float = 0.10) -> str:
        """Compare recent vs baseline. Returns 'rising', 'stable', or 'declining'."""
        if baseline == 0:
            return "stable"
        change = (recent - baseline) / baseline
        if change > threshold:
            return "rising"
        if change < -threshold:
            return "declining"
        return "stable"

    def _trend_inverse(self, baseline: float, recent: float, threshold: float = 0.10) -> str:
        """Like _trend but semantics are inverted (lower is 'improving')."""
        raw = self._trend(baseline, recent, threshold)
        if raw == "rising":
            return "worsening"
        if raw == "declining":
            return "improving"
        return "stable"
