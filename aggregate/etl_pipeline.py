"""Phase 4: ETL Pipeline Maestro (Master Orchestrator).

This is the main conductor that orchestrates the entire aggregation process.
Instead of running transform logic in each aggregator, we run it once here
and pass the "clean" batch to specialized aggregators for their specific tasks.

Flow:
1. EXTRACT: Query raw_activity_logs for unprocessed, tagged logs
2. TRANSFORM: Run once:
   - Sticky-project logic (with 15-min TTL)
   - Blacklist/distraction detection
   - Midnight splitting (convert UTC to local dates, split overnight boundaries)
3. DELEGATE: Pass clean batch to each aggregator → get SQL commands
4. LOAD: Execute all SQL commands in ONE atomic transaction
"""

from datetime import datetime, time, timedelta
from collections import defaultdict


class ETLPipeline:
    """The Maestro: orchestrates extraction, transformation, and delegation."""

    def __init__(self, db, config=None, local_tz_offset_hours=None):
        """Initialize ETL pipeline.
        
        Args:
            db: Database instance (from database/db.py)
            config: Config instance (from config/config.py) - optional but recommended
            local_tz_offset_hours: Hours offset from UTC for local date bucketing (e.g., -5 for EST)
                                  If None, will be read from config under etl_pipeline.local_tz_offset_hours
        """
        self.db = db
        self.config = config
        if local_tz_offset_hours is None and config is not None:
            etl_cfg = config.get("etl_pipeline", {})
            local_tz_offset_hours = etl_cfg.get("local_tz_offset_hours", 0)

        self.local_tz_offset = local_tz_offset_hours or 0
        
        # Read configuration
        if config:
            etl_cfg = config.get('etl_pipeline', {})
            self.sticky_project_ttl_sec = etl_cfg.get('sticky_project_ttl_sec', 900)  # 15 min default
            self.app_name_mapping = etl_cfg.get('app_name_mapping', {})
            self.language_to_skill_mapping = etl_cfg.get('language_to_skill_mapping', {})
        else:
            # Fallback defaults if no config provided
            self.sticky_project_ttl_sec = 900  # 15 minutes
            self.app_name_mapping = {}
            self.language_to_skill_mapping = {}
        
        # Build distraction apps list (default hardcoded + custom from config)
        self.distraction_apps = self._build_distraction_apps_list(config)
        
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
    
    def _build_distraction_apps_list(self, config):
        """Build a list of distraction apps from config (main list + custom additions).
        
        Returns:
            Set of lowercase app names to treat as distractions
        """
        distraction_apps = set()
        
        if config:
            app_cat = config.get('app_categorization', {})
            
            # Read main distraction apps list from config
            main_list = app_cat.get('distraction_apps', [])
            if main_list:
                distraction_apps.update({app.lower() for app in main_list})
            
            # Add custom distraction apps from config
            custom = app_cat.get('custom_distraction_apps', [])
            if custom:
                distraction_apps.update({app.lower() for app in custom})
        
        return distraction_apps

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
                           detected_language, context_state, duration_sec, typing_intensity,
                           mouse_click_rate, mouse_scroll_events, idle_duration_sec)
        """
        cursor = self.db.conn.execute(
            """
            SELECT log_id, start_time, end_time, app_name, project_name, project_path,
                   detected_language, context_state, duration_sec, typing_intensity,
                   mouse_click_rate, mouse_scroll_events, idle_duration_sec
            FROM raw_activity_logs
            WHERE is_aggregated = 0
              AND context_state IS NOT NULL
            ORDER BY start_time ASC
            """
        )
        return cursor.fetchall()

    def _transform_logs(self, raw_logs):
        """Apply transformation logic once to create "clean" logs.
        
        1. Sticky-project inheritance (15-min TTL)
        2. Blacklist detection (distracted apps)
        3. Midnight splitting (UTC → local dates, split overnight boundaries)
        
        Args:
            raw_logs: List of raw log tuples from _extract_raw_logs()
            
        Returns:
            List of dicts with keys: log_id, date, app_name, project_name, language_name,
                                     context_state, duration_sec, end_time_utc
                                     AND pre-split into local-date segments
        """
        sticky_project = None
        sticky_project_last_seen = None
        sticky_project_ttl_sec = self.sticky_project_ttl_sec

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
            duration_sec,
            typing_intensity,
            mouse_click_rate,
            mouse_scroll_events,
            idle_duration_sec,
        ) in raw_logs:
            # Parse timestamps (UTC)
            start_utc = datetime.fromisoformat(start_time_iso)
            end_utc = datetime.fromisoformat(end_time_iso)

            # Convert to local time for date bucketing
            start_local = start_utc + timedelta(hours=self.local_tz_offset)
            end_local = end_utc + timedelta(hours=self.local_tz_offset)

            # ==================== BLACKLIST CHECK ====================
            # Check if app is in distraction/blacklist apps
            app_name_lower = app_name.lower() if app_name else ""
            is_blacklisted = any(
                dist_app in app_name_lower 
                for dist_app in self.distraction_apps
            )

            if is_blacklisted:
                # Distraction apps → force __unassigned__ project and Distracted state
                attributed_project = "__unassigned__"
                context_state = "Distracted"
                sticky_project = None  # Clear sticky on distraction
                sticky_project_last_seen = None
            else:
                # ==================== STICKY PROJECT LOGIC ====================
                if project_name:
                    # Log has explicit project → use it and update sticky
                    attributed_project = project_name
                    sticky_project = project_name
                    sticky_project_last_seen = end_utc
                else:
                    # No explicit project → try sticky inheritance
                    if (
                        sticky_project
                        and sticky_project_last_seen
                        and (end_utc - sticky_project_last_seen).total_seconds()
                        < sticky_project_ttl_sec
                    ):
                        attributed_project = sticky_project
                        sticky_project_last_seen = end_utc
                    else:
                        # Sticky expired or not set → unassigned
                        attributed_project = "__unassigned__"
                        sticky_project = None

            # Handle language (default to "Unknown")
            language_name = detected_language or "Unknown"

            # Clean the app name
            clean_app_name = app_mapping.get(app_name.lower(), app_name.replace(".exe", "").title())

            # ==================== MIDNIGHT SPLITTING ====================
            segments = self._split_across_midnight_local(start_local, end_local)

            for seg_start, seg_end in segments:
                date_str = seg_start.strftime("%Y-%m-%d")
                seg_duration = int((seg_end - seg_start).total_seconds())
                
                # Convert segment end back to UTC for accurate DB timestamps
                seg_end_utc = seg_end - timedelta(hours=self.local_tz_offset)

                transformed.append({
                    "log_id": log_id,
                    "date": date_str,
                    "app_name": clean_app_name,
                    "project_name": attributed_project,
                    "project_path": project_path,
                    "language_name": language_name,
                    "context_state": context_state,
                    "duration_sec": seg_duration,
                    "end_time_utc": seg_end_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "typing_intensity": typing_intensity,
                    "mouse_click_rate": mouse_click_rate,
                    "mouse_scroll_events": mouse_scroll_events,
                    "idle_duration_sec": idle_duration_sec,
                })

        return transformed

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
                # Execute all aggregator-generated SQL commands
                for query, params in sql_commands:
                    self.db.conn.execute(query, params)

                # Mark all raw logs as aggregated
                log_ids = list(set(log["log_id"] for log in transformed_logs))
                if log_ids:
                    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
