"""
Test Suite: App Categorization Integration

Tests that app categorization is properly integrated into BlockEvaluator
and correctly identifies distraction vs. productive apps.

Run with: pytest test/test_app_categorization.py -v
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3
import tempfile
import os

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.db import Database
from analyze.context_detector import ContextDetector
from analyze.block_evaluator import BlockEvaluator


def test_is_distraction_app_detection():
    """Test that context detector correctly identifies distraction apps."""
    print("\n[Test 1] App Categorization Detection")
    print("=" * 60)
    
    detector = ContextDetector()
    
    # Test distraction apps
    distraction_cases = [
        ('discord.exe', True),
        ('Discord.exe', True),
        ('DISCORD', True),
        ('telegram.exe', True),
        ('whatsapp.exe', True),
        ('twitter.exe', True),
        ('x.exe', True),
        ('spotify.exe', True),
        ('netflix.exe', True),
        ('reddit.exe', True),
    ]
    
    for app, should_be_distraction in distraction_cases:
        result = detector.is_distraction_app(app)
        assert result == should_be_distraction, f"Failed for {app}: expected {should_be_distraction}, got {result}"
        print(f"  [OK] {app:20} -> Distraction: {result}")
    
    # Test productivity apps (should be False)
    productivity_cases = [
        ('code.exe', False),
        ('pycharm.exe', False),
        ('chrome.exe', False),
        ('terminal.exe', False),
        ('powershell.exe', False),
        ('teams.exe', False),
    ]
    
    for app, should_be_distraction in productivity_cases:
        result = detector.is_distraction_app(app)
        assert result == should_be_distraction, f"Failed for {app}: expected {should_be_distraction}, got {result}"
        print(f"  [OK] {app:20} -> Not Distraction: {not result}")
    
    print("\n[Test 1] PASSED - App categorization detection works")
    return True


def test_block_evaluator_distraction_tracking():
    """Test that BlockEvaluator properly tracks distraction app usage."""
    print("\n[Test 2] BlockEvaluator Distraction Tracking")
    print("=" * 60)
    
    detector = ContextDetector()
    
    # Test scenario 1: Logs with productivity apps only
    logs_productivity = [
        {
            'log_id': 1,
            'app_name': 'code.exe',
            'duration_sec': 60,
            'typing_intensity': 40.0,
            'mouse_click_rate': 10.0,
            'mouse_scroll_events': 2,
            'idle_duration_sec': 0,
            'project_name': 'test_project',
        },
        {
            'log_id': 2,
            'app_name': 'chrome.exe',
            'duration_sec': 60,
            'typing_intensity': 5.0,
            'mouse_click_rate': 8.0,
            'mouse_scroll_events': 10,  # Reading docs
            'idle_duration_sec': 0,
            'project_name': 'test_project',
        },
        {
            'log_id': 3,
            'app_name': 'terminal.exe',
            'duration_sec': 60,
            'typing_intensity': 30.0,
            'mouse_click_rate': 5.0,
            'mouse_scroll_events': 0,
            'idle_duration_sec': 0,
            'project_name': 'test_project',
        },
    ]
    
    db_temp = Database(':memory:')  # Use in-memory DB for test
    db_temp.connect()
    evaluator = BlockEvaluator(db_temp, detector)
    
    # Aggregate productivity logs
    metrics = evaluator._aggregate_block_metrics(logs_productivity)
    
    assert metrics['app_switch_count'] == 3, f"Expected 3 apps, got {metrics['app_switch_count']}"
    assert metrics['touched_distraction_app'] == False, "Should not detect distraction apps"
    print(f"  [OK] Productivity apps (3 switches): touched_distraction = {metrics['touched_distraction_app']}")
    
    # Verify the context classification for this
    context, confidence = detector.detect_context(metrics)
    print(f"      Context: {context} ({confidence:.0%})")
    assert context == "Focused (Research)", f"Expected Focused (Research), got {context}"
    print(f"  [OK] Correctly classified as Focused (Research)")
    
    # Test scenario 2: Logs with distraction app
    logs_with_distraction = logs_productivity + [
        {
            'log_id': 4,
            'app_name': 'discord.exe',  # Add distraction
            'duration_sec': 60,
            'typing_intensity': 2.0,
            'mouse_click_rate': 5.0,
            'mouse_scroll_events': 1,
            'idle_duration_sec': 50,  # Mostly idle while on Discord
            'project_name': 'test_project',
        },
    ]
    
    metrics2 = evaluator._aggregate_block_metrics(logs_with_distraction)
    
    assert metrics2['app_switch_count'] == 4, f"Expected 4 apps, got {metrics2['app_switch_count']}"
    assert metrics2['touched_distraction_app'] == True, "Should detect distraction app (Discord)"
    print(f"  [OK] Mix with Discord (4 switches): touched_distraction = {metrics2['touched_distraction_app']}")
    
    # Verify the context classification for this
    context2, confidence2 = detector.detect_context(metrics2)
    print(f"      Context: {context2} ({confidence2:.0%})")
    # With low typing and distraction app, should be Distracted
    assert context2 == "Distracted", f"Expected Distracted when Discord touched, got {context2}"
    print(f"  [OK] Correctly classified as Distracted when Discord touched")
    
    db_temp.close()
    
    print("\n[Test 2] PASSED - BlockEvaluator distraction tracking works")
    return True


def test_real_world_scenarios():
    """Test real-world activity patterns."""
    print("\n[Test 3] Real-World Scenarios")
    print("=" * 60)
    
    detector = ContextDetector()
    
    scenarios = [
        {
            'name': 'Debugging workflow',
            'metrics': {
                'typing_intensity': 35.0,
                'mouse_click_rate': 12.0,
                'mouse_scroll_events': 8,
                'idle_duration_sec': 10,
                'total_duration_sec': 300,
                'app_switch_count': 4,  # VS Code -> Chrome -> Terminal -> VS Code
                'project_switch_count': 1,
                'touched_distraction_app': False,  # All productivity apps
            },
            'expected': 'Focused (Research)',
        },
        {
            'name': 'Distracted browsing',
            'metrics': {
                'typing_intensity': 15.0,
                'mouse_click_rate': 20.0,
                'mouse_scroll_events': 3,
                'idle_duration_sec': 50,
                'total_duration_sec': 300,
                'app_switch_count': 5,  # VS Code -> Discord -> Twitter -> YouTube -> VS Code
                'project_switch_count': 1,
                'touched_distraction_app': True,  # Discord/Twitter/YouTube
            },
            'expected': 'Distracted',
        },
        {
            'name': 'Deep focus coding',
            'metrics': {
                'typing_intensity': 55.0,
                'mouse_click_rate': 18.0,
                'mouse_scroll_events': 2,
                'idle_duration_sec': 5,
                'total_duration_sec': 300,
                'app_switch_count': 1,  # Just VS Code
                'project_switch_count': 1,
                'touched_distraction_app': False,
            },
            'expected': 'Focused',
        },
        {
            'name': 'Reading documentation',
            'metrics': {
                'typing_intensity': 8.0,
                'mouse_click_rate': 5.0,
                'mouse_scroll_events': 15,
                'idle_duration_sec': 20,
                'total_duration_sec': 300,
                'app_switch_count': 2,  # VS Code -> Chrome for docs
                'project_switch_count': 1,
                'touched_distraction_app': False,
            },
            'expected': 'Reading',
        },
        {
            'name': 'Away from desk',
            'metrics': {
                'typing_intensity': 0,
                'mouse_click_rate': 0,
                'mouse_scroll_events': 0,
                'idle_duration_sec': 270,  # 90% idle
                'total_duration_sec': 300,
                'app_switch_count': 1,
                'project_switch_count': 1,
                'touched_distraction_app': False,
            },
            'expected': 'Idle',
        },
    ]
    
    for scenario in scenarios:
        context, confidence = detector.detect_context(scenario['metrics'])
        is_correct = context == scenario['expected']
        status = "OK" if is_correct else "FAIL"
        print(f"  [{status}] {scenario['name']:25} -> {context:25} ({confidence:.0%})")
        if not is_correct:
            print(f"       Expected: {scenario['expected']}")
            assert is_correct, f"Scenario '{scenario['name']}' failed"
    
    print("\n[Test 3] PASSED - All real-world scenarios classified correctly")
    return True


if __name__ == '__main__':
    test_is_distraction_app_detection()
    test_block_evaluator_distraction_tracking()
    test_real_world_scenarios()
    print("\n" + "=" * 60)
    print("ALL APP CATEGORIZATION TESTS PASSED!")
    print("=" * 60)
