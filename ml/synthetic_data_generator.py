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
- Debugging: ~18% (estimated - mentioned but not in current logs)
- Communication: ~9% (estimated - mentioned but not in current logs)
- Distracted: ~9% (estimated - mentioned but not in current logs)

KEY INSIGHT: Blocks are variable duration (5-300 sec), not uniform 5-min!
"""

import pandas as pd
import numpy as np
import random
from datetime import datetime, timedelta
from pathlib import Path


class SyntheticDataGenerator:
    """Generate synthetic training data based on REAL signal observations."""

    def __init__(self, seed=42):
        """Initialize with optional seed for reproducibility."""
        random.seed(seed)
        np.random.seed(seed)

    def generate_dataset(self, num_rows=10000, output_path='training_data_synthetic.csv'):
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
        FLOW STATE (Creation) - REAL CALIBRATED
        Real data (Flow, n=13):
        - typing_intensity: 0-200 KPM, mean=63.60
        - mouse_movement: 0-16,768 pixels, mean=4,448
        - mouse_click: 0-12 CPM, mean=3.28 (LOW - mostly navigation)
        - idle: 0-78 sec, mean=9.85
        
        Note: Variable duration (5-300 sec) → px_per_sec varies accordingly
        """
        # Real typing variation: 0-200 KPM, heavily skewed toward 50-120
        typing_kpm = self._add_noise(random.uniform(20, 120))
        
        # Correction ratio: Low errors = confidence (0.00-0.15)
        correction_ratio = self._add_noise(random.uniform(0.00, 0.12))
        
        # Real mouse movement: 0-16,768 pixels
        # For variable block duration, normalize to px_per_sec
        # Mean observed: 4,448 pixels per block (avg ~50-60 sec) = ~75 px/sec
        mouse_movement_px = self._add_noise(random.uniform(1000, 12000))
        
        # Block duration varies (5-300 sec), affecting px/sec calculation
        # Use 0.1-0.2 ratio: 10-30 px/sec is realistic for coding
        mouse_px_per_sec = self._add_noise(random.uniform(10, 50))
        
        # Real clicks: 0-12 CPM, mean=3.28 (LOW - focused work has fewer clicks)
        mouse_cpm = self._add_noise(random.uniform(1, 8))
        
        # App switching: Low (0.2-1.0 per block, ~2-5 per 5 min)
        switch_freq = self._add_noise(random.uniform(0.2, 1.0))
        
        # App score: Productive (0.6-1.0 for IDE work)
        app_score = random.uniform(0.6, 1.0)
        
        # Idle ratio: Real mean=9.85 sec per ~50 sec block = ~20% idle
        idle_ratio = self._add_noise(random.uniform(0.05, 0.35))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Flow'
        ]

    def _generate_debugging(self, fatigue_hrs):
        """
        DEBUGGING (Fixing) - REAL CALIBRATED
        Inferred from real data patterns:
        - High typing variability (problem-solving)
        - High correction ratio (trial & error)
        - Scattered mouse movement
        - High app switching
        """
        # Active problem solving: 20-100 KPM (can drop to 0 while reading errors)
        typing_kpm = self._add_noise(random.uniform(15, 90))
        
        # PRIMARY SIGNAL: High corrections (>12% for debugging)
        correction_ratio = self._add_noise(random.uniform(0.12, 0.35))
        
        # Scattered navigation between error messages and code
        # Real flow data showed up to 16k pixels, Debugging likely similar
        mouse_movement_px = self._add_noise(random.uniform(2000, 10000))
        mouse_px_per_sec = self._add_noise(random.uniform(15, 60))
        
        # Moderate clicking (inspecting errors, clicking through stack traces)
        mouse_cpm = self._add_noise(random.uniform(5, 25))
        
        # High switching: IDE ↔ Browser ↔ Terminal (1.5-4 switches per block)
        switch_freq = self._add_noise(random.uniform(1.0, 3.0))
        
        # Mixed: IDE (1.0) + Browser (0.5) + Terminal (0.5) = 0.5-0.8
        app_score = random.uniform(0.3, 0.8)
        
        # Idle while reading errors
        idle_ratio = self._add_noise(random.uniform(0.05, 0.30))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Debugging'
        ]

    def _generate_research(self, fatigue_hrs):
        """
        RESEARCH (Learning/Reading) - REAL CALIBRATED
        Real data (Research, n=10):
        - typing_intensity: ALL ZEROS (reading mode, no coding)
        - mouse_movement: 982-4,261 pixels, mean=2,433
        - mouse_click: 1.16-35.98 CPM, mean=15.71 (HIGH - lots of link clicking)
        - idle: 0-10 sec, mean=1.0
        
        KEY INSIGHT: Reading = 0 typing but moderate mouse movement (scrolling)
        """
        # CRITICAL: Research = ZERO typing (reading, not coding!)
        typing_kpm = 0.0
        
        # Minimal corrections while reading
        correction_ratio = self._add_noise(random.uniform(0.0, 0.05))
        
        # Real observed: 982-4,261 pixels per block, mean=2,433
        # This is moderate scrolling through docs/tutorials
        mouse_movement_px = self._add_noise(random.uniform(500, 4500))
        mouse_px_per_sec = self._add_noise(random.uniform(15, 45))
        
        # Real observed: High clicking (1.16-35.98 CPM, mean=15.71)
        # Clicking links, expanding code blocks, navigating tutorials
        mouse_cpm = self._add_noise(random.uniform(8, 28))
        
        # Some tab/app switching (between browser tabs, IDE and browser)
        switch_freq = self._add_noise(random.uniform(0.3, 1.2))
        
        # App score: Browser/tutorial = 0.4-0.6 (neutral/research)
        app_score = random.uniform(0.3, 0.6)
        
        # Real observed: mean=1.0 sec per block (very low idle in reading)
        # Reading is active: scrolling, clicking, skimming
        idle_ratio = self._add_noise(random.uniform(0.01, 0.15))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Research'
        ]

    def _generate_communication(self, fatigue_hrs):
        """
        COMMUNICATION (Chat/Collaboration) - INFERRED
        Inferred characteristics:
        - Moderate typing (message composition)
        - Low mouse movement (mostly in text boxes)
        - Moderate clicking (reactions, @ mentions)
        - Some app switching (between channels/apps)
        """
        # Bursty typing during chat (composing messages)
        typing_kpm = self._add_noise(random.uniform(30, 80))
        
        # Casual chat has some corrections (typos)
        correction_ratio = self._add_noise(random.uniform(0.03, 0.12))
        
        # Slow deliberate mouse in chat UI
        mouse_movement_px = self._add_noise(random.uniform(200, 3000))
        mouse_px_per_sec = self._add_noise(random.uniform(5, 30))
        
        # Reactions and mentions
        mouse_cpm = self._add_noise(random.uniform(5, 18))
        
        # Some context switching (between channels/apps)
        switch_freq = self._add_noise(random.uniform(0.3, 1.5))
        
        # Communication apps: Slack/Teams = 0.0 (neutral)
        app_score = random.uniform(-0.1, 0.1)
        
        # Waiting for responses increases idle time
        idle_ratio = self._add_noise(random.uniform(0.1, 0.35))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Communication'
        ]

    def _generate_distracted(self, fatigue_hrs):
        """
        DISTRACTED (Off-Task) - INFERRED
        Inferred from real data patterns:
        - Variable typing (some games, some social media)
        - Erratic or minimal mouse movement
        - High app switching (doomscrolling)
        - Distraction apps (negative app_score)
        """
        # Unpredictable typing (games, social media)
        typing_kpm = self._add_noise(random.uniform(0, 60))
        
        # Variable corrections
        correction_ratio = self._add_noise(random.uniform(0.0, 0.20))
        
        # Either erratic (gaming) or stationary (video)
        if random.random() < 0.6:
            # Social scrolling / light gaming: moderate movement
            mouse_movement_px = self._add_noise(random.uniform(1000, 8000))
            mouse_px_per_sec = self._add_noise(random.uniform(10, 50))
            mouse_cpm = self._add_noise(random.uniform(10, 35))
        else:
            # Video watching: minimal movement
            mouse_movement_px = self._add_noise(random.uniform(0, 1000))
            mouse_px_per_sec = self._add_noise(random.uniform(0, 10))
            mouse_cpm = self._add_noise(random.uniform(0, 15))
        
        # High app switching (doomscrolling)
        switch_freq = self._add_noise(random.uniform(1.5, 4.0))
        
        # Distraction apps: YouTube/Reddit etc = -0.6 to -1.0
        app_score = random.uniform(-1.0, -0.5)
        
        # Variable idle (gaming = low, video = high)
        idle_ratio = self._add_noise(random.uniform(0.0, 0.50))
        
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


    def _generate_row_for_context(self, context_state):
        """Generate a realistic 5-minute block matching the given context state."""
        
        # Random temporal features
        # Matches FeatureExtractor: fatigue_hrs represents consecutive work hours since last break.
        # FeatureExtractor clips to 0-24; synthetic data stays within that range.
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
        FLOW STATE (Creation) - REALISTIC
        - Typing: 30-110 KPM (Thinking + Bursts of coding)
        - Correction Ratio: < 0.08 (The key differentiator: Low errors = Confidence)
        - Mouse: 10-45 px/sec (Real observed: ~5k-13k pixels/5min)
        - App Score: 0.6-1.0 (Time-weighted: mostly IDE, minor distractions OK)
        - Idle: 10-40% (Thinking time is part of Flow)
        """
        # Human-calibrated: Deep thought coding at 30-40 KPM is productive
        typing_kpm = self._add_noise(random.uniform(30, 110))
        
        # The gatekeeper: Confident typing = low corrections
        correction_ratio = self._add_noise(random.uniform(0.00, 0.08))
        
        # Real observed data: 10-45 px/sec = 3k-13.5k pixels per 5-min block
        mouse_px_per_sec = self._add_noise(random.uniform(10, 45))
        
        # Interaction rate: Moderate
        mouse_cpm = self._add_noise(random.uniform(2, 12))
        
        # Locked in: Few context switches
        switch_freq = self._add_noise(random.uniform(0.0, 0.6))  # 0-3 switches per 5 min
        
        # TIME-WEIGHTED APP SCORE: 4 min IDE (1.0) + 1 min Spotify (-1.0) = 0.6
        # Generate as continuous distribution, not discrete choices
        app_score = random.uniform(0.6, 1.0)
        
        # Thinking time allowed
        idle_ratio = self._add_noise(random.uniform(0.10, 0.40))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Flow'
        ]

    def _generate_debugging(self, fatigue_hrs):
        """
        DEBUGGING (Fixing) - REALISTIC
        - Typing: 30-90 KPM (active problem-solving)
        - Correction Ratio: > 12% (The struggle signal: high deletions/rewriting)
        - Mouse: 20-55 px/sec (scattered, jumping between windows; real ~6k-16.5k pixels)
        - Switching: 4-12 per 5 min (IDE ↔ Browser ↔ Terminal)
        - App Score: 0.4-0.9 (Time-weighted: IDE + Browser mix)
        """
        typing_kpm = self._add_noise(random.uniform(30, 90))
        
        # The primary differentiator: High struggles signal
        correction_ratio = self._add_noise(random.uniform(0.12, 0.35))
        
        # Debugging: scattered movement between errors and code (20-55 px/sec = 6k-16.5k pixels)
        mouse_px_per_sec = self._add_noise(random.uniform(20, 55))
        
        # Moderately high interaction (clicking error messages, etc.)
        mouse_cpm = self._add_noise(random.uniform(15, 40))
        
        # High switching: Code -> Browser -> Terminal for debugging
        switch_freq = self._add_noise(random.uniform(0.8, 2.5))  # 4-12 per 5 min
        
        # TIME-WEIGHTED: IDE (1.0) + Browser (0.5) mix = 0.4-0.9
        app_score = random.uniform(0.4, 0.9)
        idle_ratio = self._add_noise(random.uniform(0.10, 0.30))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Debugging'
        ]

    def _generate_research(self, fatigue_hrs):
        """
        RESEARCH (Learning/Reading) - REALISTIC
        - Typing: < 25 KPM (mostly reading, occasional copy-paste)
        - Mouse: 100-300 px/sec (constant scrolling through docs)
        - App Score: 0.4-0.6 (Browser or "Tutorial with Project" = neutral/research)
        - Idle: 20-50% (reading long text blocks)
        """
        # Very low typing: mostly reading
        typing_kpm = self._add_noise(random.uniform(0, 25))
        
        # Minimal corrections while reading
        correction_ratio = self._add_noise(random.uniform(0.0, 0.05))
        
        # Real observed: Research scrolling 30-70 px/sec (9k-21k pixels per 5min)
        # NOTE: Scrolling is bursty, not continuous - high idle ratio reflects reading
        mouse_px_per_sec = self._add_noise(random.uniform(30, 70))
        
        # Moderate clicking (maybe expanding code samples, clicking links)
        mouse_cpm = self._add_noise(random.uniform(10, 30))
        
        # Some switching (between tabs, tutorials, IDE)
        switch_freq = self._add_noise(random.uniform(0.4, 1.5))
        
        # TIME-WEIGHTED: Browser (0.5) or YouTube with project (0.5)
        app_score = random.uniform(0.4, 0.6)
        
        # Reading requires substantial staring time
        # Extended range to 0.65 for better training on high-idle edge cases
        idle_ratio = self._add_noise(random.uniform(0.20, 0.65))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Research'
        ]

    def _generate_communication(self, fatigue_hrs):
        """
        COMMUNICATION (Chat/Collaboration) - REALISTIC
        - Typing: 40-100 KPM (bursty messages and quick responses)
        - Corrections: 5-15% (casual chat, some typos)
        - Mouse: 10-60 px/sec (mostly in text boxes, slow movement)
        - CPM: 5-20 (reactions, @mentions, emojis)
        - App Score: ~0.0 (Weighted near zero: mostly Slack/Teams)
        - Idle: 20-50% (waiting for responses)
        """
        # Bursty typing during chat
        typing_kpm = self._add_noise(random.uniform(40, 100))
        
        # Casual messages have more typos
        correction_ratio = self._add_noise(random.uniform(0.05, 0.15))
        
        # Slow deliberate mouse in chat UI
        mouse_px_per_sec = self._add_noise(random.uniform(10, 60))
        
        # Reactions and mentions
        mouse_cpm = self._add_noise(random.uniform(5, 20))
        
        # Some context switching (between channels/apps)
        switch_freq = self._add_noise(random.uniform(0.5, 2.0))
        
        # Strictly at communication apps (Slack, Teams, Discord)
        app_score = random.uniform(-0.1, 0.1)  # Near 0.0 (communication)
        
        # Waiting for responses increases idle time
        idle_ratio = self._add_noise(random.uniform(0.20, 0.50))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Communication'
        ]

    def _generate_distracted(self, fatigue_hrs):
        """
        DISTRACTED (Off-Task) - REALISTIC
        - App Score: -1.0 to -0.6 (Social/Game/Entertainment without project)
        - Switching: > 10 per 5 min (doomscrolling, constant context switching)
        - Mouse: Either erratic (200-500 px/sec for gaming) OR minimal (0-20 for video)
        - Idle: 0-60% (variable: gaming = low idle, video = high idle)
        """
        # Unpredictable typing (some games require input, some don't)
        typing_kpm = self._add_noise(random.uniform(0, 80))
        
        # Variable corrections (casual, often 0)
        correction_ratio = self._add_noise(random.uniform(0.0, 0.20))
        
        # Distracted mouse movement varies: YouTube scrolling vs gaming
        if random.random() < 0.5:
            # YouTube/Reddit scrolling: moderate mouse movement (max observed ~20k pixels)
            mouse_px_per_sec = self._add_noise(random.uniform(25, 67))
            # Scrolling interactions: moderate click rate
            mouse_cpm = self._add_noise(random.uniform(15, 50))
        else:
            # Video watching OR brief distraction: minimal mouse movement
            mouse_px_per_sec = self._add_noise(random.uniform(0, 15))
            mouse_cpm = self._add_noise(random.uniform(5, 30))
        
        # The key differentiator: Extremely high switching (doomscrolling)
        switch_freq = self._add_noise(random.uniform(2.0, 5.0))  # 10-25 per 5 min
        
        # Strictly distraction apps with NO project context
        app_score = random.uniform(-1.0, -0.6)
        
        # Variable idle (gaming = low, video = high)
        # Extended range to 0.70 for better training on edge cases near heuristic threshold
        idle_ratio = self._add_noise(random.uniform(0.0, 0.70))
        
        return [
            typing_kpm, correction_ratio, mouse_px_per_sec, mouse_cpm,
            switch_freq, app_score, idle_ratio, fatigue_hrs, 'Distracted'
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

