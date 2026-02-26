"""SQLite database layer for agent."""
import sqlite3
from pathlib import Path
from datetime import datetime


class Database:
    """SQLite connection and schema management."""

    def __init__(
        self,
        db_path: str,
        *,
        check_same_thread: bool = False,
        timeout: float = 10.0,
        journal_mode: str = "WAL",
    ):
        """Initialize database connection.
        
        Args:
            db_path: path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self.check_same_thread = bool(check_same_thread)
        self.timeout = float(timeout)
        self.journal_mode = journal_mode

    @staticmethod
    def _sanitize_journal_mode(journal_mode: str) -> str:
        """Return a safe SQLite journal_mode value."""
        if not journal_mode:
            return "WAL"
        candidate = str(journal_mode).strip().upper()
        allowed = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY", "OFF"}
        return candidate if candidate in allowed else "WAL"

    def connect(self):
        """Open connection and enable WAL mode."""
        self.conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=self.check_same_thread,
            timeout=self.timeout,
        )

        journal_mode = self._sanitize_journal_mode(self.journal_mode)
        self.conn.execute(f"PRAGMA journal_mode={journal_mode}")
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
                confidence_score REAL,    -- e.g., 0.92
                
                -- Phase 3B: ESM Verification (Ground-Truth User Feedback)
                manually_verified_label TEXT NULL,    -- User's corrected answer (NULL = unverified)
                verified_at TIMESTAMP NULL,           -- When user verified this entry
                
                -- Phase 4: Aggregation Tracking
                is_aggregated INTEGER NOT NULL DEFAULT 0,      -- 0 = pending, 1 = processed
                aggregated_at TEXT NULL,                       -- UTC ISO when aggregated
                aggregation_version INTEGER NOT NULL DEFAULT 1  -- For future re-aggregation
            )
        """)
        self.conn.commit()
        
        # Create indexes for Phase 4 aggregation queries (optimized for end_time)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_agg_pending ON raw_activity_logs(is_aggregated, end_time)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_raw_end_time ON raw_activity_logs(end_time)"
        )
        self.conn.commit()
        
        # Phase 4: Create projects table (Metadata Hub)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                project_name TEXT PRIMARY KEY,
                project_path TEXT,                 -- local-only (never synced)
                first_seen_at TEXT NOT NULL,       -- UTC ISO
                last_active_at TEXT NOT NULL,      -- UTC ISO
                needs_sync INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_needs_sync ON projects(needs_sync)"
        )
        self.conn.commit()
        
        # Initialize special __unassigned__ project (for distracted/unattributed time tracking)
        # This allows daily_project_context to record distracted sessions without violating FK constraints
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        self.conn.execute(
            """
            INSERT OR IGNORE INTO projects (project_name, project_path, first_seen_at, last_active_at, needs_sync)
            VALUES (?, NULL, ?, ?, 0)
            """,
            ("__unassigned__", now, now)
        )
        self.conn.commit()
        
        # Phase 4: Create daily_project_languages table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_project_languages (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                language_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, language_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dpl_needs_sync ON daily_project_languages(needs_sync)"
        )
        self.conn.commit()
        
        # Phase 4: Create daily_project_apps table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_project_apps (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                app_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, app_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dpa_needs_sync ON daily_project_apps(needs_sync)"
        )
        self.conn.commit()
        
        # Phase 4: Create daily_project_skills table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_project_skills (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, skill_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dps_needs_sync ON daily_project_skills(needs_sync)"
        )
        self.conn.commit()
        
        # Phase 4: Create daily_project_context table
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_project_context (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                context_state TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, context_state),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dpc_needs_sync ON daily_project_context(needs_sync)"
        )
        self.conn.commit()

        # Phase 4: Create daily_project_behavior table (Physical effort metrics)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_project_behavior (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                total_keystrokes INTEGER NOT NULL DEFAULT 0,
                total_mouse_clicks INTEGER NOT NULL DEFAULT 0,
                total_scroll_events INTEGER NOT NULL DEFAULT 0,
                total_idle_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dpb_needs_sync ON daily_project_behavior(needs_sync)"
        )
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
                   where_context_is_null: bool = False, query_by_end_time: bool = True) -> list:
        """Query activity logs within a time range.
        
        Args:
            start_time: ISO format start time (e.g., "2026-02-24T14:05:00")
            end_time: ISO format end time
            where_context_is_null: If True, only return logs with context_state IS NULL
            query_by_end_time: If True (default), query using end_time (catches long sessions).
                              If False, query using start_time (legacy behavior).
        
        Returns:
            List of log dictionaries from the query
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        # Phase 2 Hardening: Query by end_time to catch sessions that start before the block
        # but end within it (prevents "never-tagged" long sessions)
        if query_by_end_time:
            query = """
                SELECT * FROM raw_activity_logs 
                WHERE end_time >= ? AND end_time < ?
            """
        else:
            # Legacy: query by start_time
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

    def update_log_verification(self, log_id: int, verified_label: str) -> bool:
        """Record user's manual verification for a log entry (ESM popup response).
        
        Args:
            log_id: ID of the log to update
            verified_label: The verified context state ("Focused", "Reading", "Distracted", "Idle")
        
        Returns:
            True if successful, False otherwise
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        try:
            cursor = self.conn.execute(
                """
                UPDATE raw_activity_logs
                SET manually_verified_label = ?, verified_at = ?
                WHERE log_id = ?
                """,
                (verified_label, datetime.now().isoformat(), log_id)
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            print(f"[ESM] Database error updating verification: {e}")
            return False

    def query_verified_logs(self, limit: int = 100) -> list:
        """Query verified logs for model retraining (manually verified entries).
        
        Returns only logs where manually_verified_label IS NOT NULL,
        ordered by most recent first.
        
        Args:
            limit: Maximum number of logs to return
        
        Returns:
            List of verified log dictionaries
        """
        if not self.conn:
            raise RuntimeError("Database not connected. Call connect() first.")
        
        cursor = self.conn.execute(
            """
            SELECT * FROM raw_activity_logs
            WHERE manually_verified_label IS NOT NULL
            ORDER BY verified_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = cursor.fetchall()
        
        # Convert to dict (matching all columns including verification fields)
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
                'manually_verified_label': row[16],
                'verified_at': row[17],
            })
        
        return result

    def upsert_project(self, project_name, project_path):
        """Insert new project or update last_active_at if exists.
        
        Args:
            project_name: Unique project identifier (PRIMARY KEY)
            project_path: Local filesystem path to project
        """
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        with self.conn:
            self.conn.execute('''
                INSERT INTO projects (project_name, project_path, first_seen_at, last_active_at, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(project_name) DO UPDATE SET
                    last_active_at = ?,
                    needs_sync = 1
            ''', (project_name, project_path, now, now, now))

    def update_project_last_active(self, project_name, timestamp=None):
        """Update last_active_at for a project.
        
        Args:
            project_name: Project identifier
            timestamp: UTC ISO string (default: now)
        """
        ts = timestamp or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        
        with self.conn:
            self.conn.execute('''
                UPDATE projects
                SET last_active_at = ?, needs_sync = 1
                WHERE project_name = ?
            ''', (ts, project_name))

    def get_project(self, project_name):
        """Get single project by name.
        
        Args:
            project_name: Project identifier
            
        Returns:
            Dict with keys: project_name, project_path, first_seen_at, last_active_at, needs_sync
            or None if not found
        """
        cursor = self.conn.execute('''
            SELECT project_name, project_path, first_seen_at, last_active_at, needs_sync
            FROM projects
            WHERE project_name = ?
        ''', (project_name,))
        
        row = cursor.fetchone()
        if not row:
            return None
        
        return {
            'project_name': row[0],
            'project_path': row[1],
            'first_seen_at': row[2],
            'last_active_at': row[3],
            'needs_sync': row[4],
        }

    def get_all_projects(self, needs_sync=None):
        """Get all projects, optionally filtered by sync status.
        
        Args:
            needs_sync: If True, return only projects needing sync.
                       If False, return only synced projects.
                       If None (default), return all projects.
            
        Returns:
            List of project dicts with keys: project_name, project_path, first_seen_at, last_active_at, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT project_name, project_path, first_seen_at, last_active_at, needs_sync
                FROM projects
                ORDER BY last_active_at DESC
            ''')
        else:
            cursor = self.conn.execute('''
                SELECT project_name, project_path, first_seen_at, last_active_at, needs_sync
                FROM projects
                WHERE needs_sync = ?
                ORDER BY last_active_at DESC
            ''', (1 if needs_sync else 0,))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'project_name': row[0],
                'project_path': row[1],
                'first_seen_at': row[2],
                'last_active_at': row[3],
                'needs_sync': row[4],
            })
        
        return result

    # ==================== DAILY PROJECT LANGUAGES ====================

    def get_daily_languages_by_date(self, date, needs_sync=None):
        """Get all languages for a given date, optionally filtered by sync status.
        
        Args:
            date: YYYY-MM-DD (local date)
            needs_sync: If True/False, filter by sync status. If None, return all.
            
        Returns:
            List of dicts with keys: date, project_name, language_name, duration_sec, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT date, project_name, language_name, duration_sec, needs_sync
                FROM daily_project_languages
                WHERE date = ?
                ORDER BY project_name, language_name
            ''', (date,))
        else:
            cursor = self.conn.execute('''
                SELECT date, project_name, language_name, duration_sec, needs_sync
                FROM daily_project_languages
                WHERE date = ? AND needs_sync = ?
                ORDER BY project_name, language_name
            ''', (date, 1 if needs_sync else 0))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'language_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def get_daily_languages_pending_sync(self):
        """Get all language rows pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: date, project_name, language_name, duration_sec, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT date, project_name, language_name, duration_sec, needs_sync
            FROM daily_project_languages
            WHERE needs_sync = 1
            ORDER BY date DESC, project_name, language_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'language_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def mark_daily_languages_synced(self, date, project_name=None):
        """Mark language rows as synced (needs_sync = 0).
        
        Args:
            date: YYYY-MM-DD (local date) to mark synced
            project_name: Optional project filter. If None, marks all rows for that date.
        """
        if project_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_languages
                    SET needs_sync = 0
                    WHERE date = ?
                ''', (date,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_languages
                    SET needs_sync = 0
                    WHERE date = ? AND project_name = ?
                ''', (date, project_name))

    # ==================== DAILY PROJECT APPS ====================

    def get_daily_apps_by_date(self, date, needs_sync=None):
        """Get all apps for a given date, optionally filtered by sync status.
        
        Args:
            date: YYYY-MM-DD (local date)
            needs_sync: If True/False, filter by sync status. If None, return all.
            
        Returns:
            List of dicts with keys: date, project_name, app_name, duration_sec, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT date, project_name, app_name, duration_sec, needs_sync
                FROM daily_project_apps
                WHERE date = ?
                ORDER BY project_name, app_name
            ''', (date,))
        else:
            cursor = self.conn.execute('''
                SELECT date, project_name, app_name, duration_sec, needs_sync
                FROM daily_project_apps
                WHERE date = ? AND needs_sync = ?
                ORDER BY project_name, app_name
            ''', (date, 1 if needs_sync else 0))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'app_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def get_daily_apps_pending_sync(self):
        """Get all app rows pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: date, project_name, app_name, duration_sec, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT date, project_name, app_name, duration_sec, needs_sync
            FROM daily_project_apps
            WHERE needs_sync = 1
            ORDER BY date DESC, project_name, app_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'app_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def mark_daily_apps_synced(self, date, project_name=None):
        """Mark app rows as synced (needs_sync = 0).
        
        Args:
            date: YYYY-MM-DD (local date) to mark synced
            project_name: Optional project filter. If None, marks all rows for that date.
        """
        if project_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_apps
                    SET needs_sync = 0
                    WHERE date = ?
                ''', (date,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_apps
                    SET needs_sync = 0
                    WHERE date = ? AND project_name = ?
                ''', (date, project_name))

    # ==================== DAILY PROJECT SKILLS ====================

    def get_daily_skills_by_date(self, date, needs_sync=None):
        """Get all skills for a given date, optionally filtered by sync status.
        
        Args:
            date: YYYY-MM-DD (local date)
            needs_sync: If True/False, filter by sync status. If None, return all.
            
        Returns:
            List of dicts with keys: date, project_name, skill_name, duration_sec, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT date, project_name, skill_name, duration_sec, needs_sync
                FROM daily_project_skills
                WHERE date = ?
                ORDER BY project_name, skill_name
            ''', (date,))
        else:
            cursor = self.conn.execute('''
                SELECT date, project_name, skill_name, duration_sec, needs_sync
                FROM daily_project_skills
                WHERE date = ? AND needs_sync = ?
                ORDER BY project_name, skill_name
            ''', (date, 1 if needs_sync else 0))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'skill_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def get_daily_skills_pending_sync(self):
        """Get all skill rows pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: date, project_name, skill_name, duration_sec, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT date, project_name, skill_name, duration_sec, needs_sync
            FROM daily_project_skills
            WHERE needs_sync = 1
            ORDER BY date DESC, project_name, skill_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'skill_name': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def mark_daily_skills_synced(self, date, project_name=None):
        """Mark skill rows as synced (needs_sync = 0).
        
        Args:
            date: YYYY-MM-DD (local date) to mark synced
            project_name: Optional project filter. If None, marks all rows for that date.
        """
        if project_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_skills
                    SET needs_sync = 0
                    WHERE date = ?
                ''', (date,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_skills
                    SET needs_sync = 0
                    WHERE date = ? AND project_name = ?
                ''', (date, project_name))

    # ==================== DAILY PROJECT CONTEXT ====================

    def get_daily_context_by_date(self, date, needs_sync=None):
        """Get all context states for a given date, optionally filtered by sync status.
        
        Args:
            date: YYYY-MM-DD (local date)
            needs_sync: If True/False, filter by sync status. If None, return all.
            
        Returns:
            List of dicts with keys: date, project_name, context_state, duration_sec, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT date, project_name, context_state, duration_sec, needs_sync
                FROM daily_project_context
                WHERE date = ?
                ORDER BY project_name, context_state
            ''', (date,))
        else:
            cursor = self.conn.execute('''
                SELECT date, project_name, context_state, duration_sec, needs_sync
                FROM daily_project_context
                WHERE date = ? AND needs_sync = ?
                ORDER BY project_name, context_state
            ''', (date, 1 if needs_sync else 0))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'context_state': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def get_daily_context_pending_sync(self):
        """Get all context rows pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: date, project_name, context_state, duration_sec, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT date, project_name, context_state, duration_sec, needs_sync
            FROM daily_project_context
            WHERE needs_sync = 1
            ORDER BY date DESC, project_name, context_state
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'context_state': row[2],
                'duration_sec': row[3],
                'needs_sync': row[4],
            })
        return result

    def mark_daily_context_synced(self, date, project_name=None):
        """Mark context rows as synced (needs_sync = 0).
        
        Args:
            date: YYYY-MM-DD (local date) to mark synced
            project_name: Optional project filter. If None, marks all rows for that date.
        """
        if project_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_context
                    SET needs_sync = 0
                    WHERE date = ?
                ''', (date,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_context
                    SET needs_sync = 0
                    WHERE date = ? AND project_name = ?
                ''', (date, project_name))

    # ==================== DAILY PROJECT BEHAVIOR ====================

    def get_daily_behavior_by_date(self, date, needs_sync=None):
        """Get all behavior metrics for a given date, optionally filtered by sync status.
        
        Args:
            date: YYYY-MM-DD (local date)
            needs_sync: If True/False, filter by sync status. If None, return all.
            
        Returns:
            List of dicts with keys: date, project_name, total_keystrokes, total_mouse_clicks,
                                     total_scroll_events, total_idle_sec, needs_sync
        """
        if needs_sync is None:
            cursor = self.conn.execute('''
                SELECT date, project_name, total_keystrokes, total_mouse_clicks, 
                       total_scroll_events, total_idle_sec, needs_sync
                FROM daily_project_behavior
                WHERE date = ?
                ORDER BY project_name
            ''', (date,))
        else:
            cursor = self.conn.execute('''
                SELECT date, project_name, total_keystrokes, total_mouse_clicks, 
                       total_scroll_events, total_idle_sec, needs_sync
                FROM daily_project_behavior
                WHERE date = ? AND needs_sync = ?
                ORDER BY project_name
            ''', (date, 1 if needs_sync else 0))
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'total_keystrokes': row[2],
                'total_mouse_clicks': row[3],
                'total_scroll_events': row[4],
                'total_idle_sec': row[5],
                'needs_sync': row[6],
            })
        return result

    def get_daily_behavior_pending_sync(self):
        """Get all behavior rows pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: date, project_name, total_keystrokes, total_mouse_clicks,
                                     total_scroll_events, total_idle_sec, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT date, project_name, total_keystrokes, total_mouse_clicks, 
                   total_scroll_events, total_idle_sec, needs_sync
            FROM daily_project_behavior
            WHERE needs_sync = 1
            ORDER BY date DESC, project_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'date': row[0],
                'project_name': row[1],
                'total_keystrokes': row[2],
                'total_mouse_clicks': row[3],
                'total_scroll_events': row[4],
                'total_idle_sec': row[5],
                'needs_sync': row[6],
            })
        return result

    def mark_daily_behavior_synced(self, date, project_name=None):
        """Mark behavior rows as synced (needs_sync = 0).
        
        Args:
            date: YYYY-MM-DD (local date) to mark synced
            project_name: Optional project filter. If None, marks all rows for that date.
        """
        if project_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_behavior
                    SET needs_sync = 0
                    WHERE date = ?
                ''', (date,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE daily_project_behavior
                    SET needs_sync = 0
                    WHERE date = ? AND project_name = ?
                ''', (date, project_name))

    # ==================== PROJECT LOC SNAPSHOTS ====================

    def get_project_loc(self, project_name, language_name=None):
        """Get LOC snapshot(s) for a project.
        
        Args:
            project_name: Project identifier
            language_name: Optional language filter. If None, return all languages for project.
            
        Returns:
            If language_name specified: dict with keys project_name, language_name, lines_of_code, last_scanned_at, needs_sync
            If language_name is None: list of such dicts
        """
        if language_name is None:
            # Get all languages for project
            cursor = self.conn.execute('''
                SELECT project_name, language_name, lines_of_code, last_scanned_at, needs_sync
                FROM project_loc_snapshots
                WHERE project_name = ?
                ORDER BY language_name
            ''', (project_name,))
            
            result = []
            for row in cursor.fetchall():
                result.append({
                    'project_name': row[0],
                    'language_name': row[1],
                    'lines_of_code': row[2],
                    'last_scanned_at': row[3],
                    'needs_sync': row[4],
                })
            return result
        else:
            # Get specific language
            cursor = self.conn.execute('''
                SELECT project_name, language_name, lines_of_code, last_scanned_at, needs_sync
                FROM project_loc_snapshots
                WHERE project_name = ? AND language_name = ?
            ''', (project_name, language_name))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            return {
                'project_name': row[0],
                'language_name': row[1],
                'lines_of_code': row[2],
                'last_scanned_at': row[3],
                'needs_sync': row[4],
            }

    def get_all_loc_snapshots(self):
        """Get all LOC snapshots across all projects and languages.
        
        Useful for dashboard or bulk operations.
        
        Returns:
            List of dicts with keys: project_name, language_name, lines_of_code, last_scanned_at, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT project_name, language_name, lines_of_code, last_scanned_at, needs_sync
            FROM project_loc_snapshots
            ORDER BY project_name, language_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'project_name': row[0],
                'language_name': row[1],
                'lines_of_code': row[2],
                'last_scanned_at': row[3],
                'needs_sync': row[4],
            })
        return result

    def get_loc_snapshots_pending_sync(self):
        """Get all LOC snapshots pending cloud sync (needs_sync = 1).
        
        Returns:
            List of dicts with keys: project_name, language_name, lines_of_code, last_scanned_at, needs_sync
        """
        cursor = self.conn.execute('''
            SELECT project_name, language_name, lines_of_code, last_scanned_at, needs_sync
            FROM project_loc_snapshots
            WHERE needs_sync = 1
            ORDER BY project_name, language_name
        ''')
        
        result = []
        for row in cursor.fetchall():
            result.append({
                'project_name': row[0],
                'language_name': row[1],
                'lines_of_code': row[2],
                'last_scanned_at': row[3],
                'needs_sync': row[4],
            })
        return result

    def mark_loc_synced(self, project_name, language_name=None):
        """Mark LOC snapshot(s) as synced (needs_sync = 0).
        
        Args:
            project_name: Project identifier
            language_name: Optional language filter. If None, marks all languages for project.
        """
        if language_name is None:
            with self.conn:
                self.conn.execute('''
                    UPDATE project_loc_snapshots
                    SET needs_sync = 0
                    WHERE project_name = ?
                ''', (project_name,))
        else:
            with self.conn:
                self.conn.execute('''
                    UPDATE project_loc_snapshots
                    SET needs_sync = 0
                    WHERE project_name = ? AND language_name = ?
                ''', (project_name, language_name))
