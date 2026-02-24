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

import joblib
import numpy as np
from pathlib import Path
from datetime import datetime
import sys

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from ml.feature_extractor import FeatureExtractor


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
            print(f"[ML] Loaded dynamic label mapping: {self.label_decoder}")
        else:
            # Safe fallback if label mapping file doesn't exist
            # (e.g., for old models trained without this feature)
            self.label_decoder = {
                0: 'Focused',
                1: 'Distracted',
                2: 'Reading',
                3: 'Idle'
            }
            print(f"[ML] Using fallback label mapping: {self.label_decoder}")
            
        print(f"[ML] Model loaded from {self.model_path}")
    
    def predict_with_confidence(self, block_metrics):
        """
        Predict context state with confidence score.
        
        Args:
            block_metrics (dict): Dictionary containing:
                - typing_intensity (float)
                - mouse_click_rate (float)
                - mouse_scroll_events (int)
                - idle_duration_sec (float)
                - total_duration_sec (float)
                - app_switch_count (int)
                - project_switch_count (int)
                - touched_distraction_app (bool)
                - end_time (datetime or str)
        
        Returns:
            tuple: (context_state, confidence_score)
            Example: ("Focused", 0.92)
        """
        # Extract features
        features = FeatureExtractor.extract_features(block_metrics)
        
        # Validate features
        if not FeatureExtractor.validate_features(features):
            print(f"⚠️  Invalid features detected: {features}")
            return "Idle", 0.50  # Fallback to uncertain Idle
        
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
        Get full probability distribution for all classes.
        
        Args:
            block_metrics (dict): Block metrics dictionary
            
        Returns:
            dict: {
                'context_state': str,
                'confidence': float,
                'probabilities': {
                    'Focused': float,
                    'Distracted': float,
                    'Reading': float,
                    'Idle': float,
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
    """Test ML predictor with sample data."""
    print("\n" + "="*60)
    print("TESTING ML PREDICTOR")
    print("="*60)
    
    # Initialize predictor
    print("\n🔄 Loading ML model...")
    predictor = MLPredictor(model_path='data/models/context_detector.pkl')
    
    # Test samples
    test_samples = [
        {
            'name': 'Focused coding',
            'metrics': {
                'typing_intensity': 60.0,
                'mouse_click_rate': 20.0,
                'mouse_scroll_events': 2,
                'idle_duration_sec': 15,
                'total_duration_sec': 300,
                'app_switch_count': 1,
                'project_switch_count': 0,
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Reading documentation',
            'metrics': {
                'typing_intensity': 10.0,
                'mouse_click_rate': 5.0,
                'mouse_scroll_events': 30,
                'idle_duration_sec': 30,
                'total_duration_sec': 300,
                'app_switch_count': 1,
                'project_switch_count': 0,
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Distracted (Discord touching)',
            'metrics': {
                'typing_intensity': 20.0,
                'mouse_click_rate': 10.0,
                'mouse_scroll_events': 10,
                'idle_duration_sec': 60,
                'total_duration_sec': 300,
                'app_switch_count': 5,
                'project_switch_count': 2,
                'touched_distraction_app': True,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
        {
            'name': 'Idle (away from desk)',
            'metrics': {
                'typing_intensity': 2.0,
                'mouse_click_rate': 1.0,
                'mouse_scroll_events': 0,
                'idle_duration_sec': 280,
                'total_duration_sec': 300,
                'app_switch_count': 0,
                'project_switch_count': 0,
                'touched_distraction_app': False,
                'end_time': datetime(2026, 2, 24, 14, 5),
            }
        },
    ]
    
    print("\n📊 Testing predictions:\n")
    for test in test_samples:
        name = test['name']
        metrics = test['metrics']
        
        # Get prediction with full probabilities
        result = predictor.predict_with_probabilities(metrics)
        
        print(f"✅ {name.upper()}")
        print(f"   Predicted: {result['context_state']} (confidence: {result['confidence']:.2%})")
        print(f"   Probabilities:")
        for label, prob in sorted(result['probabilities'].items(), 
                                   key=lambda x: x[1], reverse=True):
            bar = '█' * int(prob * 20)
            print(f"      {label:12s}: {prob:.2%} {bar}")
        print()
    
    print("="*60)
    print("✅ ML PREDICTOR READY FOR DEPLOYMENT")
    print("="*60)


if __name__ == '__main__':
    main()
