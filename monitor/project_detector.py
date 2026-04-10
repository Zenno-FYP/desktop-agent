"""Detect project context and active file."""
import os
import re
import time
import string
from pathlib import Path
from typing import Tuple, Optional
import psutil


class ProjectDetector:
    """Detect project name and active file from IDE context."""

    def __init__(self, config=None):
        """Initialize project detector.

        Args:
            config: Optional Config instance for config-driven behavior.
        """
        self.config = config
        self.last_detected_project = None
        self.last_detected_file = None

        self.project_markers = self._get_project_markers()
        self.watch_dirs = self._get_watch_dirs()
        self.language_extensions = self._get_language_extensions()
        self.lightweight_search_cfg = self._get_lightweight_search_cfg()
        self.ides_config = self._get_ide_configurations()

    def _get_project_markers(self):
        """Get project markers from config.yaml (required)."""
        if not self.config:
            return []
        return self.config.get("project_detector.project_markers", [])

    def _get_watch_dirs(self):
        """Get watch directories from config.yaml (required)."""
        if not self.config:
            return []
        
        raw = self.config.get("project_detector.watch_dirs", [])
        watch_dirs = []
        for path_str in raw:
            try:
                watch_dirs.append(Path(path_str).expanduser())
            except Exception:
                continue
        return watch_dirs

    def _get_language_extensions(self):
        """Get language extensions from config.yaml.
        
        Language extension mappings should be configured in config.yaml 
        under project_detector.language_extensions.
        """
        if not self.config:
            return {}
        return self.config.get("project_detector.language_extensions", {})

    def _get_lightweight_search_cfg(self):
        """Get lightweight search config from config.yaml (required).
        
        Settings should be configured in config.yaml under 
        project_detector.lightweight_search with keys:
        - exclude_dirs: list of directory names to skip
        - max_depth: maximum directory depth to search
        - time_limit_sec: maximum search time in seconds
        - search_system_drive_last: whether to search C: drive last
        """
        if not self.config:
            return {
                "exclude_dirs": set(),
                "max_depth": 0,
                "time_limit_sec": 0.0,
                "search_system_drive_last": False,
            }

        cfg = self.config.get("project_detector.lightweight_search", {}) or {}
        exclude_dirs = cfg.get("exclude_dirs", [])
        if isinstance(exclude_dirs, list):
            exclude_dirs = {str(d).lower() for d in exclude_dirs}
        else:
            exclude_dirs = set()

        return {
            "exclude_dirs": exclude_dirs,
            "max_depth": int(cfg.get("max_depth", 0)),
            "time_limit_sec": float(cfg.get("time_limit_sec", 0.0)),
            "search_system_drive_last": bool(cfg.get("search_system_drive_last", False)),
        }

    def _get_ide_configurations(self):
        """Get IDE detection configurations from config.yaml.
        
        Structure:
        ```yaml
        ides:
          - name: "VS Code"
            executable_names: ["code.exe", "code"]
            title_suffixes: ["Visual Studio Code"]
            title_format: "vscode"
        ```
        
        Returns:
            list: List of IDE config dicts, or empty list if config unavailable
        """
        if not self.config:
            return []
        
        ides_raw = self.config.get("project_detector.ides", []) or []
        
        # Normalize: convert executable_names and title_suffixes to lowercase for case-insensitive matching
        ides_normalized = []
        for ide in ides_raw:
            if not isinstance(ide, dict):
                continue
            
            normalized_ide = {
                "name": ide.get("name", "Unknown IDE"),
                "executable_names": {name.lower() for name in ide.get("executable_names", [])},
                "title_suffixes": {suffix.lower() for suffix in ide.get("title_suffixes", [])},
                "title_format": ide.get("title_format", "generic"),
            }
            ides_normalized.append(normalized_ide)
        
        return ides_normalized

    def _find_matching_ide(self, app_name: str, window_title: str) -> Optional[dict]:
        """Find which IDE configuration matches the current app/window.
        
        IMPORTANT: Only matches based on EXECUTABLE NAME, not window title.
        This prevents browser tabs (Brave, Chrome) containing IDE keywords from 
        being misidentified as actual IDEs.
        
        Example:
        - Brave browser with tab "VS Code tutorial" → NOT matched as VS Code IDE
        - code.exe with title "main.py - /project" → Matched as VS Code IDE
        
        Args:
            app_name: Executable name (e.g., "code.exe")
            window_title: Full window title
            
        Returns:
            dict: Matching IDE config, or None if no match found
        """
        app_name_lower = app_name.lower() if app_name else ""
        
        for ide_config in self.ides_config:
            # Only check if executable name matches (case-insensitive partial match)
            # This is the ONLY reliable way to identify IDEs vs browsers
            if any(exe_name in app_name_lower for exe_name in ide_config["executable_names"]):
                return ide_config
        
        # Title suffix matching is disabled to prevent false positives
        # (e.g., Brave browser opening a tab about VS Code shouldn't be treated as VS Code IDE)
        
        return None

    def _lightweight_drive_search(self, active_file_name: str, expected_project_name: str) -> Optional[str]:
        """Strictly bounded, high-speed fallback search across drives.
        
        Uses hard safety limits to protect < 5% CPU constraint:
        - Time Limit: Aborts if search takes longer than configured limit (PRIMARY safety)
        - Depth Limit: Searches only up to configured depth (SECONDARY safety)
        - Exclusion: Skips Windows, Program Files, node_modules, etc.
        - Drive Filtering: Optionally includes the system drive (C:) last, or skips it entirely
        
        Args:
            active_file_name: The file to find (e.g., 'parser.y')
            expected_project_name: Project folder name to match (e.g., 'CC project')
            
        Returns:
            Project root path if found within limits, None otherwise
        """
        exclude_dirs = self.lightweight_search_cfg["exclude_dirs"]
        max_depth = self.lightweight_search_cfg["max_depth"]
        time_limit_sec = self.lightweight_search_cfg["time_limit_sec"]
        search_system_drive_last = self.lightweight_search_cfg["search_system_drive_last"]

        # Disabled by configuration.
        if max_depth <= 0 or time_limit_sec <= 0:
            return None
        
        start_time = time.time()
        
        system_drive = "C:\\"
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        drives_non_system = [d for d in drives if d.upper() != system_drive]

        # Default behavior: skip C: entirely (expensive). If enabled, search it last.
        drives = drives_non_system
        if search_system_drive_last and os.path.exists(system_drive):
            drives = drives_non_system + [system_drive]
        
        for drive in drives:
            try:
                for root, dirs, files in os.walk(drive):
                    
                    # 1. Circuit Breaker: Enforce time limit (PRIMARY safety)
                    if time.time() - start_time > time_limit_sec:
                        return None 
                    
                    # 2. Filter out excluded directories (modifying in-place stops os.walk from entering)
                    dirs[:] = [d for d in dirs if d.lower() not in exclude_dirs]
                    
                    # 3. Circuit Breaker: Enforce depth limit (SECONDARY safety)
                    depth = root.count(os.sep) - drive.count(os.sep)
                    if depth >= max_depth:
                        del dirs[:]  # Don't go deeper
                        continue
                    
                    # 4. Match logic: Look for the active file
                    if active_file_name in files:
                        # If we have a project name, ensure it's part of the folder path
                        if expected_project_name and expected_project_name.lower() in root.lower():
                            return root
                        elif not expected_project_name:
                            # No project name to match, just found the file
                            return root
            except (OSError, PermissionError):
                # Skip drives with permission issues
                pass
        
        return None

    def _get_project_path_from_pid(self, pid: int, active_file_name: Optional[str] = None) -> Optional[str]:
        """Use OS-level process inspection to find exact project path.
        
        Best approach for modern IDEs (VS Code, PyCharm):
        - Process CWD is always installation dir (not useful)
        - Command line args don't include project paths (not reliable)
        - Open FILES are the ultimate truth - walk up to find project root
        
        Args:
            pid: Process ID of the IDE
            active_file_name: The active file name to validate against
            
        Returns:
            Full project path or None
        """
        try:
            process = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        
        # Primary: Inspect open files and walk up directory tree to find project root
        try:
            open_files = process.open_files()
            
            for file_info in open_files:
                file_path = file_info.path
                
                # If we have an active file, prioritize matching it
                if active_file_name:
                    if not file_path.lower().endswith(f"\\{active_file_name.lower()}"):
                        continue  # Skip files that don't match active file name
                
                # Walk up from this file to find project root (marked by .git, package.json, etc.)
                current = Path(file_path).parent
                
                for _ in range(100):  # Check up to 100 levels up (filesystem depth limit)
                    if current == current.parent:  # Reached filesystem root
                        break
                    
                    # Check for project markers from config
                    if not self.project_markers:
                        break
                    for marker in self.project_markers:
                        if (current / marker).exists():
                            return str(current)
                    
                    current = current.parent
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        
        # Fallback: Try Layer 2 (cmdline args) if Layer 3 didn't work
        try:
            cmdline = process.cmdline()
            for arg in cmdline:
                # Check if any argument is a valid directory path (not a flag)
                if os.path.isdir(arg) and not arg.startswith("--"):
                    # Skip system paths
                    if "AppData" not in arg and "WINDOWS" not in arg:
                        return arg
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        
        return None

    def extract_from_window_title(self, 
                                   app_name: str, 
                                   window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract project and file info from IDE window title.
        
        Uses IDE configurations from config.yaml for flexible, extensible IDE support.
        
        Args:
            app_name: Application name (e.g., 'code.exe')
            window_title: Full window title
            
        Returns:
            tuple: (project_name, active_file) or (None, None)
        """
        if not window_title:
            return None, None
        
        # Try to find matching IDE from config
        matched_ide = self._find_matching_ide(app_name, window_title)
        
        if matched_ide:
            title_format = matched_ide.get("title_format", "generic")
            
            if title_format == "vscode":
                # Format: "filename - /path/to/project - IDE_Name"
                return self._parse_title_vscode_format(window_title, matched_ide["name"])
            
            elif title_format == "pycharm":
                # Format: "project_name - [file.py]" or "[file.py] - project_name"
                return self._parse_title_pycharm_format(window_title)
            
            else:  # generic fallback
                # For generic IDEs, try to extract from title
                return self._parse_title_generic_format(window_title)
        
        # No IDE matched - handle as browser/generic application
        if ' - ' in window_title:
            # For browsers (Chrome, Firefox, Edge, Brave, Safari, Opera, etc.),
            # the format is typically: "Tab Title - Browser Name"
            # We want to preserve the full tab title and remove only the browser name at the end.
            
            # Find the last occurrence of ' - '
            last_dash_idx = window_title.rfind(' - ')
            if last_dash_idx != -1:
                potential_browser = window_title[last_dash_idx + 3:].strip()
                # Check if the part after the last ' - ' is a known browser name
                browser_names = {
                    'chrome', 'chromium', 'google chrome',
                    'firefox', 'mozilla firefox',
                    'edge', 'microsoft edge',
                    'brave', 'brave browser',
                    'safari',
                    'opera',
                    'vivaldi',
                    'yandex',
                    'internet explorer', 'iexplore',
                }
                if potential_browser.lower() in browser_names:
                    # Remove the browser name, keep the tab title
                    active_file = window_title[:last_dash_idx].strip()
                else:
                    # Not a recognized browser pattern, keep everything
                    active_file = window_title.strip()
            else:
                active_file = window_title.strip()
            return None, active_file
        
        return None, None

    def _parse_title_vscode_format(self, window_title: str, ide_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse VS Code / Cursor style title format.
        
        Format: "filename - /path/to/project - IDE_Name"
        """
        # Remove IDE name suffix (e.g., "Visual Studio Code", "Cursor")
        title = window_title.replace(ide_name, '').strip('- ')
        
        parts = [p.strip() for p in title.split(' - ')]
        
        if len(parts) >= 2:
            active_file = parts[0]
            project_path = parts[1]
            project_name = Path(project_path).name
            return project_name, active_file
        
        return None, parts[0] if parts else None

    def _parse_title_pycharm_format(self, window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse PyCharm / IntelliJ style title format.
        
        Format: "project_name - [file.py]" or "[file.py] - project_name"
        """
        # Look for [ ] pattern (file)
        file_match = re.search(r'\[([^\]]+)\]', window_title)
        active_file = file_match.group(1) if file_match else None
        
        # Extract project name (before dash or in brackets)
        dash_parts = window_title.split(' - ')
        project_name = dash_parts[0].strip() if dash_parts else None
        
        return project_name, active_file

    def _parse_title_generic_format(self, window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse generic IDE title format.
        
        Tries to extract project and file from title with basic heuristics.
        """
        if ' - ' in window_title:
            parts = [p.strip() for p in window_title.split(' - ')]
            if len(parts) >= 2:
                # Assume: filename - project_name format
                return parts[-1], parts[0]
            elif len(parts) == 1:
                # Just one part, treat as active file
                return None, parts[0]
        
        return None, window_title

    def detect_project(self, 
                      app_name: str, 
                      window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Detect project and active file from window context.
        
        Uses window title parsing as primary method.
        
        Args:
            app_name: Application name
            window_title: Window title
            
        Returns:
            tuple: (project_name, active_file)
        """
        project_name, active_file = self.extract_from_window_title(app_name, window_title)
        
        # Cache result
        if project_name:
            self.last_detected_project = project_name
        if active_file:
            self.last_detected_file = active_file
        
        return project_name or self.last_detected_project, \
               active_file or self.last_detected_file



    def get_detected_language(self, active_file: Optional[str]) -> Optional[str]:
        """Detect programming language from file extension.
        
        Args:
            active_file: Active filename (e.g., 'main.py')
            
        Returns:
            Programming language or None
        """
        if not active_file:
            return None
        
        file_ext = Path(active_file).suffix.lower()
        skill = (self.language_extensions or {}).get(file_ext)
        
        return skill if skill else None

    def get_project_path(self, app_name: str, window_title: str, pid: Optional[int] = None, active_file_name: Optional[str] = None) -> Optional[str]:
        """Extract project path securely without blocking the main thread.
        
        Uses OS-level detection via psutil and safe directory checks.
        Performs safely bounded filesystem searches with hard time limits (< 1 sec).
        """
        
        # Layer 1: Try PID-based OS detection first (Most reliable)
        if pid is not None:
            pid_result = self._get_project_path_from_pid(pid, active_file_name)
            if pid_result and pid_result != str(Path.cwd()):
                return pid_result
                
        if not window_title:
            return None
        
        # Layer 2: Extract from title and check known safe directories
        extracted_name = None
        
        # Use IDE configuration to extract project name from window title
        matched_ide = self._find_matching_ide(app_name, window_title)
        if matched_ide:
            title_format = matched_ide.get("title_format", "generic")
            
            if title_format == "vscode":
                # Format: "filename - /path/to/project - IDE_Name"
                title = window_title.replace(matched_ide["name"], '').strip('- ')
                parts = [p.strip() for p in title.split(' - ')]
                if len(parts) >= 2:
                    extracted_name = parts[1]
                    
            elif title_format == "pycharm":
                # Format: "project_name - [file.py]"
                dash_parts = window_title.split(' - ')
                if dash_parts:
                    extracted_name = dash_parts[0].strip()
            
            else:  # generic
                # Try basic extraction
                if ' - ' in window_title:
                    parts = [p.strip() for p in window_title.split(' - ')]
                    if len(parts) >= 2:
                        extracted_name = parts[-1]

        # If we successfully parsed a name from the title, safely resolve it
        if extracted_name:
            # If it's already an absolute path that exists
            if Path(extracted_name).is_absolute() and Path(extracted_name).exists():
                return extracted_name
                
            cwd = Path.cwd()
            
            # Check CWD
            if cwd.name == extracted_name:
                return str(cwd)
                
            # Check sibling
            sibling = cwd.parent / extracted_name
            if sibling.exists() and sibling.is_dir():
                return str(sibling)
                
            # Check safe Watch Directories ONLY
            for base_dir in self.watch_dirs:
                if base_dir.exists():
                    candidate = base_dir / extracted_name
                    if candidate.exists() and candidate.is_dir():
                        return str(candidate)
            
            # Safe Fallback: Lightweight bounded drive search (time + depth limited)
            if active_file_name:
                search_result = self._lightweight_drive_search(active_file_name, extracted_name)
                if search_result:
                    return search_result
            
            # Final fallback: return extracted name as-is
            return extracted_name
            
        return None
