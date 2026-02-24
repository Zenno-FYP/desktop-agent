"""SQLite database layer for agent."""
import sqlite3
from pathlib import Path
from datetime import datetime


class Database:
    """SQLite connection and schema management."""

    def __init__(self, db_path: str):
        """Initialize database connection.
        
        Args:
            db_path: path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = None

    def connect(self):
        """Open connection and enable WAL mode."""
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,  # Allow use in different threads (safe with WAL mode)
            timeout=10.0  # Wait up to 10 seconds for locks
        )
        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Enable foreign keys
        self.conn.execute("PRAGMA foreign_keys=ON")
        return self.conn

    def close(self):
        """Close connection gracefully."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def create_tables(self):
        """Create raw_activity_logs table if it doesn't exist."""
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_activity_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,             -- When the user started looking at this window
                end_time TEXT NOT NULL,               -- When they switched away or the flusher ran
                app_name TEXT NOT NULL,
                window_title TEXT,
                duration_sec INTEGER NOT NULL,
                
                -- Extracted Context
                project_name TEXT, 
                project_path TEXT,
                active_file TEXT,
                detected_language TEXT,
                
                -- Behavioral Signals (Features for ML Retraining)
                typing_intensity REAL DEFAULT 0.0,    -- Keystrokes per minute (KPM)
                mouse_click_rate REAL DEFAULT 0.0,    -- Clicks per minute
                mouse_scroll_events INTEGER DEFAULT 0,-- Great for detecting "Reading Docs"
                idle_duration_sec INTEGER DEFAULT 0,  -- Exactly how much of 'duration_sec' was idle
                
                -- ML Output (Current Model's Guess)
                context_state TEXT,       -- "Focused", "Distracted", "Idle", "Reading"
                confidence_score REAL     -- e.g., 0.92
            )
        """)
        self.conn.commit()

    def start_session(self, app_name: str, window_title: str = "") -> int:
        """DEPRECATED: Use insert_activity_log instead."""
        raise NotImplementedError("Use insert_activity_log instead")

    def end_session(self, session_id: int) -> None:
        """DEPRECATED: Use insert_activity_log instead."""
        raise NotImplementedError("Use insert_activity_log instead")

    def insert_activity_log(self, activity_data: dict) -> int:
        """Insert an activity log entry into raw_activity_logs.
        
        Args:
            activity_data: dict with all required fields for the log entry
            
        Returns:
            log_id of inserted entry
        """
        # Validate required fields
        required_fields = ['start_time', 'end_time', 'app_name', 'duration_sec']
        for field in required_fields:
            if field not in activity_data:
                raise ValueError(f"Missing required field: {field}")
        
        # Set defaults for optional fields
        fields = {
            'start_time': activity_data['start_time'],
            'end_time': activity_data['end_time'],
            'app_name': activity_data['app_name'],
            'window_title': activity_data.get('window_title', ''),
            'duration_sec': activity_data['duration_sec'],
            'project_name': activity_data.get('project_name'),
            'project_path': activity_data.get('project_path'),
            'active_file': activity_data.get('active_file'),
            'detected_language': activity_data.get('detected_language'),
            'typing_intensity': activity_data.get('typing_intensity', 0.0),
            'mouse_click_rate': activity_data.get('mouse_click_rate', 0.0),
            'mouse_scroll_events': activity_data.get('mouse_scroll_events', 0),
            'idle_duration_sec': activity_data.get('idle_duration_sec', 0),
            'context_state': activity_data.get('context_state'),
            'confidence_score': activity_data.get('confidence_score'),
        }
        
        cursor = self.conn.execute(
            """
            INSERT INTO raw_activity_logs (
                start_time, end_time, app_name, window_title, duration_sec,
                project_name, project_path, active_file, detected_language,
                typing_intensity, mouse_click_rate, mouse_scroll_events, idle_duration_sec,
                context_state, confidence_score
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            tuple(fields.values())
        )
        self.conn.commit()
        return cursor.lastrowid

    def validate_activity_log(self, log_dict: dict) -> bool:
        """Validate activity log data before insertion.
        
        Args:
            log_dict: activity log dictionary
            
        Returns:
            True if valid, raises ValueError if invalid
        """
        # Duration must be positive
        if log_dict.get('duration_sec', 0) <= 0:
            raise ValueError("Duration must be positive")
        
        # Time ordering
        start = datetime.fromisoformat(log_dict['start_time'])
        end = datetime.fromisoformat(log_dict['end_time'])
        if start >= end:
            raise ValueError("Start time must be before end time")
        
        # Metrics cannot be negative
        if log_dict.get('typing_intensity', 0) < 0:
            raise ValueError("KPM cannot be negative")
        if log_dict.get('mouse_click_rate', 0) < 0:
            raise ValueError("CPM cannot be negative")
        
        # Idle cannot exceed duration
        if log_dict.get('idle_duration_sec', 0) > log_dict['duration_sec']:
            raise ValueError("Idle duration cannot exceed total duration")
        
        # Cap unrealistic values
        if log_dict.get('typing_intensity', 0) > 200:
            log_dict['typing_intensity'] = 200
        
        return True
    
    def query_logs(self, start_time: str, end_time: str, 
                   where_context_is_null: bool = False) -> list:
        """Query activity logs within a time range.
        
        Args:
            start_time: ISO format start time (e.g., "2026-02-24T14:05:00")
            end_time: ISO format end time
            where_context_is_null: If True, only return logs with context_state IS NULL
        
        Returns:
            List of log dictionaries from the query
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        query = """
            SELECT * FROM raw_activity_logs 
            WHERE start_time >= ? AND start_time < ?
        """
        params = [start_time, end_time]
        
        if where_context_is_null:
            query += " AND context_state IS NULL"
        
        query += " ORDER BY start_time ASC"
        
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert sqlite3.Row to dict using column names
        result = []
        for row in rows:
            result.append({
                'log_id': row[0],
                'start_time': row[1],
                'end_time': row[2],
                'app_name': row[3],
                'window_title': row[4],
                'duration_sec': row[5],
                'project_name': row[6],
                'project_path': row[7],
                'active_file': row[8],
                'detected_language': row[9],
                'typing_intensity': row[10],
                'mouse_click_rate': row[11],
                'mouse_scroll_events': row[12],
                'idle_duration_sec': row[13],
                'context_state': row[14],
                'confidence_score': row[15],
            })
        
        return result
    
    def update_logs_context(self, log_ids: list, context_state: str, 
                           confidence_score: float) -> int:
        """Retroactively tag logs with context state and confidence.
        
        Used by BlockEvaluator to batch-update all logs in a 5-minute block
        with the aggregated context evaluation.
        
        Args:
            log_ids: List of log_id integers to update
            context_state: Context state to set (e.g., "Focused")
            confidence_score: Confidence score to set (0.0-1.0)
        
        Returns:
            Number of rows updated
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        if not log_ids:
            return 0
        
        # Build parameterized query (avoid SQL injection)
        placeholders = ','.join('?' * len(log_ids))
        query = f"""
            UPDATE raw_activity_logs 
            SET context_state = ?, confidence_score = ?
            WHERE log_id IN ({placeholders})
        """
        
        params = [context_state, confidence_score] + log_ids
        cursor = self.conn.execute(query, params)
        self.conn.commit()
        
        return cursor.rowcount
