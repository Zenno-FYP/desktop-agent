"""ESM (Experience Sampling Method) popup handler for collecting ground-truth verification."""
import sys
import json
import logging
import subprocess
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_RUNNER = Path(__file__).resolve().parent / "_esm_runner.py"


class ESMPopup:
    """
    Experience Sampling Method popup handler for immediate verification.

    When BlockEvaluator detects low-confidence predictions, immediately displays
    a verification popup (if rate limits allow). User verifies while context is fresh.
    Rate limits are configured via config.yaml.
    """

    def __init__(self, db, config=None, confidence_threshold: float = None):
        self.db = db
        self.config = config

        esm_config = config.get("esm_popup")

        self.confidence_threshold = confidence_threshold or esm_config.get("confidence_threshold")

        rate_limits = esm_config.get("rate_limiting")
        self.min_interval_seconds = rate_limits.get("min_interval_hours") * 3600
        self.daily_max = rate_limits.get("max_per_day")

        ui_config = esm_config.get("ui")
        self.auto_dismiss_ms = int(ui_config.get("auto_dismiss_seconds", 30) * 1000)

        self.last_popup_time = 0
        self.popups_today = 0
        self.last_reset_date = datetime.now().date()

        logger.info(
            "[ESM] Popup handler initialized (min_interval_sec=%s, max_per_day=%s)",
            int(self.min_interval_seconds),
            self.daily_max,
        )

    # ── Public API ─────────────────────────────────────────────────────────

    def queue_for_verification(
        self,
        log_ids: list,
        context_state: str,
        confidence: float,
        block_metrics: dict = None,
    ):
        """Show verification popup if confidence is low and rate limits allow."""
        if confidence >= self.confidence_threshold:
            return

        if not self._check_rate_limit():
            logger.info("[ESM] Rate limited, skipping popup (confidence=%.0f%%)", confidence * 100)
            return

        self.last_popup_time = datetime.now().timestamp()
        self.popups_today += 1

        logger.debug(
            "[ESM] Showing popup: %s (%.0f%%) — popups today: %s/%s",
            context_state,
            confidence * 100,
            self.popups_today,
            self.daily_max,
        )

        threading.Thread(
            target=self._run_popup_subprocess,
            args=(log_ids, context_state, confidence, block_metrics or {}),
            daemon=True,
            name="ESMPopupThread",
        ).start()

    def stop(self):
        logger.info("[ESM] Popup handler stopped")

    # ── Rate limiting ──────────────────────────────────────────────────────

    def _check_rate_limit(self) -> bool:
        now = datetime.now()

        if now.date() > self.last_reset_date:
            self.popups_today = 0
            self.last_reset_date = now.date()

        if (now.timestamp() - self.last_popup_time) < self.min_interval_seconds:
            remaining = (self.min_interval_seconds - (now.timestamp() - self.last_popup_time)) / 60
            logger.debug("[ESM] Cooldown: %.1f min remaining", remaining)
            return False

        if self.popups_today >= self.daily_max:
            logger.debug("[ESM] Daily max reached: %s/%s", self.popups_today, self.daily_max)
            return False

        return True

    # ── Signal summary ─────────────────────────────────────────────────────

    def _build_signal_chips(self, block_metrics: dict) -> list:
        """Return a list of short signal label strings for the UI chips row."""
        chips = []

        kpm = block_metrics.get("typing_intensity", 0)
        if kpm > 0:
            chips.append(f"KPM: {kpm:.0f}")

        total_dur = block_metrics.get("total_duration_sec", 1)
        mouse_dist = block_metrics.get("mouse_movement_distance", 0)
        velocity = mouse_dist / total_dur if total_dur > 0 else 0
        if velocity > 0:
            chips.append(f"Mouse: {velocity:.1f} px/s")

        fatigue = block_metrics.get("consecutive_work_hours", 0)
        if fatigue > 0:
            chips.append(f"Session: {fatigue:.1f}h")

        total_keys = block_metrics.get("total_keystrokes", 1)
        deletions  = block_metrics.get("deletion_key_presses", 0)
        if total_keys > 0:
            corr = (deletions / total_keys) * 100
            if corr > 5:
                chips.append(f"Corrections: {corr:.0f}%")

        return chips

    # ── Subprocess display ─────────────────────────────────────────────────

    def _run_popup_subprocess(
        self,
        log_ids: list,
        context_state: str,
        confidence: float,
        block_metrics: dict,
    ):
        """Launch the ESM popup in a child process and wait for the user's answer."""
        payload = json.dumps({
            "predicted_state": context_state,
            "confidence":      confidence,
            "signal_chips":    self._build_signal_chips(block_metrics),
            "dismiss_ms":      self.auto_dismiss_ms,
        })

        try:
            proc = subprocess.Popen(
                [sys.executable, str(_RUNNER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            proc.stdin.write((payload + "\n").encode("utf-8"))
            proc.stdin.close()

            raw_out, _ = proc.communicate(timeout=self.auto_dismiss_ms / 1000 + 5)
            result = json.loads(raw_out.decode("utf-8").strip())
            verified_label = result.get("label")

            if verified_label:
                self._record_verification(log_ids, verified_label)
                logger.info(
                    "[ESM] User verified: %s — popups today: %s/%s",
                    verified_label,
                    self.popups_today,
                    self.daily_max,
                )
            else:
                logger.debug("[ESM] Popup auto-dismissed (no answer)")

        except subprocess.TimeoutExpired:
            proc.kill()
            logger.debug("[ESM] Subprocess timed out")
        except Exception:
            logger.exception("[ESM] Error running popup subprocess")

    # ── DB write ───────────────────────────────────────────────────────────

    def _record_verification(self, log_ids: list, verified_label: str):
        try:
            for log_id in log_ids:
                success = self.db.update_log_verification(log_id, verified_label)
                if success:
                    logger.debug("[ESM] Recorded: log_id=%s label=%s", log_id, verified_label)
                else:
                    logger.warning("[ESM] Failed to record for log_id=%s", log_id)
        except Exception:
            logger.exception("[ESM] Error recording verification")
