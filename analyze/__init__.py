"""PHASE 2: Block evaluation and context state detection.

This module evaluates the raw behavioral data from Phase 1 to determine
the developer's mental/work context (Focused, Reading, Distracted, Idle).

Architecture:
  - BlockEvaluator: Background thread that runs every 5 minutes
  - ContextDetector: Decision logic for classifying work context

Workflow:
  1. BlockEvaluator wakes every 5 minutes
  2. Queries all unevaluated logs (context_state IS NULL) from last 5 minutes
  3. Aggregates their behavioral metrics into block-level statistics
  4. Calls ContextDetector to classify the developer's state
  5. Retroactively updates all logs in the block with context_state + confidence_score

Output: Populated raw_activity_logs with context_state and confidence_score filled
"""
