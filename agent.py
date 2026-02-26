"""
Zenno Desktop Agent - Entry Point (Phase 1: Core Activity Detection)
"""
import time
from datetime import datetime
from typing import Optional

from config.config import Config
from database.db import Database
from monitor.app_focus import get_active_window
from monitor.behavioral_metrics import BehavioralMetrics
from monitor.idle_detector import IdleDetector
from monitor.project_detector import ProjectDetector
from analyze.context_detector import ContextDetector
from analyze.block_evaluator import BlockEvaluator


class ActivitySession:
    """Track a single application/window session with all behavioral data."""

    def __init__(self, app_name: str, window_title: str, pid: Optional[int] = None, 
                 idle_threshold_sec: int = 10, click_debounce_ms: int = 50, config=None):
        """Initialize new activity session.
        
        Args:
            app_name: Application name
            window_title: Window title
            pid: Process ID (optional)
            idle_threshold_sec: Seconds of inactivity before marking as idle (default 10)
            click_debounce_ms: Ignore clicks closer than this (ms) - default 50
        """
        self.start_time = datetime.utcnow().isoformat()
        self.app_name = app_name
        self.window_title = window_title
        self.pid = pid
        
        # Initialize behavioral tracking with config values
        self.metrics = BehavioralMetrics(click_debounce_ms=click_debounce_ms)
        self.idle_detector = IdleDetector(idle_threshold_sec=idle_threshold_sec)
        self.project_detector = ProjectDetector(config=config)
        
        # Track file changes (for tab switching)
        project, file = self.project_detector.detect_project(app_name, window_title)
        self.current_file = file
        self.current_project = project
        
        self.metrics.start_listening()
        
        print(f"[Session] Started: {app_name} | {window_title} | File: {file}")

    def collect_data(self) -> dict:
        """Collect all behavioral data for this session.
        
        Returns:
            dict with complete session data ready for database insertion
        """
        end_time = datetime.utcnow().isoformat()
        start_dt = datetime.fromisoformat(self.start_time)
        end_dt = datetime.fromisoformat(end_time)
        duration_sec = int((end_dt - start_dt).total_seconds())
        
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
            'mouse_scroll_events': metrics['mouse_scroll_events'],
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
        """End the session and stop listening for inputs."""
        self.metrics.stop_listening()
        print(f"[Session] Ended: {self.app_name}")


class DesktopAgent:
    """Main agent for activity detection and logging."""

    def __init__(self, config_path: str = None):
        """Initialize the desktop agent.
        
        Args:
            config_path: Optional path to config file
        """
        self.config = Config(config_path) if config_path else Config()
        self.sample_interval = self.config.get("sample_interval_sec", 2)
        self.flush_interval = self.config.get("flush_interval_sec", 300)  # 5 min default
        self.idle_threshold_sec = self.config.get("idle_threshold_sec", 10)
        self.click_debounce_ms = self.config.get("behavioral_metrics.click_debounce_ms", 50)
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
        )
        self.db.connect()
        self.db.create_tables()
        print(f"[Agent] Database initialized: {self.db_path}")
        
        # Initialize Phase 2: Context detection via 5-minute block evaluator
        self.context_detector = ContextDetector(self.config)
        block_duration_sec = self.config.get("block_duration_sec", 300)
        self.block_evaluator = BlockEvaluator(self.db, self.context_detector, config=self.config, block_duration_sec=block_duration_sec)
        self.block_evaluator.start()
        
        # Session tracking
        self.current_session = None
        self.current_app = None
        
        self.last_flush_time = time.time()

    def start(self):
        """Start the monitoring loop."""
        print("[Agent] Starting desktop activity monitoring...")
        
        try:
            while True:
                # Get active window
                app_name, window_title, pid = get_active_window()
                
                # Handle app/window change (e.g., VS Code → Chrome)
                if app_name != self.current_app:
                    if self.current_session:
                        self._flush_session()
                    
                    if app_name:
                        self.current_session = ActivitySession(
                            app_name, window_title, pid, 
                            self.idle_threshold_sec, self.click_debounce_ms, config=self.config
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
                            app_name, window_title, pid,
                            self.idle_threshold_sec, self.click_debounce_ms, config=self.config
                        )
                        new_file = self.current_session.current_file
                        print(f"[Tab Switch] {old_file} -> {new_file}")
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
                                click_debounce_ms=self.click_debounce_ms,
                                config=self.config,
                            )
                
                time.sleep(self.sample_interval)
        
        except KeyboardInterrupt:
            print("\n[Agent] Shutdown signal received...")
            self._shutdown()

    def _flush_session(self):
        """Flush current session to database."""
        if not self.current_session:
            return
        
        try:
            self.current_session.end_session()
            activity_data = self.current_session.collect_data()
            
            # Validate before insertion
            self.db.validate_activity_log(activity_data)
            
            # Insert into database
            log_id = self.db.insert_activity_log(activity_data)
            
            # Enhanced debug output with file info
            print(f"[DB] Inserted log #{log_id}: {activity_data['app_name']} "
                  f"({activity_data['duration_sec']}s) | File: {activity_data['active_file']} | "
                  f"KPM:{activity_data['typing_intensity']:.1f} CPM:{activity_data['mouse_click_rate']:.1f} "
                  f"Scrolls:{activity_data['mouse_scroll_events']} Idle:{activity_data['idle_duration_sec']}s")
            
            self.last_flush_time = time.time()
            self.current_session = None
        
        except Exception as e:
            print(f"[Error] Failed to flush session: {e}")
            import traceback
            traceback.print_exc()

    def _shutdown(self):
        """Graceful shutdown."""
        print("[Agent] Shutting down...")
        
        # Stop background block evaluator
        self.block_evaluator.stop()
        
        # Flush final session
        if self.current_session:
            self._flush_session()
        
        # Close database
        self.db.close()
        print("[Agent] Database closed")
        print("[Agent] Stopped")


def main():
    """Main entry point."""
    agent = DesktopAgent()
    agent.start()


if __name__ == "__main__":
    main()

