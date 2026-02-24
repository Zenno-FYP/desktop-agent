"""
Feature Extractor for ML Model

Extracts 9 features from 5-minute block_metrics dictionary.
Used by both synthetic data generator and ML predictor.

Features:
  1. typing_intensity (KPM) - Keyboard activity
  2. click_rate (CPM) - Mouse click activity
  3. scrolls - Mouse scroll events
  4. idle_ratio - Proportion of time idle
  5. app_switches - Number of application switches
  6. project_switches - Number of project switches
  7. touched_distraction - Binary: touched Discord/Twitter/etc
  8. time_of_day - Hour of day (0-23)
  9. day_of_week - Day of week (0-6)
"""

import numpy as np
from datetime import datetime


class FeatureExtractor:
    """Extract ML features from block_metrics dictionary."""
    
    @staticmethod
    def extract_features(block_metrics):
        """
        Convert block_metrics dict (from BlockEvaluator) to feature vector.
        
        Args:
            block_metrics (dict): Contains:
                - typing_intensity: KPM (float)
                - mouse_click_rate: CPM (float)
                - mouse_scroll_events: Count (int)
                - idle_duration_sec: Accumulated idle time (float)
                - total_duration_sec: Block duration in seconds (float)
                - app_switch_count: Number of app switches (int)
                - project_switch_count: Number of project switches (int)
                - touched_distraction_app: Boolean (bool)
                - end_time: End datetime of block (datetime)
        
        Returns:
            np.ndarray: Feature vector [kpm, cpm, scrolls, idle_ratio, 
                                       app_sw, proj_sw, distraction_bool, hour, dow]
        
        Example:
            >>> metrics = {
            ...     'typing_intensity': 45.3,
            ...     'mouse_click_rate': 12.5,
            ...     'mouse_scroll_events': 8,
            ...     'idle_duration_sec': 45,
            ...     'total_duration_sec': 300,
            ...     'app_switch_count': 2,
            ...     'project_switch_count': 1,
            ...     'touched_distraction_app': False,
            ...     'end_time': datetime(2026, 2, 24, 14, 5),
            ... }
            >>> features = FeatureExtractor.extract_features(metrics)
            >>> features.shape
            (9,)
        """
        # 1. Typing Intensity (KPM)
        typing_intensity = float(block_metrics.get('typing_intensity', 0))
        
        # 2. Click Rate (CPM)
        click_rate = float(block_metrics.get('mouse_click_rate', 0))
        
        # 3. Scroll Events
        scrolls = float(block_metrics.get('mouse_scroll_events', 0))
        
        # 4. Idle Ratio (0.0 to 1.0)
        idle_duration = float(block_metrics.get('idle_duration_sec', 0))
        total_duration = float(block_metrics.get('total_duration_sec', 300))
        idle_ratio = idle_duration / max(total_duration, 1)
        
        # 5. App Switch Count
        app_switches = float(block_metrics.get('app_switch_count', 0))
        
        # 6. Project Switch Count
        project_switches = float(block_metrics.get('project_switch_count', 0))
        
        # 7. Touched Distraction App (0.0 or 1.0)
        touched_distraction = float(block_metrics.get('touched_distraction_app', False))
        
        # 8. Time of Day (0-23)
        end_time = block_metrics.get('end_time')
        if isinstance(end_time, datetime):
            time_of_day = float(end_time.hour)
        elif isinstance(end_time, str):
            # Parse ISO format string
            end_time = datetime.fromisoformat(end_time)
            time_of_day = float(end_time.hour)
        else:
            time_of_day = 12.0  # Default to noon
        
        # 9. Day of Week (0-6, Monday-Sunday)
        if isinstance(end_time, datetime):
            day_of_week = float(end_time.weekday())
        elif isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)
            day_of_week = float(end_time.weekday())
        else:
            day_of_week = 2.0  # Default to Wednesday
        
        # Return as numpy array
        return np.array([
            typing_intensity,
            click_rate,
            scrolls,
            idle_ratio,
            app_switches,
            project_switches,
            touched_distraction,
            time_of_day,
            day_of_week,
        ], dtype=np.float32)
    
    @staticmethod
    def extract_features_batch(block_metrics_list):
        """
        Extract features from multiple blocks at once.
        
        Args:
            block_metrics_list (list): List of block_metrics dicts
            
        Returns:
            np.ndarray: Shape (num_blocks, 9) feature matrix
        """
        features = []
        for metrics in block_metrics_list:
            features.append(FeatureExtractor.extract_features(metrics))
        return np.array(features, dtype=np.float32)
    
    @staticmethod
    def get_feature_names():
        """Return list of feature names."""
        return [
            'typing_intensity',
            'click_rate',
            'scrolls',
            'idle_ratio',
            'app_switches',
            'project_switches',
            'touched_distraction',
            'time_of_day',
            'day_of_week',
        ]
    
    @staticmethod
    def validate_features(features):
        """
        Validate feature vector for bounds and NaN values.
        
        Args:
            features (np.ndarray): Feature vector to validate
            
        Returns:
            bool: True if valid, False otherwise
        """
        # Check for NaN or inf
        if not np.isfinite(features).all():
            return False
        
        # Check reasonable bounds
        bounds = {
            0: (0, 200),           # typing_intensity KPM
            1: (0, 200),           # click_rate CPM
            2: (0, 500),           # scrolls
            3: (0, 1),             # idle_ratio
            4: (0, 50),            # app_switches
            5: (0, 20),            # project_switches
            6: (0, 1),             # touched_distraction (binary)
            7: (0, 24),            # time_of_day (hours)
            8: (0, 7),             # day_of_week
        }
        
        for i, (min_val, max_val) in bounds.items():
            if not (min_val <= features[i] <= max_val):
                return False
        
        return True


def main():
    """Test feature extraction."""
    print("Testing Feature Extractor...")
    
    # Test with sample block_metrics
    sample_metrics = {
        'typing_intensity': 45.3,
        'mouse_click_rate': 12.5,
        'mouse_scroll_events': 8,
        'idle_duration_sec': 45,
        'total_duration_sec': 300,
        'app_switch_count': 2,
        'project_switch_count': 1,
        'touched_distraction_app': False,
        'end_time': datetime(2026, 2, 24, 14, 5),
    }
    
    features = FeatureExtractor.extract_features(sample_metrics)
    print(f"✅ Extracted features: {features}")
    print(f"✅ Feature count: {len(features)}")
    print(f"✅ Feature names: {FeatureExtractor.get_feature_names()}")
    print(f"✅ Valid features: {FeatureExtractor.validate_features(features)}")


if __name__ == '__main__':
    main()
