"""Phase 4: App Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Groups durations by (date, project_name, app_name) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict


class AppAggregator:
    """Generates UPSERT commands for daily_project_apps table."""

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for app durations grouped by date/project/app.
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, language_name,
                                  context_state, duration_sec
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name, app_name) → sum duration_sec
        aggregates = defaultdict(int)
        
        for log in transformed_logs:
            # Skip __unassigned__ projects (they don't exist in projects table)
            if log["project_name"] == "__unassigned__":
                continue
            
            key = (log["date"], log["project_name"], log["app_name"])
            aggregates[key] += log["duration_sec"]

        sql_commands = []
        
        for (date, project_name, app_name), total_duration in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO daily_project_apps (date, project_name, app_name, duration_sec, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name, app_name) DO UPDATE SET
                    duration_sec = daily_project_apps.duration_sec + excluded.duration_sec,
                    needs_sync = 1
                """,
                (date, project_name, app_name, total_duration),
            ))

        return sql_commands
