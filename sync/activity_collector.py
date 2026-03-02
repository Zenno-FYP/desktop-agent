"""Activity data collection from SQLite for backend sync.

Collects pending aggregates (needs_sync=1) from daily project tables,
groups them by project and date, and structures them according to the
sync API specification.
"""

import logging
from datetime import datetime
from typing import Optional


logger = logging.getLogger(__name__)


class ActivityCollector:
    """Collects and structures pending activity data from SQLite."""

    def __init__(self, db):
        """Initialize collector with database reference.
        
        Args:
            db: Database instance for querying aggregates
        """
        self.db = db

    def collect_pending_projects(self) -> list[dict]:
        """Collect all projects with pending sync data.
        
        Groups all pending aggregates by project and date, structures them
        according to the sync API spec, and includes project metadata.
        
        Returns:
            List of project dicts with metadata, loc snapshots, and daily buckets.
            
        Example:
            [
                {
                    "project_name": "desktop-agent",
                    "metadata": {
                        "first_seen_at": "2026-02-27T09:00:00",
                        "last_active_at": "2026-03-01T14:25:00"
                    },
                    "current_loc": [
                        {"language": "python", "lines": 4500, "files": 12}
                    ],
                    "days": [
                        {
                            "date": "2026-02-27",
                            "languages": {"python": 1200, "sql": 300},
                            "apps": {"vscode": 1400},
                            ...
                        }
                    ]
                }
            ]
        """
        try:
            # Get list of projects that have pending sync data
            pending_projects = self.db.get_projects_pending_sync()
            
            if not pending_projects:
                logger.debug("No pending projects to sync")
                return []
            
            projects_data = []
            
            for project_name in pending_projects:
                try:
                    project_dict = self._build_project_dict(project_name)
                    if project_dict and project_dict.get("days"):
                        projects_data.append(project_dict)
                except Exception as e:
                    logger.error(f"Error collecting data for project '{project_name}': {e}")
                    # Continue with next project; don't block entire sync
                    continue
            
            return projects_data
            
        except Exception as e:
            logger.error(f"Error collecting pending projects: {e}")
            return []

    def _build_project_dict(self, project_name: str) -> Optional[dict]:
        """Build complete project dict with metadata, LOC, and daily data.
        
        Args:
            project_name: Name of project to collect data for
            
        Returns:
            Project dict or None if project has no data
        """
        # Get project metadata (first_seen_at, last_active_at)
        metadata = self._get_project_metadata(project_name)
        if not metadata:
            logger.warning(f"Project '{project_name}' has no metadata; skipping")
            return None
        
        # Get current LOC snapshots for this project
        current_loc = self._get_project_loc(project_name)
        
        # Collect daily aggregates
        days = self._collect_daily_buckets(project_name)
        
        if not days:
            logger.debug(f"Project '{project_name}' has no pending daily data")
            return None
        
        # Build metadata object - always send both timestamps, backend handles differentiation
        metadata_obj = {}
        if metadata.get("first_seen_at"):
            metadata_obj["first_seen_at"] = metadata["first_seen_at"]
        if metadata.get("last_active_at"):
            metadata_obj["last_active_at"] = metadata["last_active_at"]
        
        return {
            "project_name": project_name,
            "metadata": metadata_obj,
            "current_loc": current_loc,
            "days": days,
        }

    def _get_project_metadata(self, project_name: str) -> Optional[dict]:
        """Get project metadata from projects table.
        
        Args:
            project_name: Project identifier
            
        Returns:
            Dict with first_seen_at, last_active_at (in local time), or None if not found
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT first_seen_at, last_active_at
                FROM projects
                WHERE project_name = ?
                """,
                (project_name,),
            )
            row = cursor.fetchone()
            
            if not row:
                return None
            
            return {
                "first_seen_at": row[0],
                "last_active_at": row[1],
            }
        except Exception as e:
            logger.error(f"Error querying metadata for '{project_name}': {e}")
            return None
    
    def _get_project_loc(self, project_name: str) -> list[dict]:
        """Get current LOC snapshots for project.
        
        Args:
            project_name: Project identifier
            
        Returns:
            List of {"language": "...", "lines": ..., "files": ...}
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT language_name, lines_of_code, file_count
                FROM project_loc_snapshots
                WHERE project_name = ?
                ORDER BY language_name
                """,
                (project_name,),
            )
            rows = cursor.fetchall()
            
            result = []
            for row in rows:
                if row[1] > 0:  # Only include if lines > 0
                    result.append({
                        "language": row[0],
                        "lines": row[1],
                        "files": row[2],
                    })
            
            return result
        except Exception as e:
            logger.error(f"Error querying LOC for '{project_name}': {e}")
            return []

    def _collect_daily_buckets(self, project_name: str) -> list[dict]:
        """Collect all daily aggregates for a project.
        
        Groups pending data (needs_sync=1) by date into daily buckets.
        Each bucket contains languages, apps, skills, context, and behavior metrics.
        
        Args:
            project_name: Project identifier
            
        Returns:
            List of daily bucket dicts, sorted by date
        """
        try:
            # Get all unique dates with pending data for this project
            cursor = self.db.conn.execute(
                """
                SELECT DISTINCT date
                FROM daily_project_languages
                WHERE project_name = ? AND needs_sync = 1
                ORDER BY date
                """,
                (project_name,),
            )
            dates = [row[0] for row in cursor.fetchall()]
            
            if not dates:
                logger.debug(f"No pending daily data for project '{project_name}'")
                return []
            
            days = []
            for date in dates:
                day_bucket = self._build_daily_bucket(project_name, date)
                if day_bucket:
                    days.append(day_bucket)
            
            return days
            
        except Exception as e:
            logger.error(f"Error collecting daily buckets for '{project_name}': {e}")
            return []

    def _build_daily_bucket(self, project_name: str, date: str) -> Optional[dict]:
        """Build a single daily aggregate bucket.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Daily bucket dict or None if empty
        """
        languages = self._get_daily_languages(project_name, date)
        apps = self._get_daily_apps(project_name, date)
        skills = self._get_daily_skills(project_name, date)
        context = self._get_daily_context(project_name, date)
        behavior = self._get_daily_behavior(project_name, date)
        
        # Skip if all metrics are empty
        if not any([languages, apps, skills, context, behavior]):
            logger.debug(f"No metrics for {project_name} on {date}")
            return None
        
        return {
            "date": date,
            "languages": languages,
            "apps": apps,
            "skills": skills,
            "context": context,
            "behavior": behavior,
        }

    def _get_daily_languages(self, project_name: str, date: str) -> dict:
        """Get language metrics for a specific day.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Dict mapping language name to duration_sec
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT language_name, duration_sec
                FROM daily_project_languages
                WHERE project_name = ? AND date = ? AND needs_sync = 1
                """,
                (project_name, date),
            )
            
            result = {}
            for row in cursor.fetchall():
                if row[1] > 0:  # Only include non-zero durations
                    result[row[0]] = row[1]
            
            return result
        except Exception as e:
            logger.error(f"Error querying languages for {project_name}/{date}: {e}")
            return {}

    def _get_daily_apps(self, project_name: str, date: str) -> dict:
        """Get app metrics for a specific day.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Dict mapping app name to duration_sec
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT app_name, duration_sec
                FROM daily_project_apps
                WHERE project_name = ? AND date = ? AND needs_sync = 1
                """,
                (project_name, date),
            )
            
            result = {}
            for row in cursor.fetchall():
                if row[1] > 0:
                    result[row[0]] = row[1]
            
            return result
        except Exception as e:
            logger.error(f"Error querying apps for {project_name}/{date}: {e}")
            return {}

    def _get_daily_skills(self, project_name: str, date: str) -> dict:
        """Get skill metrics for a specific day.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Dict mapping skill name to duration_sec
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT skill_name, duration_sec
                FROM daily_project_skills
                WHERE project_name = ? AND date = ? AND needs_sync = 1
                """,
                (project_name, date),
            )
            
            result = {}
            for row in cursor.fetchall():
                if row[1] > 0:
                    result[row[0]] = row[1]
            
            return result
        except Exception as e:
            logger.error(f"Error querying skills for {project_name}/{date}: {e}")
            return {}

    def _get_daily_context(self, project_name: str, date: str) -> dict:
        """Get context/state metrics for a specific day.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Dict mapping context state to duration_sec
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT context_state, duration_sec
                FROM daily_project_context
                WHERE project_name = ? AND date = ? AND needs_sync = 1
                """,
                (project_name, date),
            )
            
            result = {}
            for row in cursor.fetchall():
                if row[1] > 0:
                    result[row[0]] = row[1]
            
            return result
        except Exception as e:
            logger.error(f"Error querying context for {project_name}/{date}: {e}")
            return {}

    def _get_daily_behavior(self, project_name: str, date: str) -> dict:
        """Get behavior metrics (keystrokes, clicks, scrolls, idle) for a day.
        
        Args:
            project_name: Project identifier
            date: YYYY-MM-DD date string
            
        Returns:
            Dict with keystrokes, clicks, scrolls, idle_sec keys
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT total_keystrokes, total_mouse_clicks, 
                       total_scroll_events, total_idle_sec
                FROM daily_project_behavior
                WHERE project_name = ? AND date = ? AND needs_sync = 1
                """,
                (project_name, date),
            )
            
            row = cursor.fetchone()
            if not row:
                return {}
            
            return {
                "keystrokes": row[0],
                "clicks": row[1],
                "scrolls": row[2],
                "idle_sec": row[3],
            }
        except Exception as e:
            logger.error(f"Error querying behavior for {project_name}/{date}: {e}")
            return {}
