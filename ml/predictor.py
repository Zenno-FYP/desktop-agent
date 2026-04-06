"""
ML Predictor - Deployment Ready

Loads trained XGBoost model and provides predictions with confidence scores.
Used by BlockEvaluator to replace heuristic-based context detection.

Features:
- Load trained model from disk
- Predict context_state from block_metrics
- Return confidence scores using predict_proba
- Fallback to heuristic if model unavailable
"""

import logging
import joblib
import numpy as np
from pathlib import Path
from datetime import datetime

try:
    from ml.feature_extractor import FeatureExtractor
except ModuleNotFoundError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ml.feature_extractor import FeatureExtractor


# Configure logging for this module
logger = logging.getLogger(__name__)


class MLPredictor:
    """Load and use trained ML model for context prediction."""
    
    def __init__(self, model_path='data/models/context_detector.pkl'):
        """
        Initialize predictor by loading trained model.
        
        Args:
            model_path (str): Path to trained model file
            
        Raises:
            FileNotFoundError: If model file doesn't exist
        """
        self.model_path = model_path
        self.model = None
        self.label_decoder = None  # Will be loaded dynamically from file
        
        self.load_model()
    
    def load_model(self):
        """Load trained model and class labels from disk."""
        if not Path(self.model_path).exists():
            raise FileNotFoundError(f"Model not found at {self.model_path}")
        
        self.model = joblib.load(self.model_path)
        
        # NEW: Load the dynamic label mapping instead of hardcoding
        # This ensures compatibility if new context states are added in the future
        encoder_path = self.model_path.replace('.pkl', '_classes.pkl')
        if Path(encoder_path).exists():
            self.label_decoder = joblib.load(encoder_path)
            logger.info(f"[ML] Loaded dynamic label mapping: {self.label_decoder}")
        else:
            # Safe fallback if label mapping file doesn't exist
            # (e.g., for old models trained without this feature)
            # NEW: 5-class model with psychological context states
            self.label_decoder = {
                0: 'Flow',
                1: 'Debugging',
                2: 'Research',
                3: 'Communication',
                4: 'Distracted'
            }
            logger.info(f"[ML] Using fallback label mapping (5-class): {self.label_decoder}")
            
        logger.info(f"[ML] Model loaded from {self.model_path}")
    
    def predict_with_confidence(self, block_metrics):
        """
        Predict context state with confidence score.
        
        Args:
            block_metrics (dict): Dictionary containing 8-signal metrics:
                - typing_intensity: KPM (float)
                - mouse_click_rate: CPM (float)
                - deletion_key_presses: Count (int)
                - total_keystrokes: For correction_ratio calculation (int)
                - idle_duration_sec: Total idle time (float)
                - total_duration_sec: Block duration (float)
                - app_switch_count: Unique app count (int)
                - app_names: List of app names (list)
                - touched_distraction_app: Boolean (bool)
                - end_time: Block end time (datetime or str)
        
        Returns:
            tuple: (context_state, confidence_score)
            Example: ("Flow", 0.92)
            
            5 context states: Flow, Debugging, Research, Communication, Distracted
        """
        # Extract 8 psychological signals
        features = FeatureExtractor.extract_features(block_metrics)
        
        # Validate features
        if not FeatureExtractor.validate_features(features):
            logger.warning(f"Invalid features detected: {features}")
            return "Distracted", 0.50  # Fallback to uncertain Distracted
        
        # Reshape for single prediction
        X = features.reshape(1, -1)
        
        # Predict class
        prediction = self.model.predict(X)[0]
        context_state = self.label_decoder[int(prediction)]
        
        # Get confidence from probabilities
        probabilities = self.model.predict_proba(X)[0]
        confidence = float(max(probabilities))  # Max probability = confidence
        
        return context_state, confidence
    
    def predict_batch(self, block_metrics_list):
        """
        Predict context states for multiple blocks.
        
        Args:
            block_metrics_list (list): List of block_metrics dicts
            
        Returns:
            list: List of (context_state, confidence) tuples
        """
        results = []
        for metrics in block_metrics_list:
            result = self.predict_with_confidence(metrics)
            results.append(result)
        return results
    
    def predict_with_probabilities(self, block_metrics):
        """
        Get full probability distribution for all 5 context states.
        
        Args:
            block_metrics (dict): Block metrics dictionary
            
        Returns:
            dict: {
                'context_state': str,
                'confidence': float,
                'probabilities': {
                    'Flow': float,
                    'Debugging': float,
                    'Research': float,
                    'Communication': float,
                    'Distracted': float,
                }
            }
        """
        # Extract features
        features = FeatureExtractor.extract_features(block_metrics)
        X = features.reshape(1, -1)
        
        # Get probabilities
        probabilities = self.model.predict_proba(X)[0]
        prediction = self.model.predict(X)[0]
        
        # Build result dictionary
        prob_dict = {
            self.label_decoder[i]: float(prob)
            for i, prob in enumerate(probabilities)
        }
        
        context_state = self.label_decoder[int(prediction)]
        confidence = float(max(probabilities))
        
        return {
            'context_state': context_state,
            'confidence': confidence,
            'probabilities': prob_dict,
        }


def main():
    """Test ML predictor with 8-signal sample data."""
    logger.info("\n" + "="*60)
    logger.info("TESTING ML PREDICTOR (5-CLASS, 8-SIGNAL)")
    logger.info("="*60)
    
    # Initialize predictor
    logger.info("\n🔄 Loading ML model...")
    predictor = MLPredictor(model_path='data/models/context_detector.pkl')
    
    # Test samples with 8 psychological signals
    test_samples = [
        {
            'name': 'Flow state (deep focus coding)',
            'metrics': {
                'typing_intensity': 140.0,      # High typing
                'mouse_click_rate': 8.0,        # Low clicks
                'deletion_key_presses': 15,     # Low corrections
                'total_keystrokes': 315,        # Calculated
                'idle_duration_sec': 15,        # Low idle
                'total_duration_sec': 300,
                'app_switch_count': 1,          # Single app
                'app_names': ['VSCode'],
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Debugging (trial & error with fixes)',
            'metrics': {
                'typing_intensity': 75.0,       # Moderate typing
                'mouse_click_rate': 35.0,       # High clicks
                'deletion_key_presses': 90,     # High corrections
                'total_keystrokes': 315,        # High deletion ratio
                'idle_duration_sec': 30,        # Moderate idle
                'total_duration_sec': 300,
                'app_switch_count': 5,          # Switching between tools
                'app_names': ['VSCode', 'Chrome', 'Terminal', 'GitHub'],
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Research (reading documentation)',
            'metrics': {
                'typing_intensity': 20.0,       # Low typing
                'mouse_click_rate': 25.0,       # Moderate clicks
                'deletion_key_presses': 5,      # Very few corrections
                'total_keystrokes': 305,        # Mostly clicking through
                'idle_duration_sec': 60,        # Reading time
                'total_duration_sec': 300,
                'app_switch_count': 3,          # Jumping between docs
                'app_names': ['Chrome', 'Firefox', 'VSCode'],
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Communication (chat & meetings)',
            'metrics': {
                'typing_intensity': 50.0,       # Message writing
                'mouse_click_rate': 15.0,       # Interface interaction
                'deletion_key_presses': 25,     # Some message editing
                'total_keystrokes': 325,        # Balanced with reading
                'idle_duration_sec': 90,        # Waiting/listening
                'total_duration_sec': 300,
                'app_switch_count': 4,          # Switch between apps
                'app_names': ['Slack', 'Zoom', 'VSCode', 'Chrome'],
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Distracted (social media break)',
            'metrics': {
                'typing_intensity': 30.0,       # Sporadic typing
                'mouse_click_rate': 65.0,       # Heavy clicking
                'deletion_key_presses': 10,     # Low corrections
                'total_keystrokes': 340,        # Mostly click-based
                'idle_duration_sec': 120,       # High idle
                'total_duration_sec': 300,
                'app_switch_count': 8,          # Jumping between many apps
                'app_names': ['Discord', 'Twitter', 'YouTube', 'Slack', 'VSCode', 'Chrome'],
                'touched_distraction_app': True,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
    ]
    
    logger.info("\n📊 Testing 5-class predictions:\n")
    for test in test_samples:
        name = test['name']
        metrics = test['metrics']
        
        # Get prediction with full probabilities
        result = predictor.predict_with_probabilities(metrics)
        
        logger.info(f"✅ {name.upper()}")
        logger.info(f"   Predicted: {result['context_state']} (confidence: {result['confidence']:.2%})")
        logger.info(f"   Probabilities:")
        for label, prob in sorted(result['probabilities'].items(), 
                                   key=lambda x: x[1], reverse=True):
            bar = '█' * int(prob * 20)
            logger.info(f"      {label:15s}: {prob:.2%} {bar}")
        logger.info("")
    
    logger.info("="*60)
    logger.info("✅ ML PREDICTOR READY (Flow, Debugging, Research, Communication, Distracted)")
    logger.info("="*60)


if __name__ == '__main__':
    main()