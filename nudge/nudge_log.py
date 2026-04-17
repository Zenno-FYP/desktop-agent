"""NudgeLog — read/write the nudge_log table (local audit trail)."""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Optional

from nudge.nudge_context import NudgeContext

logger = logging.getLogger(__name__)


class NudgeLog:
    """Thin wrapper around the nudge_log SQLite table."""

    _RETENTION_DAYS = 30  # Keep at most this many days of nudge history

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._prune_old_entries(self._RETENTION_DAYS)

    # ── Write ──────────────────────────────────────────────────────────────

    def record(self, ctx: NudgeContext, nudge_text: str, llm_used: bool = False) -> None:
        """Record a displayed nudge."""
        self._execute(
            """
            INSERT INTO nudge_log (
                generated_at, nudge_type, nudge_text, rationale,
                fatigue_score, fatigue_level,
                flow_ratio_today, active_min_today, min_since_break,
                top_project, was_suppressed, suppression_reason,
                llm_used, context_snapshot
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)
            """,
            (
                ctx.generated_at.isoformat(),
                ctx.recommended_nudge_type,
                nudge_text,
                ctx.nudge_rationale,
                ctx.fatigue_score,
                ctx.fatigue_level,
                ctx.context_today.get("Flow", 0.0),
                round(ctx.total_active_min_today, 1),
                round(ctx.min_since_last_break, 1),
                ctx.top_project_today,
                int(llm_used),
                json.dumps(ctx.to_dict()),
            ),
        )

    def record_suppressed(self, reason: str) -> None:
        """Record a suppressed nudge cycle (no nudge text generated)."""
        self._execute(
            """
            INSERT INTO nudge_log (
                generated_at, nudge_type, nudge_text,
                was_suppressed, suppression_reason
            ) VALUES (?, 'SUPPRESSED', '', 1, ?)
            """,
            (datetime.now().isoformat(), reason),
        )

    # ── Read ───────────────────────────────────────────────────────────────

    def min_since_last_nudge(self) -> float:
        """Return minutes since the last non-suppressed nudge (0 if none)."""
        try:
            conn = self._conn()
            cur = conn.execute(
                """
                SELECT generated_at FROM nudge_log
                WHERE was_suppressed = 0
                ORDER BY nudge_id DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
            conn.close()
            if not row:
                return float("inf")
            last_dt = datetime.fromisoformat(row[0])
            return (datetime.now() - last_dt).total_seconds() / 60
        except Exception:
            logger.exception("[NudgeLog] Failed to query last nudge time")
            return float("inf")

    def last_n_nudge_types(self, n: int = 3) -> list:
        """Return the last n nudge types shown (for diversity check)."""
        try:
            conn = self._conn()
            cur = conn.execute(
                """
                SELECT nudge_type FROM nudge_log
                WHERE was_suppressed = 0
                ORDER BY nudge_id DESC
                LIMIT ?
                """,
                (n,),
            )
            rows = cur.fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception:
            logger.exception("[NudgeLog] Failed to query recent nudge types")
            return []

    def nudges_of_type_today(self, nudge_type: str) -> int:
        """Count how many times a nudge type was shown today."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = self._conn()
            cur = conn.execute(
                """
                SELECT COUNT(*) FROM nudge_log
                WHERE was_suppressed = 0
                  AND nudge_type = ?
                  AND DATE(generated_at) = ?
                """,
                (nudge_type, today_str),
            )
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception:
            logger.exception("[NudgeLog] Failed to count nudges today")
            return 0

    # ── Internals ──────────────────────────────────────────────────────────

    def _prune_old_entries(self, days: int) -> None:
        """Delete nudge_log rows older than `days` days (Design 8: retention policy)."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        self._execute(
            "DELETE FROM nudge_log WHERE generated_at < ?",
            (cutoff,),
        )

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, sql: str, params: tuple) -> None:
        with self._lock:
            try:
                conn = self._conn()
                conn.execute(sql, params)
                conn.commit()
                conn.close()
            except Exception:
                logger.exception("[NudgeLog] DB write failed")
