"""PHASE 1: Real-time monitoring and raw data collection.

This module collects raw behavioral signals from the desktop in real-time.
All components focus on capturing ground truth data without judgment.

Components:
  - app_focus: Detects active window and application
  - behavioral_metrics: Tracks keyboard/mouse input intensity
  - idle_detector: Measures inactivity periods
  - project_detector: Extracts project/file context from IDE

Output: Raw activity logs with NULL context_state (waiting for Phase 2 evaluation)
"""
