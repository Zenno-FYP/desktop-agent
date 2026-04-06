"""Phase 4: ETL Pipeline Maestro (Master Orchestrator).

This is the main conductor that orchestrates the entire aggregation process.
Instead of running transform logic in each aggregator, we run it once here
and pass the "clean" batch to specialized aggregators for their specific tasks.

Flow:
1. EXTRACT: Query raw_activity_logs for unprocessed, tagged logs
   - Retrieves: activity_type, timestamp, duration_sec, typing_intensity, mouse_click_rate,
     deletion_key_presses, idle_duration_sec, mouse_movement_distance, app_name, raw_window_title,
     manually_verified_label, manually_verified_project, context_tag, is_processed
2. TRANSFORM: Run once:
    - Project attribution (trust raw logs; assign "__unassigned__" when missing)
    - Manual context override (use manually_verified_label when present)
    - Midnight splitting (split overnight boundaries in local time)
    - Physical effort metrics pass-through: typing_intensity, mouse_click_rate, deletion_key_presses,
      idle_duration_sec, mouse_movement_distance
3. DELEGATE: Pass clean batch to each aggregator → get SQL commands
4. LOAD: Execute all SQL commands in ONE atomic transaction
"""

from datetime import datetime, time, timedelta
from collections import defaultdict


class ETLPipeline:
    """The Maestro: orchestrates extraction, transformation, and delegation."""

    def __init__(self, db, config=None):
        """Initialize ETL pipeline.
        
        Args:
            db: Database instance (from database/db.py)
            config: Config instance (from config/config.py) - optional but recommended
        """
        self.db = db
        self.config = config
        
        # Read configuration
        if config:
            etl_cfg = config.get('etl_pipeline', {})
            self.app_name_mapping = etl_cfg.get('app_name_mapping', {})
            self.language_to_skill_mapping = etl_cfg.get('language_to_skill_mapping', {})
            
            # Browser detection config
            browser_cfg = etl_cfg.get('browser_detection', {})
            self.browsers = set(browser_cfg.get('browsers', []))
            self.service_keywords = browser_cfg.get('service_keywords', {})
        else:
            # Fallback defaults if no config provided
            self.app_name_mapping = {}
            self.language_to_skill_mapping = {}
            self.browsers = set()
            self.service_keywords = {}
        
        # Initialize aggregators
        from aggregate.project_aggregator import ProjectAggregator
        from aggregate.app_aggregator import AppAggregator
        from aggregate.language_aggregator import LanguageAggregator
        from aggregate.skill_aggregator import SkillAggregator
        from aggregate.context_aggregator import ContextAggregator
        from aggregate.behavior_aggregator import BehaviorAggregator
        
        self.aggregators = [
            ProjectAggregator(),
            AppAggregator(),
            LanguageAggregator(),
            SkillAggregator(language_to_skill_mapping=self.language_to_skill_mapping),
            ContextAggregator(),
            BehaviorAggregator(),
        ]
    
    def _get_local_time(self) -> str:
        """Get current local time as formatted string (YYYY-MM-DD HH:MM:SS)."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def run(self):
        """Execute the full ETL pipeline.
        
        Called by BlockEvaluator immediately after ML tagging.
        Runs entire transformation and loading in one atomic transaction.
        """
        if not self.db.conn:
            raise RuntimeError("Database not connected. Call db.connect() first.")

        # ==================== EXTRACT ====================
        clean_logs = self._extract_raw_logs()
        
        if not clean_logs:
            return

        # ==================== TRANSFORM ====================
        transformed_logs = self._transform_logs(clean_logs)

        # ==================== DELEGATE ====================
        all_sql_commands = []
        for aggregator in self.aggregators:
            sql_commands = aggregator.generate_upserts(transformed_logs)
            all_sql_commands.extend(sql_commands)

        # ==================== LOAD ====================
        self._execute_batch(transformed_logs, all_sql_commands)

    def _extract_raw_logs(self):
        """Query raw_activity_logs for unprocessed, ML-tagged logs.
        
        Returns:
            List of tuples: (log_id, start_time, end_time, app_name, project_name, project_path,
                           detected_language, context_state, manually_verified_label, duration_sec, typing_intensity,
                           mouse_click_rate, deletion_key_presses, idle_duration_sec, active_file, mouse_movement_distance)
        """
        cursor = self.db.conn.execute(
            """
            SELECT log_id, start_time, end_time, app_name, project_name, project_path,
                   detected_language, context_state, manually_verified_label, duration_sec, typing_intensity,
                   mouse_click_rate, deletion_key_presses, idle_duration_sec, active_file, mouse_movement_distance
            FROM raw_activity_logs
            WHERE is_aggregated = 0
              AND context_state IS NOT NULL
            ORDER BY start_time ASC
            """
        )
        return cursor.fetchall()

    def _transform_logs(self, raw_logs):
        """Apply transformation logic once to create "clean" logs.
        
        SIMPLIFIED (Shift-Left Sticky Project):
        1. Trust database project_name (already filled by upstream collector logic)
        2. Midnight splitting (split overnight boundaries in local time)
        3. Browser keyword extraction (for Chrome, Firefox, etc., extract active service from URL)
        
        NOTE: Sticky project logic has been moved to collection phase (agent.py).
        If project_name is in raw_activity_logs, it is mathematically correct.
        We simply read it and trust it here.
        
        Args:
            raw_logs: List of raw log tuples from _extract_raw_logs()
            
        Returns:
            List of dicts with keys: log_id, date, app_name, project_name, language_name,
                                     context_state, duration_sec, end_time
                                     AND pre-split into local-date segments
        """
        # App name mapping dictionary for cleaning .exe files (from config)
        app_mapping = self.app_name_mapping

        transformed = []

        for (
            log_id,
            start_time_iso,
            end_time_iso,
            app_name,
            project_name,
            project_path,
            detected_language,
            context_state,
            manually_verified_label,
            duration_sec,
            typing_intensity,
            mouse_click_rate,
            deletion_key_presses,
            idle_duration_sec,
            active_file,
            mouse_movement_distance,
        ) in raw_logs:
            # Parse timestamps (already in local time)
            start_local = datetime.fromisoformat(start_time_iso)
            end_local = datetime.fromisoformat(end_time_iso)

            # ==================== PROJECT RESOLUTION ====================
            # Trust the database! The upstream tracker already handled the 15-min TTL.
            # If there's a project name here, it's because the collector put it there.
            # If there's no project name, it's definitively unassigned.
            attributed_project = project_name if project_name else "__unassigned__"

            # Handle language (default to "Unknown")
            language_name = detected_language or "Unknown"

            # ==================== CONTEXT STATE PRIORITIZATION ====================
            # Phase 3B: If user manually verified the context (ESM feedback), use that.
            # Otherwise, fall back to ML-generated context_state (model prediction).
            final_context = manually_verified_label if manually_verified_label else context_state

            # Clean the app name
            # First, try to find exact match in app_mapping (case-insensitive)
            app_lower = app_name.lower()
            if app_lower in app_mapping:
                clean_app_name = app_mapping[app_lower]
            else:
                # Fallback: remove everything after the first dot and title-case
                cleaned = app_name.split('.')[0]
                clean_app_name = cleaned.title()
            
            # Browser keyword extraction: if it's a browser, try to extract service from URL
            clean_app_name = self._extract_browser_app_name(clean_app_name, active_file)

            # ==================== MIDNIGHT SPLITTING ====================
            segments = self._split_across_midnight_local(start_local, end_local)

            for seg_start, seg_end in segments:
                date_str = seg_start.strftime("%Y-%m-%d")
                seg_duration = int((seg_end - seg_start).total_seconds())

                transformed.append({
                    "log_id": log_id,
                    "date": date_str,
                    "app_name": clean_app_name,
                    "project_name": attributed_project,
                    "project_path": project_path,
                    "language_name": language_name,
                    "context_state": final_context,
                    "duration_sec": seg_duration,
                    "end_time_local": seg_end.strftime("%Y-%m-%d %H:%M:%S"),
                    "typing_intensity": typing_intensity,
                    "mouse_click_rate": mouse_click_rate,
                    "deletion_key_presses": deletion_key_presses,
                    "idle_duration_sec": idle_duration_sec,
                    "mouse_movement_distance": mouse_movement_distance,
                })

        return transformed

    def _extract_browser_app_name(self, app_name: str, active_file: str) -> str:
        """Extract service name from browser URL/tab if app is a browser.
        
        For browsers configured in browser_detection.browsers, checks active_file (URL/tab name)
        for keywords matching known services. If found, returns service name as app_name.
        Otherwise returns original app_name (e.g., "Browser", "Chrome").
        
        Keywords and browsers are loaded from config (etl_pipeline.browser_detection).
        This allows easy addition of new browsers and services without code changes.
        
        Args:
            app_name: Cleaned app name (e.g., "Chrome", "Browser")
            active_file: URL/tab name from browser (can be None)
            
        Returns:
            Service name if keyword found, otherwise original app_name
        """
        # Check if this is a known browser (case-insensitive)
        app_lower = app_name.lower()
        if app_lower not in self.browsers:
            return app_name
        
        # If no active_file, can't extract service name
        if not active_file:
            return app_name
        
        active_lower = active_file.lower()
        
        # Search for keywords in active_file (case-insensitive)
        # service_keywords dict has: keyword → display_name
        for keyword, display_name in self.service_keywords.items():
            if keyword in active_lower:
                return display_name
        
        # No keyword found, return original app_name (browser name)
        return app_name

    def _split_across_midnight_local(self, start_local, end_local):
        """Split a time interval across local midnight boundaries.
        
        Args:
            start_local: datetime in local time
            end_local: datetime in local time
            
        Returns:
            List of (segment_start, segment_end) tuples [start, end)
        """
        segments = []
        cursor = start_local

        while cursor.date() < end_local.date():
            # Compute midnight boundary
            midnight = datetime.combine(cursor.date() + timedelta(days=1), time(0, 0, 0))
            segments.append((cursor, midnight))
            cursor = midnight

        # Add final segment
        segments.append((cursor, end_local))

        return segments

    def _execute_batch(self, transformed_logs, sql_commands):
        """Execute all SQL commands in one atomic transaction.
        
        After all aggregators have generated their UPSERT commands,
        execute them all at once. Then mark raw logs as aggregated.
        
        Args:
            transformed_logs: List of transformed log dicts (used to get log_ids)
            sql_commands: List of (query_string, params_tuple) to execute
        """
        try:
            with self.db.conn:
                # Ensure __unassigned__ project exists before inserting aggregated data
                # (needed for foreign key constraints in aggregation tables)
                now = self._get_local_time()
                self.db.conn.execute(
                    """
                    INSERT OR IGNORE INTO projects (project_name, project_path, first_seen_at, last_active_at, needs_sync)
                    VALUES (?, NULL, ?, ?, 0)
                    """,
                    ("__unassigned__", now, now)
                )
                
                # Execute all aggregator-generated SQL commands
                for query, params in sql_commands:
                    self.db.conn.execute(query, params)

                # Mark all raw logs as aggregated
                log_ids = list(set(log["log_id"] for log in transformed_logs))
                if log_ids:
                    now = self._get_local_time()
                    placeholders = ",".join("?" * len(log_ids))
                    self.db.conn.execute(
                        f"""
                        UPDATE raw_activity_logs
                        SET is_aggregated = 1,
                            aggregated_at = ?
                        WHERE log_id IN ({placeholders})
                        """,
                        [now] + log_ids,
                    )

                print(
                    f"[ETLPipeline] Processed {len(log_ids)} raw logs → {len(sql_commands)} UPSERT commands executed"
                )

        except Exception as e:
            print(f"[ETLPipeline] Error executing batch: {e}")
            raise
