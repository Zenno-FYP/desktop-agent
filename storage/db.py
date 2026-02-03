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
        self.conn = sqlite3.connect(str(self.db_path))
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
        """Create app_sessions table if it doesn't exist."""
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS app_sessions (
                id INTEGER PRIMARY KEY,
                app_name TEXT NOT NULL,
                window_title TEXT,
                start_time TEXT NOT NULL,
                end_time TEXT,
                duration_sec INTEGER
            )
        """)
        self.conn.commit()

    def start_session(self, app_name: str, window_title: str = "") -> int:
        """Start a new app session.
        
        Args:
            app_name: process/app name
            window_title: window title (optional)
            
        Returns:
            session id
        """
        cursor = self.conn.execute(
            """
            INSERT INTO app_sessions (app_name, window_title, start_time)
            VALUES (?, ?, ?)
            """,
            (app_name, window_title, datetime.utcnow().isoformat())
        )
        self.conn.commit()
        return cursor.lastrowid

    def end_session(self, session_id: int) -> None:
        """End a session and calculate duration.
        
        Args:
            session_id: session id to close
        """
        end_time = datetime.utcnow().isoformat()

        # Get start time to calculate duration
        row = self.conn.execute(
            "SELECT start_time FROM app_sessions WHERE id = ?",
            (session_id,)
        ).fetchone()

        if row:
            start = datetime.fromisoformat(row[0])
            end = datetime.fromisoformat(end_time)
            duration_sec = int((end - start).total_seconds())

            self.conn.execute(
                """
                UPDATE app_sessions
                SET end_time = ?, duration_sec = ?
                WHERE id = ?
                """,
                (end_time, duration_sec, session_id)
            )
            self.conn.commit()
