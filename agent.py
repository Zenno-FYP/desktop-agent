"""
Zenno Desktop Agent - Entry Point (Phase 1: Core Activity Detection)
"""
import time
import logging
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config.config import Config
from database.db import Database
from monitor.app_focus import get_active_window
from monitor.behavioral_metrics import BehavioralMetrics
from monitor.idle_detector import IdleDetector
from monitor.project_detector import ProjectDetector
from analyze.context_detector import ContextDetector
from analyze.block_evaluator import BlockEvaluator
from aggregate.loc_scanner import LOCScanner
from aggregate.etl_pipeline import ETLPipeline
from sync.activity_syncer import ActivitySyncer
from nudge.nudge_scheduler import NudgeScheduler
from nudge.user_preferences import UserPreferences


class ActivitySession:
    """Track a single application/window session with all behavioral data."""

    def __init__(
        self,
        app_name: str,
        window_title: str,
        pid: Optional[int] = None,
        idle_threshold_sec: int = 10,
        metrics: Optional[BehavioralMetrics] = None,
        config=None,
    ):
        """Initialize new activity session.
        
        Args:
            app_name: Application name
            window_title: Window title
            pid: Process ID (optional)
            idle_threshold_sec: Seconds of inactivity before marking as idle (default 10)
            config: Configuration instance
        """
        # Store local time (system's current timezone)
        self.start_time = datetime.now().isoformat()
        self.app_name = app_name
        self.window_title = window_title
        self.pid = pid

        self.logger = logging.getLogger(__name__)
        
        # Behavioral tracking is global (listeners started once by DesktopAgent).
        # Per-session metrics are obtained by resetting counters at session start.
        self.metrics = metrics or BehavioralMetrics()
        self.metrics.reset()
        self.idle_detector = IdleDetector(idle_threshold_sec=idle_threshold_sec)
        self.project_detector = ProjectDetector(config=config)
        
        # Track file changes (for tab switching)
        project, file = self.project_detector.detect_project(app_name, window_title)
        self.current_file = file
        self.current_project = project
        
        self.logger.info("[Session] Started: %s | %s | File: %s", app_name, window_title, file)

    def collect_data(self) -> dict:
        """Collect all behavioral data for this session.
        
        Returns:
            dict with complete session data ready for database insertion
        """
        # Get local time
        end_time = datetime.now().isoformat()
        start_dt = datetime.fromisoformat(self.start_time)
        end_dt = datetime.fromisoformat(end_time)
        duration_sec = int((end_dt - start_dt).total_seconds())
        # Ensure minimum duration of 1 second to avoid validation errors
        if duration_sec <= 0:
            duration_sec = 1
            # Adjust end_time to be 1 second after start_time
            end_dt = start_dt + timedelta(seconds=1)
            end_time = end_dt.isoformat()
        # Cap duration to 1 hour to prevent hibernation/sleep anomalies from
        # inflating a single session with 8+ hours of phantom work time.
        elif duration_sec > 3600:
            self.logger.warning(
                "[Session] Duration anomaly detected (%ds > 3600s cap). "
                "Likely caused by system sleep/hibernate. Capping to 3600s.",
                duration_sec,
            )
            duration_sec = 3600
            end_dt = start_dt + timedelta(seconds=3600)
            end_time = end_dt.isoformat()
        
        # Get behavioral metrics
        metrics = self.metrics.get_metrics()
        idle_metrics = self.idle_detector.get_idle_metrics()
        
        # Use current file info (may have been updated by tab switch detection)
        detected_language = self.project_detector.get_detected_language(self.current_file)
        project_path = self.project_detector.get_project_path(self.app_name, self.window_title, self.pid, self.current_file)
        
        # Compile activity log entry
        activity_data = {
            'start_time': self.start_time,
            'end_time': end_time,
            'app_name': self.app_name,
            'window_title': self.window_title,
            'duration_sec': duration_sec,
            'project_name': self.current_project,
            'project_path': project_path,
            'active_file': self.current_file,
            'detected_language': detected_language,
            'typing_intensity': metrics['typing_intensity'],
            'mouse_click_rate': metrics['mouse_click_rate'],
            'deletion_key_presses': metrics['deletion_key_presses'],
            # Raw distances for improved block feature extraction (Signal 2)
            'mouse_movement_distance': float(metrics.get('mouse_movement_distance', 0.0) or 0.0),
            'idle_duration_sec': idle_metrics['idle_duration_sec'],
            # Context state (Phase 2 will populate this with heuristics/ML)
            'context_state': None,
            'confidence_score': None,
        }
        
        return activity_data

    def has_file_changed(self, new_window_title: str) -> bool:
        """Check if the file changed (tab switch detection).
        
        Args:
            new_window_title: New window title from active window
            
        Returns:
            bool: True if file changed
        """
        _, new_file = self.project_detector.detect_project(self.app_name, new_window_title)
        
        # File changed if different from current
        if new_file and new_file != self.current_file:
            return True
        
        return False
    
    def update_file_context(self, new_window_title: str):
        """Update current file/project info from window title.
        
        Args:
            new_window_title: New window title from active window
        """
        project, file = self.project_detector.detect_project(
            self.app_name, new_window_title
        )
        
        self.current_file = file or self.current_file  # Keep old if not detected
        self.current_project = project or self.current_project
        self.window_title = new_window_title

    def end_session(self):
        """End the session."""
        self.logger.info("[Session] Ended: %s", self.app_name)


class DesktopAgent:
    """Main agent for activity detection and logging."""

    def __init__(self, config_path: str = None, user_preferences: UserPreferences | None = None):
        """Initialize the desktop agent.
        
        Args:
            config_path:       Optional path to config file.
            user_preferences:  Onboarding preferences loaded before agent start.
                               If None, the NudgeScheduler will use config defaults.
        """
        self._user_preferences = user_preferences
        self.config = Config(config_path) if config_path else Config()
        self._setup_logging()
        self.sample_interval = self.config.get("sample_interval_sec", 2)
        # Note: sample_interval of 2 seconds might cause input lag
        # Consider increasing to 3-4 seconds if experiencing mouse/keyboard stutter
        # Longer interval = more responsive input, but less frequent activity tracking
        self.flush_interval = self.config.get("flush_interval_sec", 300)
        self.idle_threshold_sec = self.config.get("idle_threshold_sec", 10)
        self.click_debounce_ms = self.config.get("behavioral_metrics.click_debounce_ms", 50)

        self.logger = logging.getLogger(__name__)

        # Global input listeners (do not start/stop per session).
        self.metrics = BehavioralMetrics(click_debounce_ms=int(self.click_debounce_ms))
        self.db_path = self.config.get("db.path", "./agent.db")
        db_check_same_thread = self.config.get("db.check_same_thread", False)
        db_timeout = self.config.get("db.timeout", 10.0)
        db_journal_mode = self.config.get("db.journal_mode", "WAL")
        
        # Initialize database
        self.db = Database(
            self.db_path,
            check_same_thread=db_check_same_thread,
            timeout=db_timeout,
            journal_mode=db_journal_mode,
            config=self.config,
        )
        self.db.connect()
        self.db.create_tables()
        self.logger.info("[Agent] Database initialized: %s", self.db_path)
        
        # Initialize Phase 2: Context detection via block evaluator
        self.context_detector = ContextDetector(self.config)
        block_duration_sec = self.config.get("block_duration_sec", 300)
        self.block_evaluator = BlockEvaluator(self.db, self.context_detector, config=self.config, block_duration_sec=block_duration_sec)
        self.block_evaluator.start()
        
        # Initialize Phase 4: LOC Scanner (triggered hourly)
        self.loc_scanner = LOCScanner(self.db, config=self.config)
        self.loc_scan_interval_sec = self.config.get("loc_scanner.scan_interval_sec", 3600)  # 1 hour default
        self.last_loc_scan_time = time.time()  # Initialize to now; first scan waits 1 hour
        
        # Initialize ETL Pipeline (aggregation; triggered every 15 minutes)
        self.etl_pipeline = ETLPipeline(self.db, config=self.config)
        self.etl_interval_sec = self.config.get("etl_pipeline.interval_sec", 900)  # 15 min default
        self.last_etl_time = time.time()  # Initialize to now; first sync waits 15 minutes
        
        # Initialize Activity Syncer (send aggregates to backend after ETL)
        self.activity_syncer = ActivitySyncer(self.db)
        self.sync_interval_sec = self.config.get("sync.interval_sec", 900)  # 15 min default (mirrors ETL)
        self.last_sync_time = time.time()  # Initialize to now; first sync waits 15 minutes

        # Initialize Phase 5: Nudge Scheduler (wellbeing & motivation engine)
        nudge_cfg = self.config.get("nudge", {}) or {}
        self.nudge_scheduler: NudgeScheduler | None = None
        if nudge_cfg.get("enabled", True):
            llm_cfg  = nudge_cfg.get("llm", {}) or {}
            notif_cfg = nudge_cfg.get("notification", {}) or {}
            self.nudge_scheduler = NudgeScheduler(
                db_path=self.db_path,
                interval_min=int(nudge_cfg.get("interval_min", 30)),
                suppression_min=int(nudge_cfg.get("suppression_min", 25)),
                window_min=int(nudge_cfg.get("window_min", 30)),
                min_active_min=float(nudge_cfg.get("min_active_min", 15.0)),
                idle_break_threshold_min=int(nudge_cfg.get("idle_break_threshold_min", 5)),
                late_night_hour=int(nudge_cfg.get("late_night_hour", 21)),
                flow_streak_min=float(nudge_cfg.get("flow_streak_min", 45.0)),
                break_reminder_min=float(nudge_cfg.get("break_reminder_min", 90.0)),
                distraction_threshold=float(nudge_cfg.get("distraction_threshold", 0.30)),
                meeting_suppression_threshold=float(nudge_cfg.get("meeting_suppression_threshold", 0.80)),
                llm_enabled=bool(llm_cfg.get("enabled", True)),
                llm_timeout_sec=float(llm_cfg.get("timeout_sec", 4.0)),
                notification_enabled=bool(notif_cfg.get("enabled", True)),
                notification_display_sec=int(notif_cfg.get("display_sec", 7)),
                # User preferences override config defaults where applicable
                user_preferences=self._user_preferences,
            )
            self.logger.info("[Agent] NudgeScheduler initialized")
        
        # Session tracking
        self.current_session = None
        self.current_app = None
        
        # Sticky project tracking (shift-left sticky project logic from aggregation to collection)
        # When switching to generic app (browser, terminal), inherit last detected project if within TTL
        self.sticky_project_name = None  # Last detected project name
        self.sticky_last_seen = None     # When it was last seen (ISO format)
        self.sticky_ttl_sec = self.config.get("etl_pipeline.sticky_project_ttl_sec", 900)  # 15 min default
        
        # Recover sticky project from last session (survive agent restart)
        self._recover_sticky_project()

        # Event used for interruptible sleep and clean shutdown signalling
        self._stop_event = threading.Event()
        
        self.last_flush_time = time.time()

    def _setup_logging(self):
        """Initialize Python logging from config.yaml (if present)."""
        cfg = self.config.get("logging", {}) or {}
        level_name = str(cfg.get("level", "INFO")).upper()
        log_level = getattr(logging, level_name, logging.INFO)
        log_format = cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        log_file = cfg.get("file")

        handlers = [logging.StreamHandler()]
        if log_file:
            try:
                log_path = Path(str(log_file))
                log_path.parent.mkdir(parents=True, exist_ok=True)
                handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
            except Exception:
                # If file handler fails, continue with console logging.
                pass

        logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    def start(self):
        """Start the monitoring loop."""
        self.logger.info("[Agent] Starting desktop activity monitoring...")

        # Start global keyboard/mouse listeners once.
        self.metrics.start_listening()

        # Start nudge scheduler (background thread)
        if self.nudge_scheduler:
            self.nudge_scheduler.start()
        
        try:
            while True:
              try:
                # Get active window
                app_name, window_title, pid = get_active_window()
                
                # Handle app/window change (e.g., VS Code → Chrome)
                if app_name != self.current_app:
                    if self.current_session:
                        self._flush_session()
                    
                    if app_name:
                        self.current_session = ActivitySession(
                            app_name,
                            window_title,
                            pid,
                            idle_threshold_sec=self.idle_threshold_sec,
                            metrics=self.metrics,
                            config=self.config,
                        )
                        self.current_app = app_name
                
                # Handle file change within same app (e.g., Tab switch in VS Code)
                elif self.current_session and window_title:
                    if self.current_session.has_file_changed(window_title):
                        old_file = self.current_session.current_file
                        # Flush current file's session
                        self._flush_session()
                        # Start new file session (same app, different file)
                        self.current_session = ActivitySession(
                            app_name,
                            window_title,
                            pid,
                            idle_threshold_sec=self.idle_threshold_sec,
                            metrics=self.metrics,
                            config=self.config,
                        )
                        new_file = self.current_session.current_file
                        self.logger.info("[Tab Switch] %s -> %s", old_file, new_file)
                    else:
                        # Same file, just update window title
                        self.current_session.update_file_context(window_title)
                
                # Update activity in current session (for idle detection)
                if self.current_session:
                    # Get the actual last activity time from behavioral metrics
                    last_activity = self.current_session.metrics.get_last_activity_time()
                    self.current_session.idle_detector.update_activity(last_activity)
                
                # Periodic flush (even if app didn't change)
                if time.time() - self.last_flush_time >= self.flush_interval:
                    if self.current_session:
                        self._flush_session()
                        # Restart session if still on same app
                        if self.current_app:
                            self.current_session = ActivitySession(
                                self.current_app, window_title,
                                idle_threshold_sec=self.idle_threshold_sec,
                                metrics=self.metrics,
                                config=self.config,
                            )
                
                # Check idle and trigger LOC scanning (non-blocking — fires background thread)
                self._check_idle_and_scan_loc_async()
                
                # Run ETL pipeline and sync (non-blocking — fires background thread)
                self._run_etl_and_sync_async()

              except Exception:
                self.logger.exception("[Agent] Unexpected error in main loop iteration; continuing")

              # Interruptible sleep outside the inner try so it always runs,
              # and so KeyboardInterrupt is never swallowed by the inner handler.
              self._stop_event.wait(self.sample_interval)
        
        except KeyboardInterrupt:
            self.logger.info("[Agent] Shutdown signal received...")
            self._shutdown()

    def _recover_sticky_project(self):
        """Recover sticky project from database on agent startup.
        
        This allows the agent to survive restarts by querying the last activity log
        that has a non-NULL project_name. If it's within the TTL, we consider it
        the "active" project and seed it into memory.
        
        This implements the "shift-left" sticky project logic at collection time.
        """
        try:
            cursor = self.db.conn.execute(
                """
                SELECT project_name, end_time FROM raw_activity_logs 
                WHERE project_name IS NOT NULL 
                ORDER BY end_time DESC LIMIT 1
                """
            )
            row = cursor.fetchone()
            
            if row:
                project_name, end_time_str = row
                # Parse the end_time ISO format (stored as local time)
                end_dt = datetime.fromisoformat(end_time_str)
                elapsed_sec = (datetime.now() - end_dt).total_seconds()
                
                # If less than TTL, restore to sticky state
                if elapsed_sec <= self.sticky_ttl_sec:
                    self.sticky_project_name = project_name
                    self.sticky_last_seen = end_time_str
                    self.logger.info(
                        "[Agent] Recovered sticky project '%s' (%.0fs ago, TTL=%ss)",
                        project_name,
                        elapsed_sec,
                        self.sticky_ttl_sec,
                    )
                else:
                    self.logger.info(
                        "[Agent] Last project '%s' expired (%.0fs ago > TTL=%ss)",
                        project_name,
                        elapsed_sec,
                        self.sticky_ttl_sec,
                    )
        except Exception as e:
            self.logger.exception("[Agent] Could not recover sticky project")

    def _is_sticky_ttl_valid(self) -> bool:
        """Check if sticky project is still within TTL.
        
        Returns:
            bool: True if sticky_project_name exists and is within TTL window
        """
        if not self.sticky_last_seen or not self.sticky_project_name:
            return False
        
        try:
            last_seen_dt = datetime.fromisoformat(self.sticky_last_seen)
            elapsed_sec = (datetime.now() - last_seen_dt).total_seconds()
            return elapsed_sec <= self.sticky_ttl_sec
        except Exception:
            return False

    def _apply_sticky_project(self, activity_data: dict) -> dict:
        """Apply sticky project logic to activity data.
        
        Shift-left strategy: When collecting raw data, if a generic app (browser, terminal)
        has no project context, inherit the last-detected project if within 15-min TTL.
        
        This is implemented at collection time, not aggregation time, so the database
        is clean from the start.
        
        Args:
            activity_data: dict with activity data (may have project_name=None)
            
        Returns:
            dict: activity_data with potentially filled-in project_name
        """
        current_project = activity_data.get('project_name')
        current_app = activity_data.get('app_name')
        current_time = activity_data.get('end_time')
        
        # Case 1: Real project detected (IDE)
        # Update sticky state and return as-is
        if current_project:
            self.sticky_project_name = current_project
            self.sticky_last_seen = current_time
            # print(f"[Sticky] Updated to project: {current_project}")
            return activity_data
        
        # Case 2: No project detected (generic app like browser)
        # Check if we can use sticky project
        if self._is_sticky_ttl_valid():
            activity_data['project_name'] = self.sticky_project_name
            # print(f"[Sticky] Applied sticky project '{self.sticky_project_name}' to {current_app}")
        else:
            # TTL expired or no sticky project
            activity_data['project_name'] = None
            # print(f"[Sticky] No valid sticky project for {current_app}")
        
        return activity_data

    def _flush_session(self):
        """Flush current session to database.
        
        Applies sticky project logic at collection time (shift-left):
        If the session has no detected project (generic app), inherit last-detected
        project if within TTL (15 min default).
        """
        if not self.current_session:
            return
        
        try:
            self.current_session.end_session()
            activity_data = self.current_session.collect_data()
            
            # Shift-Left Sticky Project: Apply at collection time, not aggregation
            # This cleans the raw data immediately, no need for ETL aggregation logic
            activity_data = self._apply_sticky_project(activity_data)
            
            # Validate before insertion
            self.db.validate_activity_log(activity_data)
            
            # Insert into database
            log_id = self.db.insert_activity_log(activity_data)
            
            # Enhanced debug output with file info
            self.logger.info(
                "[DB] Inserted log #%s: %s (%ss) | File: %s | Project: %s | KPM:%.1f CPM:%.1f Deletions:%s Idle:%ss",
                log_id,
                activity_data.get("app_name"),
                activity_data.get("duration_sec"),
                activity_data.get("active_file"),
                activity_data.get("project_name"),
                float(activity_data.get("typing_intensity", 0.0)),
                float(activity_data.get("mouse_click_rate", 0.0)),
                activity_data.get("deletion_key_presses", 0),
                activity_data.get("idle_duration_sec"),
            )
            
            self.last_flush_time = time.time()
            self.current_session = None
        
        except Exception as e:
            self.logger.exception("[Error] Failed to flush session")

    def _check_idle_and_scan_loc_async(self):
        """Fire a background thread for LOC scanning if the interval has elapsed.

        The timestamp is updated before the thread starts to prevent double-firing
        if the previous scan is still running when the next tick arrives.
        """
        current_time = time.time()
        if current_time - self.last_loc_scan_time < self.loc_scan_interval_sec:
            return
        self.last_loc_scan_time = current_time
        threading.Thread(target=self._loc_scan_worker, daemon=True, name="LOCScan").start()

    def _loc_scan_worker(self):
        """Worker: scan LOC for all active projects (runs in its own thread)."""
        try:
            self.logger.info("[LOCScanner] Starting background LOC scan...")
            self.loc_scanner.scan_all_projects()
            self.logger.info("[LOCScanner] Completed LOC scan")
        except Exception:
            self.logger.exception("[Error] LOC scanning failed")

    def _run_etl_and_sync_async(self):
        """Fire a background thread for ETL + sync if the interval has elapsed.

        The timestamp is updated before the thread starts to prevent double-firing.
        """
        current_time = time.time()
        if current_time - self.last_etl_time < self.etl_interval_sec:
            return
        self.last_etl_time = current_time
        threading.Thread(target=self._etl_and_sync_worker, daemon=True, name="ETLSync").start()

    def _etl_and_sync_worker(self):
        """Worker: run ETL aggregation then sync pending data (runs in its own thread)."""
        try:
            self.logger.info("[ETL] Starting ETL pipeline...")
            self.etl_pipeline.run()
            self.logger.info("[ETL] Completed ETL aggregation")

            if self.db.has_pending_sync():
                self.logger.info("[Sync] Pending data detected; starting activity sync...")
                success = self.activity_syncer.sync_activity()
                if success:
                    self.logger.info("[Sync] Activity sync completed successfully")
                else:
                    self.logger.warning("[Sync] Activity sync failed; will retry next cycle")
            else:
                self.logger.debug("[Sync] No pending data to sync")
        except Exception:
            self.logger.exception("[Error] ETL/Sync cycle failed")

    def _shutdown(self):
        """Graceful shutdown."""
        self.logger.info("[Agent] Shutting down...")
        # Wake the main loop immediately so it exits without waiting for sleep to expire
        self._stop_event.set()
        
        # Stop background block evaluator
        self.block_evaluator.stop()

        # Stop nudge scheduler
        if self.nudge_scheduler:
            self.nudge_scheduler.stop()
        
        # Flush final session
        if self.current_session:
            self._flush_session()
        
        # Final ETL if needed
        try:
            self.logger.info("[Agent] Running final ETL before shutdown...")
            self.etl_pipeline.run()
            
            # Final sync if pending data exists
            if self.db.has_pending_sync():
                self.logger.info("[Agent] Syncing pending data before shutdown...")
                self.activity_syncer.sync_activity()
        except Exception as e:
            self.logger.warning(f"[Agent] Final ETL/sync failed: {e}")

        # Stop global listeners.
        self.metrics.stop_listening()
        
        # Close database
        self.db.close()
        self.logger.info("[Agent] Database closed")
        self.logger.info("[Agent] Stopped")


def main():
    """Main entry point."""
    agent = DesktopAgent()
    agent.start()


if __name__ == "__main__":
    main()

