"""
Zenno Desktop Agent - Entry Point
"""
import time
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from config.config import Config
from storage.db import Database
from observer.app_focus import get_active_window


def main():
    print("Zenno Agent Started")
    
    # Load config
    config = Config()
    sample_interval = config.get("sample_interval_sec", 2)
    db_path = config.get("db.path", "./agent.db")
    
    # Initialize database
    db = Database(db_path)
    db.connect()
    db.create_tables()
    print(f"Database initialized: {db_path}")
    
    # Track current session
    current_session_id = None
    current_app = None
    
    try:
        while True:
            # Get active window
            app_name, window_title = get_active_window()
            
            # If app changed, close previous session and start new one
            if app_name != current_app:
                if current_session_id:
                    db.end_session(current_session_id)
                    print(f"Closed session for: {current_app}")
                
                if app_name:
                    current_session_id = db.start_session(app_name, window_title)
                    current_app = app_name
                    print(f"Started session for: {app_name} - {window_title}")
            
            time.sleep(sample_interval)
            
    except KeyboardInterrupt:
        print("\nShutting down...")
        if current_session_id:
            db.end_session(current_session_id)
            print(f"Closed final session for: {current_app}")
        db.close()
        print("Agent stopped")


if __name__ == "__main__":
    main()
