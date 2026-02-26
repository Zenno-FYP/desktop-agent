"""Phase 4: Behavior Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Calculates physical effort metrics (keystrokes, clicks, scrolls, idle time).
Groups by (date, project_name) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Mathematical Logic:
- Keystrokes: typing_intensity (KPM) * duration_sec / 60
- Clicks: mouse_click_rate (CPM) * duration_sec / 60
- Scrolls: mouse_scroll_events (already a count, take as-is)
- Idle: idle_duration_sec (already a count, take as-is)

Part of the Maestro pattern: specialized aggregator with ONE job.
"""

from collections import defaultdict


class BehaviorAggregator:
    """Generates UPSERT commands for daily_project_behavior table."""

    def generate_upserts(self, transformed_logs):
        """Generate UPSERT commands for behavior metrics grouped by date/project.
        
        Args:
            transformed_logs: List of transformed log dicts from ETLPipeline.
                            Keys: log_id, date, app_name, project_name, project_path,
                                  language_name, context_state, duration_sec, end_time_utc
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name) → aggregate metrics
        aggregates = defaultdict(lambda: {
            "keystrokes": 0,
            "clicks": 0,
            "scrolls": 0,
            "idle": 0
        })
        
        for log in transformed_logs:
            # Skip __unassigned__ projects (they don't exist in projects table)
            if log["project_name"] == "__unassigned__":
                continue
            
            # Calculate duration in minutes for rate-to-count conversion
            duration_minutes = log["duration_sec"] / 60.0
            
            # Extract behavioral metrics from log
            typing_intensity = log.get("typing_intensity", 0.0) or 0.0
            mouse_click_rate = log.get("mouse_click_rate", 0.0) or 0.0
            mouse_scroll_events = log.get("mouse_scroll_events", 0) or 0
            idle_duration_sec = log.get("idle_duration_sec", 0) or 0
            
            # Convert rates to counts
            keystrokes = int(round(typing_intensity * duration_minutes))
            clicks = int(round(mouse_click_rate * duration_minutes))
            scrolls = int(mouse_scroll_events)
            idle = int(idle_duration_sec)
            
            key = (log["date"], log["project_name"])
            aggregates[key]["keystrokes"] += keystrokes
            aggregates[key]["clicks"] += clicks
            aggregates[key]["scrolls"] += scrolls
            aggregates[key]["idle"] += idle

        sql_commands = []
        
        for (date, project_name), metrics in aggregates.items():
            sql_commands.append((
                """
                INSERT INTO daily_project_behavior (date, project_name, total_keystrokes, total_mouse_clicks, total_scroll_events, total_idle_sec, needs_sync)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name) DO UPDATE SET
                    total_keystrokes = daily_project_behavior.total_keystrokes + excluded.total_keystrokes,
                    total_mouse_clicks = daily_project_behavior.total_mouse_clicks + excluded.total_mouse_clicks,
                    total_scroll_events = daily_project_behavior.total_scroll_events + excluded.total_scroll_events,
                    total_idle_sec = daily_project_behavior.total_idle_sec + excluded.total_idle_sec,
                    needs_sync = 1
                """,
                (date, project_name, metrics["keystrokes"], metrics["clicks"], metrics["scrolls"], metrics["idle"]),
            ))

        return sql_commands
