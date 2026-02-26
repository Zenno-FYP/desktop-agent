"""Phase 4: Language Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Groups durations by (date, project_name, language_name) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict


class LanguageAggregator:
    """Generates UPSERT commands for daily_project_languages table."""

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for language durations grouped by date/project/language.
        
        FILTER: Only tracks ACTUAL CODING (where project_path is not NULL)
        - IDE sessions with open files have project_path (e.g., "E:\Zenno\desktop-agent")
        - Browser/generic apps have project_name (from sticky) but project_path = NULL
        - This ensures we only count real development time, not browsing time
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, project_path,
                                  language_name, context_state, duration_sec
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name, language_name) → sum duration_sec
        aggregates = defaultdict(int)
        
        for log in transformed_logs:
            # Skip __unassigned__ projects (browser time with expired sticky)
            if log["project_name"] == "__unassigned__":
                continue
            
            # Skip rows with NO project_path (browser, generic apps, etc.)
            # Only count ACTUAL CODING sessions where files were opened
            if not log["project_path"]:
                continue
            
            key = (log["date"], log["project_name"], log["language_name"])
            aggregates[key] += log["duration_sec"]

        sql_commands = []
        
        for (date, project_name, language_name), total_duration in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO daily_project_languages (date, project_name, language_name, duration_sec, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name, language_name) DO UPDATE SET
                    duration_sec = daily_project_languages.duration_sec + excluded.duration_sec,
                    needs_sync = 1
                """,
                (date, project_name, language_name, total_duration),
            ))

        return sql_commands
