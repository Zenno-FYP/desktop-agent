"""Phase 4: ETL Pipeline Maestro (Master Orchestrator).

This is the main conductor that orchestrates the entire aggregation process.
Instead of running transform logic in each aggregator, we run it once here
and pass the "clean" batch to specialized aggregators for their specific tasks.

Flow:
1. EXTRACT: Query raw_activity_logs for unprocessed, tagged logs
2. TRANSFORM: Run once:
    - Project attribution (trust raw logs; assign "__unassigned__" when missing)
    - Manual context override (use manually_verified_label when present)
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
            self.app_name_mapping = etl_cfg.get('app_name_mapping', {})
            self.language_to_skill_mapping = etl_cfg.get('language_to_skill_mapping', {})
        else:
            # Fallback defaults if no config provided
            self.app_name_mapping = {}
            self.language_to_skill_mapping = {}
        
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
                           mouse_click_rate, mouse_scroll_events, idle_duration_sec)
        """
        cursor = self.db.conn.execute(
            """
            SELECT log_id, start_time, end_time, app_name, project_name, project_path,
                   detected_language, context_state, manually_verified_label, duration_sec, typing_intensity,
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
        
        SIMPLIFIED (Shift-Left Sticky Project):
        1. Trust database project_name (already filled by upstream collector logic)
        2. Midnight splitting (UTC → local dates, split overnight boundaries)
        
        NOTE: Sticky project logic has been moved to collection phase (agent.py).
        If project_name is in raw_activity_logs, it is mathematically correct.
        We simply read it and trust it here.
        
        Args:
            raw_logs: List of raw log tuples from _extract_raw_logs()
            
        Returns:
            List of dicts with keys: log_id, date, app_name, project_name, language_name,
                                     context_state, duration_sec, end_time_utc
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
            mouse_scroll_events,
            idle_duration_sec,
        ) in raw_logs:
            # Parse timestamps (UTC)
            start_utc = datetime.fromisoformat(start_time_iso)
            end_utc = datetime.fromisoformat(end_time_iso)

            # Convert to local time for date bucketing
            start_local = start_utc + timedelta(hours=self.local_tz_offset)
            end_local = end_utc + timedelta(hours=self.local_tz_offset)

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
                    "context_state": final_context,
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
                # Ensure __unassigned__ project exists before inserting aggregated data
                # (needed for foreign key constraints in aggregation tables)
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
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
