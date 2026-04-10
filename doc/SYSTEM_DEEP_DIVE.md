# Zenno Desktop Agent — Complete System Deep Dive

**Version**: 2.0  
**Scope**: Full technical reference — collection, analysis, aggregation, nudge engine, auth, sync, database schema, threading model, and timing.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Startup Sequence](#2-startup-sequence)
3. [Phase 1 — Real-Time Data Collection](#3-phase-1--real-time-data-collection)
4. [Phase 2 — Context Detection (Block Evaluator)](#4-phase-2--context-detection-block-evaluator)
5. [Phase 3 — Machine Learning Pipeline](#5-phase-3--machine-learning-pipeline)
6. [Phase 4 — ETL Aggregation Pipeline](#6-phase-4--etl-aggregation-pipeline)
7. [Phase 5 — Nudge Engine](#7-phase-5--nudge-engine)
8. [Auth & Token Management](#8-auth--token-management)
9. [Backend Sync](#9-backend-sync)
10. [Database Schema — All Tables](#10-database-schema--all-tables)
11. [Threading Model](#11-threading-model)
12. [Timing Reference](#12-timing-reference)
13. [Configuration Reference](#13-configuration-reference)
14. [Data Flow Diagram](#14-data-flow-diagram)

---

## 1. System Overview

Zenno Desktop Agent is a locally-running Python application that silently observes developer activity and builds a rich model of how a developer spends their time. It operates in five pipelined phases:

```
┌──────────────────────────────────────────────────────────────┐
│  PHASE 1: Collection                                          │
│  app_focus → session → behavioral_metrics → raw_activity_logs│
└─────────────────────────┬────────────────────────────────────┘
                          │ writes
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  PHASE 2 + 3: Block Evaluation + ML                          │
│  BlockEvaluator → FeatureExtractor → XGBoost / Heuristic     │
│  → updates context_state on raw_activity_logs                │
└─────────────────────────┬────────────────────────────────────┘
                          │ triggers
                          ▼
┌──────────────────────────────────────────────────────────────┐
│  PHASE 4: ETL Aggregation                                     │
│  ETLPipeline → 6 aggregators → daily_project_* tables        │
└──────────┬──────────────────────────┬────────────────────────┘
           │ read-only                │ syncs to backend
           ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│  PHASE 5: Nudge      │   │  ActivitySyncer → REST API       │
│  NudgeScheduler      │   └──────────────────────────────────┘
│  → Gemini LLM        │
│  → Desktop Toast     │
└──────────────────────┘
```

All phases share a single **SQLite database** (`data/db/zenno.db`) opened in WAL (Write-Ahead Logging) mode for safe concurrent access. No phase modifies data owned by an earlier phase except for the context tagging write-back in Phase 2.

---

## 2. Startup Sequence

**Entry point:** `main.py`

```
main()
  │
  ├── Load Config (config/config.yaml)
  ├── Configure Python logging
  ├── Database.connect() + create_tables()     ← ensures all tables exist
  ├── run_auth_window(db)                       ← pywebview Firebase sign-in
  │     blocks until user authenticates or closes window
  │     on success → upsert_local_user(db)
  │
  └── DesktopAgent(config_path)
        │
        ├── BehavioralMetrics (global listeners — NOT started yet)
        ├── BlockEvaluator.start()              ← background thread begins
        ├── LOCScanner init
        ├── ETLPipeline init
        ├── ActivitySyncer init
        ├── NudgeScheduler init (if nudge.enabled)
        │
        └── DesktopAgent.start()
              ├── metrics.start_listening()      ← keyboard/mouse listeners ON
              ├── NudgeScheduler.start()         ← nudge background thread
              └── main monitoring loop (infinite)
```

**Alternative entry:** `agent.py` (`if __name__ == "__main__"`) skips auth and starts the agent directly. Used for development/testing.

---

## 3. Phase 1 — Real-Time Data Collection

### 3.1 Main Monitoring Loop

**File:** `agent.py` — `DesktopAgent.start()`  
**Tick rate:** `sample_interval_sec` (default **5 s**, configurable)

On every tick:

1. **`get_active_window()`** (`monitor/app_focus.py`)  
   - Win32 API: `GetForegroundWindow` → `GetWindowTextW` → `GetWindowThreadProcessId` → `GetModuleFileNameExW`  
   - Returns `(app_name, window_title, pid)` or `(None, None, None)` if nothing is focused.

2. **Session boundary detection:**
   - If `app_name ≠ current_app` → flush current session, start new `ActivitySession`
   - If same app but window title implies a file change (`has_file_changed`) → flush and start new session within same app (tab switch)
   - Otherwise → `update_file_context` (title may change without file change)

3. **Idle tracking:** Pass last activity timestamp from `BehavioralMetrics` into `IdleDetector.update_activity()`.

4. **Periodic flush:** If `time.time() - last_flush_time ≥ flush_interval_sec` (default **300 s**), force-flush and restart session.

5. **LOC scan check:** `_check_idle_and_scan_loc()` — fires `LOCScanner.scan_all_projects()` every `loc_scan_interval_sec` (default **3600 s**).

6. **ETL + Sync check:** `_run_etl_and_sync()` — runs ETL pipeline + optional sync every `etl_interval_sec` (default **900 s**).

### 3.2 ActivitySession

**File:** `agent.py` — `ActivitySession`

Each session captures one contiguous window of a single foreground application. On construction:
- Resets `BehavioralMetrics` counters (shared global instance — listeners never stop)
- Creates a fresh `IdleDetector`
- Calls `ProjectDetector.detect_project(app_name, window_title)` for initial project/file

On `collect_data()`:
- Computes `duration_sec` (minimum 1 s)
- Reads `BehavioralMetrics.get_metrics()` → `typing_intensity` (KPM proxy), `mouse_click_rate` (CPM), `deletion_key_presses`, `mouse_movement_distance`
- Reads `IdleDetector.get_idle_metrics()` → `idle_duration_sec`
- Resolves `detected_language` from file extension
- Resolves `project_path` via `ProjectDetector.get_project_path()`
- Returns a flat dict ready for database insertion

### 3.3 BehavioralMetrics

**File:** `monitor/behavioral_metrics.py`

| Signal | Collection method | Rate |
|--------|------------------|------|
| `typing_intensity` (KPM) | pynput keyboard listener counts key presses; `keys / elapsed_min` | per-keypress |
| `mouse_click_rate` (CPM) | pynput mouse listener with `click_debounce_ms` (default 50 ms) | per-click |
| `deletion_key_presses` | Backspace + Delete key counts | per-keypress |
| `mouse_movement_distance` | Daemon thread samples cursor position every **0.1 s** (10 Hz); accumulates Euclidean deltas > 1 px | 10 Hz |

All counters are **global per agent process** — they are `reset()` at the start of each new session, not between ticks.

### 3.4 IdleDetector

**File:** `monitor/idle_detector.py`

Tracks elapsed time where no input events were observed.
- `idle_threshold_sec` (default **10 s**) — gap without activity triggers idle
- `idle_duration_sec` in the collected session = total idle seconds during that session
- Used by ETL and Nudge Engine to distinguish "active" time from "away" time

### 3.5 ProjectDetector

**File:** `monitor/project_detector.py`

Three-layer resolution for `(project_name, active_file)`:

| Layer | Method | How it works |
|-------|--------|-------------|
| 1 | PID-based | `psutil` open files list for the IDE process; walk up directory tree looking for project root markers (`.git`, `package.json`, etc.) |
| 2 | Window title parsing | Parse VS Code format (`filename — path — IDE`), PyCharm format (`project — [file]`), generic fallback |
| 3 | Safe filesystem search | Bounded drive walk (`max_depth: 6`, `time_limit_sec: 1.0`); skips Windows/system dirs |

**Sticky project (shift-left):**  
When a generic app (browser, terminal) has no detected project, `DesktopAgent._apply_sticky_project()` checks if the last IDE project is within `sticky_project_ttl_sec` (default **900 s = 15 min**). If so, the raw log is saved with that project name — keeping the database clean from the first insert, with no ETL patching needed.

### 3.6 Raw Activity Log Insert

Every flushed session calls:
1. `db.validate_activity_log()` — caps KPM/CPM at 200 (configurable), ensures required fields
2. `db.insert_activity_log()` — INSERT into `raw_activity_logs` with `context_state = NULL`, `is_aggregated = 0`

The log row stays in this "untagged, unaggregated" state until Phase 2 processes it.

---

## 4. Phase 2 — Context Detection (Block Evaluator)

### 4.1 Overview

**File:** `analyze/block_evaluator.py`  
**Threading:** Daemon background thread, started at agent init  
**Trigger:** Wall-clock block boundaries — wakes up at the next epoch multiple of `block_duration_sec` (default **300 s = 5 min**)

For example, with `block_duration_sec = 300`:
- Wakes at 12:05:00, 12:10:00, 12:15:00 ...
- Queries all rows with `context_state IS NULL` from the past 5 minutes
- Classifies them as a group (not individually)
- Writes `context_state` + `confidence_score` back to those rows
- Immediately triggers `ETLPipeline.run()` so aggregates are always fresh after classification

### 4.2 Block Metric Aggregation

`_aggregate_block_metrics()` computes:

| Metric | Computation |
|--------|------------|
| `typing_intensity` | Duration-weighted average KPM across all logs in block |
| `mouse_click_rate` | Duration-weighted average CPM |
| `mouse_movement_distance` | Sum across logs |
| `deletion_key_presses` | Sum |
| `idle_duration_sec` | Sum |
| `total_duration_sec` | Sum |
| `app_sessions` | List of `(app_name, duration_sec)` tuples |
| `app_switch_count` | Count of distinct consecutive app changes |
| `project_name` | Majority project or sticky project from logs |
| `consecutive_work_hours` | Session fatigue accumulator (resets on >30 min break) |

### 4.3 Prediction Path

```
block_metrics
    │
    ├── Idle override: idle_ratio > idle_ratio_threshold (default 0.70)
    │     → immediate heuristic fallback (no ML call)
    │
    ├── ML path (if ml_enabled AND model loaded):
    │     FeatureExtractor.extract_features(block_metrics)
    │     → 8-dim feature vector
    │     → MLPredictor.predict_with_confidence()
    │     → if confidence ≥ ml_confidence_threshold (default 0.5): use ML state
    │     → else: fallback to heuristic
    │
    └── Heuristic path (ContextDetector.detect_context):
          8-signal decision tree → (state, confidence)
```

**ML is tried first; heuristic is the fallback.** If the model file does not exist, ML is silently disabled and the heuristic runs for every block.

### 4.4 Heuristic Decision Tree (ContextDetector)

**File:** `analyze/context_detector.py`

Signals evaluated in order:

| Priority | Signal | Trigger | State |
|----------|--------|---------|-------|
| 0 | Idle ratio > 0.70 | Context-aware: where were they idle? | Communication (Zoom), Research (IDE), Distracted (other) |
| 1 | Communication app | Slack/Teams/Zoom in app_sessions | Communication |
| 2 | Correction ratio > 12% | High deletion rate = trial & error | Debugging |
| 3 | Pure reading | KPM ≈ 0 + high CPM (link clicks) | Research |
| 4 | Distraction app | YouTube/Reddit/etc. in session | Distracted |
| 5 | Flow conditions | KPM in range, correction < 8%, productive app, moderate mouse | Flow (with fatigue modifier) |
| 6 | Debugging pattern | App switches + high corrections | Debugging |
| 7 | Research pattern | Low KPM + moderate mouse + IDE | Research |
| 8 | Default | None of the above | Distracted |

Confidence scores are returned along with each classification (0.55–0.92 range, lower for fatigued or ambiguous states).

### 4.5 ESM Popup (Ground-Truth Collection)

When `confidence < esm_popup.confidence_threshold` (default 0.70) AND rate limits allow:
- `ESMPopup.queue_for_verification()` spawns a daemon thread
- That thread launches `ml/_esm_runner.py` as a **subprocess**
- A pywebview HTML window appears top-right with 5 state buttons
- User clicks a button → choice written to subprocess stdout → parent reads it
- `db.update_log_verification(log_id, verified_label)` stores the correction

**Rate limits:** `min_interval_hours: 0.25` (15 min between popups), `max_per_day: 20`.  
**Auto-dismiss:** 30 seconds (configurable).

---

## 5. Phase 3 — Machine Learning Pipeline

### 5.1 Feature Vector

**File:** `ml/feature_extractor.py`  
**Output:** 8-dimensional `np.float32` array

| Index | Feature name | Description |
|-------|-------------|-------------|
| 0 | `typing_intensity` | Duration-weighted KPM (0–200) |
| 1 | `correction_ratio` | `deletion_key_presses / total_keystrokes` (0–1) |
| 2 | `mouse_velocity` | `mouse_movement_distance / total_duration_sec` (px/s, 0–500) |
| 3 | `mouse_click_rate` | Duration-weighted CPM (0–200) |
| 4 | `app_switch_freq` | App switches per minute (0–20 cap) |
| 5 | `app_score` | Time-weighted productivity score (-1 to +1) |
| 6 | `idle_ratio` | `idle_duration_sec / total_duration_sec` (0–1) |
| 7 | `session_fatigue_factor` | Consecutive work hours / 8.0, capped at 1.0 |

**App score** calculation:
- `productive_apps` (IDEs) → +1.0
- `communication_apps` → 0.0
- `neutral_apps` (browsers) → +0.5  
  - Browser with recognized service keyword → service-specific score
- `distraction_apps` → -1.0
- Unknown → 0.0
- Final: time-weighted average across all app sessions in the block

### 5.2 XGBoost Classifier

**File:** `ml/predictor.py`  
**Model file:** `data/models/context_detector.pkl` (loaded via `joblib`)  
**Classes file:** `data/models/context_detector_classes.pkl` (optional; fallback 5-class map)

| Output class | Meaning |
|-------------|---------|
| Flow | Deep focused coding |
| Debugging | Trial-and-error fixing |
| Research | Reading/learning |
| Communication | Meetings/chat |
| Distracted | Off-task activity |

`predict_with_confidence()` returns `(predicted_state, max_probability)`.  
If the feature vector is invalid (NaN, out-of-bounds), returns `("Distracted", 0.50)` as a safe default.

### 5.3 Model Training

**File:** `ml/train_model.py`  
Training uses `verified_labels` from ESM ground-truth collection. `ml/synthetic_data_generator.py` can create synthetic training data when real samples are scarce.

---

## 6. Phase 4 — ETL Aggregation Pipeline

### 6.1 When ETL Runs

ETL is triggered from **two places**:

| Source | Trigger | Frequency |
|--------|---------|-----------|
| `BlockEvaluator.evaluate_block()` | After every block classification | Every `block_duration_sec` (default 5 min) |
| `DesktopAgent._run_etl_and_sync()` | Wall-clock timer | Every `etl_pipeline.interval_sec` (default 15 min) |

Both calls are idempotent — ETL only processes rows where `is_aggregated = 0 AND context_state IS NOT NULL`.

### 6.2 Extract Step

`ETLPipeline._extract_raw_logs()`:
- Queries `raw_activity_logs` WHERE `is_aggregated = 0 AND context_state IS NOT NULL`
- Returns all unprocessed, tagged rows

### 6.3 Transform Step

`ETLPipeline._transform_logs()` applies these transformations to each log row:

| Transformation | Logic |
|---------------|-------|
| Project assignment | If `project_name` is NULL → assign `__unassigned__` placeholder |
| Label override | If `manually_verified_label` is set → use it as `context_state` |
| App name normalization | `app_name_mapping` in config (e.g., `code.exe` → `VS Code`) |
| Browser service rename | If app is a browser, search `active_file` for service keywords (e.g., `chatgpt` → `ChatGPT`) |
| Midnight split | If a session spans midnight, split into two segments at 00:00:00 |

### 6.4 Aggregators

Six aggregators run in sequence, each generating a list of SQL UPSERT statements:

| Aggregator | Table written | Groups by | Skips `__unassigned__`? |
|-----------|--------------|-----------|------------------------|
| `ProjectAggregator` | `projects` | `project_name` | No (ensures project row exists) |
| `AppAggregator` | `daily_project_apps` | `(date, project, app)` | Yes |
| `LanguageAggregator` | `daily_project_languages` | `(date, project, language)` | Yes (also skips rows without `project_path`) |
| `SkillAggregator` | `project_skills` | `(project, skill)` | Yes |
| `ContextAggregator` | `daily_project_context` | `(date, project, context_state)` | Yes |
| `BehaviorAggregator` | `daily_project_behavior` | `(date, project)` | Yes |

All aggregators use `INSERT ... ON CONFLICT DO UPDATE` (upsert) — re-running ETL on the same data is safe and additive.

### 6.5 Load Step

`ETLPipeline._execute_batch()`:
1. Inserts `__unassigned__` into `projects` if any rows need it
2. Executes all SQL statements from all 6 aggregators in a single transaction
3. Marks processed rows: `UPDATE raw_activity_logs SET is_aggregated=1, aggregated_at=NOW()`

### 6.6 LOC Scanner

**File:** `aggregate/loc_scanner.py`  
**Trigger:** Every `loc_scan_interval_sec` (default **3600 s = 1 hour**), called from `DesktopAgent._check_idle_and_scan_loc()`

`scan_all_projects()`:
- Queries `projects` that have been active since their last LOC scan
- For each: walks the project directory (max depth **10**), skips `node_modules`, `.git`, `__pycache__`, `dist`, `venv`, etc.
- Counts lines of code per language (extension map from config)
- Upserts `project_loc_snapshots`

---

## 7. Phase 5 — Nudge Engine

### 7.1 Architecture

```
NudgeScheduler (background thread)
    │
    ├── every 30 min: _tick()
    │     │
    │     ├── Suppression checks (6 rules, see below)
    │     ├── NudgeContextAggregator.aggregate() → NudgeContext
    │     ├── NudgeGenerator.generate(ctx) → (text, llm_used)
    │     │     ├── Gemini Flash API (GEMINI_API_KEY, timeout 4s)
    │     │     └── Fallback: template library
    │     ├── NudgeNotifier.show(type, text)
    │     │     └── subprocess: nudge/_notification_runner.py
    │     │           └── pywebview HTML window, top-right, 7s
    │     └── NudgeLog.record(ctx, text, llm_used)
```

### 7.2 Suppression Rules

The scheduler suppresses a nudge (logs it as `was_suppressed=1`) when any of these conditions are true:

| Rule | Condition | Config key |
|------|-----------|------------|
| Too recent | Last non-suppressed nudge < `suppression_min` ago | `nudge.suppression_min` (default 25 min) |
| Not enough data | `total_active_min_today < min_active_min` | `nudge.min_active_min` (default 10 min) |
| User idle | `idle_ratio_in_window > 0.70` | hardcoded |
| In a meeting | `context_last_window["Communication"] > 0.80` | hardcoded |
| Diversity: type capped | REENGAGEMENT shown ≥ 2× today | `_MAX_REENGAGEMENT_PER_DAY = 2` |
| Diversity: consecutive | Same type in last 2 nudges | `_MAX_CONSECUTIVE_SAME_TYPE = 2` |

### 7.3 NudgeContext — 30+ Signals

`NudgeContextAggregator` reads `raw_activity_logs` (read-only) for today and the last 30-minute window and computes:

**Time & session**
- `generated_at`, `current_hour`, `is_working_late`
- `total_active_sec_today`, `total_active_min_today`, `session_start_time`

**Window stats (last 30 min)**
- `active_sec_in_window`, `idle_sec_in_window`, `idle_ratio_in_window`

**Break tracking**
- `min_since_last_break` — scans for idle gaps ≥ `idle_break_threshold_min` (default 5 min) AND inter-session time gaps
- `has_taken_break_today`, `longest_break_min_today`

**Mental state distributions**
- `context_today` — e.g. `{"Flow": 0.65, "Debugging": 0.20, ...}` as ratios
- `context_last_window` — same but for last 30 min only

**Behavioural trends**
- `avg_kpm_today`, `avg_kpm_last_window`, `kpm_trend` (rising/stable/declining ±10%)
- `correction_ratio_today`, `correction_ratio_last_window`, `correction_trend` (improving/stable/worsening)

**Focus streak**
- `consecutive_flow_min` — unbroken Flow minutes at end of today's logs
- `peak_flow_streak_today_min`

**Distraction**
- `distraction_ratio_today`, `distraction_ratio_last_window`
- `app_switch_rate_last_window` — switches per minute

**Project context** (from `daily_project_apps`, `daily_project_languages`)
- `top_project_today`, `top_language_today`, `projects_touched_today`

**Fatigue composite** (weighted formula):

```
fatigue = 0.30 × (hours_worked / 8)
        + 0.25 × (min_since_break / 120)
        + 0.20 × kpm_score      (1.0 if declining, 0.4 if stable)
        + 0.15 × corr_score     (1.0 if worsening, 0.4 if stable)
        + 0.10 × (distraction_ratio / 0.50)

level: low (<0.25), moderate (<0.50), high (<0.75), critical (≥0.75)
```

### 7.4 Nudge Type Decision Tree

Applied in priority order (first match wins):

| Priority | Type | Trigger condition |
|----------|------|------------------|
| 1 | `MOTIVATION` | Session < 15 min (not enough data) |
| 2 | `LATE_NIGHT` | `current_hour ≥ late_night_hour` (default 21) |
| 3 | `FATIGUE_WARNING` | `fatigue_level == "critical"` |
| 4 | `BREAK_REMINDER` | `min_since_last_break > break_reminder_min` (default 90) |
| 5 | `FLOW_CELEBRATION` | `consecutive_flow_min ≥ flow_streak_min` (default 45) |
| 6 | `REENGAGEMENT` | `distraction_ratio_last_window > distraction_threshold` (default 0.30) |
| 7 | `ACHIEVEMENT` | `flow_ratio_today ≥ 0.60 AND active_hours ≥ 3` |
| 8 | `BREAK_REMINDER` | `fatigue == "moderate" AND min_since_break > 50` |
| 9 | `MOTIVATION` | Default (solid session, no flag) |

### 7.5 LLM Generation (Gemini)

The `NudgeGenerator` builds a structured prompt:

**System:** "You are a friendly engineering coach. Generate ≤ 2 sentences, ≤ 25 words. Sound like a smart colleague. Use specific numbers when impressive."

**User context includes:** active minutes, time since break, Flow %, current state, fatigue level, top project, working late flag, nudge type + rationale.

- Model: `gemini-2.0-flash` (configurable via `nudge.llm.model`)
- Timeout: `nudge.llm.timeout_sec` (default 4 s)
- API key: `GEMINI_API_KEY` from `.env`
- On any failure (timeout, API error, missing key): uses **fallback template library**

### 7.6 Notification Window

**Files:** `nudge/nudge_notifier.py`, `nudge/_notification_runner.py`, `nudge/ui/notification.html`

- Launched as a **child subprocess** (non-blocking) — parent thread continues immediately
- Data passed via stdin as JSON: `{nudge_type, nudge_text, display_ms}`
- pywebview opens `notification.html` top-right of screen (24px from top, 24px from right)
- Slides in from above, auto-dismisses after `display_sec` (default 7 s) with progress bar
- User can click ✕ to dismiss early
- Each nudge type has its own accent colour and icon

| Type | Icon | Colour |
|------|------|--------|
| BREAK_REMINDER | 🧘 | Teal `#00b894` |
| FLOW_CELEBRATION | 🔥 | Amber `#fdcb6e` |
| REENGAGEMENT | 🎯 | Purple `#6c5ce7` |
| MOTIVATION | 💪 | Blue `#0984e3` |
| FATIGUE_WARNING | ⚠️ | Coral `#e17055` |
| LATE_NIGHT | 🌙 | Lavender `#a29bfe` |
| ACHIEVEMENT | 🏆 | Gold `#f9ca24` |

---

## 8. Auth & Token Management

### 8.1 Auth Flow

**Files:** `auth/webview_app.py`, `auth/bridge.py`, `main.py`

```
main.py → run_auth_window(db)
  │
  ├── Start localhost HTTP server serving auth/ui/index.html
  ├── Create pywebview window (480×640, "Zenno — Sign In")
  ├── Inject Firebase config as JS global (window.__FIREBASE_CONFIG__)
  ├── Block until window closes
  │
  └── User signs in (email/password or Google/GitHub OAuth)
        │
        ├── Firebase SDK → ID token + refresh token
        ├── JS calls pywebview.api.on_login_success()
        │     ├── set_initial_tokens(id_token, refresh_token) → keyring
        │     ├── GET /user/me (backend) or PUT /user (new user)
        │     ├── upsert_local_user(db)
        │     └── return user data to JS
        └── JS shows Welcome screen → user clicks "Run Agent"
              └── bridge.run_agent() → destroy webview → main continues
```

**External OAuth (Google/GitHub):**
- Opens system browser with a Firebase auth page
- Loopback `ThreadingTCPServer` on a random port captures the token
- **Timeout: 180 seconds**

### 8.2 Token Management

**File:** `auth/tokens.py`

| Function | Role |
|----------|------|
| `set_initial_tokens(id_token, refresh_token)` | Caches ID token + expiry in memory; stores refresh token in OS keyring (`zenno-desktop-agent`) |
| `get_valid_id_token()` | Returns cached token if valid (expiry > 120 s away); otherwise refreshes via Google `securetoken.googleapis.com` |
| `_refresh_with_securetoken(refresh_token)` | POST to Google with `FIREBASE_API_KEY`; updates cache |
| `clear_tokens()` | Removes from memory + keyring |

Tokens never touch the local database. The keyring is used for persistence across agent restarts.

---

## 9. Backend Sync

### 9.1 Sync Trigger

**File:** `sync/activity_syncer.py`  
Called from `DesktopAgent._run_etl_and_sync()` — after every successful ETL run, checks `db.has_pending_sync()`. If any aggregate table has `needs_sync = 1` rows, `sync_activity()` fires.

### 9.2 Sync Payload Structure

```json
{
  "user_id": "firebase_uid",
  "sync_timestamp": "2026-04-11T14:00:00.000Z",
  "timezone_offset_minutes": 300,
  "data": {
    "projects": [
      {
        "project_name": "desktop-agent",
        "project_path": "/e/Zenno/desktop-agent",
        "first_seen_at": "...",
        "last_active_at": "...",
        "loc": [{"language": "Python", "lines": 4200, "files": 22}],
        "skills": [{"skill_name": "Backend", "duration_sec": 7200}],
        "daily": [
          {
            "date": "2026-04-11",
            "languages": [...],
            "apps": [...],
            "context": [{"state": "Flow", "duration_sec": 4320}],
            "behavior": {"typing_intensity_kpm": 45.2, ...}
          }
        ]
      }
    ]
  }
}
```

### 9.3 Retry Logic

| Scenario | Behaviour |
|----------|-----------|
| 401 Unauthorized | Refresh token once via `get_valid_id_token(force=True)`, retry |
| 5xx / network error | Up to 3 retries with exponential backoff starting at 2 s |
| All retries exhausted | Log warning; leave `needs_sync = 1`; retry next ETL cycle |

After successful sync: `mark_project_synced(project_name)` sets `needs_sync = 0` on `daily_project_languages`, `daily_project_apps`, `daily_project_context`, `daily_project_behavior`, `project_loc_snapshots`, `project_skills`.

---

## 10. Database Schema — All Tables

**Database file:** `data/db/zenno.db` (SQLite, WAL mode)

### `raw_activity_logs`
Primary source of truth. Written by Phase 1, tagged by Phase 2, consumed by Phase 4.

```sql
CREATE TABLE raw_activity_logs (
    log_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time             TEXT    NOT NULL,          -- ISO local time
    end_time               TEXT    NOT NULL,          -- ISO local time
    app_name               TEXT    NOT NULL,
    window_title           TEXT,
    duration_sec           INTEGER NOT NULL,

    project_name           TEXT,                      -- NULL if undetectable
    project_path           TEXT,
    active_file            TEXT,
    detected_language      TEXT,

    typing_intensity       REAL    DEFAULT 0.0,       -- KPM proxy
    mouse_click_rate       REAL    DEFAULT 0.0,       -- CPM
    deletion_key_presses   INTEGER DEFAULT 0,
    mouse_movement_distance REAL   NOT NULL DEFAULT 0.0, -- pixels
    idle_duration_sec      INTEGER DEFAULT 0,

    context_state          TEXT,                      -- NULL until Phase 2 tags it
    confidence_score       REAL,

    manually_verified_label TEXT NULL,                -- ESM correction
    verified_at             TIMESTAMP NULL,

    is_aggregated           INTEGER NOT NULL DEFAULT 0,  -- 0=pending, 1=done
    aggregated_at           TEXT NULL,
    aggregation_version     INTEGER NOT NULL DEFAULT 1
);
-- Indexes
CREATE INDEX idx_raw_agg_pending ON raw_activity_logs(is_aggregated, end_time);
CREATE INDEX idx_raw_end_time    ON raw_activity_logs(end_time);
```

**Lifecycle of a row:**
```
INSERT (context_state=NULL, is_aggregated=0)     ← Phase 1
    → UPDATE context_state, confidence_score     ← Phase 2 (BlockEvaluator)
    → UPDATE is_aggregated=1, aggregated_at      ← Phase 4 (ETLPipeline)
    → UPDATE manually_verified_label, verified_at ← ESM (optional)
```

---

### `projects`
One row per detected project. Updated whenever the project appears in a log.

```sql
CREATE TABLE projects (
    project_name  TEXT PRIMARY KEY,
    project_path  TEXT,
    first_seen_at TEXT NOT NULL,
    last_active_at TEXT NOT NULL,
    needs_sync    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_projects_needs_sync ON projects(needs_sync);
```

---

### `daily_project_languages`
Time spent per programming language per project per day.

```sql
CREATE TABLE daily_project_languages (
    date          TEXT NOT NULL,
    project_name  TEXT NOT NULL,
    language_name TEXT NOT NULL,
    duration_sec  INTEGER NOT NULL DEFAULT 0,
    needs_sync    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, project_name, language_name),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_dpl_needs_sync ON daily_project_languages(needs_sync);
```

---

### `daily_project_apps`
Time spent per application per project per day.

```sql
CREATE TABLE daily_project_apps (
    date          TEXT NOT NULL,
    project_name  TEXT NOT NULL,
    app_name      TEXT NOT NULL,
    duration_sec  INTEGER NOT NULL DEFAULT 0,
    needs_sync    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, project_name, app_name),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_dpa_needs_sync ON daily_project_apps(needs_sync);
```

---

### `daily_project_context`
Time spent in each mental/activity state per project per day.

```sql
CREATE TABLE daily_project_context (
    date          TEXT NOT NULL,
    project_name  TEXT NOT NULL,
    context_state TEXT NOT NULL,     -- Flow | Debugging | Research | Communication | Distracted
    duration_sec  INTEGER NOT NULL DEFAULT 0,
    needs_sync    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, project_name, context_state),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_dpc_needs_sync ON daily_project_context(needs_sync);
```

---

### `daily_project_behavior`
Aggregated behavioural metrics per project per day.

```sql
CREATE TABLE daily_project_behavior (
    date                          TEXT    NOT NULL,
    project_name                  TEXT    NOT NULL,
    typing_intensity_kpm          REAL    NOT NULL DEFAULT 0.0,
    mouse_click_rate_cpm          REAL    NOT NULL DEFAULT 0.0,
    total_deletion_key_presses    INTEGER NOT NULL DEFAULT 0,
    total_idle_sec                INTEGER NOT NULL DEFAULT 0,
    total_mouse_movement_distance REAL    NOT NULL DEFAULT 0.0,
    needs_sync                    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (date, project_name),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_dpb_needs_sync ON daily_project_behavior(needs_sync);
```

---

### `project_skills`
Cumulative time per skill category per project (aggregated from languages).

```sql
CREATE TABLE project_skills (
    project_name  TEXT NOT NULL,
    skill_name    TEXT NOT NULL,     -- Backend | Frontend | Systems | DevOps | etc.
    duration_sec  INTEGER NOT NULL DEFAULT 0,
    last_updated_at TEXT NOT NULL,
    needs_sync    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (project_name, skill_name),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_ps_needs_sync ON project_skills(needs_sync);
```

---

### `project_loc_snapshots`
Latest lines-of-code count per language per project.

```sql
CREATE TABLE project_loc_snapshots (
    project_name   TEXT NOT NULL,
    language_name  TEXT NOT NULL,
    lines_of_code  INTEGER NOT NULL DEFAULT 0,
    file_count     INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT NOT NULL,
    needs_sync     INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (project_name, language_name),
    FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
CREATE INDEX idx_pls_needs_sync ON project_loc_snapshots(needs_sync);
```

---

### `local_user`
Singleton row (id = 1 constraint) storing authenticated user profile.

```sql
CREATE TABLE local_user (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    backend_user_id  TEXT NOT NULL,
    email            TEXT NOT NULL,
    name             TEXT NOT NULL,
    profile_photo    TEXT,
    is_verified      INTEGER NOT NULL DEFAULT 0,
    role             TEXT NOT NULL DEFAULT 'user',
    created_at       TEXT,
    updated_at       TEXT
);
```

---

### `nudge_log`
Full audit trail of every nudge cycle (shown or suppressed).

```sql
CREATE TABLE nudge_log (
    nudge_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at       TEXT    NOT NULL,
    nudge_type         TEXT    NOT NULL,     -- BREAK_REMINDER | FLOW_CELEBRATION | etc.
    nudge_text         TEXT    NOT NULL DEFAULT '',
    rationale          TEXT,                 -- why this type was chosen
    fatigue_score      REAL,
    fatigue_level      TEXT,                 -- low | moderate | high | critical
    flow_ratio_today   REAL,
    active_min_today   REAL,
    min_since_break    REAL,
    top_project        TEXT,
    was_suppressed     INTEGER NOT NULL DEFAULT 0,   -- 1 = suppressed, no toast shown
    suppression_reason TEXT,
    llm_used           INTEGER NOT NULL DEFAULT 0,   -- 1 = Gemini, 0 = template
    context_snapshot   TEXT                          -- full NudgeContext as JSON
);
CREATE INDEX idx_nudge_generated  ON nudge_log(generated_at);
CREATE INDEX idx_nudge_suppressed ON nudge_log(was_suppressed, generated_at);
```

---

## 11. Threading Model

The agent runs **5 concurrent threads** at steady state:

| Thread | Name | Started | Purpose |
|--------|------|---------|---------|
| Main | `MainThread` | `main.py` | Monitoring loop + session management |
| Background | `BlockEvaluator` | `DesktopAgent.__init__` | Context classification + ETL trigger |
| Background | `MouseMovementSampler` | `BehavioralMetrics.start_listening` | 10 Hz cursor sampling |
| Background | `NudgeScheduler` | `DesktopAgent.start` | Nudge pipeline every 30 min |
| Per-popup (transient) | `ESMPopupThread` | On low-confidence prediction | Shows ESM verification window |

**All background threads are daemon threads** — they are automatically killed when the main thread exits.

**Database concurrency:**  
SQLite is opened with `check_same_thread=False`, `journal_mode=WAL`, `timeout=10.0`. WAL mode allows concurrent reads from all threads while a single writer holds the write lock. A `threading.RLock()` guards write operations in `Database`.

**pywebview UI threads:**  
- Auth window: runs on main thread (blocks until done)
- Notification: subprocess (separate Python process, no thread conflict)
- ESM popup: subprocess launched from daemon thread

---

## 12. Timing Reference

| Event | Interval | Configured by |
|-------|----------|--------------|
| Main loop tick | 5 s | `sample_interval_sec` |
| Forced session flush | 300 s (5 min) | `flush_interval_sec` |
| Block evaluation + ETL | 300 s (5 min) | `block_duration_sec` |
| ETL pipeline (standalone) | 900 s (15 min) | `etl_pipeline.interval_sec` |
| Backend sync (after ETL) | 900 s (15 min) | `sync.interval_sec` |
| LOC scan | 3600 s (1 hr) | `loc_scanner.scan_interval_sec` |
| Nudge check | 1800 s (30 min) | `nudge.interval_min` |
| Nudge suppression window | 1500 s (25 min) | `nudge.suppression_min` |
| Mouse position sampling | 0.1 s (10 Hz) | hardcoded in `BehavioralMetrics` |
| Auth OAuth timeout | 180 s | hardcoded in `AuthBridge` |
| Token refresh margin | 120 s before expiry | `min_validity_sec` in `tokens.py` |
| Sticky project TTL | 900 s (15 min) | `etl_pipeline.sticky_project_ttl_sec` |
| ESM popup auto-dismiss | 30 s | `esm_popup.ui.auto_dismiss_seconds` |
| Notification display | 7 s | `nudge.notification.display_sec` |

**Visual timeline of a typical 15-minute window:**

```
T+0m    Agent start → auth → monitoring loop begins
T+5m    BlockEvaluator fires: tag last 5 min of logs → ETL run #1
T+10m   BlockEvaluator fires: tag + ETL run #2
T+15m   BlockEvaluator fires: tag + ETL run #3
T+15m   DesktopAgent timer: ETL run #4 (may overlap, idempotent) → sync if pending
T+30m   NudgeScheduler tick: aggregate context → Gemini → notification
T+60m   LOCScanner fires: count lines for active projects
```

---

## 13. Configuration Reference

**File:** `config/config.yaml`

### Monitoring (Phase 1)

| Key | Default | Description |
|-----|---------|-------------|
| `sample_interval_sec` | 5 | Main loop tick rate (seconds) |
| `flush_interval_sec` | 300 | Force-flush session every N seconds |
| `idle_threshold_sec` | 10 | Seconds without input = idle |
| `behavioral_metrics.click_debounce_ms` | 50 | Ignore duplicate clicks within N ms |
| `behavioral_metrics.max_typing_intensity_kpm` | 200 | Cap KPM at validation |
| `behavioral_metrics.max_mouse_click_rate_cpm` | 200 | Cap CPM at validation |
| `etl_pipeline.sticky_project_ttl_sec` | 900 | How long to inherit last project for generic apps |

### Context Detection (Phase 2)

| Key | Default | Description |
|-----|---------|-------------|
| `block_duration_sec` | 300 | Block evaluation window (seconds) |
| `block_evaluator.startup_delay_sec` | 300 | Initial wait before first evaluation |
| `ml_enabled` | true | Use XGBoost model when available |
| `ml_model_path` | `./data/models/context_detector.pkl` | Model file |
| `ml_confidence_threshold` | 0.5 | Minimum ML confidence to trust result |
| `heuristics.*` | various | 30+ signal thresholds for decision tree |

### ESM Popup

| Key | Default | Description |
|-----|---------|-------------|
| `esm_popup.enabled` | true | Show verification popups |
| `esm_popup.confidence_threshold` | 0.70 | Below this → show popup |
| `esm_popup.rate_limiting.min_interval_hours` | 0.25 | 15 min between popups |
| `esm_popup.rate_limiting.max_per_day` | 20 | Daily cap |
| `esm_popup.ui.auto_dismiss_seconds` | 30 | Auto-close after N seconds |

### Aggregation (Phase 4)

| Key | Description |
|-----|-------------|
| `etl_pipeline.app_name_mapping` | Normalize exe names to display names |
| `etl_pipeline.browser_detection.service_keywords` | Keyword → service name for browser activity |
| `etl_pipeline.language_to_skill_mapping` | Language → skill category |
| `loc_scanner.scan_interval_sec` | Default 3600 |
| `loc_scanner.language_extensions` | Extension → language mapping |
| `loc_scanner.skip_directories` | Dirs to skip in LOC walk |

### Nudge Engine (Phase 5)

| Key | Default | Description |
|-----|---------|-------------|
| `nudge.enabled` | true | Enable/disable entire nudge system |
| `nudge.interval_min` | 30 | How often the scheduler checks |
| `nudge.suppression_min` | 25 | Minimum gap between visible nudges |
| `nudge.window_min` | 30 | Look-back window for recent signals |
| `nudge.min_active_min` | 10.0 | Session must be this long before nudging |
| `nudge.idle_break_threshold_min` | 5 | Idle gap counted as a break |
| `nudge.late_night_hour` | 21 | Hour to trigger LATE_NIGHT type |
| `nudge.flow_streak_min` | 45 | Flow minutes to trigger FLOW_CELEBRATION |
| `nudge.break_reminder_min` | 90 | Minutes without break → BREAK_REMINDER |
| `nudge.distraction_threshold` | 0.30 | Distracted ratio → REENGAGEMENT |
| `nudge.llm.enabled` | true | Use Gemini; false = always template |
| `nudge.llm.model` | gemini-2.0-flash | Gemini model name |
| `nudge.llm.timeout_sec` | 4.0 | Max wait for LLM response |
| `nudge.notification.enabled` | true | Show desktop toast |
| `nudge.notification.display_sec` | 7 | How long toast stays visible |

---

## 14. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  INPUT SIGNALS (real-time)                                                   │
│  keyboard events ──┐                                                         │
│  mouse events ─────┤──► BehavioralMetrics ──► ActivitySession.collect_data() │
│  cursor position ──┘                                ↓                        │
│  foreground window ────────────────────────────────►│                        │
│  IDE window title ─────────────────► ProjectDetector│                        │
└─────────────────────────────────────────────────────┼────────────────────────┘
                                                       │ INSERT
                                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  raw_activity_logs  (context_state=NULL, is_aggregated=0)                    │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ every 5 min
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  BlockEvaluator                                                               │
│  _aggregate_block_metrics() → FeatureExtractor → XGBoost / ContextDetector  │
│  → UPDATE context_state, confidence_score                                    │
│  → (optional) ESMPopup subprocess for verification                          │
└──────────────────────────────────┬──────────────────────────────────────────┘
                                   │ immediately after tagging
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  ETLPipeline                                                                  │
│  Extract: is_aggregated=0, context_state NOT NULL                            │
│  Transform: project assign, label override, app rename, midnight split       │
│  Load → ┌────────────────────────────────────────────────────┐               │
│          │  projects                                           │               │
│          │  daily_project_languages                           │               │
│          │  daily_project_apps                                │               │
│          │  daily_project_context                             │               │
│          │  daily_project_behavior                            │               │
│          │  project_skills                                     │               │
│          └────────────────────────────────────────────────────┘               │
│  → UPDATE is_aggregated=1                                                     │
└──────────┬───────────────────────────────────────┬───────────────────────────┘
           │ every 1 hour                           │ every 15 min (if needs_sync)
           ▼                                        ▼
┌──────────────────────┐              ┌─────────────────────────────────────┐
│  LOCScanner           │              │  ActivitySyncer                      │
│  → project_loc_       │              │  ActivityCollector.collect_pending() │
│    snapshots          │              │  → POST /sync/activity (backend API) │
└──────────────────────┘              │  → mark needs_sync=0                 │
                                       └─────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  NudgeScheduler (every 30 min)                                               │
│  ← reads raw_activity_logs (today + last window)                             │
│  ← reads daily_project_apps, daily_project_languages                        │
│  NudgeContextAggregator → 30+ signals → NudgeContext                        │
│  NudgeGenerator → Gemini API or fallback template → nudge_text              │
│  NudgeNotifier → subprocess → pywebview notification window (top-right)     │
│  NudgeLog.record() → nudge_log                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

*Document generated from codebase analysis of `e:\Zenno\desktop-agent` — all timings, thresholds, and schema definitions reflect the actual implementation.*
