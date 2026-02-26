"""Phase 4: Skill Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Maps detected_language → skill_name (using config mapping).
Groups durations by (date, project_name, skill_name) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict


class SkillAggregator:
    """Generates UPSERT commands for daily_project_skills table."""

    def __init__(self, language_to_skill_mapping=None):
        """Initialize with optional skill mapping.
        
        Args:
            language_to_skill_mapping: Dict mapping language_name → skill_name
                                      E.g., {"Python": "Backend", "JavaScript": "Frontend"}
        """
        self.language_to_skill_mapping = language_to_skill_mapping or {}

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for skill durations grouped by date/project/skill.
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, language_name,
                                  context_state, duration_sec
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name, skill_name) → sum duration_sec
        aggregates = defaultdict(int)
        
        for log in transformed_logs:
            # Skip __unassigned__ projects (they don't exist in projects table)
            if log["project_name"] == "__unassigned__":
                continue
            
            language_name = log["language_name"]
            
            # Map language to skill (default to "Unknown" if not in mapping)
            skill_name = self.language_to_skill_mapping.get(language_name, "Unknown")
            
            key = (log["date"], log["project_name"], skill_name)
            aggregates[key] += log["duration_sec"]

        sql_commands = []
        
        for (date, project_name, skill_name), total_duration in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO daily_project_skills (date, project_name, skill_name, duration_sec, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name, skill_name) DO UPDATE SET
                    duration_sec = daily_project_skills.duration_sec + excluded.duration_sec,
                    needs_sync = 1
                """,
                (date, project_name, skill_name, total_duration),
            ))

        return sql_commands
