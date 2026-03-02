"""Phase 4: Lines of Code (LOC) Scanner.

Scans project directories and counts lines of code by language.
Stores results in project_loc_snapshots table.

Can run:
1. Independently: LOCScanner(db).scan_project(project_name)
2. As background worker: LOCScanner(db).scan_all_projects()
3. On idle time: triggered when user is idle for N minutes
"""

from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
import logging


class LOCScanner:
    """Scans project directories for lines of code by language."""

    def __init__(self, db, config=None):
        """Initialize LOC scanner.
        
        Args:
            db: Database instance (from database/db.py)
            config: Config instance (from config/config.py) - REQUIRED
        """
        self.db = db
        self.logger = logging.getLogger(__name__)
        
        if not config:
            raise ValueError("LOCScanner requires config instance with loc_scanner settings")
        
        # Read configuration
        loc_cfg = config.get('loc_scanner', {})
        
        # File extensions → language mapping
        ext_map = loc_cfg.get('language_extensions', {})
        # Ensure keys have leading dots
        self.language_extensions = {
            (k if k.startswith('.') else f'.{k}'): v 
            for k, v in ext_map.items()
        }
        
        # Directories to skip during code scanning
        self.skip_dirs = set(loc_cfg.get('skip_directories', []))
    
    def _get_local_time(self) -> str:
        """Get current local time as formatted string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def scan_project(self, project_name):
        """Scan a single project by name and update project_loc_snapshots.
        
        Args:
            project_name: Project identifier
            
        Returns:
            Dict with language_name → lines_of_code, or None if project not found
        """
        # Get project from database
        project = self.db.get_project(project_name)
        if not project:
            self.logger.info("[LOCScanner] Project not found: %s", project_name)
            return None

        project_path = project["project_path"]
        if not project_path:
            self.logger.info("[LOCScanner] Project path is NULL: %s", project_name)
            return None

        # Scan the directory
        loc_by_language, file_count_by_language = self._scan_directory(project_path)

        # Store results in project_loc_snapshots
        now = self._get_local_time()

        if loc_by_language:
            with self.db.conn:
                for language_name, lines_of_code in loc_by_language.items():
                    file_count = file_count_by_language.get(language_name, 0)
                    self.db.conn.execute(
                        """
                        INSERT INTO project_loc_snapshots (project_name, language_name, lines_of_code, file_count, last_scanned_at, needs_sync)
                        VALUES (?, ?, ?, ?, ?, 1)
                        ON CONFLICT(project_name, language_name) DO UPDATE SET
                            lines_of_code = excluded.lines_of_code,
                            file_count = excluded.file_count,
                            last_scanned_at = excluded.last_scanned_at,
                            needs_sync = 1
                        """,
                        (project_name, language_name, lines_of_code, file_count, now),
                    )

            total_files = sum(file_count_by_language.values())
            self.logger.info(
                "[LOCScanner] %s: %s languages, %s LOC, %s files",
                project_name,
                len(loc_by_language),
                sum(loc_by_language.values()),
                total_files,
            )
            return loc_by_language
        else:
            self.logger.info("[LOCScanner] No source code found in %s", project_path)
            return loc_by_language

    def scan_all_projects(self):
        """Scan only projects that have been active since their last LOC scan.
        
        This optimizes scanning by skipping projects that haven't changed.
        Useful for background worker or periodic maintenance.
        """
        projects = self.db.get_active_projects_since_scan()

        if not projects:
            self.logger.info("[LOCScanner] No active projects to scan")
            return

        self.logger.info("[LOCScanner] Scanning %s active projects...", len(projects))
        scanned_count = 0

        for project in projects:
            project_name = project["project_name"]
            result = self.scan_project(project_name)
            if result is not None:
                scanned_count += 1

        self.logger.info(
            "[LOCScanner] Completed: %s/%s projects scanned",
            scanned_count,
            len(projects),
        )

    def _scan_directory(self, project_path):
        """Recursively scan a directory and count LOC + files by language.
        
        Args:
            project_path: Path to project root directory
            
        Returns:
            Tuple of (loc_by_language dict, file_count_by_language dict)
            Each: language_name → count
        """
        loc_by_language = defaultdict(int)
        file_count_by_language = defaultdict(int)
        project_path_obj = Path(project_path)

        if not project_path_obj.exists():
            self.logger.info("[LOCScanner] Path does not exist: %s", project_path)
            return dict(loc_by_language), dict(file_count_by_language)

        if not project_path_obj.is_dir():
            self.logger.info("[LOCScanner] Path is not a directory: %s", project_path)
            return dict(loc_by_language), dict(file_count_by_language)

        try:
            for file_path in project_path_obj.rglob("*"):
                # Skip hidden files and directories
                if any(part.startswith(".") for part in file_path.parts):
                    continue

                # Skip if parent is in skip list
                if self._should_skip_path(file_path):
                    continue

                # Count LOC if it's a known code file
                if file_path.is_file():
                    language = self._get_language(file_path)
                    if language:
                        loc = self._count_lines(file_path)
                        loc_by_language[language] += loc
                        file_count_by_language[language] += 1

        except (PermissionError, OSError) as e:
            self.logger.exception("[LOCScanner] Error scanning %s", project_path)

        return dict(loc_by_language), dict(file_count_by_language)

    def _should_skip_path(self, file_path):
        """Check if path contains directories to skip.
        
        Args:
            file_path: Path object to check
            
        Returns:
            True if should skip, False otherwise
        """
        for part in file_path.parts:
            if part in self.skip_dirs:
                return True
        return False

    def _get_language(self, file_path):
        """Get language for a file based on extension.
        
        Args:
            file_path: Path object
            
        Returns:
            Language name or None if unknown
        """
        suffix = file_path.suffix.lower()
        return self.language_extensions.get(suffix)

    def _count_lines(self, file_path):
        """Count lines of code in a file.
        
        Simple count: total lines in file.
        (Future: could exclude blank lines and comments)
        
        Args:
            file_path: Path object
            
        Returns:
            Number of lines, or 0 if error
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return sum(1 for _ in f)
        except Exception as e:
            self.logger.exception("[LOCScanner] Error reading %s", file_path)
            return 0
