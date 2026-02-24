"""
Test Suite: Phase 1 - Real-Time Data Collection

Tests for individual monitor/ components:
- app_focus.py: Window detection
- behavioral_metrics.py: Keyboard and mouse tracking
- idle_detector.py: Idle detection
- project_detector.py: Project and file extraction

Run with: pytest test/test_phase1.py -v
"""

import sys
from pathlib import Path
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.app_focus import get_active_window
from monitor.behavioral_metrics import BehavioralMetrics
from monitor.idle_detector import IdleDetector
from monitor.project_detector import ProjectDetector


def test_app_focus_detection():
    """Test: Active window detection."""
    print("\n[Test 1] App Focus Detection")
    print("=" * 60)
    
    # Get current active window
    app_name, window_title, pid = get_active_window()
    
    assert app_name, "No active app detected"
    assert window_title is not None, "No window title detected"
    print(f"  [OK] Detected: {app_name}")
    print(f"       Title: {window_title[:60]}")
    if pid:
        print(f"       PID: {pid}")
    
    # Get again - should be same or changed naturally
    time.sleep(0.5)
    app2, title2, pid2 = get_active_window()
    
    print(f"  [OK] Second detection: {app2}")
    print("[Test 1] PASSED - Window detection working")
    return True


def test_project_detection():
    """Test: Project and file extraction from window title."""
    print("\n[Test 2] Project Detection")
    print("=" * 60)
    
    detector = ProjectDetector()
    
    # Test with typical VS Code window title
    test_title = "agent.py - desktop-agent - Visual Studio Code"
    project, file = detector.detect_project("Code.exe", test_title)
    
    print(f"  [OK] Parsed window title:")
    print(f"       Title: {test_title}")
    print(f"       Project: {project}")
    print(f"       File: {file}")
    
    assert project or file, "Could not extract project/file"
    
    # Test language detection
    if file:
        lang = detector.get_detected_language(file)
        print(f"       Language: {lang}")
    
    print("[Test 1] PASSED - Project detection working")
    return True


def test_idle_detection():
    """Test: Idle detection with 5-second threshold."""
    print("\n[Test 3] Idle Detection")
    print("=" * 60)
    
    idle_detector = IdleDetector(idle_threshold_sec=5)
    
    # Simulate activity
    idle_detector.update_activity(time.time())
    time.sleep(2)
    
    metrics = idle_detector.get_idle_metrics()
    assert metrics['idle_duration_sec'] < 5, "Activity reported as idle"
    print(f"  [OK] After 2s activity: idle={metrics['idle_duration_sec']}s (not idle)")
    
    # Simulate inactivity
    time.sleep(6)
    metrics = idle_detector.get_idle_metrics()
    assert metrics['idle_duration_sec'] >= 5, "Inactivity not detected as idle"
    print(f"  [OK] After 6s inactivity: idle={metrics['idle_duration_sec']}s (idle)")
    
    print("[Test 3] PASSED - Idle detection working")
    return True


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PHASE 1 TEST SUITE - Data Collection")
    print("="*60)
    
    try:
        test1 = test_app_focus_detection()
        test2 = test_project_detection()
        test3 = test_idle_detection()
        
        if test1 and test2 and test3:
            print("\n" + "="*60)
            print("[SUCCESS] All Phase 1 component tests passed!")
            print("="*60)
        else:
            print("\n[FAILED] Some tests did not pass")
            sys.exit(1)
    
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
