"""NudgeScheduler — background timer that orchestrates the full nudge pipeline."""
import time
import logging
import threading
from datetime import datetime

from nudge.nudge_context_aggregator import NudgeContextAggregator
from nudge.nudge_generator import NudgeGenerator
from nudge.nudge_log import NudgeLog
from nudge.nudge_notifier import NudgeNotifier
from nudge.user_preferences import UserPreferences

logger = logging.getLogger(__name__)

# Hard guard rails for diversity: suppress a nudge type if it appeared in the
# last N consecutive nudges OR was shown more than MAX_PER_TYPE_PER_DAY times today.
_MAX_CONSECUTIVE_SAME_TYPE = 2
_MAX_REENGAGEMENT_PER_DAY  = 2   # Avoid piling on when distracted
_MAX_ACHIEVEMENT_PER_DAY   = 1   # Once the milestone is hit, celebrate once (Bug 4 fix)


class NudgeScheduler:
    """
    Background thread that fires the nudge pipeline periodically.

    Pipeline (per tick):
      1. Suppression checks (idle, too-recent, communication state)
      2. NudgeContextAggregator  → NudgeContext
      3. NudgeGenerator          → nudge_text  (Gemini or fallback)
      4. NudgeNotifier            → show toast
      5. NudgeLog                 → record to DB
    """

    def __init__(
        self,
        db_path: str,
        interval_min: int = 30,
        suppression_min: int = 25,
        window_min: int = 30,
        llm_enabled: bool = True,
        llm_timeout_sec: float = 4.0,
        notification_enabled: bool = True,
        notification_display_sec: int = 7,
        min_active_min: float = 15.0,
        idle_break_threshold_min: int = 5,
        late_night_hour: int = 21,
        flow_streak_min: float = 45.0,
        break_reminder_min: float = 90.0,
        distraction_threshold: float = 0.30,
        meeting_suppression_threshold: float = 0.80,
        user_preferences: UserPreferences | None = None,
    ):
        self.db_path = db_path
        self.window_min = window_min
        self.min_active_min = min_active_min
        self.idle_break_threshold_min = idle_break_threshold_min
        self.distraction_threshold = distraction_threshold

        # Base values from config — may be overridden by user preferences below
        self.interval_sec = interval_min * 60
        self.suppression_sec = suppression_min * 60
        self.late_night_hour = late_night_hour
        self.flow_streak_min = flow_streak_min
        self.break_reminder_min = break_reminder_min
        self.meeting_suppression_threshold = meeting_suppression_threshold

        # Persona and disabled-type state (set by preferences below, or defaults)
        self._persona: str = ""
        self._disabled_types: list[str] = []
        self._quiet_window: tuple[int, int] | None = None  # (from, until)
        self._prefs: UserPreferences | None = user_preferences

        # Apply user preference overrides (higher priority than config)
        if user_preferences:
            self.late_night_hour            = user_preferences.late_night_hour
            self.flow_streak_min            = float(user_preferences.flow_streak_min)
            self.break_reminder_min         = float(user_preferences.break_reminder_min)
            self.meeting_suppression_threshold = user_preferences.meeting_suppression_threshold
            self._persona                   = user_preferences.llm_persona_instruction
            self._disabled_types            = user_preferences.disabled_nudge_types
            self._quiet_window              = user_preferences.quiet_window
            override = user_preferences.nudge_interval_override_min
            if override:
                self.interval_sec = override * 60
            logger.info(
                "[NudgeScheduler] Preferences applied: schedule=%s focus=%s goal=%s meetings=%s",
                user_preferences.work_schedule,
                user_preferences.focus_style,
                user_preferences.wellbeing_goal,
                user_preferences.has_meetings,
            )

        self._running = False
        self._thread: threading.Thread | None = None

        self.nudge_log = NudgeLog(db_path)
        self.generator = NudgeGenerator(
            llm_enabled=llm_enabled,
            llm_timeout_sec=llm_timeout_sec,
        )
        self.notifier = NudgeNotifier(display_sec=notification_display_sec) if notification_enabled else None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background scheduler thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name="NudgeScheduler",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "[NudgeScheduler] Started — interval=%ds suppression=%ds",
            self.interval_sec,
            self.suppression_sec,
        )

    def stop(self) -> None:
        """Signal the scheduler to stop (does not block)."""
        self._running = False
        logger.info("[NudgeScheduler] Stopping")

    # ── Main Loop ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Wait for a full interval on startup so the agent accumulates data first
        slept = 0
        while self._running and slept < self.interval_sec:
            time.sleep(5)
            slept += 5

        while self._running:
            try:
                self._tick()
            except Exception:
                logger.exception("[NudgeScheduler] Unexpected error in tick")

            # Sleep in small chunks to allow clean shutdown
            slept = 0
            while self._running and slept < self.interval_sec:
                time.sleep(5)
                slept += 5

    def _tick(self) -> None:
        """One nudge evaluation cycle."""
        logger.debug("[NudgeScheduler] Tick")

        # ── Suppression: outside user's work window (quiet hours) ──────────
        if self._prefs is not None:
            current_hour = datetime.now().hour
            if self._prefs.is_quiet_hour(current_hour):
                logger.debug(
                    "[NudgeScheduler] Suppressed (quiet hours: hour=%d, window=%s)",
                    current_hour,
                    self._quiet_window,
                )
                return  # Silent — no log entry, user is not working

        # ── Suppression: too soon since last nudge ─────────────────────────
        min_since = self.nudge_log.min_since_last_nudge()
        if min_since < self.suppression_sec / 60:
            logger.debug("[NudgeScheduler] Suppressed (too recent: %.1f min ago)", min_since)
            self.nudge_log.record_suppressed("too_recent")
            return

        # ── Build context ──────────────────────────────────────────────────
        aggregator = NudgeContextAggregator(
            self.db_path,
            window_minutes=self.window_min,
            idle_break_threshold_min=self.idle_break_threshold_min,
            late_night_hour=self.late_night_hour,
            flow_streak_min=self.flow_streak_min,
            break_reminder_min=self.break_reminder_min,
            distraction_threshold=self.distraction_threshold,
        )
        try:
            ctx = aggregator.aggregate()
        except Exception:
            logger.exception("[NudgeScheduler] Aggregation failed — skipping tick")
            return

        # ── Suppression: not enough data yet ──────────────────────────────
        if ctx.total_active_min_today < self.min_active_min:
            logger.debug(
                "[NudgeScheduler] Suppressed (too little activity: %.1f min)",
                ctx.total_active_min_today,
            )
            self.nudge_log.record_suppressed("insufficient_activity")
            return

        # ── Suppression: user mostly idle in last window ───────────────────
        if ctx.idle_ratio_in_window > 0.70:
            logger.debug("[NudgeScheduler] Suppressed (user idle: %.0f%% idle)", ctx.idle_ratio_in_window * 100)
            self.nudge_log.record_suppressed("user_idle")
            return

        # ── Suppression: in Communication state (meeting) ─────────────────
        comm_ratio = ctx.context_last_window.get("Communication", 0.0)
        if comm_ratio > self.meeting_suppression_threshold:
            logger.debug("[NudgeScheduler] Suppressed (in meeting / Communication state)")
            self.nudge_log.record_suppressed("in_meeting")
            return

        # ── Diversity guard: avoid repeating same type too often ───────────
        nudge_type = self._apply_diversity_guard(ctx)

        # ── Generate text ─────────────────────────────────────────────────
        from dataclasses import replace
        ctx_for_gen = replace(ctx, recommended_nudge_type=nudge_type)
        nudge_text, llm_used = self.generator.generate(ctx_for_gen, persona=self._persona)

        # ── Show notification ─────────────────────────────────────────────
        if self.notifier:
            self.notifier.show(nudge_type, nudge_text)

        # ── Log to DB ─────────────────────────────────────────────────────
        self.nudge_log.record(ctx_for_gen, nudge_text, llm_used=llm_used)

        logger.info(
            "[NudgeScheduler] Fired %s (llm=%s): %s",
            nudge_type,
            llm_used,
            nudge_text[:80],
        )

    # ── Diversity Guard ────────────────────────────────────────────────────

    def _apply_diversity_guard(self, ctx) -> str:
        """
        Prevent showing the same nudge type repeatedly.
        If the original type is overused or disabled, rotate to the next eligible one.
        """
        original_type = ctx.recommended_nudge_type
        recent_types  = self.nudge_log.last_n_nudge_types(n=_MAX_CONSECUTIVE_SAME_TYPE)

        # Suppress types the user's wellbeing goal has disabled entirely
        if original_type in self._disabled_types:
            logger.debug(
                "[NudgeScheduler] Diversity: %s disabled by user goal", original_type
            )
            return self._fallback_type(ctx)

        # Cap REENGAGEMENT at 2 per day (avoid piling on)
        if original_type == "REENGAGEMENT":
            count = self.nudge_log.nudges_of_type_today("REENGAGEMENT")
            if count >= _MAX_REENGAGEMENT_PER_DAY:
                logger.debug("[NudgeScheduler] Diversity: REENGAGEMENT capped for today")
                return self._fallback_type(ctx)

        # Cap ACHIEVEMENT at 1 per day — milestone is celebrated once (Bug 4 fix)
        if original_type == "ACHIEVEMENT":
            count = self.nudge_log.nudges_of_type_today("ACHIEVEMENT")
            if count >= _MAX_ACHIEVEMENT_PER_DAY:
                logger.debug("[NudgeScheduler] Diversity: ACHIEVEMENT already fired today, rotating")
                return self._fallback_type(ctx)

        # If last N nudges were all the same type, rotate
        if len(recent_types) >= _MAX_CONSECUTIVE_SAME_TYPE and all(
            t == original_type for t in recent_types
        ):
            logger.debug("[NudgeScheduler] Diversity: rotating away from %s", original_type)
            return self._fallback_type(ctx)

        return original_type

    def _fallback_type(self, ctx) -> str:
        """Pick an alternative nudge type based on available signals."""
        h = ctx.current_hour
        if ctx.fatigue_level in ("high", "critical"):
            return "FATIGUE_WARNING"
        if ctx.min_since_last_break > 60:
            return "BREAK_REMINDER"
        if ctx.consecutive_flow_min >= 30:
            return "FLOW_CELEBRATION"
        if h >= 21:
            return "LATE_NIGHT"
        return "MOTIVATION"
