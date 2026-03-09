"""Detect context state (Flow, Debugging, Research, Communication, Distracted) from block metrics."""
from typing import Tuple

try:
    from config.config import Config
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from config.config import Config


class ContextDetector:
    """Detect developer's mental context from block metrics.
    
    Heuristic fallback for the 5-state ML taxonomy:
    - Flow: smooth creation / deep work
    - Debugging: trial & error / fixes
    - Research: reading docs / navigation / info intake
    - Communication: chat / meetings / email
    - Distracted: non-work activity
    
    App lists are loaded from config.yaml (ml_app_scoring.productive_apps and 
    ml_app_scoring.distraction_apps) - same source used by ML feature extractor.
    This ensures heuristic fallback and ML produce consistent app categorization.
    """
    
    def __init__(self, config: Config = None):
        """Initialize context detector.
        
        Args:
            config: Config instance for reading heuristic thresholds and app lists (optional).
                   If not provided, defaults will be used.
        """
        self.config = config or Config()
        
        # Load heuristic thresholds from config (signal-based architecture)
        # All units normalized for 5-minute blocks
        heuristics = self.config.get('heuristics', {})
        
        # SIGNAL 0: Typing Intensity (KPM) - per minute rate
        self.typing_kpm_flow_min = heuristics.get('typing_kpm_flow_min', 0)
        self.typing_kpm_research_max = heuristics.get('typing_kpm_research_max', 20)
        self.typing_kpm_light_max = heuristics.get('typing_kpm_light_max', 15)
        
        # SIGNAL 1: Correction Ratio (primary differentiator)
        self.correction_ratio_debug_min = heuristics.get('correction_ratio_debug_min', 0.12)
        self.correction_ratio_flow_max = heuristics.get('correction_ratio_flow_max', 0.08)
        
        # SIGNAL 2: Mouse Velocity (px/sec)
        self.mouse_velocity_research_min = heuristics.get('mouse_velocity_research_min', 100)
        self.mouse_velocity_debug_min = heuristics.get('mouse_velocity_debug_min', 40)
        self.mouse_velocity_debug_max = heuristics.get('mouse_velocity_debug_max', 150)
        self.mouse_velocity_flow_max = heuristics.get('mouse_velocity_flow_max', 90)
        self.mouse_velocity_distraction_high = heuristics.get('mouse_velocity_distraction_high', 200)
        
        # SIGNAL 3: App Switch Frequency (per 5-min block, not per hour)
        self.switches_per_block_debug_min = heuristics.get('switches_per_block_debug_min', 4)
        self.switches_per_block_distraction_min = heuristics.get('switches_per_block_distraction_min', 8)
        
        # SIGNAL 4: Click Rate (CPM)
        self.mouse_cpm_research_light = heuristics.get('mouse_cpm_research_light', 10)
        self.mouse_cpm_distraction_high = heuristics.get('mouse_cpm_distraction_high', 20)
        
        # SIGNAL 5: App Score (-1.0 to 1.0) - time-weighted
        self.app_score_flow_min = heuristics.get('app_score_flow_min', 0.5)
        self.app_score_research = heuristics.get('app_score_research', 0.5)
        self.app_score_distraction_max = heuristics.get('app_score_distraction_max', -0.5)
        
        # SIGNAL 6: Idle Ratio (20-50% is research, 40-80% is distracted)
        self.idle_ratio_research_min = heuristics.get('idle_ratio_research_min', 0.2)
        self.idle_ratio_research_max = heuristics.get('idle_ratio_research_max', 0.5)
        
        # SIGNAL 7: Consecutive Work Hours (Session Fatigue)
        self.fatigue_moderate_hours = heuristics.get('fatigue_moderate_hours', 4.0)
        self.fatigue_high_hours = heuristics.get('fatigue_high_hours', 6.0)
        self.session_reset_idle_minutes = heuristics.get('session_reset_idle_minutes', 30)
        
        # Confidence scores for 5-state output
        self.confidence_flow = heuristics.get('confidence_flow', 0.92)
        self.confidence_flow_fatigued_moderate = heuristics.get('confidence_flow_fatigued_moderate', 0.85)
        self.confidence_flow_fatigued_high = heuristics.get('confidence_flow_fatigued_high', 0.75)
        self.confidence_debugging = heuristics.get('confidence_debugging', 0.85)
        self.confidence_research = heuristics.get('confidence_research', 0.80)
        self.confidence_communication = heuristics.get('confidence_communication', 0.80)
        self.confidence_distracted = heuristics.get('confidence_distracted', 0.75)
        self.confidence_distracted_sticky_project = heuristics.get('confidence_distracted_sticky_project', 0.60)
        self.confidence_ambiguous = heuristics.get('confidence_ambiguous', 0.55)
        
        # Context-Aware Idle Handling (PRIORITY 0)
        # Overrides ML for high-idle blocks (idle_ratio > 0.70)
        self.idle_ratio_threshold = heuristics.get('idle_ratio_threshold', 0.70)
        self.confidence_communication_listening = heuristics.get('confidence_communication_listening', 0.75)
        self.confidence_research_thinking = heuristics.get('confidence_research_thinking', 0.65)
        self.confidence_research_tutorial = heuristics.get('confidence_research_tutorial', 0.70)
        self.confidence_distracted_away = heuristics.get('confidence_distracted_away', 0.75)
        
        # Load app categorization from ML config (single source of truth)
        ml_config = self.config.get('ml_app_scoring', {})
        
        # App categories: from ML config (single source of truth)
        self.productivity_apps = set(ml_config.get('productive_apps', []))
        self.communication_apps = set(ml_config.get('communication_apps', []))
        self.distraction_apps = set(ml_config.get('distraction_apps', []))
        self.neutral_apps = set(ml_config.get('neutral_apps', []))
    
    def _calculate_app_score_time_weighted(self, app_sessions: list) -> float:
        """Calculate time-weighted app score from app sessions.
        
        Args:
            app_sessions: List of dicts with keys:
                - 'app_name': str
                - 'duration_sec': float
        
        Returns:
            Time-weighted app score (-1.0 to 1.0)
            
        Example:
            Sessions: [
                {'app_name': 'Code.exe', 'duration_sec': 240},  # 4 min IDE
                {'app_name': 'Spotify.exe', 'duration_sec': 60}   # 1 min Music
            ]
            Total: 300 sec
            Score: (1.0 * 240 + (-1.0) * 60) / 300 = 180 / 300 = 0.6 (Productive)
        """
        if not app_sessions:
            return 0.0
        
        total_duration = sum(sess.get('duration_sec', 0) for sess in app_sessions)
        if total_duration <= 0:
            return 0.0
        
        weighted_score = 0.0
        for session in app_sessions:
            app_name = session.get('app_name', '')
            duration = session.get('duration_sec', 0)
            
            # Classify app
            scores = {
                'productive': 1.0,
                'distraction': -1.0,
                'communication': 0.0,
                'neutral': 0.5,
            }
            app_category = self._classify_app(app_name)
            app_score = scores.get(app_category, 0.5)
            
            # Add weighted contribution
            weighted_score += app_score * duration
        
        return weighted_score / total_duration
    
    def _classify_app(self, app_name: str) -> str:
        """Classify an app as productive, distraction, or neutral.
        
        Args:
            app_name: Application name (e.g., "Code.exe", "Discord.exe")
        
        Returns:
            "productive", "distraction", or "neutral"
        """
        app_name_lower = app_name.lower() if app_name else ""
        
        # Check distraction apps first (high priority)
        if any(dist_app in app_name_lower for dist_app in self.distraction_apps):
            return "distraction"

        # Communication apps
        if any(comm_app in app_name_lower for comm_app in self.communication_apps):
            return "communication"
        
        # Check productivity apps
        if any(prod_app in app_name_lower for prod_app in self.productivity_apps):
            return "productive"
        
        return "neutral"

    def _touched_any(self, app_names: list, category_set: set) -> bool:
        if not app_names or not category_set:
            return False
        for app in app_names:
            app_lower = (app or "").lower()
            if any(token in app_lower for token in category_set):
                return True
        return False
    
    def is_distraction_app(self, app_name: str) -> bool:
        """Check if app is known to be a distraction/entertainment app.
        
        Public convenience method for BlockEvaluator and other modules to check
        if an app visit represents a distraction without doing full classification.
        
        Args:
            app_name: Application name or path (e.g., "Discord.exe")
        
        Returns:
            True if app is classified as distraction, False otherwise
        """
        if not app_name:
            return False
        app_name_lower = app_name.lower()
        return any(dist_app in app_name_lower for dist_app in self.distraction_apps)
    
    def detect_context(self, block_metrics: dict) -> Tuple[str, float]:
        """Evaluate developer's mental state using 8 signals, corrected for 5-min blocks.
        
        Args:
            block_metrics: Aggregated metrics from a 5-minute block containing:
                - typing_intensity: float - KPM (per minute)
                - deletion_key_presses, total_keystrokes - for correction_ratio
                - mouse_movement_distance: float - pixels (will divide by duration for px/sec)
                - mouse_click_rate: float - CPM
                - app_switch_count: int - app switches (PER BLOCK, not per hour)
                - app_sessions: list[dict] - with 'app_name' and 'duration_sec' keys
                - idle_duration_sec, total_duration_sec - for idle_ratio
                - consecutive_work_hours: float - consecutive work (session-based fatigue)
                - project_name: str or None - sticky project (key differentiator for distraction)
                - mouse_scroll_events: int (optional)
        
        Returns:
            Tuple of (context_state, confidence_score)
            
        Decision Tree (Priority Order):
            1. COMMUNICATION: Weighted app_score <= 0.2 AND (Slack/Teams/Discord)
            2. DEBUGGING: correction_ratio > 12% OR (8-12% AND typing < 40 KPM)
            3. RESEARCH: (mouse_velocity > 100 OR scroll_events > 15) AND typing < 30 AND app_score < 0.8
            4. FLOW: correction_ratio < 8% AND typing > 30 AND app_score > 0.5
            5. DISTRACTED: app_score < -0.5 OR switch_frequency > 10 (with sticky project override)
        """
        # ========== STEP 1: Extract all 8 signals ==========
        kpm = block_metrics.get('typing_intensity', 0)
        cpm = block_metrics.get('mouse_click_rate', 0)
        
        # Signal 1: correction_ratio (PRIMARY differentiator)
        # Edge case: If deletion_keys > total_keystrokes, cap at 1.0 (data inconsistency guard)
        total_keystrokes = block_metrics.get('total_keystrokes', 1)
        deletion_keys = block_metrics.get('deletion_key_presses', 0)
        correction_ratio = deletion_keys / total_keystrokes if total_keystrokes > 0 else 0.0
        correction_ratio = max(0.0, min(correction_ratio, 1.0))  # Clamp to [0, 1]
        
        # Signal 2: mouse_velocity (px/sec)
        total_duration_sec = block_metrics.get('total_duration_sec', 300)
        mouse_movement_distance = block_metrics.get('mouse_movement_distance', 0)
        mouse_velocity = mouse_movement_distance / total_duration_sec if total_duration_sec > 0 else 0
        
        # Signal 3: scroll events
        # Guard against negative or None values (shouldn't happen, but be defensive)
        scrolls = max(0, block_metrics.get('mouse_scroll_events', 0) or 0)
        
        # Signal 6: idle_ratio
        idle_duration_sec = block_metrics.get('idle_duration_sec', 0)
        idle_ratio = idle_duration_sec / max(total_duration_sec, 1)
        
        # Signal 7: consecutive_work_hours (session-based fatigue)
        consecutive_work_hours = block_metrics.get('consecutive_work_hours', 0.5)
        
        # Signal 4: app sessions and switches
        app_sessions = block_metrics.get('app_sessions', [])
        app_switches = block_metrics.get('app_switch_count', 0)
        project_name = block_metrics.get('project_name')
        
        # Calculate weighted app_score
        weighted_app_score = self._calculate_app_score_time_weighted(app_sessions)
        
        # ========== STEP 2: Apply Sticky Project Filter (The Intent Filter) ==========
        final_app_score = weighted_app_score
        
        # If user has a project open AND app is distraction (score < -0.5)
        # CRITICAL: Check not None AND not empty (empty strings are treated as no project)
        if project_name is not None and isinstance(project_name, str) and project_name.strip() and final_app_score < -0.5:
            # Safety Check: Is this Gaming? (High intensity = impossible for passive watching)
            if mouse_velocity > 250 or cpm > 60:
                # Even with project, gaming is gaming. Keep as distraction.
                pass
            else:
                # It's passive (watching tutorial). Upgrade to neutral/research.
                final_app_score = 0.5
        
        # Determine touched app categories
        app_names = [sess.get('app_name', '') for sess in app_sessions if sess.get('app_name')]
        touched_communication = self._touched_any(app_names, self.communication_apps)
        
        # ========== STEP 3: Decision Hierarchy (Priority Order) ==========
        
        # PRIORITY 0: CONTEXT-AWARE IDLE HANDLING (> idle_ratio_threshold)
        # "Where was the user when they went silent?"
        # Don't classify all idle as Distracted. Use app/project context to decide.
        if idle_ratio > self.idle_ratio_threshold:
            
            # Case A: MEETINGS / CALLS (The "Listening" State)
            # If in Zoom/Teams/Slack, user is likely listening to a call
            # Check: Communication app touched + low/neutral score
            if final_app_score <= 0.2 and final_app_score >= -0.2 and touched_communication:
                return "Communication", self.confidence_communication_listening
            
            # Case B: DEEP THINKING (The "Staring at Code" State)
            # If in VS Code/IDE (productive app, score > 0.8), user is designing/planning
            # We classify this as Research (Intake/Processing), not Flow (Creation)
            if final_app_score > 0.8:
                return "Research", self.confidence_research_thinking
            
            # Case C: STICKY PROJECT RESCUE (The "Tutorial/Waiting" State)
            # If user has an active Project, assume silence is work-related:
            # - Watching tutorial (YouTube + project_name)
            # - Reading long docs (Browser + project_name)
            # - Waiting for build (Terminal + project_name)
            # Innocent until proven guilty: project context = benefit of doubt
            # CRITICAL: Check not None AND not empty (empty strings shouldn't trigger)
            if project_name is not None and isinstance(project_name, str) and project_name.strip():
                return "Research", self.confidence_research_tutorial
            
            # Case D: TRUE DISTRACTION / AWAY (The "Lunch/Netflix" State)
            # No productive app + No project + High idle = User is gone or disengaged
            return "Distracted", self.confidence_distracted_away
        
        # PRIORITY 1: COMMUNICATION
        # Only if weighted score is near communication (0.0) AND comm app is touched
        if final_app_score <= 0.2 and touched_communication:
            base_confidence = self.confidence_communication
            if 40 <= kpm <= 100:
                base_confidence = min(0.90, base_confidence + 0.05)
            return "Communication", base_confidence
        
        # PRIORITY 2: DEBUGGING (Hard gate + ambiguous zone)
        # Hard gate: >12% corrections
        if correction_ratio > self.correction_ratio_debug_min:
            conf = self.confidence_debugging
            if app_switches >= self.switches_per_block_debug_min:
                conf = min(0.92, conf + 0.07)
            return "Debugging", conf
        
        # Ambiguous zone: 8-12% corrections
        # If in ambiguous zone AND typing is slow (<40 KPM), treat as DEBUGGING (struggling)
        if self.correction_ratio_flow_max <= correction_ratio <= self.correction_ratio_debug_min:
            if kpm < 40:
                # Slow + errors = struggling
                return "Debugging", self.confidence_debugging - 0.05
            # Else: Fast + errors = sloppy typing, will be caught by FLOW below
        
        # PRIORITY 3: RESEARCH (Updated with Real Data Insights)
        # Pattern 1: Low output + High input (traditional scrolling/reading)
        is_low_typing = kpm < 30
        is_high_scrolling = mouse_velocity > self.mouse_velocity_research_min or scrolls > 15
        is_reading_app = final_app_score < 0.8
        
        if is_high_scrolling and is_low_typing and is_reading_app:
            return "Research", self.confidence_research
        
        # Pattern 2: ZERO typing + HIGH clicking (NEW from real data analysis!)
        # Real data insight: This user researches by READING (0 KPM) + clicking links
        # Signal: typing ≈ 0 AND clicks > 8 CPM = Active reading/navigation
        # NOTE: This is USER-SPECIFIC! Other developers may type while reading.
        is_pure_reading = kpm < 2  # Near-zero typing (tolerance for rounding)
        is_high_clicking = cpm > self.mouse_cpm_research_light  # Clicking links, expanding code blocks
        
        if is_pure_reading and is_high_clicking and is_reading_app:
            return "Research", self.confidence_research
        
        # PRIORITY 4: FLOW
        # Confident typing + productive activity
        is_confident = correction_ratio < self.correction_ratio_flow_max
        is_productive = kpm > self.typing_kpm_flow_min
        is_working = final_app_score > self.app_score_flow_min
        
        if is_confident and is_productive and is_working:
            conf = self.confidence_flow
            
            # Fatigue adjustment
            if consecutive_work_hours > self.fatigue_high_hours:
                conf = self.confidence_flow_fatigued_high
            elif consecutive_work_hours > self.fatigue_moderate_hours:
                conf = self.confidence_flow_fatigued_moderate
            
            return "Flow", conf
        
        # PRIORITY 5: DISTRACTED
        # Off-task signals
        is_distraction_app = final_app_score < -0.5
        is_doomscrolling = app_switches > 10
        
        if is_distraction_app or is_doomscrolling:
            # But check sticky project: if project_name NOT NULL and NOT empty, it's research not distracted
            # CRITICAL: Empty strings should not trigger sticky project logic
            if project_name is not None and isinstance(project_name, str) and project_name.strip():
                return "Research", self.confidence_distracted_sticky_project
            return "Distracted", self.confidence_distracted
        
        # ========== FALLBACK: Ambiguous cases ==========
        if kpm >= self.typing_kpm_flow_min and final_app_score > 0.3:
            return "Flow", self.confidence_ambiguous
        if mouse_velocity > self.mouse_velocity_research_min and kpm < 30:
            return "Research", self.confidence_ambiguous
        if final_app_score < -0.5:
            return "Distracted", self.confidence_ambiguous
        
        # Ultimate fallback
        return "Flow", 0.50
    
    def describe_classification(self, block_metrics: dict, 
                               context_state: str, confidence: float) -> str:
        """Generate human-readable explanation of classification.
        
        Useful for debugging and understanding why a block was classified a certain way.
        
        Args:
            block_metrics: The metrics that were evaluated
            context_state: The resulting context state
            confidence: The confidence score
        
        Returns:
            Human-readable explanation string
        """
        kpm = block_metrics.get('typing_intensity', 0)
        cpm = block_metrics.get('mouse_click_rate', 0)
        scrolls = block_metrics.get('mouse_scroll_events', 0)
        app_switches = block_metrics.get('app_switch_count', 0)
        touched_distraction = block_metrics.get('touched_distraction_app', False)
        idle_ratio = block_metrics.get('idle_duration_sec', 0) / max(block_metrics.get('total_duration_sec', 1), 1)
        
        signals = []
        
        # Typing signal
        if kpm > 40:
            signals.append(f"high typing ({kpm:.1f} KPM)")
        elif kpm > 20:
            signals.append(f"moderate typing ({kpm:.1f} KPM)")
        elif kpm < 15:
            signals.append(f"low typing ({kpm:.1f} KPM)")
        
        # Click signal
        if cpm > 15:
            signals.append(f"frequent clicks ({cpm:.1f} CPM)")
        elif cpm > 10:
            signals.append(f"moderate clicks ({cpm:.1f} CPM)")
        
        # Scroll signal
        if scrolls > 5:
            signals.append(f"active scrolling ({scrolls} events)")
        
        # App switch signal (with app type distinction)
        if app_switches >= 3:
            if touched_distraction:
                signals.append(f"many app switches + distraction apps ({app_switches})")
            else:
                signals.append(f"many productivity app switches ({app_switches})")
        elif app_switches >= 2:
            if touched_distraction:
                signals.append(f"some switches + distraction apps ({app_switches})")
            else:
                signals.append(f"some productivity switches ({app_switches})")
        
        # Idle signal
        if idle_ratio > 0.5:
            signals.append(f"mostly idle ({idle_ratio:.0%})")
        elif idle_ratio > 0.2:
            signals.append(f"some idle time ({idle_ratio:.0%})")
        
        signal_str = " + ".join(signals) if signals else "minimal activity"
        
        return f"{context_state} ({confidence:.0%}): {signal_str}"
