"""
Test Suite: Phase 2 - 5-Minute Block Evaluation

Tests all BlockEvaluator and ContextDetector components with realistic scenarios.

Run with: pytest test/ or python -m pytest test/
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from database.db import Database
from analyze.context_detector import ContextDetector
from analyze.block_evaluator import BlockEvaluator


def get_test_db():
    """
    Get a clean, isolated in-memory SQLite database for testing.
    
    Using in-memory database (':memory:') ensures:
    - Tests don't interfere with each other
    - Tests don't touch production database
    - Tests are ultra-fast
    - Each test gets a fresh database
    
    Returns:
        Database: Connected database with schema created
    """
    db = Database(':memory:')  # In-memory SQLite (isolated, fast)
    db.connect()
    db.create_tables()
    return db


def test_context_detector_heuristics():
    """Test 1: Context detector heuristic rules for all scenarios."""
    print("\n[Test 1] Context Detector Heuristics")
    print("=" * 60)
    
    detector = ContextDetector()
    
    # Scenario 1: Idle (high idle ratio)
    idle_metrics = {
        'typing_intensity': 0,
        'mouse_click_rate': 0,
        'mouse_scroll_events': 0,
        'idle_duration_sec': 250,  # 250/300 = 83% idle
        'total_duration_sec': 300,
        'app_switch_count': 1,
        'project_switch_count': 1,
    }
    ctx, conf = detector.detect_context(idle_metrics)
    assert ctx == "Idle", f"Expected Idle, got {ctx}"
    print(f"  [OK] Scenario 1 (High idle): {ctx} ({conf:.0%})")
    
    # Scenario 2: Reading (low typing, high scrolls)
    reading_metrics = {
        'typing_intensity': 10.0,
        'mouse_click_rate': 5.0,
        'mouse_scroll_events': 12,
        'idle_duration_sec': 20,
        'total_duration_sec': 300,
        'app_switch_count': 1,
        'project_switch_count': 1,
    }
    ctx, conf = detector.detect_context(reading_metrics)
    assert ctx == "Reading", f"Expected Reading, got {ctx}"
    print(f"  [OK] Scenario 2 (Reading): {ctx} ({conf:.0%})")
    
    # Scenario 3: Focused (high typing, few switches)
    focused_metrics = {
        'typing_intensity': 60.0,
        'mouse_click_rate': 20.0,
        'mouse_scroll_events': 3,
        'idle_duration_sec': 10,
        'total_duration_sec': 300,
        'app_switch_count': 1,
        'project_switch_count': 1,
    }
    ctx, conf = detector.detect_context(focused_metrics)
    assert ctx == "Focused", f"Expected Focused, got {ctx}"
    print(f"  [OK] Scenario 3 (Focused): {ctx} ({conf:.0%})")
    
    # Scenario 4: Focused (Research) - Multiple app switches but NO distraction apps
    # (e.g., VS Code -> Browser -> Terminal -> Stack Overflow)
    research_metrics = {
        'typing_intensity': 30.0,
        'mouse_click_rate': 15.0,
        'mouse_scroll_events': 8,  # Reading across productivity apps
        'idle_duration_sec': 15,
        'total_duration_sec': 300,
        'app_switch_count': 5,  # 5 apps but all productivity
        'project_switch_count': 2,
        'touched_distraction_app': False,  # No Discord, Twitter, etc.
    }
    ctx, conf = detector.detect_context(research_metrics)
    assert ctx == "Focused (Research)", f"Expected Focused (Research), got {ctx}"
    print(f"  [OK] Scenario 4 (Research/Debugging): {ctx} ({conf:.0%})")
    
    # Scenario 5: Distracted - Multiple app switches WITH distraction apps
    # (e.g., VS Code -> Discord -> Twitter -> Chrome)
    distracted_metrics = {
        'typing_intensity': 30.0,
        'mouse_click_rate': 15.0,
        'mouse_scroll_events': 2,
        'idle_duration_sec': 15,
        'total_duration_sec': 300,
        'app_switch_count': 5,  # 5 apps including distraction apps
        'project_switch_count': 2,
        'touched_distraction_app': True,  # Touched Discord, Twitter, etc.
    }
    ctx, conf = detector.detect_context(distracted_metrics)
    assert ctx == "Distracted", f"Expected Distracted, got {ctx}"
    print(f"  [OK] Scenario 5 (True Distraction): {ctx} ({conf:.0%})")
    
    print("\n[Test 1] PASSED - All heuristic rules work correctly")
    return True


def test_block_aggregation_and_tagging():
    """Test 2: Block aggregation and retroactive tagging."""
    print("\n[Test 2] Block Aggregation & Retroactive Tagging")
    print("=" * 60)
    
    # Create database connection (in-memory, isolated)
    db = get_test_db()
    
    # Insert test logs with current timestamps
    now = datetime.utcnow()
    test_logs = [
        {
            'start_time': (now - timedelta(seconds=40)).isoformat(),
            'end_time': (now - timedelta(seconds=35)).isoformat(),
            'app_name': 'TestApp1',
            'window_title': 'Test Window 1',
            'duration_sec': 5,
            'project_name': 'test_project',
            'project_path': '/test/path',
            'active_file': 'test1.py',
            'detected_language': 'python',
            'typing_intensity': 50.0,
            'mouse_click_rate': 20.0,
            'mouse_scroll_events': 2,
            'idle_duration_sec': 0,
        },
        {
            'start_time': (now - timedelta(seconds=33)).isoformat(),
            'end_time': (now - timedelta(seconds=28)).isoformat(),
            'app_name': 'TestApp2',
            'window_title': 'Test Window 2',
            'duration_sec': 5,
            'project_name': 'test_project',
            'project_path': '/test/path',
            'active_file': 'test2.py',
            'detected_language': 'python',
            'typing_intensity': 70.0,
            'mouse_click_rate': 15.0,
            'mouse_scroll_events': 1,
            'idle_duration_sec': 0,
        },
        {
            'start_time': (now - timedelta(seconds=25)).isoformat(),
            'end_time': (now - timedelta(seconds=20)).isoformat(),
            'app_name': 'TestApp1',
            'window_title': 'Test Window 1',
            'duration_sec': 5,
            'project_name': 'test_project',
            'project_path': '/test/path',
            'active_file': 'test1.py',
            'detected_language': 'python',
            'typing_intensity': 60.0,
            'mouse_click_rate': 18.0,
            'mouse_scroll_events': 2,
            'idle_duration_sec': 0,
        },
    ]
    
    # Insert logs
    log_ids = []
    for data in test_logs:
        db.validate_activity_log(data)
        log_id = db.insert_activity_log(data)
        log_ids.append(log_id)
    
    print(f"  [OK] Inserted {len(log_ids)} test logs")
    
    # Evaluate block manually
    detector = ContextDetector()
    evaluator = BlockEvaluator(db, detector, block_duration_sec=100)
    evaluator.evaluate_block()
    
    # Verify retroactive tagging
    # Use db.conn instead of creating new connection (works with in-memory DB)
    cursor = db.conn.cursor()
    
    cursor.execute(
        f"SELECT context_state FROM raw_activity_logs WHERE log_id IN ({','.join('?' * len(log_ids))})",
        log_ids
    )
    results = cursor.fetchall()
    
    # All logs should have context_state filled
    all_filled = all(r[0] is not None for r in results)
    assert all_filled, "Not all logs were retroactively tagged"
    print(f"  [OK] All {len(log_ids)} logs retroactively tagged")
    
    # Verify all have same context (same 5-minute block)
    contexts = [r[0] for r in results]
    all_same = len(set(contexts)) == 1
    assert all_same, "Logs in same block have different context states"
    print(f"  [OK] All logs in block have same context: {contexts[0]}")
    
    db.close()
    
    print("\n[Test 2] PASSED - Block aggregation and tagging works correctly")
    return True


def test_multi_project_scenario():
    """Test 3: Multiple projects touched in one 5-minute block."""
    print("\n[Test 3] Multi-Project Scenario")
    print("=" * 60)
    
    db = get_test_db()  # Use test database (in-memory, isolated)
    
    # Insert logs from 3 different projects in same 5-minute block
    now = datetime.utcnow()
    projects = [
        ('CC', 'main.cpp', 'C++'),
        ('website', 'index.html', 'HTML'),
        ('backend', 'api.py', 'Python'),
    ]
    
    log_ids = []
    for i, (proj, file, lang) in enumerate(projects):
        data = {
            'start_time': (now - timedelta(seconds=35 - i*10)).isoformat(),
            'end_time': (now - timedelta(seconds=25 - i*10)).isoformat(),
            'app_name': 'Code.exe',
            'window_title': f'{file} - {proj}',
            'duration_sec': 10,
            'project_name': proj,
            'project_path': f'E:/dev/{proj}',
            'active_file': file,
            'detected_language': lang,
            'typing_intensity': 45.0,
            'mouse_click_rate': 12.0,
            'mouse_scroll_events': 1,
            'idle_duration_sec': 0,
        }
        db.validate_activity_log(data)
        log_id = db.insert_activity_log(data)
        log_ids.append(log_id)
    
    print(f"  [OK] Inserted logs from {len(projects)} different projects")
    
    # Evaluate block
    detector = ContextDetector()
    evaluator = BlockEvaluator(db, detector, block_duration_sec=100)
    evaluator.evaluate_block()
    
    # Verify all get same context despite being different projects
    # Use db.conn instead of creating new connection (works with in-memory DB)
    cursor = db.conn.cursor()
    
    cursor.execute(
        f"""SELECT project_name, context_state, confidence_score 
           FROM raw_activity_logs WHERE log_id IN ({','.join('?' * len(log_ids))})
           ORDER BY log_id""",
        log_ids
    )
    results = cursor.fetchall()
    
    contexts = [r[1] for r in results]
    assert len(set(contexts)) == 1, "Different projects have different contexts"
    
    print(f"  [OK] All projects in block tagged with: {contexts[0]}")
    for proj, _, _, ctx, conf in [(r[0], r[0], r[0], r[1], r[2]) for r in results]:
        print(f"       {proj:12} → {ctx:12} ({conf:.0%})")
    
    db.close()
    
    print("\n[Test 3] PASSED - Multi-project scenario handled correctly")
    return True


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PHASE 2 TEST SUITE - Block Evaluation")
    print("="*60)
    
    try:
        test1 = test_context_detector_heuristics()
        test2 = test_block_aggregation_and_tagging()
        test3 = test_multi_project_scenario()
        
        if test1 and test2 and test3:
            print("\n" + "="*60)
            print("[SUCCESS] All Phase 2 tests passed!")
            print("="*60)
        else:
            print("\n[FAILED] Some tests did not pass")
            sys.exit(1)
    
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
