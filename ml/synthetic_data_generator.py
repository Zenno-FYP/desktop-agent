"""
Synthetic Data Generator for ML Training - CALIBRATED FROM REAL DATA

Generates training data based on REAL OBSERVED signal distributions.

REAL DATA OBSERVATIONS (from 36 activity blocks):
1. typing_intensity_kpm: 0-200 (median=0, mean=32.36) - Many blocks have NO typing!
2. mouse_click_rate_cpm: 0-36 (median=8.5, mean=10.13)
3. deletion_key_presses: 0-10 (median=0, mean=1.44) - SPARSE, mostly 0
4. mouse_movement_distance: 0-16,768 pixels (median=2,024, mean=3,382)
5. idle_duration_sec: 0-78 sec (median=0, mean=6.0)
6. duration_sec: 5-300 sec (median=10, mean=51.78) - VARIABLE BLOCKS!
7. app_score: Inferred from context (-1.0 to 1.0)
8. fatigue_hrs: 0-24 hours

Context Distributions:
- Flow: 36% (13/36) - High typing, moderate movement, low clicks
- Research: 28% (10/36) - ZERO typing (reading mode!), moderate movement, high clicks
- Debugging: ~18% (estimated)
- Communication: ~9% (estimated)
- Distracted: ~9% (estimated)

KEY INSIGHT: Blocks are variable duration (5-300 sec), not uniform 5-min!
CRITICAL: 56% of Flow blocks have ZERO typing (reading/thinking) - MUST allow this!
"""

import pandas as pd
import numpy as np
import random
from pathlib import Path


class SyntheticDataGenerator:
    """Generate synthetic training data based on REAL signal observations."""

    def __init__(self, seed=42):
        """Initialize with optional seed for reproducibility."""
        random.seed(seed)
        np.random.seed(seed)

    def generate_dataset(self, num_rows=10000, output_path='data/datasets/training_synthetic.csv'):
        """
        Generate synthetic dataset calibrated from real observations.
        
        Args:
            num_rows: Number of blocks to generate
            output_path: Path to save CSV file
            
        Returns:
            DataFrame with 8 signal columns and context_state label
        """
        rows = []
        
        # Distribution based on observed context frequencies
        distribution = {
            'Flow': int(num_rows * 0.36),         # 36% - Most productive
            'Research': int(num_rows * 0.28),     # 28% - Learning/reading
            'Debugging': int(num_rows * 0.18),    # 18% - Problem solving
            'Communication': int(num_rows * 0.09), # 9% - Collaboration
            'Distracted': int(num_rows * 0.09),   # 9% - Off-task
        }
        
        for context_state, count in distribution.items():
            print(f"Generating {count} {context_state} blocks...")
            for _ in range(count):
                row = self._generate_row_for_context(context_state)
                
                # Add 2% label noise to simulate real-world ambiguity
                if random.random() < 0.02:
                    context_state_noisy = random.choice(['Flow', 'Debugging', 'Research', 'Communication', 'Distracted'])
                    row[-1] = context_state_noisy
                
                rows.append(row)
        
        # Shuffle rows
        random.shuffle(rows)
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=[
            'typing_kpm',
            'correction_ratio',
            'mouse_px_per_sec',
            'mouse_cpm',
            'switch_freq',
            'app_score',
            'idle_ratio',
            'fatigue_hrs',
            'context_state'
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
        """Generate a realistic block matching the given context state.
        
        Note: Blocks have VARIABLE duration (5-300 seconds), not uniform.
        This affects px_per_sec calculation.
        """
        
        # Random temporal features
        fatigue_hrs = random.uniform(0, 24)
        
        if context_state == 'Flow':
            return self._generate_flow(fatigue_hrs)
        elif context_state == 'Debugging':
            return self._generate_debugging(fatigue_hrs)
        elif context_state == 'Research':
            return self._generate_research(fatigue_hrs)
        elif context_state == 'Communication':
            return self._generate_communication(fatigue_hrs)
        elif context_state == 'Distracted':
            return self._generate_distracted(fatigue_hrs)

    def _add_noise(self, value, noise_percent=0.10):
        """Add Gaussian noise to a value (±noise_percent)."""
        if value == 0:
            return 0
        noise = np.random.normal(0, value * noise_percent)
        return max(0, value + noise)

    def _generate_flow(self, fatigue_hrs):
        """
        FLOW STATE (Creation) - REAL CALIBRATED FROM 52 BLOCKS
        Real: 0-200 KPM (mean=27), 0-36 CPM (mean=10), 0-17k px (mean=4046)
        KEY: 56% have ZERO typing (reading code, thinking)
        """
        # Real: 56% are ZERO typing, rest are 0-200 (many in 20-120 range)
        if random.random() < 0.56:
            typing_kpm = 0.0  # Reading/thinking phase
        else:
            typing_kpm = self._add_noise(random.uniform(20, 200))
        
        # Correction ratio: Low for Flow
        correction_ratio = self._add_noise(random.uniform(0.00, 0.12))
        
        # Real mouse movement: 0-17,800 pixels (mean=4,046)
        # Block duration varies 5-300 sec, so px/sec varies wildly
        # Example: 4000px in 5sec = 800 px/sec, but 4000px in 300sec = 13 px/sec
        duration_sec = random.choice([
            random.uniform(5, 30),      # Often short blocks
            random.uniform(30, 60),     # Medium blocks
            random.uniform(60, 300),    # Longer blocks less common
        ])
        
        mouse_movement_px = self._add_noise(random.uniform(0, 17800))
        mouse_px_per_sec = mouse_movement_px / duration_sec if duration_sec > 0 else 0
        
        # Real clicks: 0-36 CPM (mean=10), 11% are ZERO
        if random.random() < 0.11:
            mouse_cpm = 0.0
        else:
            mouse_cpm = self._add_noise(random.uniform(0.5, 12))
        
        # App switching: Low
        switch_freq = self._add_noise(random.uniform(0.2, 1.5))
        
        # App score: Productive
        app_score = random.uniform(0.6, 1.0)
        
        # Idle ratio
        idle_ratio = self._add_noise(random.uniform(0.05, 0.35))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Flow'
        ]

    def _generate_debugging(self, fatigue_hrs):
        """
        DEBUGGING (Fixing) - INFERRED
        Characteristics:
        - High typing variability (problem-solving)
        - High correction ratio (trial & error trying different solutions)
        - Scattered mouse movement jumping between files/errors
        """
        # Problem solving: variable typing, sometimes reading stack traces
        if random.random() < 0.3:
            typing_kpm = 0.0  # Reading error messages
        else:
            typing_kpm = self._add_noise(random.uniform(15, 150))
        
        # PRIMARY SIGNAL: High corrections (>12% for debugging)
        correction_ratio = self._add_noise(random.uniform(0.12, 0.40))
        
        # Variable duration
        duration_sec = random.uniform(5, 300)
        
        # Scattered navigation: often lots of mouse movement
        mouse_movement_px = self._add_noise(random.uniform(500, 17800))
        mouse_px_per_sec = mouse_movement_px / duration_sec if duration_sec > 0 else 0
        
        # Moderate to high clicking
        if random.random() < 0.15:
            mouse_cpm = 0.0
        else:
            mouse_cpm = self._add_noise(random.uniform(2, 30))
        
        # High switching: IDE ↔ Browser ↔ Terminal
        switch_freq = self._add_noise(random.uniform(1.0, 3.0))
        
        # Mixed apps
        app_score = random.uniform(0.2, 0.8)
        
        # Idle while reading errors
        idle_ratio = self._add_noise(random.uniform(0.05, 0.35))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Debugging'
        ]

    def _generate_research(self, fatigue_hrs):
        """
        RESEARCH (Learning/Reading) - REAL CALIBRATED FROM 52 BLOCKS
        Real: ALL are 0 KPM (pure reading), 0-36 CPM (mean=10)
        """
        # ALWAYS zero typing when researching
        typing_kpm = 0.0
        
        # Minimal corrections while reading
        correction_ratio = self._add_noise(random.uniform(0.0, 0.05))
        
        # Variable duration
        duration_sec = random.uniform(5, 300)
        
        # Real mouse movement: 0-17,800 px (mean=4,046)
        mouse_movement_px = self._add_noise(random.uniform(500, 5000))
        mouse_px_per_sec = mouse_movement_px / duration_sec if duration_sec > 0 else 0
        
        # Clicks: 0-36 CPM (mean=10), some are zero
        if random.random() < 0.15:
            mouse_cpm = 0.0
        else:
            mouse_cpm = self._add_noise(random.uniform(0.5, 36))
        
        # App switching
        switch_freq = self._add_noise(random.uniform(0.3, 1.5))
        
        # App score: Browser/tutorial
        app_score = random.uniform(0.3, 0.6)
        
        # Idle ratio
        idle_ratio = self._add_noise(random.uniform(0.01, 0.30))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Research'
        ]

    def _generate_communication(self, fatigue_hrs):
        """
        COMMUNICATION (Chat/Collaboration) - INFERRED
        Characteristics:
        - Moderate typing (message composition)
        - Low mouse movement (mostly in text boxes)
        - Moderate clicking (reactions, @ mentions)
        - Some app switching
        """
        # Bursty typing during chat, sometimes reading
        if random.random() < 0.2:
            typing_kpm = 0.0  # Reading messages
        else:
            typing_kpm = self._add_noise(random.uniform(20, 100))
        
        # Casual chat has some corrections
        correction_ratio = self._add_noise(random.uniform(0.02, 0.12))
        
        # Variable duration
        duration_sec = random.uniform(5, 300)
        
        # Usually minimal mouse movement in chat
        mouse_movement_px = self._add_noise(random.uniform(100, 5000))
        mouse_px_per_sec = mouse_movement_px / duration_sec if duration_sec > 0 else 0
        
        # Reactions and mentions
        if random.random() < 0.15:
            mouse_cpm = 0.0
        else:
            mouse_cpm = self._add_noise(random.uniform(2, 20))
        
        # Some context switching
        switch_freq = self._add_noise(random.uniform(0.2, 1.5))
        
        # Communication apps: neutral
        app_score = random.uniform(-0.2, 0.2)
        
        # Waiting for responses increases idle time
        idle_ratio = self._add_noise(random.uniform(0.1, 0.40))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Communication'
        ]

    def _generate_distracted(self, fatigue_hrs):
        """
        DISTRACTED (Off-Task) - INFERRED
        Characteristics:
        - Unpredictable typing (games, social media, netflix)
        - Variable mouse movement (scrolling or static)
        - High app switching (doomscrolling)
        - Distraction apps (negative app score)
        """
        # Very unpredictable: sometimes 0, sometimes high
        if random.random() < 0.4:
            typing_kpm = 0.0  # Video/passive consumption
        else:
            typing_kpm = self._add_noise(random.uniform(0, 80))
        
        # Variable corrections
        correction_ratio = self._add_noise(random.uniform(0.0, 0.20))
        
        # Variable duration
        duration_sec = random.uniform(5, 300)
        
        # Either scrolling heavy or static
        if random.random() < 0.6:
            # Social scrolling: lots of mouse movement
            mouse_movement_px = self._add_noise(random.uniform(1000, 17800))
        else:
            # Video watching: minimal movement
            mouse_movement_px = self._add_noise(random.uniform(0, 2000))
        
        mouse_px_per_sec = mouse_movement_px / duration_sec if duration_sec > 0 else 0
        
        # Variable clicks
        if random.random() < 0.15:
            mouse_cpm = 0.0
        else:
            mouse_cpm = self._add_noise(random.uniform(1, 35))
        
        # High app switching (doomscrolling)
        switch_freq = self._add_noise(random.uniform(1.0, 4.0))
        
        # Distraction apps: negative score
        app_score = random.uniform(-1.0, -0.4)
        
        # Variable idle
        idle_ratio = self._add_noise(random.uniform(0.0, 0.60))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Distracted'
        ]


def main():
    """Generate synthetic training data."""
    generator = SyntheticDataGenerator(seed=42)
    df = generator.generate_dataset(num_rows=10000, output_path='data/datasets/training_synthetic.csv')
    print("\n" + "="*60)
    print("SYNTHETIC DATA GENERATION COMPLETE")
    print("="*60)
    print(f"Total rows: {len(df)}")
    print(f"Features: {list(df.columns[:-1])}")
    print(f"Labels: {sorted(df['context_state'].unique())}")
    print("\n✅ Ready for ML model training!")


if __name__ == '__main__':
    main()

