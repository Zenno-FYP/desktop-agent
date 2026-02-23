"""
Zenno Desktop Agent - Entry Point (Phase 1: Core Activity Detection)
"""
import time
import sys
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config.config import Config
from storage.db import Database
from observer.app_focus import get_active_window
from observer.behavioral_metrics import BehavioralMetrics
from observer.idle_detector import IdleDetector
from observer.project_detector import ProjectDetector


class ActivitySession:
    """Track a single application/window session with all behavioral data."""

    def __init__(self, app_name: str, window_title: str):
        """Initialize new activity session.
        
        Args:
            app_name: Application name
            window_title: Window title
        """
        self.start_time = datetime.utcnow().isoformat()
        self.app_name = app_name
        self.window_title = window_title
        
        # Initialize behavioral tracking
        self.metrics = BehavioralMetrics()
        self.idle_detector = IdleDetector(idle_threshold_sec=5)
        self.project_detector = ProjectDetector()
        
        self.metrics.start_listening()
        
        print(f"[Session] Started: {app_name} | {window_title}")

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
        
        # Project and file detection
        project_name, active_file = self.project_detector.detect_project(
            self.app_name, self.window_title
        )
        detected_skills = self.project_detector.get_detected_skills(active_file)
        
        # Compile activity log entry
        activity_data = {
            'start_time': self.start_time,
            'end_time': end_time,
            'app_name': self.app_name,
            'window_title': self.window_title,
            'duration_sec': duration_sec,
            'project_name': project_name,
            'active_file': active_file,
            'detected_skills': detected_skills,
            'typing_intensity': metrics['typing_intensity'],
            'mouse_click_rate': metrics['mouse_click_rate'],
            'mouse_scroll_events': metrics['mouse_scroll_events'],
            'idle_duration_sec': idle_metrics['idle_duration_sec'],
            # Context state (Phase 2 will populate this with heuristics/ML)
            'context_state': None,
            'confidence_score': None,
        }
        
        return activity_data

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
        self.db_path = self.config.get("db.path", "./agent.db")
        
        # Initialize database
        self.db = Database(self.db_path)
        self.db.connect()
        self.db.create_tables()
        print(f"[Agent] Database initialized: {self.db_path}")
        
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
                app_name, window_title = get_active_window()
                
                # Handle app/window change
                if app_name != self.current_app:
                    if self.current_session:
                        self._flush_session()
                    
                    if app_name:
                        self.current_session = ActivitySession(app_name, window_title)
                        self.current_app = app_name
                
                # Update activity in current session (for idle detection)
                if self.current_session:
                    self.current_session.idle_detector.update_activity()
                
                # Periodic flush (even if app didn't change)
                if time.time() - self.last_flush_time >= self.flush_interval:
                    if self.current_session:
                        self._flush_session()
                        # Restart session if still on same app
                        if self.current_app:
                            self.current_session = ActivitySession(self.current_app, window_title)
                
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
            
            # Debug output
            print(f"[DB] Inserted log #{log_id}: {activity_data['app_name']} "
                  f"({activity_data['duration_sec']}s, "
                  f"KPM:{activity_data['typing_intensity']:.1f}, "
                  f"CPM:{activity_data['mouse_click_rate']:.1f}, "
                  f"Idle:{activity_data['idle_duration_sec']}s)")
            
            self.last_flush_time = time.time()
            self.current_session = None
        
        except Exception as e:
            print(f"[Error] Failed to flush session: {e}")
            import traceback
            traceback.print_exc()

    def _shutdown(self):
        """Graceful shutdown."""
        print("[Agent] Shutting down...")
        
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

