"""
Synthetic Data Generator for Phase 3 ML Training

Generates realistic training data based on the 10-rule heuristic from ContextDetector.
Each row represents a 5-minute block with features and a context_state label.

This allows us to train an ML model immediately without waiting for real data collection.
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
from pathlib import Path


class SyntheticDataGenerator:
    """Generate synthetic training data based on heuristic rules with realistic noise."""
    
    def __init__(self, seed=42):
        """Initialize with optional seed for reproducibility."""
        random.seed(seed)
        np.random.seed(seed)
    
    def generate_dataset(self, num_rows=10000, output_path='training_data_synthetic.csv'):
        """
        Generate synthetic dataset and save to CSV.
        
        Args:
            num_rows: Number of 5-minute blocks to generate (default 10000)
            output_path: Path to save CSV file
            
        Returns:
            DataFrame with columns: [typing_intensity, click_rate, scrolls, idle_ratio, 
                                     app_switches, project_switches, touched_distraction, 
                                     time_of_day, day_of_week, context_state]
        """
        rows = []
        
        # Distribute labels roughly equally across contexts
        # In reality: Focused ~50%, Distracted ~20%, Reading ~15%, Idle ~15%
        distribution = {
            'Focused': int(num_rows * 0.50),
            'Distracted': int(num_rows * 0.20),
            'Reading': int(num_rows * 0.15),
            'Idle': int(num_rows * 0.15),
        }
        
        for context_state, count in distribution.items():
            print(f"Generating {count} {context_state} blocks...")
            for _ in range(count):
                row = self._generate_row_for_context(context_state)
                
                # --- NEW: Introduce 8% Label Noise ---
                # Simulates the fuzziness of real human behavior where metrics 
                # don't perfectly align with their actual mental state.
                # This prevents the model from memorizing exact threshold boundaries
                # and forces it to generalize better to real-world data.
                if random.random() < 0.08:  # 8% of data gets mislabeled
                    context_state_noisy = random.choice(['Focused', 'Reading', 'Distracted', 'Idle'])
                    row[-1] = context_state_noisy  # Override the label with noise
                    
                rows.append(row)
        
        # Shuffle rows to mix contexts
        random.shuffle(rows)
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=[
            'typing_intensity', 'click_rate', 'scrolls', 'idle_ratio',
            'app_switches', 'project_switches', 'touched_distraction',
            'time_of_day', 'day_of_week', 'context_state'
        ])
        
        # Save to CSV
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(output_path, index=False)
        print(f"\n✅ Generated {num_rows} synthetic blocks → {output_path}")
        print(f"\nDataset Distribution:")
        print(df['context_state'].value_counts())
        print(f"\nDataset Statistics:")
        print(df.describe())
        
        return df
    
    def _generate_row_for_context(self, context_state):
        """Generate a realistic 5-minute block matching the given context state."""
        
        # Random time features
        time_of_day = random.randint(0, 23)  # 0-23 hours
        day_of_week = random.randint(0, 6)   # 0-6 (Monday-Sunday)
        
        if context_state == 'Focused':
            return self._generate_focused(time_of_day, day_of_week)
        elif context_state == 'Reading':
            return self._generate_reading(time_of_day, day_of_week)
        elif context_state == 'Distracted':
            return self._generate_distracted(time_of_day, day_of_week)
        elif context_state == 'Idle':
            return self._generate_idle(time_of_day, day_of_week)
    
    def _add_noise(self, value, noise_percent=0.1):
        """Add Gaussian noise to a value (±noise_percent)."""
        noise = np.random.normal(0, value * noise_percent)
        return max(0, value + noise)  # Ensure non-negative
    
    def _generate_focused(self, time_of_day, day_of_week):
        """
        FOCUSED rule: kpm > 40 and cpm > 15 and app_switches <= 2
        OR: kpm > 20 and cpm > 10 and app_switches >= 2
        OR: Deep coding work
        """
        # High typing (40-80 KPM)
        typing_intensity = self._add_noise(random.uniform(45, 75))
        
        # Moderate clicking (15-25 CPM)
        click_rate = self._add_noise(random.uniform(15, 25))
        
        # Minimal scrolling (0-5 scrolls)
        scrolls = random.randint(0, 5)
        
        # Low idle ratio (5-20%)
        idle_ratio = self._add_noise(random.uniform(0.05, 0.20))
        
        # Few app switches (0-2)
        app_switches = random.randint(0, 2)
        
        # Few project switches (0-1)
        project_switches = random.randint(0, 1)
        
        # No distraction apps
        touched_distraction = 0
        
        return [
            typing_intensity,
            click_rate,
            scrolls,
            idle_ratio,
            app_switches,
            project_switches,
            touched_distraction,
            time_of_day,
            day_of_week,
            'Focused'
        ]
    
    def _generate_reading(self, time_of_day, day_of_week):
        """
        READING rule: kpm < 20 and cpm < 10 and scrolls > 5
        Typical: Looking at documentation, tutorials, articles
        
        NOTE: Idle ratio overlaps with Idle state (0.20-0.45) to force the model
        to use other signals (high scrolling) rather than memorizing idle_ratio thresholds.
        """
        # Low typing (5-15 KPM)
        typing_intensity = self._add_noise(random.uniform(5, 15))
        
        # Low clicking (3-8 CPM)
        click_rate = self._add_noise(random.uniform(3, 8))
        
        # High scrolling (10-50 scrolls) - main signal
        scrolls = random.randint(10, 50)
        
        # Overlapping idle ratio with Idle state (20-45% instead of 10-30%)
        # This forces the model to rely on other features like high scrolling
        idle_ratio = self._add_noise(random.uniform(0.20, 0.45))
        
        # Few app switches (1-2)
        app_switches = random.randint(1, 2)
        
        # Few project switches (0-1)
        project_switches = random.randint(0, 1)
        
        # Mostly no distraction (90% of time)
        touched_distraction = 0 if random.random() < 0.9 else 1
        
        return [
            typing_intensity,
            click_rate,
            scrolls,
            idle_ratio,
            app_switches,
            project_switches,
            touched_distraction,
            time_of_day,
            day_of_week,
            'Reading'
        ]
    
    def _generate_distracted(self, time_of_day, day_of_week):
        """
        DISTRACTED rule: Multiple variants
        1. IMMEDIATE: touched_distraction and kpm < 30
        2. DISTRACTED: app_switches >= 3 and touched_distraction
        3. MODERATE DISTRACTION: project_switches >= 3 and kpm < 30
        """
        # Moderate typing (5-30 KPM) - not very productive
        typing_intensity = self._add_noise(random.uniform(5, 30))
        
        # Moderate clicking (5-15 CPM)
        click_rate = self._add_noise(random.uniform(5, 15))
        
        # Variable scrolling (0-20 scrolls)
        scrolls = random.randint(0, 20)
        
        # Moderate-high idle (20-50%)
        idle_ratio = self._add_noise(random.uniform(0.20, 0.50))
        
        # Multiple app switches (3-8)
        app_switches = random.randint(3, 8)
        
        # Moderate project switches (1-3)
        project_switches = random.randint(1, 3)
        
        # Usually touched distraction apps
        touched_distraction = 1 if random.random() < 0.85 else 0
        
        return [
            typing_intensity,
            click_rate,
            scrolls,
            idle_ratio,
            app_switches,
            project_switches,
            touched_distraction,
            time_of_day,
            day_of_week,
            'Distracted'
        ]
    
    def _generate_idle(self, time_of_day, day_of_week):
        """
        IDLE rule: High idle ratio (>0.5) OR very low signals across board
        Typical: Away from desk, thinking, coffee break
        
        NOTE: Intentionally allows some "Idle" blocks to have typing (e.g., chatting with 
        coworker at desk, quick message). Overlapping idle_ratio with Reading (0.40-0.95 
        instead of 0.60-0.95) forces model to use full feature set, not just idle_ratio.
        """
        # Very low typing (0-25 KPM) - can have some typing when idle at desk
        # This realistic overlap forces the model to not memorize idle_ratio threshold
        typing_intensity = self._add_noise(random.uniform(0, 25))
        
        # Very low clicking (0-5 CPM)
        click_rate = self._add_noise(random.uniform(0, 5))
        
        # Minimal scrolling (0-3 scrolls)
        scrolls = random.randint(0, 3)
        
        # High idle ratio with lower threshold (40-95% instead of 60-95%)
        # Overlaps with Reading range to force feature combination learning
        idle_ratio = self._add_noise(random.uniform(0.40, 0.95))
        
        # Few app switches (0-2)
        app_switches = random.randint(0, 2)
        
        # Few project switches (0-1)
        project_switches = random.randint(0, 1)
        
        # Unlikely to touch distraction while idle
        touched_distraction = 0 if random.random() < 0.95 else 1
        
        return [
            typing_intensity,
            click_rate,
            scrolls,
            idle_ratio,
            app_switches,
            project_switches,
            touched_distraction,
            time_of_day,
            day_of_week,
            'Idle'
        ]


def main():
    """Generate 10,000 synthetic training rows."""
    generator = SyntheticDataGenerator(seed=42)
    df = generator.generate_dataset(num_rows=10000, output_path='data/datasets/training_synthetic.csv')
    print("\n" + "="*60)
    print("SYNTHETIC DATA GENERATION COMPLETE")
    print("="*60)
    print(f"Total rows: {len(df)}")
    print(f"Features: {list(df.columns[:-1])}")
    print(f"Labels: {df['context_state'].unique()}")
    print("\n✅ Ready for ML model training!")


if __name__ == '__main__':
    main()
