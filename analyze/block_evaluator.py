"""5-Minute rolling block evaluator for context state detection."""
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

# Phase 4: Aggregation
from aggregate.etl_pipeline import ETLPipeline


class BlockEvaluator:
    """Background evaluator that retroactively tags activity logs with context state.
    
    Runs on a 5-minute heartbeat with ML-based predictions:
    1. Wakes up every 5 minutes (e.g., at 2:00, 2:05, 2:10 PM)
    2. Queries all unevaluated logs (context_state IS NULL) from the last 5 minutes
    3. Aggregates behavioral metrics across all sessions in that block
    4. Extracts 9-dimensional feature vector from block metrics
    5. Runs ML model (XGBoost) on features for prediction
    6. Falls back to heuristic if ML confidence is low
    7. Retroactively updates all logs in the block with context_state + confidence_score
    """
    
    def __init__(self, db, context_detector, config=None, block_duration_sec: int = 300, use_ml: bool = True):
        """Initialize the BlockEvaluator.
        
        Args:
            db: Database instance for querying and updating logs
            context_detector: ContextDetector instance for heuristic fallback
            config: Config instance for reading ML settings (optional)
            block_duration_sec: Duration of evaluation blocks in seconds (default: 300 = 5 min)
            use_ml: Whether to use ML model (default: True). Falls back to heuristic if model unavailable
                    Overrides config['ml_enabled'] if explicitly set to False
        """
        self.db = db
        self.context_detector = context_detector
        self.config = config
        self.block_duration_sec = block_duration_sec
        self.thread = None
        self.running = False
        self.ml_predictor = None
        self.use_ml = use_ml
        self.ml_available = False
        self.ml_confidence_threshold = 0.5  # Default threshold
        self.esm_confidence_threshold = None  # Will be read from config
        self.esm_popup = None  # Phase 3B: ESM popup handler
        
        # Read ML settings from config if available
        if config:
            self.use_ml = config.get('ml_enabled', use_ml)
            self.ml_confidence_threshold = config.get('ml_confidence_threshold', 0.5)
            # Read ESM popup threshold from config
            esm_config = config.get('esm_popup', {})
            self.esm_confidence_threshold = esm_config.get('confidence_threshold')
        
        # Try to load ML model
        if self.use_ml:
            self._init_ml()
            # Phase 3B: Initialize ESM popup handler for verification collection
            self._init_esm_popup()
        
        # Phase 4: Initialize ETL Pipeline (Maestro) for coordinating all aggregators
        self.etl_pipeline = ETLPipeline(db)
    
    def _init_esm_popup(self) -> None:
        """Initialize ESM popup handler for ground-truth collection."""
        if not self.config:
            return

        if not self.config.get("esm_popup.enabled", True):
            print("[BlockEvaluator] ESM popups disabled (esm_popup.enabled=false)")
            return

        try:
            from ml.esm_popup import ESMPopup
            
            self.esm_popup = ESMPopup(
                db=self.db,
                config=self.config
            )
            print(f"[BlockEvaluator] ESM popup handler initialized")
        except Exception as e:
            print(f"[BlockEvaluator] Failed to initialize ESM popup: {e}")
            self.esm_popup = None
    
    def _init_ml(self) -> None:
        """Initialize ML model."""
        try:
            from ml.predictor import MLPredictor
            
            # Determine model path from config or default
            model_path_str = self.config.get("ml_model_path") if self.config else None
            if model_path_str:
                model_path = Path(model_path_str).expanduser()
                if not model_path.is_absolute():
                    model_path = model_path.resolve()
            else:
                model_path = Path(__file__).parent.parent / "data" / "models" / "context_detector.pkl"
            
            if model_path.exists():
                self.ml_predictor = MLPredictor(str(model_path))
                self.ml_available = True
                print(f"[BlockEvaluator] ML model loaded from {model_path}")
            else:
                print(f"[BlockEvaluator] ML model not found at {model_path}, using heuristic fallback")
        except Exception as e:
            print(f"[BlockEvaluator] Failed to load ML model: {e}, using heuristic fallback")
            self.ml_available = False
    
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
        """Stop the background evaluator thread and clean up resources."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        # Clean up ESM popup handler
        if self.esm_popup:
            self.esm_popup.stop()
            self.esm_popup = None
        print(f"[BlockEvaluator] Stopped background thread")
    
    def _run_loop(self) -> None:
        """Main loop: wake at wall-clock boundaries (e.g., 2:00, 2:05, 2:10) and evaluate.
        
        Phase 2 Hardening: Align to exact block boundaries instead of sleeping for a fixed
        duration. This prevents heartbeat drift and makes aggregation scheduling predictable.
        """
        while self.running:
            try:
                # Compute seconds until next block boundary
                now = datetime.utcnow()
                seconds_since_epoch = now.timestamp()
                seconds_until_next_boundary = self.block_duration_sec - (int(seconds_since_epoch) % self.block_duration_sec)
                
                # Sleep until next boundary (max sleep_duration is block_duration_sec)
                time.sleep(seconds_until_next_boundary)
                
                self.evaluate_block()
            except Exception as e:
                print(f"[BlockEvaluator] Error in evaluation loop: {e}")
                import traceback
                traceback.print_exc()
    
    def evaluate_block(self) -> None:
        """Evaluate the most recent block of unevaluated logs.
        
        Queries all NULL-context logs from the last 5 minutes,
        aggregates their metrics, determines context state using ML,
        and retroactively tags them all.
        
        Phase 2 Hardening Improvements:
        - Queries by end_time (not start_time) to catch long sessions that span blocks
        - Runs at wall-clock boundaries (via _run_loop) for stable, predictable scheduling
        """
        now = datetime.utcnow()  # Use UTC to match agent's logging
        five_mins_ago = now - timedelta(seconds=self.block_duration_sec)
        
        try:
            # Query unevaluated logs from last 5-minute block.
            # Phase 2 Hardening: query_by_end_time=True (default) ensures we catch sessions
            # that started before the block but ended within it (prevents "never-tagged" logs).
            logs = self.db.query_logs(
                start_time=five_mins_ago.isoformat(),
                end_time=now.isoformat(),
                where_context_is_null=True,
                query_by_end_time=True
            )
            
            if not logs:
                # No new logs to evaluate
                return
            
            # Aggregate metrics from all sessions in this block
            block_metrics = self._aggregate_block_metrics(logs)
            
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
            if self.esm_popup and confidence_score < self.esm_confidence_threshold:
                self.esm_popup.queue_for_verification(
                    log_ids=log_ids,
                    context_state=context_state,
                    confidence=confidence_score
                )
            
            # Phase 4: Run ETL pipeline (Maestro coordinates all aggregations)
            self.etl_pipeline.run()
            
            # Log the evaluation
            block_start = five_mins_ago.strftime("%H:%M")
            block_end = now.strftime("%H:%M")
            model_type = "ML" if self.ml_available else "Heuristic"
            print(
                f"[BlockEvaluator] {block_start}-{block_end}: "
                f"{updated_count} logs → {context_state} ({confidence_score:.0%}) [{model_type}]"
            )
            
        except Exception as e:
            print(f"[BlockEvaluator] Error evaluating block: {e}")
    
    def _predict_context(self, block_metrics: dict) -> tuple:
        """Predict context state using ML model with heuristic fallback.
        
        Args:
            block_metrics: Aggregated metrics dictionary
        
        Returns:
            Tuple of (context_state: str, confidence_score: float)
        """
        if self.ml_available:
            try:
                # Get ML prediction with confidence
                # MLPredictor handles feature extraction internally
                context_state, confidence_score = self.ml_predictor.predict_with_confidence(block_metrics)
                
                # Use ML prediction if confidence is high enough
                if confidence_score >= self.ml_confidence_threshold:
                    return context_state, confidence_score
                
                # Otherwise fall back to heuristic
                print(f"[BlockEvaluator] Low ML confidence ({confidence_score:.0%}) < threshold ({self.ml_confidence_threshold:.0%}), using heuristic")
                return self.context_detector.detect_context(block_metrics)
                
            except Exception as e:
                print(f"[BlockEvaluator] ML prediction failed: {e}, falling back to heuristic")
                return self.context_detector.detect_context(block_metrics)
        else:
            # Use heuristic if ML not available
            return self.context_detector.detect_context(block_metrics)
    
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
                - touched_distraction_app: True if any distraction app was used during block
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
                'touched_distraction_app': False,
            }
        
        # Sum up raw quantities
        total_typing_events = 0
        total_click_events = 0
        total_scroll_events = 0
        total_idle_duration = 0
        total_session_duration = 0
        unique_apps = set()
        unique_projects = set()
        touched_distraction = False
        
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
                # Check if this app is a distraction app
                if self.context_detector.is_distraction_app(log['app_name']):
                    touched_distraction = True
            
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
            'touched_distraction_app': touched_distraction,
        }
