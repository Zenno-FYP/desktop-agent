"""Detect idle time based on user activity."""
import time
from typing import Tuple


class IdleDetector:
    """Detect idle periods by monitoring user activity."""

    def __init__(self, idle_threshold_sec: int = 5):
        """Initialize idle detector.
        
        Args:
            idle_threshold_sec: seconds of inactivity to consider idle (default 5)
        """
        self.idle_threshold_sec = idle_threshold_sec
        self.last_activity_time = time.time()
        self.idle_start_time = None
        self.total_idle_sec = 0
        self.session_start_time = time.time()

    def update_activity(self, activity_time: float = None):
        """Record user activity timestamp.
        
        Args:
            activity_time: timestamp of activity (default: now)
        """
        if activity_time is None:
            activity_time = time.time()
        
        was_idle = self.is_idle()
        self.last_activity_time = activity_time
        
        # If transitioning from idle to active, record idle duration
        if was_idle and self.idle_start_time:
            idle_duration = activity_time - self.idle_start_time
            self.total_idle_sec += idle_duration
            self.idle_start_time = None

    def is_idle(self) -> bool:
        """Check if currently in idle state.
        
        Returns:
            True if no activity for >= idle_threshold_sec
        """
        current_time = time.time()
        inactivity_duration = current_time - self.last_activity_time
        return inactivity_duration >= self.idle_threshold_sec

    def check_and_accumulate_idle(self) -> float:
        """Check current idle duration and accumulate it.
        
        Returns:
            Accumulated idle duration in seconds
        """
        current_time = time.time()
        inactivity_duration = current_time - self.last_activity_time
        
        # If idle and idle_start_time not set, start tracking
        if inactivity_duration >= self.idle_threshold_sec:
            if self.idle_start_time is None:
                self.idle_start_time = current_time - inactivity_duration
            # Update accumulated idle time (from idle start to now)
            self.total_idle_sec = current_time - self.idle_start_time
        else:
            # User is active, finalize any idle period
            if self.idle_start_time is not None:
                idle_duration = current_time - self.idle_start_time
                self.total_idle_sec = max(self.total_idle_sec, idle_duration)
                self.idle_start_time = None
        
        return self.total_idle_sec

    def get_idle_metrics(self) -> dict:
        """Get comprehensive idle metrics for current session.
        
        Returns:
            dict with idle_duration_sec and idle_ratio
        """
        session_duration = time.time() - self.session_start_time
        idle_duration = self.check_and_accumulate_idle()
        
        # Clamp idle to not exceed session duration
        idle_duration = min(idle_duration, session_duration)
        
        idle_ratio = idle_duration / max(session_duration, 1)
        
        return {
            'idle_duration_sec': int(idle_duration),
            'idle_ratio': round(idle_ratio, 3),
            'is_currently_idle': self.is_idle(),
        }

    def reset(self):
        """Reset idle detector for new session."""
        self.last_activity_time = time.time()
        self.idle_start_time = None
        self.total_idle_sec = 0
        self.session_start_time = time.time()
