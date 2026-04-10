"""Activity sync client for uploading aggregates to backend.

Handles formatting collected data into sync API requests, authentication,
error recovery (including token refresh), and marking records as synced.
"""

import os
import json
import logging
import time
import requests
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

from sync.activity_collector import ActivityCollector
from auth.tokens import get_valid_id_token, TokenError


logger = logging.getLogger(__name__)

# Load .env
load_dotenv()

_BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_TIMEOUT = int(os.getenv("SYNC_TIMEOUT_SECONDS", "30"))
_TRANSIENT_RETRIES = 3
_INITIAL_BACKOFF_SEC = 2


class ActivitySyncer:
    """Sync activity aggregates to backend API."""

    def __init__(self, db, user_id: Optional[str] = None):
        self.db = db
        self.user_id = user_id
        self.collector = ActivityCollector(db)

    def sync_activity(self) -> bool:
        """Perform full sync of pending activity data to backend.

        Returns True if sync succeeded, False otherwise.
        On failure no records are marked as synced (safe for retry).
        """
        sync_start = time.monotonic()
        try:
            logger.info("[ActivitySyncer] Starting sync cycle")

            projects = self.collector.collect_pending_projects()

            if not projects:
                logger.debug("[ActivitySyncer] No pending projects; sync complete")
                return True

            logger.info(
                "[ActivitySyncer] Collected %d projects with pending data",
                len(projects),
            )

            payload = self._build_payload(projects)

            id_token = self._get_id_token()
            if not id_token:
                logger.error("[ActivitySyncer] Failed to obtain ID token")
                return False

            try:
                self._send_sync_request(payload, id_token)
                logger.info("[ActivitySyncer] Sync succeeded; marking records as synced")
                self._mark_all_synced(projects)
                self._log_outcome(True, len(projects), sync_start)
                return True

            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 401:
                    logger.info(
                        "[ActivitySyncer] Got 401 (auth); attempting token refresh and retry"
                    )
                    ok = self._retry_with_refreshed_token(payload, projects)
                    self._log_outcome(ok, len(projects), sync_start, "401-retry")
                    return ok
                if status >= 500 or status == 0:
                    ok = self._retry_transient(payload, projects)
                    self._log_outcome(ok, len(projects), sync_start, "transient-retry")
                    return ok
                logger.error(
                    "[ActivitySyncer] Client error %d: %s",
                    status,
                    e.response.text if e.response is not None else "",
                )
                self._log_outcome(False, len(projects), sync_start, f"http-{status}")
                return False

            except (requests.ConnectionError, requests.Timeout) as e:
                logger.warning("[ActivitySyncer] Network error: %s", e)
                ok = self._retry_transient(payload, projects)
                self._log_outcome(ok, len(projects), sync_start, "network-retry")
                return ok

        except Exception as e:
            logger.error("[ActivitySyncer] Unexpected error: %s", e, exc_info=True)
            self._log_outcome(False, 0, sync_start, "exception")
            return False

    # ------------------------------------------------------------------ helpers

    def _build_payload(self, projects: list[dict]) -> dict:
        user_id = self.user_id
        if not user_id:
            local_user = self.db.get_local_user()
            if local_user:
                user_id = local_user.get("_id")
            else:
                raise ValueError("Cannot determine user_id; not signed in?")

        local_now = datetime.now()
        utc_offset = local_now.astimezone().utcoffset()
        offset_minutes = int(utc_offset.total_seconds() // 60) if utc_offset else 0

        payload = {
            "user_id": user_id,
            "sync_timestamp": local_now.isoformat(),
            "timezone_offset_minutes": offset_minutes,
            "data": projects,
        }

        logger.debug(
            "[ActivitySyncer] Built payload with %d projects, tz_offset=%d min",
            len(projects),
            offset_minutes,
        )
        return payload

    def _get_id_token(self) -> Optional[str]:
        try:
            token = get_valid_id_token()
            if token:
                return token
            logger.warning("[ActivitySyncer] get_valid_id_token returned None")
            return None
        except TokenError as e:
            logger.error("[ActivitySyncer] TokenError: %s", e)
            return None
        except Exception as e:
            logger.error("[ActivitySyncer] Unexpected error getting token: %s", e)
            return None

    def _send_sync_request(self, payload: dict, id_token: str) -> dict:
        url = f"{_BACKEND_BASE_URL}/sync/activity"
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
        }

        logger.info("[ActivitySyncer] POST %s", url)
        response = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)

        if response.status_code >= 400:
            raise requests.HTTPError(response=response)

        return response.json()

    def _retry_with_refreshed_token(self, payload: dict, projects: list[dict]) -> bool:
        try:
            id_token = self._get_id_token()
            if not id_token:
                logger.error("[ActivitySyncer] Failed to obtain fresh token")
                return False
            self._send_sync_request(payload, id_token)
            self._mark_all_synced(projects)
            logger.info("[ActivitySyncer] Auth-retry succeeded")
            return True
        except Exception as e:
            logger.error("[ActivitySyncer] Auth-retry failed: %s", e)
            return False

    def _retry_transient(self, payload: dict, projects: list[dict]) -> bool:
        """Retry with exponential backoff for transient / network errors."""
        backoff = _INITIAL_BACKOFF_SEC
        for attempt in range(1, _TRANSIENT_RETRIES + 1):
            logger.info(
                "[ActivitySyncer] Transient retry %d/%d in %.1fs",
                attempt,
                _TRANSIENT_RETRIES,
                backoff,
            )
            time.sleep(backoff)
            id_token = self._get_id_token()
            if not id_token:
                backoff *= 2
                continue
            try:
                self._send_sync_request(payload, id_token)
                self._mark_all_synced(projects)
                logger.info("[ActivitySyncer] Transient retry %d succeeded", attempt)
                return True
            except (requests.HTTPError, requests.ConnectionError, requests.Timeout) as e:
                logger.warning("[ActivitySyncer] Retry %d failed: %s", attempt, e)
                backoff *= 2
        logger.error("[ActivitySyncer] All transient retries exhausted")
        return False

    def _mark_all_synced(self, projects: list[dict]):
        try:
            for project in projects:
                project_name = project.get("project_name")
                days = project.get("days", [])

                for day in days:
                    date = day.get("date")
                    self.db.mark_project_synced(project_name, date)

                with self.db.conn:
                    self.db.conn.execute(
                        "UPDATE project_loc_snapshots SET needs_sync = 0 WHERE project_name = ?",
                        (project_name,),
                    )
                    self.db.conn.execute(
                        "UPDATE project_skills SET needs_sync = 0 WHERE project_name = ?",
                        (project_name,),
                    )

            logger.info("[ActivitySyncer] Marked %d projects as synced", len(projects))
        except Exception as e:
            logger.error("[ActivitySyncer] Error marking records as synced: %s", e)
            raise

    @staticmethod
    def _log_outcome(
        success: bool,
        project_count: int,
        start_mono: float,
        context: str = "",
    ):
        elapsed = time.monotonic() - start_mono
        status = "OK" if success else "FAIL"
        logger.info(
            "[ActivitySyncer] outcome=%s projects=%d elapsed=%.2fs context=%s",
            status,
            project_count,
            elapsed,
            context or "first-attempt",
        )
