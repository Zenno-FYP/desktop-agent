"""Activity sync client for uploading aggregates to backend.

Handles formatting collected data into sync API requests, authentication,
error recovery (including token refresh), and marking records as synced.
"""

import os
import json
import logging
import requests
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv

from sync.activity_collector import ActivityCollector
from auth.tokens import get_valid_id_token, TokenError


logger = logging.getLogger(__name__)

# Load .env
load_dotenv()

_BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_TIMEOUT = int(os.getenv("SYNC_TIMEOUT_SECONDS", "30"))


class ActivitySyncer:
    """Sync activity aggregates to backend API."""

    def __init__(self, db, user_id: Optional[str] = None):
        """Initialize syncer.
        
        Args:
            db: Database instance
            user_id: Backend user ID. If None, will be fetched from local_user table.
        """
        self.db = db
        self.user_id = user_id
        self.collector = ActivityCollector(db)

    def sync_activity(self) -> bool:
        """Perform full sync of pending activity data to backend.
        
        Flow:
        1. Collect pending projects from SQLite
        2. Build sync payload (user_id, sync_timestamp, data)
        3. Get Firebase ID token
        4. POST to /sync/activity endpoint
        5. On success: mark all records as synced (needs_sync = 0)
        6. On 401: refresh token and retry once
        7. On other errors: log and return False
        
        Returns:
            True if entire sync succeeded, False otherwise.
            On failure, no records are marked as synced (safe for retry).
        """
        try:
            logger.info("[ActivitySyncer] Starting sync cycle")
            
            # Collect pending projects
            projects = self.collector.collect_pending_projects()
            
            if not projects:
                logger.debug("[ActivitySyncer] No pending projects; sync complete")
                return True
            
            logger.info(f"[ActivitySyncer] Collected {len(projects)} projects with pending data")
            
            # Build request payload
            payload = self._build_payload(projects)
            
            # Get Firebase ID token
            id_token = self._get_id_token()
            if not id_token:
                logger.error("[ActivitySyncer] Failed to obtain ID token")
                return False
            
            # Send request
            try:
                response = self._send_sync_request(payload, id_token)
                logger.info("[ActivitySyncer] Sync succeeded; marking records as synced")
                
                # Mark all synced records
                self._mark_all_synced(projects)
                return True
                
            except requests.HTTPError as e:
                if e.response.status_code == 401:
                    # Token expired; try refreshing and retrying once
                    logger.info("[ActivitySyncer] Got 401; attempting token refresh and retry")
                    return self._retry_with_refreshed_token(payload, projects)
                else:
                    # Other HTTP error
                    logger.error(
                        f"[ActivitySyncer] HTTP error {e.response.status_code}: {e.response.text}"
                    )
                    return False
            
        except Exception as e:
            logger.error(f"[ActivitySyncer] Unexpected error: {e}", exc_info=True)
            return False

    def _build_payload(self, projects: list[dict]) -> dict:
        """Build sync API request payload.
        
        Args:
            projects: List of project dicts from ActivityCollector
            
        Returns:
            Request body dict ready for JSON serialization
        """
        # Get user_id
        user_id = self.user_id
        if not user_id:
            local_user = self.db.get_local_user()
            if local_user:
                user_id = local_user.get("_id")
            else:
                raise ValueError("Cannot determine user_id; not signed in?")
        
        payload = {
            "user_id": user_id,
            "sync_timestamp": datetime.utcnow().isoformat() + "Z",
            "data": projects,
        }
        
        logger.debug(f"[ActivitySyncer] Built payload with {len(projects)} projects")
        return payload

    def _get_id_token(self) -> Optional[str]:
        """Get current Firebase ID token.
        
        Will automatically refresh if expired. Returns None if unavailable.
        
        Returns:
            Valid ID token or None if unavailable
        """
        try:
            token = get_valid_id_token()
            if token:
                logger.debug("[ActivitySyncer] Obtained ID token")
                return token
            else:
                logger.warning("[ActivitySyncer] get_valid_id_token returned None")
                return None
        except TokenError as e:
            logger.error(f"[ActivitySyncer] Error getting ID token: {e}")
            return None
        except Exception as e:
            logger.error(f"[ActivitySyncer] Unexpected error getting ID token: {e}")
            return None

    def _send_sync_request(self, payload: dict, id_token: str) -> dict:
        """Send sync request to backend.
        
        Args:
            payload: Request body dict
            id_token: Firebase ID token for Authorization header
            
        Returns:
            Response JSON dict
            
        Raises:
            requests.HTTPError: On non-2xx response
        """
        url = f"{_BACKEND_BASE_URL}/sync/activity"
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json",
        }
        
        logger.info(f"[ActivitySyncer] Sending POST {url}")
        logger.debug(f"[ActivitySyncer] Payload size: {len(json.dumps(payload))} bytes")
        
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
        
        logger.debug(f"[ActivitySyncer] Response status: {response.status_code}")
        
        if response.status_code >= 400:
            raise requests.HTTPError(response=response)
        
        response_json = response.json()
        logger.info(f"[ActivitySyncer] Sync response: {response_json.get('message', 'OK')}")
        
        return response_json

    def _retry_with_refreshed_token(self, payload: dict, projects: list[dict]) -> bool:
        """Retry sync request after refreshing token.
        
        Since get_valid_id_token() automatically refreshes expired tokens,
        we just fetch a fresh token and retry the request.
        
        Args:
            payload: Request payload
            projects: List of projects for marking synced on success
            
        Returns:
            True if retry succeeded, False otherwise
        """
        try:
            logger.info("[ActivitySyncer] Got 401; fetching fresh token and retrying")
            
            # get_valid_id_token() will refresh automatically if needed
            id_token = self._get_id_token()
            if not id_token:
                logger.error("[ActivitySyncer] Failed to obtain fresh token")
                return False
            
            # Retry request with fresh token
            logger.info("[ActivitySyncer] Retrying request with fresh token")
            response = self._send_sync_request(payload, id_token)
            
            # Mark as synced
            self._mark_all_synced(projects)
            logger.info("[ActivitySyncer] Retry succeeded")
            return True
            
        except Exception as e:
            logger.error(f"[ActivitySyncer] Token refresh/retry failed: {e}")
            return False

    def _mark_all_synced(self, projects: list[dict]):
        """Mark all synced projects and dates as needs_sync = 0.
        
        Args:
            projects: List of project dicts (same as sync payload data)
        """
        try:
            for project in projects:
                project_name = project.get("project_name")
                days = project.get("days", [])
                
                # Mark all dates for this project
                for day in days:
                    date = day.get("date")
                    self.db.mark_project_synced(project_name, date)
                
                # Also mark LOC snapshots as synced
                self.db.conn.execute(
                    "UPDATE project_loc_snapshots SET needs_sync = 0 WHERE project_name = ?",
                    (project_name,),
                )
            
            self.db.conn.commit()
            logger.info(f"[ActivitySyncer] Marked {len(projects)} projects as synced")
            
        except Exception as e:
            logger.error(f"[ActivitySyncer] Error marking records as synced: {e}")
            raise
