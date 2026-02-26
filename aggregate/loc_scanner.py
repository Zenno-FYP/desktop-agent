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
from datetime import datetime


class LOCScanner:
    """Scans project directories for lines of code by language."""

    # File extensions → language mapping
    LANGUAGE_EXTENSIONS = {
        ".py": "Python",
        ".js": "JavaScript",
        ".ts": "TypeScript",
        ".jsx": "JavaScript",
        ".tsx": "TypeScript",
        ".java": "Java",
        ".cpp": "C++",
        ".cc": "C++",
        ".cxx": "C++",
        ".c": "C",
        ".h": "C",
        ".hpp": "C++",
        ".cs": "C#",
        ".go": "Go",
        ".rs": "Rust",
        ".rb": "Ruby",
        ".php": "PHP",
        ".swift": "Swift",
        ".kt": "Kotlin",
        ".scala": "Scala",
        ".sh": "Bash",
        ".bash": "Bash",
        ".sql": "SQL",
        ".html": "HTML",
        ".htm": "HTML",
        ".css": "CSS",
        ".scss": "SCSS",
        ".sass": "SASS",
        ".less": "LESS",
        ".json": "JSON",
        ".yaml": "YAML",
        ".yml": "YAML",
        ".xml": "XML",
        ".md": "Markdown",
        ".rst": "ReStructuredText",
        ".tex": "LaTeX",
        ".r": "R",
        ".lua": "Lua",
        ".perl": "Perl",
        ".pl": "Perl",
        ".dart": "Dart",
        ".groovy": "Groovy",
        ".gradle": "Gradle",
        ".clj": "Clojure",
        ".cljs": "ClojureScript",
        ".erl": "Erlang",
        ".ex": "Elixir",
        ".exs": "Elixir",
        ".hx": "Haxe",
        ".vim": "VimScript",
        ".m": "Objective-C",
        ".mm": "Objective-C++",
    }

    # Directories to skip (common build/dependency folders)
    SKIP_DIRS = {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "venv",
        ".venv",
        "env",
        ".env",
        "__pycache__",
        "dist",
        "build",
        ".build",
        "target",
        "out",
        ".gradle",
        ".idea",
        ".vscode",
        ".vs",
        "vendor",
        ".cargo",
        "elm-stuff",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "htmlcov",
        ".hypothesis",
        "eggs",
        ".eggs",
        "parts",
        "sdist",
        "var",
        "wheels",
        "pip-wheel-metadata",
        "share",
        "python-eggs",
        "lib",
        "lib64",
        ".Python",
        "develop-eggs",
        "downloads",
        ".webassets-cache",
        ".scrapy",
        ".coverage",
        ".coverage.*",
        ".cache",
        "nosetests.xml",
        "coverage.xml",
        "*.cover",
        ".ipynb_checkpoints",
        ".pytype",
        ".dmypy.json",
        "dmypy.json",
        ".pyre",
        ".egg-info",
        "site",
        ".terraform",
    }

    def __init__(self, db):
        """Initialize LOC scanner.
        
        Args:
            db: Database instance (from database/db.py)
        """
        self.db = db

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
            print(f"[LOCScanner] Project not found: {project_name}")
            return None

        project_path = project["project_path"]
        if not project_path:
            print(f"[LOCScanner] Project path is NULL: {project_name}")
            return None

        # Scan the directory
        loc_by_language = self._scan_directory(project_path)

        # Store results in project_loc_snapshots
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if loc_by_language:
            with self.db.conn:
                for language_name, lines_of_code in loc_by_language.items():
                    self.db.conn.execute(
                        """
                        INSERT INTO project_loc_snapshots (project_name, language_name, lines_of_code, last_scanned_at, needs_sync)
                        VALUES (?, ?, ?, ?, 1)
                        ON CONFLICT(project_name, language_name) DO UPDATE SET
                            lines_of_code = excluded.lines_of_code,
                            last_scanned_at = excluded.last_scanned_at,
                            needs_sync = 1
                        """,
                        (project_name, language_name, lines_of_code, now),
                    )

            print(f"[LOCScanner] {project_name}: {len(loc_by_language)} languages, {sum(loc_by_language.values())} LOC")
            return loc_by_language
        else:
            print(f"[LOCScanner] No source code found in {project_path}")
            return loc_by_language

    def scan_all_projects(self):
        """Scan all projects in the projects table.
        
        Useful for background worker or bulk initialization.
        """
        projects = self.db.get_all_projects()

        if not projects:
            print("[LOCScanner] No projects to scan")
            return

        print(f"[LOCScanner] Scanning {len(projects)} projects...")
        scanned_count = 0

        for project in projects:
            project_name = project["project_name"]
            result = self.scan_project(project_name)
            if result is not None:
                scanned_count += 1

        print(f"[LOCScanner] Completed: {scanned_count}/{len(projects)} projects scanned")

    def _scan_directory(self, project_path):
        """Recursively scan a directory and count LOC by language.
        
        Args:
            project_path: Path to project root directory
            
        Returns:
            Dict: language_name → total lines_of_code
        """
        loc_by_language = defaultdict(int)
        project_path_obj = Path(project_path)

        if not project_path_obj.exists():
            print(f"[LOCScanner] Path does not exist: {project_path}")
            return loc_by_language

        if not project_path_obj.is_dir():
            print(f"[LOCScanner] Path is not a directory: {project_path}")
            return loc_by_language

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

        except (PermissionError, OSError) as e:
            print(f"[LOCScanner] Error scanning {project_path}: {e}")

        return dict(loc_by_language)

    def _should_skip_path(self, file_path):
        """Check if path contains directories to skip.
        
        Args:
            file_path: Path object to check
            
        Returns:
            True if should skip, False otherwise
        """
        for part in file_path.parts:
            if part in self.SKIP_DIRS:
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
        return self.LANGUAGE_EXTENSIONS.get(suffix)

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
            print(f"[LOCScanner] Error reading {file_path}: {e}")
            return 0
