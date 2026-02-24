"""5-Minute rolling block evaluator for context state detection."""
import threading
import time
from datetime import datetime, timedelta
from typing import Optional


class BlockEvaluator:
    """Background evaluator that retroactively tags activity logs with context state.
    
    Runs on a 5-minute heartbeat:
    1. Wakes up every 5 minutes (e.g., at 2:00, 2:05, 2:10 PM)
    2. Queries all unevaluated logs (context_state IS NULL) from the last 5 minutes
    3. Aggregates behavioral metrics across all sessions in that block
    4. Runs ContextDetector heuristic on aggregated metrics
    5. Retroactively updates all logs in the block with context_state + confidence_score
    """
    
    def __init__(self, db, context_detector, block_duration_sec: int = 300):
        """Initialize the BlockEvaluator.
        
        Args:
            db: Database instance for querying and updating logs
            context_detector: ContextDetector instance for evaluating blocks
            block_duration_sec: Duration of evaluation blocks in seconds (default: 300 = 5 min)
        """
        self.db = db
        self.context_detector = context_detector
        self.block_duration_sec = block_duration_sec
        self.thread = None
        self.running = False
    
    def start(self) -> None:
        """Start the background evaluator thread.
        
        Thread runs as daemon, so it won't prevent Python from exiting.
        """
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print(f"[BlockEvaluator] Started background thread (5-min heartbeat)")
    
    def stop(self) -> None:
        """Stop the background evaluator thread."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
            print(f"[BlockEvaluator] Stopped background thread")
    
    def _run_loop(self) -> None:
        """Main loop: wake every 5 minutes and evaluate the last block."""
        while self.running:
            try:
                time.sleep(self.block_duration_sec)
                self.evaluate_block()
            except Exception as e:
                print(f"[BlockEvaluator] Error in evaluation loop: {e}")
    
    def evaluate_block(self) -> None:
        """Evaluate the most recent block of unevaluated logs.
        
        Queries all NULL-context logs from the last 5 minutes,
        aggregates their metrics, determines context state,
        and retroactively tags them all.
        """
        now = datetime.now()
        five_mins_ago = now - timedelta(seconds=self.block_duration_sec)
        
        try:
            # Query unevaluated logs from last 5-minute block
            logs = self.db.query_logs(
                start_time=five_mins_ago.isoformat(),
                end_time=now.isoformat(),
                where_context_is_null=True
            )
            
            if not logs:
                # No new logs to evaluate
                return
            
            # Aggregate metrics from all sessions in this block
            block_metrics = self._aggregate_block_metrics(logs)
            
            # Evaluate developer's mental state for this block
            context_state, confidence_score = self.context_detector.detect_context(block_metrics)
            
            # Get list of log IDs to update
            log_ids = [log['log_id'] for log in logs]
            
            # Retroactively tag all logs in this block
            updated_count = self.db.update_logs_context(
                log_ids=log_ids,
                context_state=context_state,
                confidence_score=confidence_score
            )
            
            # Log the evaluation
            block_start = five_mins_ago.strftime("%H:%M")
            block_end = now.strftime("%H:%M")
            print(
                f"[BlockEvaluator] {block_start}-{block_end}: "
                f"{updated_count} logs → {context_state} ({confidence_score:.0%})"
            )
            
        except Exception as e:
            print(f"[BlockEvaluator] Error evaluating block: {e}")
    
    def _aggregate_block_metrics(self, logs: list) -> dict:
        """Aggregate behavioral metrics from all sessions in a 5-minute block.
        
        Takes individual session metrics and combines them into block-level metrics
        that represent the developer's overall behavioral state during the block.
        
        Args:
            logs: List of activity log dicts from query_logs()
        
        Returns:
            Dictionary of aggregated metrics:
                - typing_intensity: Effective KPM for the block
                - mouse_click_rate: Effective CPM for the block
                - mouse_scroll_events: Total scrolls across all sessions
                - idle_duration_sec: Total idle time in block
                - total_duration_sec: Total block time (typically 300 sec)
                - app_switch_count: Number of unique apps touched
                - project_switch_count: Number of unique projects touched
        """
        if not logs:
            return {
                'typing_intensity': 0,
                'mouse_click_rate': 0,
                'mouse_scroll_events': 0,
                'idle_duration_sec': 0,
                'total_duration_sec': self.block_duration_sec,
                'app_switch_count': 0,
                'project_switch_count': 0,
            }
        
        # Sum up raw quantities
        total_typing_events = 0
        total_click_events = 0
        total_scroll_events = 0
        total_idle_duration = 0
        total_session_duration = 0
        unique_apps = set()
        unique_projects = set()
        
        for log in logs:
            # Calculate events from rates and durations
            session_duration = log.get('duration_sec', 0)
            kpm = log.get('typing_intensity', 0)
            cpm = log.get('mouse_click_rate', 0)
            
            # Convert KPM back to total keystrokes in this session
            typing_events = (kpm * session_duration) / 60
            click_events = (cpm * session_duration) / 60
            
            total_typing_events += typing_events
            total_click_events += click_events
            total_scroll_events += log.get('mouse_scroll_events', 0)
            total_idle_duration += log.get('idle_duration_sec', 0)
            total_session_duration += session_duration
            
            # Track unique apps and projects
            if log.get('app_name'):
                unique_apps.add(log['app_name'].lower())
            if log.get('project_name'):
                unique_projects.add(log['project_name'].lower())
        
        # Convert back to rates
        total_duration = max(total_session_duration, 1)  # Avoid divide by zero
        block_kpm = (total_typing_events / total_duration) * 60
        block_cpm = (total_click_events / total_duration) * 60
        
        return {
            'typing_intensity': block_kpm,
            'mouse_click_rate': block_cpm,
            'mouse_scroll_events': int(total_scroll_events),
            'idle_duration_sec': int(total_idle_duration),
            'total_duration_sec': total_duration,
            'app_switch_count': len(unique_apps),
            'project_switch_count': len(unique_projects),
        }
