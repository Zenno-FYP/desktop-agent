"""Rolling block evaluator for context state detection."""
import logging
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# Phase 4: Aggregation
from aggregate.etl_pipeline import ETLPipeline


class BlockEvaluator:
    """Background evaluator that retroactively tags activity logs with context state.
    
    Runs on a configurable heartbeat (block_duration_sec) with ML-based predictions:
    1. Wakes up every block (e.g., at 2:00, 2:05, 2:10 PM when block=5 min)
    2. Queries all unevaluated logs (context_state IS NULL) from the last block
    3. Aggregates behavioral metrics across all sessions in that block
    4. Extracts 9-dimensional feature vector from block metrics
    5. Runs ML model (XGBoost) on features for prediction
    6. Falls back to heuristic if ML confidence is low
    7. Retroactively updates all logs in the block with context_state + confidence_score
    """
    
    def __init__(self, db, context_detector, config=None, block_duration_sec: int = None, use_ml: bool = True, startup_delay_sec: int = None):
        """Initialize the BlockEvaluator.
        
        Args:
            db: Database instance for querying and updating logs
            context_detector: ContextDetector instance for heuristic fallback
            config: Config instance for reading settings (optional)
            block_duration_sec: Duration of evaluation blocks in seconds. If None, reads from config.
                               Falls back to default 300 seconds if not specified in config
            use_ml: Whether to use ML model (default: True). Falls back to heuristic if model unavailable
                    Overrides config['ml_enabled'] if explicitly set to False
            startup_delay_sec: Delay in seconds before first evaluation. If None, reads from config.
                              Falls back to default 300 seconds if not specified in config
        """
        self.db = db
        self.context_detector = context_detector
        self.config = config
        
        # Read block_duration_sec from config first, then parameter, then default to 300
        if config:
            self.block_duration_sec = config.get('block_duration_sec', block_duration_sec or 300)
        else:
            self.block_duration_sec = block_duration_sec or 300
        
        # Read startup_delay_sec: parameter > config > default 300 (5 min)
        if startup_delay_sec is not None:
            self.startup_delay_sec = startup_delay_sec
        elif config:
            self.startup_delay_sec = config.get('block_evaluator.startup_delay_sec', 300)
        else:
            self.startup_delay_sec = 300
        
        self.thread = None
        self.running = False
        self.first_evaluation_done = False
        self.ml_predictor = None
        self.use_ml = use_ml
        self.ml_available = False
        self.ml_confidence_threshold = 0.5  # Default threshold (overridden by config below)
        self.esm_confidence_threshold = None  # Optional; only prompts when set
        self.esm_popup = None  # Phase 3B: ESM popup handler

        self.logger = logging.getLogger(__name__)
        
        # Read ML settings from config if available
        if config:
            self.use_ml = config.get('ml_enabled', use_ml)
            self.ml_confidence_threshold = config.get('ml_confidence_threshold', 0.5)
            # Read ESM popup threshold from config
            esm_config = config.get('esm_popup', {})
            self.esm_confidence_threshold = esm_config.get('confidence_threshold')
            # If not explicitly configured, default ESM prompting threshold to ML threshold.
            if self.esm_confidence_threshold is None:
                self.esm_confidence_threshold = self.ml_confidence_threshold
        
        # Try to load ML model
        if self.use_ml:
            self._init_ml()
            # Phase 3B: Initialize ESM popup handler for verification collection
            self._init_esm_popup()
        
        # Phase 4: Initialize ETL Pipeline (Maestro) for coordinating all aggregators
        self.etl_pipeline = ETLPipeline(db, config=config)
        
        # Signal 7: Consecutive work hours tracking (resets on long breaks)
        # IMPROVED: Restore from database on startup (don't reset work session)
        self.consecutive_work_hours = 0.0  # Hours worked since last break
        self.last_block_end_time = datetime.now()  # Track last block time
        self.break_threshold_sec = 1800  # 30 minutes - reset if idle/gap exceeds this
        
        # Restore fatigue counter from last log (prevents reset on agent restart)
        self._restore_fatigue_from_database()
    
    def _init_esm_popup(self) -> None:
        """Initialize ESM popup handler for ground-truth collection."""
        if not self.config:
            return

        if not self.config.get("esm_popup.enabled", True):
            self.logger.info("ESM popups disabled (esm_popup.enabled=false)")
            return

        try:
            from ml.esm_popup import ESMPopup
            
            self.esm_popup = ESMPopup(
                db=self.db,
                config=self.config
            )
            self.logger.info("ESM popup handler initialized")
        except Exception as e:
            self.logger.exception("Failed to initialize ESM popup")
            self.esm_popup = None
    
    def _init_ml(self) -> None:
        """Initialize ML model from config.yaml path."""
        try:
            from ml.predictor import MLPredictor
            
            # Read model path from config (required for this phase)
            if not self.config:
                self.logger.info("No config provided, ML disabled")
                self.ml_available = False
                return
            
            model_path_str = self.config.get("ml_model_path")
            if not model_path_str:
                self.logger.info("ml_model_path not configured in config.yaml, ML disabled")
                self.ml_available = False
                return
            
            # Resolve path: handle relative paths and expand ~
            model_path = Path(model_path_str).expanduser()
            if not model_path.is_absolute():
                # Relative paths are relative to workspace root
                model_path = Path(__file__).parent.parent / model_path_str
            
            if model_path.exists():
                self.ml_predictor = MLPredictor(str(model_path))
                self.ml_available = True
                self.logger.info("ML model loaded from %s", model_path)
            else:
                self.logger.info("ML model not found at %s, using heuristic fallback", model_path)
        except Exception as e:
            self.logger.exception("Failed to load ML model; using heuristic fallback")
            self.ml_available = False
    
    def _restore_fatigue_from_database(self) -> None:
        """Restore consecutive work hours from last log on startup.
        
        BUGFIX: When agent restarts, it should not reset the user's work session.
        This queries the last activity log and calculates:
        1. Time since last activity: If > 30 min, reset consecutive_work_hours to 0
        2. If < 30 min, estimate consecutive hours from last log's timestamp onwards
        
        This maintains the "session fatigue" counter across agent restarts.
        """
        try:
            # Query the most recent log from database
            query = """
                SELECT end_time FROM raw_activity_logs 
                ORDER BY end_time DESC LIMIT 1
            """
            result = self.db.conn.execute(query).fetchone()
            
            if not result:
                # No previous logs, start fresh
                self.consecutive_work_hours = 0.0
                self.last_block_end_time = datetime.now()
                self.logger.info("[BlockEvaluator] No previous logs found, starting with fresh fatigue counter")
                return
            
            # Parse last log's end time
            last_end_time_str = result[0]
            try:
                # Handle ISO format: 2026-03-02T15:30:45.123456
                if 'T' in last_end_time_str:
                    last_end_time = datetime.fromisoformat(last_end_time_str)
                else:
                    # Handle space-separated format: 2026-03-02 15:30:45
                    last_end_time = datetime.strptime(last_end_time_str, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError) as e:
                self.logger.warning(f"Could not parse last_end_time '{last_end_time_str}': {e}")
                self.consecutive_work_hours = 0.0
                self.last_block_end_time = datetime.now()
                return
            
            now = datetime.now()
            time_gap_sec = (now - last_end_time).total_seconds()
            
            # Determine if user took a break (> 30 min gap = reset)
            if time_gap_sec > self.break_threshold_sec:
                self.consecutive_work_hours = 0.0
                self.logger.info(
                    "[BlockEvaluator] Large gap since last activity (%.0f min) > break threshold (%.0f min), "
                    "resetting consecutive work hours",
                    time_gap_sec / 60, self.break_threshold_sec / 60
                )
            else:
                # Estimate consecutive hours: assume continuous work from last_end_time
                # This assumes the user was working before we restarted
                gap_hours = time_gap_sec / 3600
                self.consecutive_work_hours = max(gap_hours, 0.1)  # At least 0.1 hours
                self.logger.info(
                    "[BlockEvaluator] Restored consecutive work hours from last log: %.2f hours "
                    "(gap: %.1f minutes)",
                    self.consecutive_work_hours, time_gap_sec / 60
                )
            
            # Update last_block_end_time to now
            self.last_block_end_time = now
            
        except Exception as e:
            self.logger.exception("[BlockEvaluator] Error restoring fatigue from database, starting fresh")
            self.consecutive_work_hours = 0.0
            self.last_block_end_time = datetime.now()
    
    def start(self) -> None:
        """Start the background evaluator thread.
        
        Thread runs as daemon, so it won't prevent Python from exiting.
        """
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        minutes = self.block_duration_sec / 60
        self.logger.info("Started background thread (%g-min heartbeat)", minutes)
    
    def stop(self) -> None:
        """Stop the background evaluator thread and clean up resources."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        # Clean up ESM popup handler
        if self.esm_popup:
            self.esm_popup.stop()
            self.esm_popup = None
        self.logger.info("Stopped background thread")
    
    def _run_loop(self) -> None:
        """Main loop: wake at wall-clock boundaries (e.g., 2:00, 2:05, 2:10) and evaluate.
        
        Phase 2 Hardening: Align to exact block boundaries instead of sleeping for a fixed
        duration. This prevents heartbeat drift and makes aggregation scheduling predictable.
        """
        # Wait for startup delay before first evaluation
        if not self.first_evaluation_done and self.startup_delay_sec > 0:
            self.logger.info("Waiting %d seconds before first block evaluation", self.startup_delay_sec)
            time.sleep(self.startup_delay_sec)
            self.first_evaluation_done = True
        
        while self.running:
            try:
                # Compute seconds until next block boundary
                now = datetime.now()
                seconds_since_epoch = now.timestamp()
                remainder = seconds_since_epoch % float(self.block_duration_sec)
                seconds_until_next_boundary = float(self.block_duration_sec) - remainder
                # Guard against pathological 0-second sleeps.
                if seconds_until_next_boundary <= 0.001:
                    seconds_until_next_boundary = float(self.block_duration_sec)
                
                # Sleep until next boundary (max sleep_duration is block_duration_sec)
                time.sleep(seconds_until_next_boundary)
                
                self.evaluate_block()
            except Exception as e:
                self.logger.exception("Error in evaluation loop")
    
    def evaluate_block(self) -> None:
        """Evaluate the most recent block of unevaluated logs.
        
        Queries all NULL-context logs from the last 5 minutes,
        aggregates their metrics, determines context state using ML,
        and retroactively tags them all.
        
        Phase 2 Hardening Improvements:
        - Queries by end_time (not start_time) to catch long sessions that span blocks
        - Runs at wall-clock boundaries (via _run_loop) for stable, predictable scheduling
        """
        # Use LOCAL time to match agent's logging and activity logs
        now = datetime.now()
        block_start_time = now - timedelta(seconds=self.block_duration_sec)
        
        try:
            # Query unevaluated logs from the last block.
            # Phase 2 Hardening: query_by_end_time=True (default) ensures we catch sessions
            # that started before the block but ended within it (prevents "never-tagged" logs).
            logs = self.db.query_logs(
                start_time=block_start_time.isoformat(),
                end_time=now.isoformat(),
                where_context_is_null=True,
                query_by_end_time=True
            )
            
            if not logs:
                # No new logs to evaluate
                return
            
            # Aggregate metrics from all sessions in this block
            block_metrics = self._aggregate_block_metrics(logs)
            # Add end_time for feature extraction (hour of day and day of week)
            block_metrics['end_time'] = now
            
            # Update consecutive work hours (Signal 7)
            # Calculate gap since last evaluation
            time_gap_sec = (now - self.last_block_end_time).total_seconds()
            
            # Reset consecutive work if gap > 30 min (user took a break)
            if time_gap_sec > self.break_threshold_sec:
                self.consecutive_work_hours = 0.0
            
            # Add block duration to consecutive hours
            self.consecutive_work_hours += self.block_duration_sec / 3600
            
            # Clamp to reasonable max (24 hours, prevents overflow)
            self.consecutive_work_hours = min(self.consecutive_work_hours, 24.0)
            
            # Add to block_metrics for feature extraction
            block_metrics['consecutive_work_hours'] = self.consecutive_work_hours
            
            # Update last block time for next evaluation
            self.last_block_end_time = now
            
            # Evaluate using ML model (with heuristic fallback)
            context_state, confidence_score = self._predict_context(block_metrics)
            
            # Get list of log IDs to update
            log_ids = [log['log_id'] for log in logs]
            
            # Retroactively tag all logs in this block
            updated_count = self.db.update_logs_context(
                log_ids=log_ids,
                context_state=context_state,
                confidence_score=confidence_score
            )
            
            # Phase 3B: Prompt immediate verification for low-confidence blocks (non-blocking)
            if (
                self.esm_popup
                and self.esm_confidence_threshold is not None
                and confidence_score < float(self.esm_confidence_threshold)
            ):
                self.esm_popup.queue_for_verification(
                    log_ids=log_ids,
                    context_state=context_state,
                    confidence=confidence_score,
                    block_metrics=block_metrics
                )
            
            # Phase 4: Run ETL pipeline (Maestro coordinates all aggregations)
            self.etl_pipeline.run()
            
            # Log the evaluation
            block_start = block_start_time.strftime("%H:%M")
            block_end = now.strftime("%H:%M")
            model_type = "ML" if self.ml_available else "Heuristic"
            self.logger.info(
                "%s-%s: %s logs → %s (%.0f%%) [%s]",
                block_start,
                block_end,
                updated_count,
                context_state,
                confidence_score * 100.0,
                model_type,
            )
            
        except Exception as e:
            self.logger.exception("Error evaluating block")
    
    def _predict_context(self, block_metrics: dict) -> tuple:
        """Predict context state using ML model with heuristic fallback.
        
        Implements IDLE CIRCUIT BREAKER (PRIORITY 0):
        If idle_ratio > 0.70 (70%), bypass ML entirely and use heuristic.
        Reason: ML is trained on activity data; cannot classify from silence.
        
        Args:
            block_metrics: Aggregated metrics dictionary
        
        Returns:
            Tuple of (context_state: str, confidence_score: float)
        """
        from ml.feature_extractor import FeatureExtractor
        
        # PRIORITY 0: IDLE CIRCUIT BREAKER
        # Extract idle_ratio to check if user is mostly idle
        idle_duration_sec = float(block_metrics.get('idle_duration_sec', 0))
        total_duration_sec = float(block_metrics.get('total_duration_sec', 1))
        idle_ratio = idle_duration_sec / max(total_duration_sec, 1)
        idle_ratio = float(min(idle_ratio, 1.0))
        
        # Threshold from config or heuristic default (70%)
        idle_threshold = 0.70
        if self.context_detector and hasattr(self.context_detector, 'idle_ratio_threshold'):
            idle_threshold = self.context_detector.idle_ratio_threshold
        
        if idle_ratio > idle_threshold:
            # High idle: Use heuristic's context-aware idle handling
            # (Communication/Research/Distracted based on app_score and other signals)
            context_state, confidence_score = self.context_detector.detect_context(block_metrics)
            self.logger.info(
                "[BlockEvaluator] Idle Circuit Breaker: idle_ratio=%.1f%% > threshold=%.1f%% → %s (%.0f%%)",
                idle_ratio * 100, idle_threshold * 100, context_state, confidence_score * 100
            )
            return context_state, confidence_score
        
        # Normal flow: Use ML if available, heuristic if not
        if self.ml_available:
            try:
                # Get ML prediction with confidence
                # MLPredictor handles feature extraction internally
                context_state, confidence_score = self.ml_predictor.predict_with_confidence(block_metrics)
                
                # Use ML prediction if confidence is high enough
                if confidence_score >= self.ml_confidence_threshold:
                    return context_state, confidence_score
                
                # Otherwise fall back to heuristic
                self.logger.info(
                    "[BlockEvaluator] Low ML confidence (%.0f%%) < threshold (%.0f%%), using heuristic",
                    confidence_score * 100, self.ml_confidence_threshold * 100
                )
                return self.context_detector.detect_context(block_metrics)
                
            except Exception as e:
                self.logger.exception("ML prediction failed, falling back to heuristic")
                return self.context_detector.detect_context(block_metrics)
        else:
            # Use heuristic if ML not available
            return self.context_detector.detect_context(block_metrics)
    
    def _aggregate_block_metrics(self, logs: list) -> dict:
        """Aggregate behavioral metrics from all sessions in a block (5 minutes).
        
        Corrects for human-calibrated signals:
        - app_sessions: List of {'app_name', 'duration_sec'} for TIME-WEIGHTED app_score
        - app_switch_count: Actual app transitions (not just unique apps)
        - project_name: Sticky project (None = off-task, key for distraction filtering)
        - mouse_scroll_events: Count of scroll events
        
        Args:
            logs: List of activity log dicts from query_logs()
        
        Returns:
            Dictionary of aggregated metrics for 8-signal extraction
        """
        if not logs:
            return {
                'typing_intensity': 0,
                'mouse_click_rate': 0,
                'deletion_key_presses': 0,
                'total_keystrokes': 0,
                'idle_duration_sec': 0,
                'total_duration_sec': self.block_duration_sec,
                'app_sessions': [],
                'app_switch_count': 0,
                'mouse_movement_distance': 0,
                'mouse_scroll_events': 0,
                'project_name': None,
                'end_time': datetime.now(),
            }
        
        # === Aggregation for 8 signals ===
        total_typing_events = 0
        total_click_events = 0
        total_deletion_keys = 0
        total_keystrokes = 0
        total_mouse_movement_distance = 0.0
        total_idle_duration = 0
        total_session_duration = 0
        total_scroll_events = 0
        
        # === App tracking (build sessions with durations) ===
        app_sessions = []  # List of {'app_name', 'duration_sec'}
        app_duration_map = {}  # Track time per app for weighted scoring
        current_app = None
        app_switch_count = 0
        
        # === Project tracking (sticky: use any non-NULL project in block) ===
        project_name = None
        
        # Sort logs by timestamp to track app transitions
        logs_sorted = sorted(logs, key=lambda x: x.get('start_time', datetime.now()))
        
        for log in logs_sorted:
            # === Base signal aggregation ===
            session_duration = log.get('duration_sec', 0)
            kpm = log.get('typing_intensity', 0)
            cpm = log.get('mouse_click_rate', 0)
            
            typing_events = (kpm * session_duration) / 60
            click_events = (cpm * session_duration) / 60
            
            total_typing_events += typing_events
            total_click_events += click_events
            total_deletion_keys += log.get('deletion_key_presses', 0)
            total_keystrokes += int(round(typing_events))
            total_mouse_movement_distance += float(log.get('mouse_movement_distance', 0.0) or 0.0)
            total_idle_duration += log.get('idle_duration_sec', 0)
            total_session_duration += session_duration
            total_scroll_events += log.get('mouse_scroll_events', 0)
            
            # === App session tracking (for time-weighted scoring) ===
            app_name = log.get('app_name', 'unknown').strip()
            if app_name != current_app:
                if current_app is not None:
                    app_switch_count += 1
                current_app = app_name
            
            # Accumulate duration per app
            app_duration_map[app_name] = app_duration_map.get(app_name, 0) + session_duration
            
            # === Sticky project (use first non-NULL or any non-NULL in block) ===
            if project_name is None:
                log_project = log.get('project_name')
                if log_project:
                    project_name = log_project
        
        # Convert app_duration_map → app_sessions list
        for app_name, duration in app_duration_map.items():
            app_sessions.append({
                'app_name': app_name,
                'duration_sec': duration,
            })
        
        # Convert aggregated counts back to rates
        total_duration = max(total_session_duration, 1)
        block_kpm = (total_typing_events / total_duration) * 60
        block_cpm = (total_click_events / total_duration) * 60
        
        # Get end time from last log
        end_time = logs_sorted[-1].get('end_time', datetime.now()) if logs_sorted else datetime.now()
        
        return {
            'typing_intensity': block_kpm,
            'mouse_click_rate': block_cpm,
            'deletion_key_presses': int(total_deletion_keys),
            'total_keystrokes': int(total_keystrokes),
            'mouse_movement_distance': float(total_mouse_movement_distance),
            'idle_duration_sec': int(total_idle_duration),
            'total_duration_sec': total_duration,
            'app_sessions': app_sessions,  # For time-weighted app_score
            'app_switch_count': app_switch_count,  # Actual transitions
            'mouse_scroll_events': total_scroll_events,
            'project_name': project_name,  # Sticky project differentiator
            'end_time': end_time,
        }
