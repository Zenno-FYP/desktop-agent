"""Phase 4: Context Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Groups durations by (date, project_name, context_state) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict


class ContextAggregator:
    """Generates UPSERT commands for daily_project_context table."""

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for context durations grouped by date/project/context.
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, language_name,
                                  context_state, duration_sec
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name, context_state) → sum duration_sec
        aggregates = defaultdict(int)
        
        for log in transformed_logs:
            # Note: We include __unassigned__ in context tracking.
            # This allows us to record "Distracted" sessions (from blacklist detection)
            # and other unattributed time. Other aggregators skip __unassigned__.
            
            key = (log["date"], log["project_name"], log["context_state"])
            aggregates[key] += log["duration_sec"]

        sql_commands = []
        
        for (date, project_name, context_state), total_duration in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO daily_project_context (date, project_name, context_state, duration_sec, needs_sync)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name, context_state) DO UPDATE SET
                    duration_sec = daily_project_context.duration_sec + excluded.duration_sec,
                    needs_sync = 1
                """,
                (date, project_name, context_state, total_duration),
            ))

        return sql_commands
