"""Detect context state (Focused, Reading, Distracted, Idle) from behavioral metrics."""
from typing import Tuple, Optional


class ContextDetector:
    """Detect developer's mental context from 5-minute block metrics."""
    
    def __init__(self):
        """Initialize context detector."""
        pass
    
    def detect_context(self, block_metrics: dict) -> Tuple[str, float]:
        """Evaluate developer's mental state for a 5-minute block.
        
        Args:
            block_metrics: Aggregated metrics from a 5-minute block containing:
                - typing_intensity: float - KPM (keystrokes per minute) for this block
                - mouse_click_rate: float - CPM (clicks per minute) for this block
                - mouse_scroll_events: int - Total scrolls in the entire block
                - idle_duration_sec: int - Total seconds idle during block
                - total_duration_sec: int - Total block duration (typically 300 sec)
                - app_switch_count: int - Number of different apps touched
                - project_switch_count: int - Number of different projects touched
        
        Returns:
            Tuple of (context_state, confidence_score) where:
                - context_state: str - One of "Focused", "Reading", "Distracted", "Idle"
                - confidence_score: float - Confidence (0.0-1.0)
        
        Example:
            >>> metrics = {
            ...     'typing_intensity': 45.0,
            ...     'mouse_click_rate': 12.5,
            ...     'mouse_scroll_events': 2,
            ...     'idle_duration_sec': 10,
            ...     'total_duration_sec': 300,
            ...     'app_switch_count': 1,
            ...     'project_switch_count': 1,
            ... }
            >>> detector.detect_context(metrics)
            ('Focused', 0.92)
        """
        # Extract metrics
        idle_duration = block_metrics.get('idle_duration_sec', 0)
        total_duration = block_metrics.get('total_duration_sec', 1)
        kpm = block_metrics.get('typing_intensity', 0)
        cpm = block_metrics.get('mouse_click_rate', 0)
        scrolls = block_metrics.get('mouse_scroll_events', 0)
        app_switches = block_metrics.get('app_switch_count', 0)
        project_switches = block_metrics.get('project_switch_count', 0)
        
        # Calculate ratios
        idle_ratio = idle_duration / max(total_duration, 1)
        
        # --- DECISION TREE (in priority order) ---
        
        # 1. HIGH IDLE RATIO: Developer away from keyboard
        if idle_ratio > 0.5:
            return "Idle", 0.85
        
        # 2. READING: Low typing, low clicks, but active scrolling
        #    (Suggests reading documentation/articles)
        if (kpm < 20 and cpm < 10 and scrolls > 5):
            return "Reading", 0.80
        
        # 3. FOCUSED: High typing, moderate clicks, few app switches
        #    (Suggests deep work on single project)
        if kpm > 40 and cpm > 15 and app_switches <= 2:
            return "Focused", 0.92
        
        # 4. HIGHLY DISTRACTED: Many app switches (≥ 3) throughout block
        #    (Suggests frequent context switching)
        if app_switches >= 3:
            return "Distracted", 0.70
        
        # 5. MODERATELY DISTRACTED: Moderate typing but high project switching
        #    (Suggests hopping between projects)
        if project_switches >= 3 and kpm < 30:
            return "Distracted", 0.65
        
        # 6. MODERATE ACTIVITY: Balanced typing/clicking but some distraction signals
        #    (Suggests active development with some interruptions)
        if (kpm > 20 and cpm > 10 and app_switches >= 2):
            return "Distracted", 0.70
        
        # 7. LIGHT ACTIVITY: Low typing, low clicking, minimal scrolling
        #    (Suggests thinking/pausing or very light work)
        if (kpm < 15 and cpm < 8 and scrolls <= 2):
            return "Idle", 0.60
        
        # 8. DEFAULT FALLBACK: Unclassified activity
        return "Idle", 0.50
    
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
        idle_ratio = block_metrics.get('idle_duration_sec', 0) / max(block_metrics.get('total_duration_sec', 1), 1)
        
        signals = []
        if kpm > 40:
            signals.append(f"high typing ({kpm:.1f} KPM)")
        elif kpm > 20:
            signals.append(f"moderate typing ({kpm:.1f} KPM)")
        elif kpm < 15:
            signals.append(f"low typing ({kpm:.1f} KPM)")
        
        if cpm > 15:
            signals.append(f"frequent clicks ({cpm:.1f} CPM)")
        elif cpm > 10:
            signals.append(f"moderate clicks ({cpm:.1f} CPM)")
        
        if scrolls > 5:
            signals.append(f"active scrolling ({scrolls} events)")
        
        if app_switches >= 3:
            signals.append(f"many app switches ({app_switches})")
        elif app_switches >= 2:
            signals.append(f"some app switches ({app_switches})")
        
        if idle_ratio > 0.5:
            signals.append(f"mostly idle ({idle_ratio:.0%})")
        
        signal_str = " + ".join(signals) if signals else "minimal activity"
        
        return f"{context_state} ({confidence:.0%}): {signal_str}"
