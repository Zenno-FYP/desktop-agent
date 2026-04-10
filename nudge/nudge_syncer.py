"""NudgeSyncer — uploads local nudge_log records to the Zenno backend.

Runs as a background thread that batches up to _BATCH_SIZE unsent nudges
and POSTs them to  POST /api/v1/agent/nudges/sync  every _INTERVAL_SEC.

Records are tracked via a local `nudge_sync_cursor` table (single row) that
stores the highest nudge_id already sent, so we never re-send the same row.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from auth.tokens import get_valid_id_token, TokenError

load_dotenv()

logger = logging.getLogger(__name__)

_BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_INTERVAL_SEC = int(os.getenv("NUDGE_SYNC_INTERVAL_SEC", "300"))  # 5 minutes
_BATCH_SIZE = 100
_TIMEOUT = 20


class NudgeSyncer:
    """Background thread that syncs nudge_log rows to the backend."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ensure_cursor_table()

    # ── Public API ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background sync loop."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="NudgeSyncer", daemon=True
        )
        self._thread.start()
        logger.info("[NudgeSyncer] Started (interval=%ds)", _INTERVAL_SEC)

    def stop(self) -> None:
        self._stop.set()

    def sync_now(self) -> bool:
        """Trigger an immediate sync (blocking). Returns True on success."""
        return self._do_sync()

    # ── Background loop ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        # Small initial delay so agent has time to sign in
        time.sleep(30)
        while not self._stop.is_set():
            self._do_sync()
            self._stop.wait(_INTERVAL_SEC)

    # ── Core sync ──────────────────────────────────────────────────────────────

    def _do_sync(self) -> bool:
        try:
            rows = self._fetch_pending_rows()
            if not rows:
                return True

            token = self._get_token()
            if not token:
                return False

            payload = {
                "records": [
                    {
                        "generated_at": row["generated_at"],
                        "nudge_type": row["nudge_type"],
                        "nudge_text": row["nudge_text"] or "",
                        "was_suppressed": bool(row["was_suppressed"]),
                    }
                    for row in rows
                ]
            }

            resp = requests.post(
                f"{_BACKEND_BASE_URL}/agent/nudges/sync",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()

            max_id = max(r["nudge_id"] for r in rows)
            self._advance_cursor(max_id)
            logger.info("[NudgeSyncer] Synced %d nudges (cursor → %d)", len(rows), max_id)
            return True

        except TokenError as e:
            logger.warning("[NudgeSyncer] Token error: %s", e)
        except requests.HTTPError as e:
            logger.warning("[NudgeSyncer] HTTP error: %s", e)
        except requests.RequestException as e:
            logger.warning("[NudgeSyncer] Network error: %s", e)
        except Exception:
            logger.exception("[NudgeSyncer] Unexpected error during sync")
        return False

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _get_token(self) -> Optional[str]:
        try:
            return get_valid_id_token()
        except Exception as e:
            logger.warning("[NudgeSyncer] Could not obtain token: %s", e)
            return None

    def _fetch_pending_rows(self) -> list[dict]:
        cursor_id = self._get_cursor()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT nudge_id, generated_at, nudge_type, nudge_text, was_suppressed
            FROM nudge_log
            WHERE nudge_id > ?
            ORDER BY nudge_id ASC
            LIMIT ?
            """,
            (cursor_id, _BATCH_SIZE),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _get_cursor(self) -> int:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.execute("SELECT last_synced_nudge_id FROM nudge_sync_cursor WHERE id = 1")
            row = cur.fetchone()
            conn.close()
            return row[0] if row else 0
        except Exception:
            return 0

    def _advance_cursor(self, nudge_id: int) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT OR REPLACE INTO nudge_sync_cursor (id, last_synced_nudge_id) VALUES (1, ?)",
                (nudge_id,),
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("[NudgeSyncer] Failed to advance cursor")

    def _ensure_cursor_table(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nudge_sync_cursor (
                    id                  INTEGER PRIMARY KEY DEFAULT 1,
                    last_synced_nudge_id INTEGER DEFAULT 0
                )
                """
            )
            conn.commit()
            conn.close()
        except Exception:
            logger.exception("[NudgeSyncer] Failed to create cursor table")
