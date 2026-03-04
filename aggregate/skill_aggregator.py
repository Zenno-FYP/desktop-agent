"""Phase 4: Skill Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Maps detected_language → skill_name (using config mapping).
Aggregates durations by (project_name, skill_name) cumulatively and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict
from datetime import datetime


class SkillAggregator:
    """Generates UPSERT commands for project_skills table."""

    def __init__(self, language_to_skill_mapping=None):
        """Initialize with optional skill mapping.
        
        Args:
            language_to_skill_mapping: Dict mapping language_name → skill_name
                                      E.g., {"Python": "Backend", "JavaScript": "Frontend"}
        """
        self.language_to_skill_mapping = language_to_skill_mapping or {}

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for skill durations grouped by project/skill (cumulative).
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, language_name,
                                  context_state, duration_sec
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (project_name, skill_name) → sum duration_sec (cumulative per project)
        aggregates = defaultdict(int)
        now = datetime.now().isoformat()
        
        for log in transformed_logs:
            # Skip __unassigned__ projects (they don't exist in projects table)
            if log["project_name"] == "__unassigned__":
                continue
            
            language_name = log["language_name"]
            
            # Map language to skill (default to "Unknown" if not in mapping)
            skill_name = self.language_to_skill_mapping.get(language_name, "Unknown")
            
            key = (log["project_name"], skill_name)
            aggregates[key] += log["duration_sec"]

        sql_commands = []
        
        for (project_name, skill_name), total_duration in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO project_skills (project_name, skill_name, duration_sec, last_updated_at, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(project_name, skill_name) DO UPDATE SET
                    duration_sec = project_skills.duration_sec + excluded.duration_sec,
                    last_updated_at = excluded.last_updated_at,
                    needs_sync = 1
                """,
                (project_name, skill_name, total_duration, now),
            ))

        return sql_commands
