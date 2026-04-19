"""Phase 4: Behavior Table Aggregator (Specialized).

Receives a "clean" batch of transformed logs from ETLPipeline.
Calculates physical effort metrics (typing rate, click rate, deletion edits, idle time, mouse movement).
Groups by (date, project_name) and generates UPSERT commands.
Does NOT execute SQL; returns commands for pipeline to execute atomically.

Mathematical Logic:
- Typing Intensity (KPM): weighted average of typing_intensity rates across all logs
  Formula: sum(keystrokes) / total_duration_minutes
  where keystrokes = typing_intensity * duration_sec / 60
- Mouse Click Rate (CPM): weighted average of mouse_click_rate rates across all logs
  Formula: sum(clicks) / total_duration_minutes
  where clicks = mouse_click_rate * duration_sec / 60
- Deletions: total_deletion_key_presses (sum of all deletion key presses)
- Idle: total_idle_duration_sec (sum of all idle time)
- Mouse Movement Distance: total_mouse_movement_distance (sum of all pixels moved)

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
                                  language_name, context_state, duration_sec, end_time_local
        
        Returns:
            List of (query, params) tuples ready to execute in a transaction
        """
        # Group by (date, project_name) → aggregate metrics
        aggregates = defaultdict(lambda: {
            "total_keystrokes": 0,      # For calculating KPM
            "total_duration_min": 0.0,  # For calculating KPM
            "total_clicks": 0,          # For calculating CPM
            "total_deletions": 0,       # Sum of deletion presses
            "total_idle": 0,            # Sum of idle time
            "total_mouse_distance": 0.0 # Sum of mouse movement distance (pixels)
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
            deletion_key_presses = log.get("deletion_key_presses", 0) or 0
            idle_duration_sec = log.get("idle_duration_sec", 0) or 0
            mouse_movement_distance = log.get("mouse_movement_distance", 0.0) or 0.0
            
            # Convert rates to counts
            keystrokes = int(round(typing_intensity * duration_minutes))
            clicks = int(round(mouse_click_rate * duration_minutes))
            deletions = int(deletion_key_presses)
            idle = int(idle_duration_sec)
            
            key = (log["date"], log["project_name"])
            aggregates[key]["total_keystrokes"] += keystrokes
            aggregates[key]["total_duration_min"] += duration_minutes
            aggregates[key]["total_clicks"] += clicks
            aggregates[key]["total_deletions"] += deletions
            aggregates[key]["total_idle"] += idle
            aggregates[key]["total_mouse_distance"] += mouse_movement_distance

        sql_commands = []
        
        for (date, project_name), metrics in aggregates.items():
            batch_keystrokes = metrics["total_keystrokes"]
            batch_duration_min = metrics["total_duration_min"]
            batch_clicks = metrics["total_clicks"]

            # Derive KPM/CPM from this batch's raw totals (used on fresh INSERT)
            batch_kpm = batch_keystrokes / batch_duration_min if batch_duration_min > 0 else 0.0
            batch_cpm = batch_clicks / batch_duration_min if batch_duration_min > 0 else 0.0

            sql_commands.append((
                """
                INSERT INTO daily_project_behavior (
                    date, project_name,
                    typing_intensity_kpm, mouse_click_rate_cpm,
                    total_deletion_key_presses, total_idle_sec, total_mouse_movement_distance,
                    total_keystrokes, total_duration_min, total_clicks,
                    needs_sync
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(date, project_name) DO UPDATE SET
                    total_keystrokes    = daily_project_behavior.total_keystrokes    + excluded.total_keystrokes,
                    total_duration_min  = daily_project_behavior.total_duration_min  + excluded.total_duration_min,
                    total_clicks        = daily_project_behavior.total_clicks        + excluded.total_clicks,
                    typing_intensity_kpm = CASE
                        WHEN (daily_project_behavior.total_duration_min + excluded.total_duration_min) > 0
                        THEN (daily_project_behavior.total_keystrokes + excluded.total_keystrokes)
                             / (daily_project_behavior.total_duration_min + excluded.total_duration_min)
                        ELSE 0.0
                    END,
                    mouse_click_rate_cpm = CASE
                        WHEN (daily_project_behavior.total_duration_min + excluded.total_duration_min) > 0
                        THEN (daily_project_behavior.total_clicks + excluded.total_clicks)
                             / (daily_project_behavior.total_duration_min + excluded.total_duration_min)
                        ELSE 0.0
                    END,
                    total_deletion_key_presses    = daily_project_behavior.total_deletion_key_presses    + excluded.total_deletion_key_presses,
                    total_idle_sec                = daily_project_behavior.total_idle_sec                + excluded.total_idle_sec,
                    total_mouse_movement_distance = daily_project_behavior.total_mouse_movement_distance + excluded.total_mouse_movement_distance,
                    needs_sync = 1
                """,
                (
                    date, project_name,
                    round(batch_kpm, 2), round(batch_cpm, 2),
                    metrics["total_deletions"], metrics["total_idle"], round(metrics["total_mouse_distance"], 2),
                    round(batch_keystrokes, 2), round(batch_duration_min, 4), round(batch_clicks, 2),
                ),
            ))

        return sql_commands
