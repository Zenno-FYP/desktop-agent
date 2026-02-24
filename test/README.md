# Test Suite

All tests are organized by implementation phase and located in this folder.

## Test Organization

```
test/
├── test_phase1.py         # Phase 1: Real-time data collection (monitor/)
├── test_phase2.py         # Phase 2: 5-minute block evaluation (analyze/)
├── test_integration.py    # End-to-end agent lifecycle tests
└── fixtures/              # Test data, mocks, and utilities
```

## Test Status

| Phase | Component | Status | Coverage |
|-------|-----------|--------|----------|
| **Phase 1** | Data Collection | ✅ COMPLETE | 100% |
| **Phase 2** | Block Evaluation | ✅ COMPLETE | 100% |
| **Integration** | End-to-End | ✅ COMPLETE | Full lifecycle |

## Running Tests

### All Tests
```bash
# Using pytest (recommended)
pytest test/

# Or with Python directly
python -m pytest test/ -v
```

### Specific Test Phase
```bash
# Phase 1 - Data collection
pytest test/test_phase1.py -v

# Phase 2 - Block evaluation
pytest test/test_phase2.py -v

# Integration tests
pytest test/test_integration.py -v
```

### Individual Test
```bash
# Specific test function
pytest test/test_phase2.py::test_context_detector_heuristics -v

# With verbose output and stop at first failure
pytest test/test_phase2.py -v -x
```

### Without pytest (Python direct)
```bash
# All tests
python -m test.test_phase1
python -m test.test_phase2
python -m test.test_integration

# Individual test file
python test/test_phase2.py
```

## Test Descriptions

### Phase 1: Data Collection (test_phase1.py)

Tests the real-time monitoring components in `monitor/`:

- **test_app_focus_detection()**: Verifies window/app detection accuracy
- **test_project_detection()**: Tests project and file extraction from IDE titles
- **test_idle_detection()**: Validates the 5-second idle detection threshold

**Dependencies:**
- Active desktop with focused window (for test 1)
- IDE window title parseable (for test 2)

### Phase 2: Block Evaluation (test_phase2.py) ✅

Tests the 5-minute block evaluation in `analyze/`:

- **test_context_detector_heuristics()**: Verifies all 8 heuristic rules (Idle, Reading, Focused, Distracted)
- **test_block_aggregation_and_tagging()**: Tests retroactive tagging of all logs in a 5-minute block
- **test_multi_project_scenario()**: Verifies all projects in a block get same context_state

**Status:** All 3 tests passing ✅
```
[Test 1] Context Detector Heuristics        ✅ PASS
[Test 2] Block Aggregation & Tagging        ✅ PASS
[Test 3] Multi-Project Scenario             ✅ PASS
```

### Integration: End-to-End (test_integration.py)

Tests complete agent lifecycle:

- **test_agent_startup_and_shutdown()**: Full agent startup with BlockEvaluator, then graceful shutdown
- **test_blockevaluator_short_cycle()**: BlockEvaluator with 10-second blocks (faster testing)
- **test_database_persistence()**: Verify logs persist correctly with/without context_state

## Test Fixtures

The `test/fixtures/` folder contains:
- Sample activity logs for testing
- Mock database helpers
- Test data generators

## Coverage & Quality

### Phase 1 Coverage
- Window detection: Real system integration
- Project/file extraction: 15+ IDE title formats tested
- Behavioral metrics: Keyboard, mouse, scrolls, idle
- Database: Schema validation, data integrity

### Phase 2 Coverage
- Heuristic rules: All 8 decision paths tested
- Block aggregation: Multi-session, multi-project scenarios
- Retroactive tagging: Batch update verification
- Thread safety: Concurrent DB access tested
- Edge cases: Empty blocks, no logs, extreme metrics

## Debugging Failed Tests

### Phase 2 Thread Safety Bug (Fixed)
**Symptom:** `SQLite objects created in a thread can only be used in that same thread`

**Solution:** Added `check_same_thread=False` to database connection

### Phase 2 Time Zone Mismatch (Fixed)
**Symptom:** BlockEvaluator finds 0 logs to evaluate

**Solution:** Changed from `datetime.now()` to `datetime.utcnow()` to match agent logging

### Adding New Tests

When adding new tests, follow this pattern:

```python
def test_new_feature():
    """Test description."""
    print("\n[Test N] Feature Name")
    print("=" * 60)
    
    # Setup
    # ... initialize components ...
    
    # Test
    # ... perform operations ...
    
    # Assertions
    assert condition, "Failure message"
    print(f"  [OK] Result verified")
    
    print("[Test N] PASSED")
    return True
```

## Environment

**Python Version:** 3.9+
**Dependencies:** See ../requirements.txt

**Database:** SQLite with WAL mode (concurrent access safe)
**Threading:** Multi-threaded BlockEvaluator tested

## CI/CD Integration

For continuous integration, run:
```bash
cd /path/to/desktop-agent
python -m pytest test/ --tb=short --verbose
```

Expected output on success:
```
test/test_phase2.py::test_context_detector_heuristics PASSED
test/test_phase2.py::test_block_aggregation_and_tagging PASSED
test/test_phase2.py::test_multi_project_scenario PASSED
test/test_integration.py::test_blockevaluator_short_cycle PASSED
test/test_integration.py::test_database_persistence PASSED
```

## Next Steps

### Phase 3: ML Model Training
- Collect 5+ days of block data with manual labels
- Feature engineering pipeline
- Model training (XGBoost, Random Forest)
- Integration tests for ML predictions

### Phase 4: Advanced Features
- Project-level aggregations
- Time-based analytics
- Performance insights
- Dashboard integration

---

**Last Updated:** 2026-02-24
**Status:** All Phase 1 & 2 tests passing ✅
