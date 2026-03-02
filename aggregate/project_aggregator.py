"""Phase 4: Project Table Aggregator (Specialized).

Path Superiority Rule: Absolute paths are ALWAYS superior to relative paths.
Both Python layer (memory) and SQL layer (database) enforce this.
"""
import os
from datetime import datetime

class ProjectAggregator:
    """Generates UPSERT commands for projects table."""
    
    def __init__(self):
        """Initialize aggregator."""
        pass
    
    def _get_local_time(self) -> str:
        """Get current local time as formatted string."""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def generate_sql(self, transformed_logs: list) -> list:
        """Generate UPSERT commands for unique projects in the batch.
        
        Implements Path Superiority Rule:
        - Python layer: Prefer absolute paths over partial paths
        - SQL layer: Database refuses to overwrite absolute paths with partial ones
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, project_path,
                                  language_name, context_state, duration_sec, end_time
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        unique_projects = {}
        
        for log in transformed_logs:
            p_name = log["project_name"]
            p_path = log["project_path"]
            end_time = log["end_time"]
            
            # Only track real projects (not __unassigned__)
            if p_name and p_name != "__unassigned__":
                if p_name not in unique_projects:
                    unique_projects[p_name] = {
                        "path": p_path,
                        "last_active": end_time
                    }
                else:
                    # FIX 1: Timestamp - Keep the absolute latest end_time
                    if end_time > unique_projects[p_name]["last_active"]:
                        unique_projects[p_name]["last_active"] = end_time

                    # FIX 2: Path Superiority (Memory Layer)
                    current_path = unique_projects[p_name]["path"]
                    
                    if p_path:
                        if not current_path:
                            # If we had no path, take this new one
                            unique_projects[p_name]["path"] = p_path
                        else:
                            # Check if the new path is an absolute path (e.g., E:\ or /usr/)
                            is_new_abs = os.path.isabs(p_path)
                            is_old_abs = os.path.isabs(current_path)
                            
                            if is_new_abs and not is_old_abs:
                                # Upgrade! Overwrite the partial path with the absolute one
                                unique_projects[p_name]["path"] = p_path
                            elif is_new_abs == is_old_abs and len(p_path) > len(current_path):
                                # If both are absolute (or both partial), keep the longer/more detailed one
                                unique_projects[p_name]["path"] = p_path

        sql_commands = []
        
        for p_name, data in unique_projects.items():
            now = self._get_local_time()
            path = data["path"]
            last_active = data["last_active"]
            
            # FIX 3: Path Superiority (Database Layer)
            # The CASE statement ensures we never overwrite a DB absolute path with a partial one
            sql_commands.append((
                """
                INSERT INTO projects (project_name, project_path, first_seen_at, last_active_at, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(project_name) DO UPDATE SET
                    last_active_at = ?,
                    needs_sync = 1,
                    project_path = CASE 
                        -- Rule A: If new path is NULL, keep DB path
                        WHEN ? IS NULL THEN projects.project_path
                        
                        -- Rule B: If new path is absolute Windows (E:\) or Unix (/), take it!
                        WHEN ? LIKE '_:\%' OR ? LIKE '/%' THEN ?
                        
                        -- Rule C: If DB path is absolute but new path is relative, protect the DB path!
                        WHEN projects.project_path LIKE '_:\%' OR projects.project_path LIKE '/%' THEN projects.project_path
                        
                        -- Rule D: Otherwise, take the newest path
                        ELSE ?
                    END
                """,
                # We have to pass the 'path' variable 5 times to satisfy the CASE statement variables
                (p_name, path, now, last_active, last_active, path, path, path, path, path),
            ))

        return sql_commands
