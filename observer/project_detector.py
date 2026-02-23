"""Detect project context and active file."""
import os
import re
from pathlib import Path
from typing import Tuple, Optional


class ProjectDetector:
    """Detect project name and active file from IDE context."""

    # Project root markers to look for
    PROJECT_MARKERS = ['.git', 'package.json', 'requirements.txt', 'setup.py', 
                       'Makefile', 'gradle.build', 'pom.xml', '.project', 
                       'tsconfig.json', 'next.config.js', 'vite.config.js']
    
    # Common project directories
    WATCH_DIRS = [
        Path.home() / 'Documents',
        Path.home() / 'Projects',
        Path.home() / 'Development',
        Path.home() / 'Code',
        Path.home() / 'workspace',
    ]

    def __init__(self):
        """Initialize project detector."""
        self.last_detected_project = None
        self.last_detected_file = None

    def extract_from_window_title(self, 
                                   app_name: str, 
                                   window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract project and file info from IDE window title.
        
        Args:
            app_name: Application name (e.g., 'code.exe')
            window_title: Full window title
            
        Returns:
            tuple: (project_name, active_file) or (None, None)
        """
        if not window_title:
            return None, None
        
        app_name_lower = app_name.lower() if app_name else ""
        
        # VS Code pattern: "filename - path/to/project - Visual Studio Code"
        if 'code.exe' in app_name_lower or 'visual studio code' in window_title.lower():
            return self._parse_vscode_title(window_title)
        
        # PyCharm pattern: "project_name - [file.py]"
        if 'pycharm' in app_name_lower or 'idea' in app_name_lower:
            return self._parse_pycharm_title(window_title)
        
        # Generic pattern: extract filename before dash
        if ' - ' in window_title:
            parts = window_title.split(' - ')
            active_file = parts[0].strip()
            return None, active_file
        
        return None, None

    def _parse_vscode_title(self, window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse VS Code window title format.
        
        Format: "filename - /path/to/project - Visual Studio Code"
        """
        # Remove "Visual Studio Code" suffix
        title = window_title.replace('Visual Studio Code', '').strip('- ')
        
        parts = [p.strip() for p in title.split(' - ')]
        
        if len(parts) >= 2:
            active_file = parts[0]
            project_path = parts[1]
            project_name = Path(project_path).name
            return project_name, active_file
        
        return None, parts[0] if parts else None

    def _parse_pycharm_title(self, window_title: str) -> Tuple[Optional[str], Optional[str]]:
        """Parse PyCharm window title format.
        
        Format: "project_name - [file.py]" or "[file.py] - project_name"
        """
        # Look for [ ] pattern (file)
        file_match = re.search(r'\[([^\]]+)\]', window_title)
        active_file = file_match.group(1) if file_match else None
        
        # Extract project name (before dash or in brackets)
        dash_parts = window_title.split(' - ')
        project_name = dash_parts[0].strip() if dash_parts else None
        
        return project_name, active_file

    def find_project_root(self, start_path: Path = None) -> Optional[Path]:
        """Find project root by looking for markers.
        
        Args:
            start_path: path to start searching from (default: current directory)
            
        Returns:
            Path to project root or None if not found
        """
        if start_path is None:
            start_path = Path.cwd()
        
        current = Path(start_path).resolve()
        
        # Search up to 10 levels
        for _ in range(10):
            # Check if any project markers exist in current directory
            for marker in self.PROJECT_MARKERS:
                if (current / marker).exists():
                    return current
            
            # Move to parent directory
            if current.parent == current:
                # Reached filesystem root
                break
            current = current.parent
        
        return None

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
        
        skill_map = {
            '.py': 'Python',
            '.js': 'JavaScript',
            '.ts': 'TypeScript',
            '.jsx': 'React',
            '.tsx': 'React',
            '.java': 'Java',
            '.cpp': 'C++',
            '.c': 'C',
            '.cs': 'C#',
            '.go': 'Go',
            '.rs': 'Rust',
            '.rb': 'Ruby',
            '.php': 'PHP',
            '.swift': 'Swift',
            '.kt': 'Kotlin',
            '.html': 'HTML',
            '.css': 'CSS',
            '.sql': 'SQL',
            '.json': 'JSON',
            '.yaml': 'YAML',
            '.yml': 'YAML',
            '.xml': 'XML',
            '.md': 'Markdown',
        }
        
        file_ext = Path(active_file).suffix.lower()
        skill = skill_map.get(file_ext)
        
        return skill if skill else None

    def get_project_path(self, app_name: str, window_title: str) -> Optional[str]:
        """Extract project path from window title.
        
        Args:
            app_name: Application name
            window_title: Window title
            
        Returns:
            Full project path or None
        """
        if not window_title:
            return None
        
        app_name_lower = app_name.lower() if app_name else ""
        
        # VS Code: Extract path from "filename - /path/to/project - VS Code"
        if 'code.exe' in app_name_lower or 'visual studio code' in window_title.lower():
            title = window_title.replace('Visual Studio Code', '').strip('- ')
            parts = [p.strip() for p in title.split(' - ')]
            if len(parts) >= 2:
                extracted = parts[1]
                
                # If already absolute path, return it
                if Path(extracted).is_absolute():
                    return extracted
                
                # Otherwise try to resolve the project name to full path
                # Check if it matches current working directory
                cwd = Path.cwd()
                if cwd.name == extracted:
                    return str(cwd)
                
                # Check parent directory
                if cwd.parent.name == extracted:
                    return str(cwd.parent)
                
                # Check common project directories
                for base_dir in self.WATCH_DIRS:
                    if not base_dir.exists():
                        continue
                    candidate = base_dir / extracted
                    if candidate.exists():
                        return str(candidate)
                
                # If nothing found, return as-is
                return extracted
        
        # PyCharm: Extract from "project_name - [file.py]"
        if 'pycharm' in app_name_lower or 'idea' in app_name_lower:
            dash_parts = window_title.split(' - ')
            if dash_parts:
                project_name = dash_parts[0].strip()
                # Try to resolve like VS Code
                cwd = Path.cwd()
                if cwd.name == project_name:
                    return str(cwd)
                if cwd.parent.name == project_name:
                    return str(cwd.parent)
                return project_name
        
        return None
