"""
Feature Extractor for ML Model

Extracts 8 psychological signals from a block_metrics dictionary.
Used by both synthetic data generator and ML predictor.

8 Raw Signals (Psychology-Based):
  1. typing_kpm - Keystrokes per minute (memory dump=high >120, thinking=50-100, reading=<30)
  2. correction_ratio - Deletions/Total keystrokes (confidence <0.05, trial&error >0.15)
  3. mouse_px_per_sec - Cursor velocity (creation <10, reading >100)
  4. mouse_cpm - Clicks per minute (logic work <5, navigation 20-40, gaming >60)
  5. switch_freq - Window switches/min (deep focus <1, context loop 2-8, distraction >10)
  6. app_score - App productivity weight (-1.0 distraction, 0.0 communication, 0.5 neutral, 1.0 productive)
  7. idle_ratio - Idle time percentage (high intensity 0.0, thinking 0.1-0.2, away >0.5)
  8. fatigue_hrs - Hours since day start (0-16 biological context)
"""

import numpy as np
from datetime import datetime
import os
import yaml


class FeatureExtractor:
    """Extract 8-signal features from block_metrics dictionary."""
    
    # Class-level app categories (loaded from config)
    _distraction_apps = None
    _communication_apps = None
    _productive_apps = None
    _neutral_apps = None
    _config_loaded = False
    
    # Browser detection (loaded from config)
    _browsers = None
    _service_keywords = None
    _service_scores = None  # Maps detected service → score
    
    @classmethod
    def _load_app_categories_from_config(cls):
        """Load app categories and browser detection from config.yaml.
        
        Configuration structure in config.yaml:
        ml_app_scoring:
          productive_apps: [list of apps]
          communication_apps: [list of apps]
          distraction_apps: [list of apps]
          neutral_apps: [list of apps]
        
        browser_detection:
          browsers: [list of browser processes]
          service_keywords: {keyword: display_name}
        """
        if cls._config_loaded:
            return  # Already loaded
        
        try:
            # Find config.yaml path (relative to this file)
            config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(config_dir, 'config', 'config.yaml')
            
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                
                # Load ML app scoring categories
                ml_scoring = config.get('ml_app_scoring', {})
                cls._productive_apps = set(
                    app.lower() for app in ml_scoring.get('productive_apps', [])
                )
                cls._communication_apps = set(
                    app.lower() for app in ml_scoring.get('communication_apps', [])
                )
                cls._distraction_apps = set(
                    app.lower() for app in ml_scoring.get('distraction_apps', [])
                )
                cls._neutral_apps = set(
                    app.lower() for app in ml_scoring.get('neutral_apps', [])
                )
                
                # Load browser detection config
                browser_cfg = config.get('browser_detection', {})
                cls._browsers = set(app.lower() for app in browser_cfg.get('browsers', []))
                cls._service_keywords = browser_cfg.get('service_keywords', {})
                
                # Build service score mapping (detected services → productivity scores)
                cls._service_scores = cls._build_service_scores(ml_scoring)
                
                print(f"✅ App categories and browser detection loaded from config: {config_path}")
            else:
                # Config not found, use empty sets
                cls._use_empty_categories()
                print(f"⚠️  Config not found at {config_path}, using empty app categories")
        except Exception as e:
            # Error loading config, fall back to empty sets
            print(f"⚠️  Error loading app categories from config: {e}, using empty app categories")
            cls._use_empty_categories()
        finally:
            cls._config_loaded = True
    
    @classmethod
    def _build_service_scores(cls, ml_scoring):
        """Build a mapping of detected service names to productivity scores.
        
        Maps service keywords (from config) to app scoring categories.
        Example: 'GitHub' → 1.0 (productive), 'YouTube' → -1.0 (distraction)
        
        Args:
            ml_scoring: ml_app_scoring section from config
            
        Returns:
            dict: Maps service display_name → productivity score
        """
        service_scores = {}
        
        # Map each service keyword to its category score
        for keyword, service_name in (cls._service_keywords or {}).items():
            service_lower = service_name.lower()
            
            # Check which category this service belongs to
            if any(prod in service_lower for prod in ml_scoring.get('productive_apps', [])):
                service_scores[service_name] = 1.0  # Productive
            elif any(comm in service_lower for comm in ml_scoring.get('communication_apps', [])):
                service_scores[service_name] = 0.0  # Communication
            elif any(dist in service_lower for dist in ml_scoring.get('distraction_apps', [])):
                service_scores[service_name] = -1.0  # Distraction
            else:
                # Auto-classify based on service name
                # Development tools → productive
                if any(x in service_lower for x in ['github', 'gitlab', 'bitbucket', 'swagger', 'postman', 'insomnia', 'stack overflow', 'aws', 'azure', 'google cloud']):
                    service_scores[service_name] = 1.0
                # Social media / entertainment → distraction
                elif any(x in service_lower for x in ['youtube', 'reddit', 'twitter', 'instagram', 'tiktok', 'facebook']):
                    service_scores[service_name] = -1.0
                # Communication / work tools → neutral or communication
                elif any(x in service_lower for x in ['slack', 'notion', 'figma', 'linkedin', 'medium']):
                    service_scores[service_name] = 0.5
                # Default to neutral
                else:
                    service_scores[service_name] = 0.5
        
        return service_scores
    
    @classmethod
    def _use_empty_categories(cls):
        """Use empty sets for app categories (all apps become neutral)."""
        cls._productive_apps = set()
        cls._communication_apps = set()
        cls._distraction_apps = set()
        cls._neutral_apps = set()
    
    
    @staticmethod
    def get_app_score(app_name, browser_context=None):
        """Determine productivity score for an app.
        
        App categories are loaded from config.yaml (ml_app_scoring section):
        - Distraction apps: -1.0 (Discord, Twitter, Reddit, etc.)
        - Communication apps: 0.0 (Slack, Teams, Zoom, etc.)
        - Productive apps: 1.0 (VSCode, PyCharm, etc.)
        - Neutral apps: 0.5 (Chrome, Firefox, GitHub, etc.)
        
        Browser Tab Detection:
        For browser apps (Chrome, Firefox, etc.), if browser_context is provided,
        checks service keywords (from browser tab/URL) to refine scoring:
        - GitHub, AWS, Stack Overflow → 1.0 (productive)
        - YouTube, Reddit, Twitter → -1.0 (distraction)
        - Slack, Notion, Medium → 0.5 (neutral/work)
        - ChatGPT, Copilot → 0.5-1.0 (work-related)
        
        Scoring mechanism:
        1. If browser + keywords found: use service-based score
        2. Check distraction_apps (highest penalty)
        3. Check communication_apps (zero impact)
        4. Check productive_apps (positive impact)  
        5. Check neutral_apps (moderate impact)
        6. Default to 0.5 (neutral) if no match
        
        Args:
            app_name: Application name (case-insensitive)
            browser_context: Optional string (URL/tab name) for browser classification
            
        Returns:
            float: Score from -1.0 (distraction) to 1.0 (productive)
        """
        if not app_name:
            return 0.5  # Neutral default
        
        # Ensure app categories and browser detection are loaded from config
        FeatureExtractor._load_app_categories_from_config()
        
        app_lower = app_name.lower()
        
        # Browser Tab Detection: If it's a browser and we have context, check service keywords
        if browser_context and app_lower in FeatureExtractor._browsers:
            context_lower = browser_context.lower()
            
            # Check service keywords in order (more specific first)
            for keyword, service_name in FeatureExtractor._service_keywords.items():
                if keyword in context_lower:
                    # Found a service keyword, use its pre-computed score
                    service_score = FeatureExtractor._service_scores.get(service_name, 0.5)
                    return float(service_score)
        
        # Standard app category matching (non-browser or no browser context)
        # Check category membership using substring matching (case-insensitive)
        if any(dist in app_lower for dist in FeatureExtractor._distraction_apps):
            return -1.0
        elif any(comm in app_lower for comm in FeatureExtractor._communication_apps):
            return 0.0
        elif any(prod in app_lower for prod in FeatureExtractor._productive_apps):
            return 1.0
        elif any(neutr in app_lower for neutr in FeatureExtractor._neutral_apps):
            return 0.5
        
        # Default: neutral
        return 0.5
    
    @staticmethod
    def extract_features(block_metrics):
        """
        Convert block_metrics dict (from BlockEvaluator) to 8-signal feature vector.
        
        Args:
            block_metrics (dict): Contains:
                - typing_intensity: KPM (float, required)
                - mouse_click_rate: CPM (float, required)
                - deletion_key_presses: Count of deletion keys (int, optional)
                - idle_duration_sec: Accumulated idle time (float, required)
                - total_duration_sec: Block duration in seconds (float, required)
                - app_switch_count: Number of app switches (int, optional)
                - total_keystrokes: Total keystrokes in block (int, optional - calculated if missing)
                - app_names: List of apps used (list, optional - for app_score)
                - mouse_movement_distance: Total pixels cursor moved (float, optional - for velocity)
                - browser_context: Browser tab/URL name (str, optional - for browser service detection)
                - active_file: Browser URL/tab (str, optional - fallback for browser_context)
                - consecutive_work_hours: Hours worked since last break (float, optional - for fatigue_hrs)
                  (Resets when idle > 30 min; default 0.5 if missing)
        
        Returns:
            np.ndarray: 8-dimensional feature vector [
                typing_kpm,               # 0
                correction_ratio,         # 1
                mouse_velocity_px_per_sec,# 2 (actual cursor velocity from movement distance)
                mouse_cpm,                # 3 (click-based interaction: clicks/min)
                switch_freq,              # 4
                app_score,                # 5 (considers browser tab keywords)
                idle_ratio,               # 6
                fatigue_hrs               # 7 (consecutive work hours since last break, 0-24)
            ]
            
            NOTE: Signal 2 now uses actual mouse_movement_distance (pixels) when available.
            Signal 7 is consecutive work hours (resets after 30+ min idle), not time-of-day.
        """
        # 1. Typing KPM (keystrokes per minute)
        typing_kpm = float(block_metrics.get('typing_intensity', 0))
        
        # 4. Mouse CPM (clicks per minute)
        mouse_cpm = float(block_metrics.get('mouse_click_rate', 0))
        
        # Calculate total duration and keystrokes first
        total_duration_sec = float(block_metrics.get('total_duration_sec', 1))
        total_duration_min = max(total_duration_sec / 60, 0.01)  # Avoid divide by zero
        
        total_keystrokes = block_metrics.get('total_keystrokes')
        if total_keystrokes is None:
            # Estimate from KPM * duration
            total_keystrokes = int((typing_kpm * total_duration_min))
        else:
            total_keystrokes = float(total_keystrokes)
        
        deletion_key_presses = float(block_metrics.get('deletion_key_presses', 0))
        
        # 1. Correction Ratio (deletions / total keystrokes)
        if total_keystrokes > 0:
            correction_ratio = deletion_key_presses / total_keystrokes
        else:
            correction_ratio = 0.0
        correction_ratio = float(np.clip(correction_ratio, 0, 1))
        
        # 2. Mouse Velocity (pixels per second from actual cursor movement)
        # NEW: Use actual mouse_movement_distance when available
        mouse_movement_distance = float(block_metrics.get('mouse_movement_distance', 0))
        if total_duration_sec > 0:
            mouse_velocity_px_per_sec = mouse_movement_distance / total_duration_sec
        else:
            mouse_velocity_px_per_sec = 0.0
        
        # Fallback to use CPM if distance not available
        if mouse_movement_distance == 0 and mouse_cpm > 0:
            # Estimate velocity from click rate (2.5 px per click is rough estimate)
            mouse_velocity_px_per_sec = mouse_cpm * 2.5 / 60  # Convert CPM to px/sec
        
        mouse_velocity_px_per_sec = float(np.clip(mouse_velocity_px_per_sec, 0, 500))
        app_switch_count = float(block_metrics.get('app_switch_count', 0))
        switch_freq = (app_switch_count / total_duration_min) if total_duration_min > 0 else 0.0
        switch_freq = float(np.clip(switch_freq, 0, 20))  # Cap at 20 switches/min (theoretical max)
        
        # 6. App Score (productivity weight -1.0 to 1.0)
        # TIME-WEIGHTED calculation from app_sessions list
        # Matches block_evaluator.py and synthetic_data_generator.py time-weighting approach
        app_sessions = block_metrics.get('app_sessions', [])
        browser_context = block_metrics.get('browser_context') or block_metrics.get('active_file')
        
        if app_sessions:
            # Calculate TIME-WEIGHTED app score: (score1*duration1 + score2*duration2) / total_duration
            weighted_score = 0.0
            total_duration = 0
            for session in app_sessions:
                app_name = session.get('app_name', '')
                duration_sec = session.get('duration_sec', 0)
                if app_name and duration_sec > 0:
                    # If this app is a browser and we have context, use it
                    app_lower = app_name.lower()
                    if app_lower in (FeatureExtractor._browsers or set()) and browser_context:
                        score = FeatureExtractor.get_app_score(app_name, browser_context)
                    else:
                        score = FeatureExtractor.get_app_score(app_name)
                    weighted_score += score * duration_sec
                    total_duration += duration_sec
            
            if total_duration > 0:
                app_score = float(weighted_score / total_duration)
            else:
                app_score = 0.5  # Neutral default if no sessions
        else:
            # Fallback: try app_names list (simple average, legacy)
            app_names = block_metrics.get('app_names', [])
            if app_names:
                scores = []
                for app in app_names:
                    app_lower = app.lower()
                    if app_lower in (FeatureExtractor._browsers or set()) and browser_context:
                        score = FeatureExtractor.get_app_score(app, browser_context)
                    else:
                        score = FeatureExtractor.get_app_score(app)
                    scores.append(score)
                app_score = float(np.mean(scores)) if scores else 0.5
            elif block_metrics.get('touched_distraction_app', False):
                app_score = -1.0  # Was marked as distraction
            else:
                app_score = 0.5  # Neutral default
        
        app_score = float(np.clip(app_score, -1.0, 1.0))
        
        # 7. Idle Ratio (proportion of time idle, 0.0 to 1.0)
        idle_duration_sec = float(block_metrics.get('idle_duration_sec', 0))
        idle_ratio = idle_duration_sec / max(total_duration_sec, 1)
        idle_ratio = float(np.clip(idle_ratio, 0, 1))
        
        # 8. Fatigue Hours (consecutive work hours, 0-24)
        # Tracks how long user has been working continuously since last break
        # Resets when idle > 30 minutes
        # Default to 0.5 hours if not provided
        consecutive_work_hours = block_metrics.get('consecutive_work_hours', 0.5)
        fatigue_hrs = float(consecutive_work_hours)
        fatigue_hrs = float(np.clip(fatigue_hrs, 0, 24))
        
        # Return as numpy array
        return np.array([
            typing_kpm,
            correction_ratio,
            mouse_velocity_px_per_sec,    # Index 2: actual cursor velocity (pixels/sec)
            mouse_cpm,                     # Index 3: mouse CPM (clicks/min)
            switch_freq,
            app_score,
            idle_ratio,
            fatigue_hrs,
        ], dtype=np.float32)
    
    @staticmethod
    def extract_features_batch(block_metrics_list):
        """
        Extract 8 features from multiple blocks at once.
        
        Args:
            block_metrics_list (list): List of block_metrics dicts
            
        Returns:
            np.ndarray: Shape (num_blocks, 8) feature matrix
        """
        features = []
        for metrics in block_metrics_list:
            features.append(FeatureExtractor.extract_features(metrics))
        return np.array(features, dtype=np.float32)
    
    @staticmethod
    def get_feature_names():
        """Return list of 8 feature names (psychological signals)."""
        return [
            'typing_kpm',
            'correction_ratio',
            'mouse_px_per_sec',
            'mouse_cpm',
            'switch_freq',
            'app_score',
            'idle_ratio',
            'fatigue_hrs',
        ]

    @staticmethod
    def validate_features(features):
        """
        Validate 8-signal feature vector for bounds and NaN values.
        
        Args:
            features (np.ndarray): Feature vector to validate (should be length 8)
            
        Returns:
            bool: True if valid, False otherwise
        """
        # Check shape
        if features.shape != (8,):
            return False
        
        # Check for NaN or inf
        if not np.isfinite(features).all():
            return False
        
        # Check reasonable bounds for 8 signals
        bounds = [
            (0, 200),        # 0: typing_kpm (0-200 KPM)
            (0, 1),          # 1: correction_ratio (0-100%)
            (0, 500),        # 2: mouse_px_per_sec (0-500 px/sec)
            (0, 200),        # 3: mouse_cpm (0-200 clicks/min)
            (0, 20),         # 4: switch_freq (0-20 switches/min)
            (-1, 1),         # 5: app_score (-1.0 to 1.0)
            (0, 1),          # 6: idle_ratio (0-100%)
            (0, 24),         # 7: fatigue_hrs (0-24 hours)
        ]
        
        for i, (min_val, max_val) in enumerate(bounds):
            if not (min_val <= features[i] <= max_val):
                return False
        
        return True



def main():
    """Test 8-signal feature extraction with browser tab detection."""
    print("Testing 8-Signal Feature Extractor with Browser Tab Detection...")
    print()
    
    # Test 1: Standard app scoring
    print("=" * 70)
    print("TEST 1: Standard App Scoring (No Browser Context)")
    print("=" * 70)
    sample_metrics = {
        'typing_intensity': 75.5,
        'mouse_click_rate': 18.3,
        'deletion_key_presses': 45,
        'total_keystrokes': 900,
        'idle_duration_sec': 60,
        'total_duration_sec': 300,
        'app_switch_count': 3,
        'app_names': ['VSCode', 'Chrome', 'Slack'],
        'touched_distraction_app': False,
        'end_time': datetime(2026, 2, 24, 14, 5),
    }
    
    features = FeatureExtractor.extract_features(sample_metrics)
    print(f"App names: {sample_metrics['app_names']}")
    print(f"app_score: {features[5]:.2f} (average of app scores)")
    print(f"✅ Features valid: {FeatureExtractor.validate_features(features)}")
    print()
    
    # Test 2: Browser tab detection for productive work
    print("=" * 70)
    print("TEST 2: Browser Tab Detection - Productive (GitHub)")
    print("=" * 70)
    github_metrics = {
        'typing_intensity': 85.0,
        'mouse_click_rate': 20.0,
        'deletion_key_presses': 30,
        'total_duration_sec': 300,
        'app_switch_count': 2,
        'app_names': ['Chrome'],
        'browser_context': 'github.com - awesome-python-project',  # GitHub keyword
        'idle_duration_sec': 20,
        'end_time': datetime(2026, 2, 24, 14, 0),
    }
    
    features_github = FeatureExtractor.extract_features(github_metrics)
    print(f"App: Chrome")
    print(f"Browser context: {github_metrics['browser_context']}")
    print(f"app_score: {features_github[5]:.2f} (GitHub detected → productive)")
    print(f"✅ Features valid: {FeatureExtractor.validate_features(features_github)}")
    print()
    
    # Test 3: Browser tab detection for distraction
    print("=" * 70)
    print("TEST 3: Browser Tab Detection - Distraction (YouTube)")
    print("=" * 70)
    youtube_metrics = {
        'typing_intensity': 30.0,
        'mouse_click_rate': 45.0,
        'deletion_key_presses': 5,
        'total_duration_sec': 300,
        'app_switch_count': 8,
        'app_names': ['Firefox'],
        'browser_context': 'youtube.com - Cat Videos',  # YouTube keyword
        'idle_duration_sec': 120,
        'end_time': datetime(2026, 2, 24, 14, 0),
    }
    
    features_youtube = FeatureExtractor.extract_features(youtube_metrics)
    print(f"App: Firefox")
    print(f"Browser context: {youtube_metrics['browser_context']}")
    print(f"app_score: {features_youtube[5]:.2f} (YouTube detected → distraction)")
    print(f"✅ Features valid: {FeatureExtractor.validate_features(features_youtube)}")
    print()
    
    # Test 4: Browser tab detection for communication
    print("=" * 70)
    print("TEST 4: Browser Tab Detection - Communication (Slack)")
    print("=" * 70)
    slack_metrics = {
        'typing_intensity': 50.0,
        'mouse_click_rate': 15.0,
        'deletion_key_presses': 10,
        'total_duration_sec': 300,
        'app_switch_count': 1,
        'app_names': ['Chrome'],
        'browser_context': 'slack.com - dev-team #general',  # Slack keyword
        'idle_duration_sec': 60,
        'end_time': datetime(2026, 2, 24, 14, 0),
    }
    
    features_slack = FeatureExtractor.extract_features(slack_metrics)
    print(f"App: Chrome")
    print(f"Browser context: {slack_metrics['browser_context']}")
    print(f"app_score: {features_slack[5]:.2f} (Slack detected → communication)")
    print(f"✅ Features valid: {FeatureExtractor.validate_features(features_slack)}")
    print()
    
    # Summary
    print("=" * 70)
    print("✅ Feature Extraction with Browser Tab Detection Complete")
    print("=" * 70)
    print(f"Feature names: {FeatureExtractor.get_feature_names()}")
    print()
    print("Browser Tab Detection enabled:")
    print("  • GitHub/GitLab/AWS → 1.0 (productive)")
    print("  • YouTube/Reddit/Twitter → -1.0 (distraction)")
    print("  • Slack/Notion → 0.5 (neutral/work)")
    print("  • All services configured in: config/config.yaml")


if __name__ == '__main__':
    main()
