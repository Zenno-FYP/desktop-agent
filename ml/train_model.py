"""
ML Model Trainer - Phase 3

Trains an XGBoost classifier on synthetic training data.
Saves trained model to data/models/context_detector.pkl for deployment.

Model: XGBClassifier
Expected Accuracy: 92-95% (on synthetic data)
Inference Time: <5ms per prediction
"""

import pandas as pd
import numpy as np
import joblib
from pathlib import Path
from xgboost import XGBClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score


class MLModelTrainer:
    """Train and evaluate XGBoost model for context detection."""
    
    def __init__(self, model_params=None):
        """
        Initialize with model hyperparameters.
        
        Args:
            model_params (dict): Optional XGBoost hyperparameters
        """
        self.model_params = model_params or {
            'max_depth': 5,
            'learning_rate': 0.1,
            'n_estimators': 100,
            'subsample': 0.8,
            'colsample_bytree': 0.8,
            'random_state': 42,
            'objective': 'multi:softmax',
            'num_class': 4,  # Focused, Distracted, Reading, Idle
            'verbosity': 0,
        }
        self.model = None
        self.X_train = None
        self.X_test = None
        self.y_train = None
        self.y_test = None
        self.label_encoder = {
            'Focused': 0,
            'Distracted': 1,
            'Reading': 2,
            'Idle': 3,
        }
        self.label_decoder = {v: k for k, v in self.label_encoder.items()}
    
    def load_training_data(self, csv_path='data/datasets/training_synthetic.csv'):
        """
        Load synthetic training data from CSV.
        
        Args:
            csv_path (str): Path to training CSV file
            
        Returns:
            tuple: (X, y) feature matrix and labels
        """
        print(f"Loading training data from {csv_path}...")
        df = pd.read_csv(csv_path)
        
        # Separate features and labels
        feature_columns = [
            'typing_intensity', 'click_rate', 'scrolls', 'idle_ratio',
            'app_switches', 'project_switches', 'touched_distraction',
            'time_of_day', 'day_of_week'
        ]
        
        X = df[feature_columns].values
        y = df['context_state'].values
        
        # Create label encoder mapping
        self.label_encoder = {
            'Focused': 0,
            'Distracted': 1,
            'Reading': 2,
            'Idle': 3,
        }
        self.label_decoder = {v: k for k, v in self.label_encoder.items()}
        
        # Encode labels to numeric values
        y_encoded = np.array([self.label_encoder[label] for label in y])
        
        print(f"✅ Loaded {len(df)} samples")
        print(f"   Features: {feature_columns}")
        print(f"   Labels: {self.label_decoder}")
        print(f"   X shape: {X.shape}, y shape: {y_encoded.shape}")
        
        return X, y_encoded
    
    def train_model(self, csv_path='data/datasets/training_synthetic.csv', test_size=0.2):
        """
        Load data, split, and train XGBoost model.
        
        Args:
            csv_path (str): Path to training CSV
            test_size (float): Fraction of data for testing
            
        Returns:
            dict: Training results with metrics
        """
        # Load data
        X, y = self.load_training_data(csv_path)
        
        # Split into train/test
        print(f"\nSplitting data (80/20 train/test)...")
        self.X_train, self.X_test, self.y_train, self.y_test = train_test_split(
            X, y, test_size=test_size, random_state=42, stratify=y
        )
        
        print(f"✅ Train set: {len(self.X_train)} samples")
        print(f"✅ Test set: {len(self.X_test)} samples")
        
        # Train model
        print(f"\nTraining XGBoost model...")
        print(f"   Parameters: {self.model_params}")
        
        self.model = XGBClassifier(**self.model_params)
        self.model.fit(
            self.X_train, self.y_train,
            eval_set=[(self.X_test, self.y_test)],
            verbose=False
        )
        
        print("✅ Training complete!")
        
        # Evaluate
        results = self.evaluate_model()
        
        return results
    
    def evaluate_model(self):
        """
        Evaluate trained model on test set.
        
        Returns:
            dict: Evaluation metrics
        """
        if self.model is None or self.X_test is None:
            raise ValueError("Model must be trained first")
        
        print("\n" + "="*60)
        print("MODEL EVALUATION")
        print("="*60)
        
        # Predictions
        y_pred = self.model.predict(self.X_test)
        
        # Accuracy
        accuracy = accuracy_score(self.y_test, y_pred)
        print(f"\n📊 Overall Accuracy: {accuracy:.2%}")
        
        # Per-class metrics (convert back to string labels for readability)
        y_test_labels = np.array([self.label_decoder[int(y)] for y in self.y_test])
        y_pred_labels = np.array([self.label_decoder[int(y)] for y in y_pred])
        
        print("\n📊 Classification Report:")
        print(classification_report(y_test_labels, y_pred_labels))
        
        # Confusion matrix
        cm = confusion_matrix(self.y_test, y_pred)
        print(f"\n📊 Confusion Matrix:")
        print(cm)
        
        # Feature importance
        feature_importance = self.model.feature_importances_
        feature_names = [
            'typing_intensity', 'click_rate', 'scrolls', 'idle_ratio',
            'app_switches', 'project_switches', 'touched_distraction',
            'time_of_day', 'day_of_week'
        ]
        
        print("\n📊 Feature Importance:")
        importance_df = pd.DataFrame({
            'feature': feature_names,
            'importance': feature_importance
        }).sort_values('importance', ascending=False)
        
        print(importance_df.to_string(index=False))
        
        return {
            'accuracy': accuracy,
            'confusion_matrix': cm,
            'feature_importance': importance_df,
        }
    
    def save_model(self, output_path='data/models/context_detector.pkl'):
        """
        Save trained model and label mapping to disk.
        
        Args:
            output_path (str): Path to save model (.pkl file)
            
        Note:
            Also saves label_decoder to {output_path.replace('.pkl', '_classes.pkl')}
            This ensures the label mapping is always in sync with the model.
        """
        if self.model is None:
            raise ValueError("Model must be trained first")
        
        # Create directory if needed
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Save model
        joblib.dump(self.model, output_path)
        print(f"\n[ML] Model saved to {output_path}")
        
        # NEW: Save label mapping alongside the model
        # This ensures the predictor can always load the correct label mapping
        # even if new context states are added in the future
        encoder_path = output_path.replace('.pkl', '_classes.pkl')
        joblib.dump(self.label_decoder, encoder_path)
        print(f"[ML] Label mapping saved to {encoder_path}")
        print(f"     Label decoder: {self.label_decoder}")
    
    def load_model(self, model_path='data/models/context_detector.pkl'):
        """
        Load trained model from disk.
        
        Args:
            model_path (str): Path to model file
        """
        self.model = joblib.load(model_path)
        print(f"✅ Model loaded from {model_path}")
    
    def predict(self, X):
        """
        Make predictions using trained model.
        
        Args:
            X (np.ndarray): Feature matrix (n_samples, 9)
            
        Returns:
            np.ndarray: Predicted context states
        """
        if self.model is None:
            raise ValueError("Model must be trained or loaded first")
        
        return self.model.predict(X)
    
    def predict_proba(self, X):
        """
        Get prediction probabilities using trained model.
        
        Args:
            X (np.ndarray): Feature matrix (n_samples, 9)
            
        Returns:
            dict: Probabilities for each class
        """
        if self.model is None:
            raise ValueError("Model must be trained or loaded first")
        
        # Get probabilities
        proba = self.model.predict_proba(X)
        
        # Get class labels
        classes = self.model.classes_
        
        # Return as dict
        return {
            'predictions': self.model.predict(X),
            'probabilities': proba,
            'classes': classes,
            'confidences': np.max(proba, axis=1),
        }


def main():
    """Train and save ML model."""
    print("\n" + "="*60)
    print("PHASE 3: ML MODEL TRAINING")
    print("="*60)
    
    # Initialize trainer
    trainer = MLModelTrainer()
    
    # Train model
    print("\n🚀 Starting model training...")
    results = trainer.train_model(csv_path='data/datasets/training_synthetic.csv')
    
    # Save model
    print("\n💾 Saving model...")
    trainer.save_model(output_path='data/models/context_detector.pkl')
    
    # Print summary
    print("\n" + "="*60)
    print("TRAINING COMPLETE")
    print("="*60)
    print(f"✅ Model Accuracy: {results['accuracy']:.2%}")
    print(f"✅ Model saved to: data/models/context_detector.pkl")
    print(f"✅ Ready for deployment in BlockEvaluator!")


if __name__ == '__main__':
    main()
