# Desktop Agent - Comprehensive Implementation Guide

**Last Updated:** April 10, 2026  
**Version:** 1.0  
**Status:** Production Ready

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture & Phases](#architecture--phases)
3. [Components & Responsibilities](#components--responsibilities)
4. [Database Schema](#database-schema)
5. [Data Flow & Workflows](#data-flow--workflows)
6. [Context Detection System](#context-detection-system)
7. [Aggregation Pipeline (ETL)](#aggregation-pipeline-etl)
8. [Configuration System](#configuration-system)
9. [IDE Detection & Project Identification](#ide-detection--project-identification)
10. [Performance & Safety](#performance--safety)

---

## System Overview

Zenno Desktop Agent is a **Windows activity monitor** that captures real-time user behavior, analyzes it through behavioral and ML models, and aggregates it into actionable project metrics.

### Core Purpose
- **Track developer activity** across all applications (IDEs, browsers, terminals)
- **Detect mental states** (Flow, Debugging, Research, Communication, Distracted)
- **Extract productivity metrics** (KPM, CPM, project focus time, skills)
- **Build developer profiles** (languages, projects, patterns)

### Key Features
✅ Real-time window monitoring (2-5 second intervals)  
✅ Behavioral signal capture (typing, clicks, mouse, idle)  
✅ ML-powered context detection with heuristic fallback  
✅ Automatic project & file detection from IDE context  
✅ Sticky project attribution (browser→IDE context inheritance)  
✅ Atomic ETL pipeline (guaranteed data consistency)  
✅ Manual verification (ESM popups for low-confidence predictions)  
✅ Configurable thresholds (all parameters in config.yaml)  

---

## Architecture & Phases

The system operates in 4 phases:

```
┌─────────────────────────────────────────────────────────────┐
│ PHASE 1: COLLECTION (Real-time, < 2% CPU)                  │
│ • Sample active window every 2-5 seconds                    │
│ • Track behavioral signals in ActivitySession               │
│ • Detect project & file from IDE/window context            │
│ • Insert raw_activity_logs (context_state = NULL)          │
└─────────────────────────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 2: ANALYSIS (Background, 300s blocks)                │
│ • Extract unevaluated logs (context_state IS NULL)         │
│ • Aggregate behavioral signals into 8-signal fingerprint   │
│ • ML prediction (XGBoost) → context_state + confidence    │
│ • Heuristic fallback if ML confidence < threshold          │
│ • ESM popup for manual verification (optional)             │
│ • Update raw_activity_logs with context_state              │
└─────────────────────────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 3: AGGREGATION (Atomic ETL)                          │
│ • Extract tagged logs (is_aggregated = 0)                  │
│ • Transform (normalize, split midnight, resolve paths)     │
│ • Delegate to 6 aggregators (ProjectAgg, AppAgg, etc.)     │
│ • Load all SQL in single atomic transaction                │
│ • Update 7 daily project metrics tables                    │
│ • Mark logs as aggregated (is_aggregated = 1)              │
└─────────────────────────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────┐
│ PHASE 4: SYNC (Background, hourly)                         │
│ • Query tables with needs_sync = 1                         │
│ • Upload to backend API                                    │
│ • Mark as synced (needs_sync = 0)                          │
│ • Retry on failure                                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Components & Responsibilities

### Phase 1: Collection Components

#### **DesktopAgent (agent.py)**
The main event loop orchestrating all collection activities.

**Responsibilities:**
- Sample active window every 2-5 seconds (configurable: `sample_interval_sec`)
- Detect app/window changes (triggers new ActivitySession)
- Track session start/end
- Flush sessions to database every 300s (`flush_interval_sec`)
- Detect idle periods (>10 sec inactivity → `idle_threshold_sec`)
- Recover sticky project from DB on startup
- Periodically trigger LOC scanning

**Key Methods:**
```python
DesktopAgent._sample_loop()           # Main event loop
DesktopAgent._flush_pending_session() # Persist session to DB
DesktopAgent._apply_sticky_project()  # Browser→IDE context inheritance
```

#### **ActivitySession**
Per-window session representing continuous activity in one app/window.

**Lifecycle:**
1. Created when app/window changes
2. Collects behavioral data for duration it's active
3. Flushed to raw_activity_logs when window closes or time cap hit

**Data Collected:**
- Timestamps (ISO format, local timezone)
- App name & window title
- Project name & file (from IDE detection)
- Behavioral signals (KPM, CPM, deletions, mouse, idle)
- Duration in seconds

**Key Methods:**
```python
ActivitySession.collect_data()        # Gather all signals
ActivitySession.detect_project()      # Extract project/file from window title
```

#### **BehavioralMetrics**
Real-time signal capture from Windows events (global listeners).

**Signals Tracked:**
- **Typing Intensity (KPM)**: Keystrokes per minute (excludes modifiers)
- **Mouse Click Rate (CPM)**: Clicks per minute
- **Deletion Key Presses**: Backspace/Delete count (indicates correction/debugging)
- **Mouse Movement Distance**: Total pixels traveled
- **Idle Duration**: Time without keyboard/mouse input

**Global Listeners:**
- Keyboard hook: Track typing + deletions
- Mouse hook: Track clicks + movement
- Debouncing: Prevent noise (click_debounce_ms=50)
- Rate limiting: Ignore spam (max_typing_intensity_kpm=200, max_mouse_click_rate_cpm=200)

**Key Methods:**
```python
BehavioralMetrics.reset()            # Clear counters at session start
BehavioralMetrics.get_metrics()      # Retrieve accumulated signals
```

#### **ProjectDetector (monitor/project_detector.py)**
Extracts project name, file, and language from IDE/window context.

**Strategy (3-Layer Fallback):**

1. **PID-based Detection** (Most Accurate)
   - Query IDE process for open files
   - Walk directory tree up to find project root markers
   - Recognized markers: `.git`, `package.json`, `requirements.txt`, setup.py, etc.

2. **Window Title Parsing** (Fast, IDE-Specific)
   - VS Code format: `"filename - /path/to/project - VS Code"`
   - PyCharm format: `"project_name - [file.py]"`
   - Generic format: `"text - text - IDE_Name"`
   - Browser tabs: Remove browser name suffix, keep tab title

3. **Safe Directory Search** (Fallback)
   - Bounded filesystem search with safety limits:
     - Time limit: 1.0 second max
     - Depth limit: 6 directory levels
     - Exclude: `node_modules`, `.git`, `__pycache__`, `Windows`, `Program Files`, etc.

**IDE Configuration (config.yaml):**
All IDEs defined in `project_detector.ides` section:
```yaml
ides:
  - name: "VS Code"
    executable_names: ["code.exe", "code", "code-oss"]
    title_suffixes: ["Visual Studio Code"]
    title_format: "vscode"
```

**Language Detection:**
File extension → Language mapping (e.g., `.py` → `Python`)

**Key Methods:**
```python
ProjectDetector.detect_project()      # Detect project from window
ProjectDetector.get_project_path()    # Resolve project path
ProjectDetector.get_detected_language()  # Extract language from file
```

#### **IdleDetector**
Tracks inactivity within sessions.

**Definition:** Idle = No keyboard/mouse input for > idle_threshold_sec (default 10s)

**Use Cases:**
- Differentiate thinking/planning (normal, part of Flow) from away-from-desk
- Signal input for context detection heuristics
- Reduce noise in behavioral metrics

**Key Methods:**
```python
IdleDetector.mark_activity()       # Reset idle timer
IdleDetector.get_idle_metrics()    # Get idle duration for session
```

---

### Phase 2: Analysis Components

#### **BlockEvaluator (analyze/block_evaluator.py)**
Background thread that tags logs with context state.

**Execution Model:**
- Runs every block_duration_sec (default 300 seconds)
- Infinite loop in separate thread
- Wakes at wall-clock boundaries (0s, 300s, 600s, etc.)

**Algorithm:**
```
1. Wait until next block boundary
2. Query: SELECT * FROM raw_activity_logs 
   WHERE is_aggregated=0 AND context_state IS NULL 
   AND end_time > NOW() - block_duration_sec
3. Aggregate 8 behavioral signals across block
4. ML Prediction:
   a. If ml_enabled=true:
      - Extract features (FeatureExtractor)
      - Predict with XGBoost model
      - Get confidence score
   b. If confidence >= ml_confidence_threshold:
      - Use ML prediction
   c. Else:
      - Use heuristic fallback
5. ESM Popup (if enabled & confidence < esm_popup.confidence_threshold):
   - Show popup to user
   - Wait for verification
   - Use user's verification as ground truth
6. Update all logs in block:
   UPDATE raw_activity_logs 
   SET context_state = ?, confidence_score = ?
   WHERE log_id IN (...)
```

**Key Methods:**
```python
BlockEvaluator._run_loop()         # Main evaluation loop
BlockEvaluator.evaluate_block()    # Process one block
BlockEvaluator._predict_context()  # ML + heuristic prediction
```

#### **ContextDetector (analyze/context_detector.py)**
Heuristic fallback for context classification.

**5-State Classification:**
- **Flow**: Deep focus mode, confident typing, productive app, minimal context switches
- **Debugging**: High correction ratio (trial & error), scattered navigation
- **Research**: Pure reading (zero typing), high scrolling/clicking, learning mode
- **Communication**: Active in Slack/Teams/Zoom with user context
- **Distracted**: Social media/entertainment apps, scattered activity

**8-Signal Decision Tree:**

| Signal | Flow | Debug | Research | Comms | Distracted |
|--------|------|-------|----------|-------|-----------|
| **Typing (KPM)** | 0-200 | 50-200 | 0 | 0-50 | 0-100 |
| **Correction Ratio** | <8% | >12% | 0% | <5% | N/A |
| **Mouse Velocity** | 10-45 | 20-55 | 30-70 | N/A | 0-15 or 25-67 |
| **Click Rate (CPM)** | 0-12 | 1-10 | 8-35 | 20+ | 20+ |
| **App Switches** | <3/block | >4/block | 1-2 | 2-4 | >8 |
| **App Score** | >0.5 | 0.3-0.7 | 0.5 (neutral) | 0.0 | <-0.5 |
| **Idle Ratio** | 20% | 10% | 20-50% | 30% | 40-80% |
| **Session Hours** | <4h | <6h | <4h | <4h | N/A |

**Confidence Scoring:**
```python
confidence_flow = 0.92              # Good signals, fresh user
confidence_flow_fatigued_moderate = 0.85  # >4 hours worked
confidence_flow_fatigued_high = 0.75      # >6 hours worked
confidence_debugging = 0.85
confidence_research = 0.80
confidence_communication = 0.80
confidence_distracted = 0.75
confidence_ambiguous = 0.55
```

**Key Methods:**
```python
ContextDetector.detect_context()   # Classify into 5 states
ContextDetector.compute_app_score() # Productivity score per app
ContextDetector.is_fatigue_factor() # Check session duration
```

#### **FeatureExtractor (ml/feature_extractor.py)**
Converts behavioral signals into ML features for XGBoost model.

**Features Generated (12-15 features):**
```
1. kpm_normalized           - Typing intensity (0-1 scale)
2. cpm_normalized           - Click rate (0-1 scale)
3. correction_ratio         - Deletion keys / total keystrokes
4. mouse_velocity           - Pixels per second
5. app_switch_count         - Number of app switches in block
6. app_score                - Time-weighted productivity score of apps
7. idle_ratio               - Idle time / total time
8. max_mouse_click_burst    - Peak clicks in 30-second window
9. typing_pause_duration    - Average time between typing bursts
10. deletion_intensity       - Deletion keys per 100 keystrokes
11. max_idle_duration        - Longest idle period in block
12. session_fatigue_factor   - 0-1 based on hours worked
```

**Model Details:**
- **Framework**: XGBoost (gradient boosted decision trees)
- **Input**: Feature vector (12-15 normalized features)
- **Output**: 5-class probability distribution
- **Classes**: Flow, Debugging, Research, Communication, Distracted
- **Confidence**: Max probability from output distribution

**Key Methods:**
```python
FeatureExtractor.extract_features()  # Generate feature vector from block metrics
FeatureExtractor._normalize_feature() # Min-max scaling per feature
```

---

### Phase 3: Aggregation Components

#### **ETLPipeline (aggregate/etl_pipeline.py) - "Maestro"**
Orchestrates atomic transformation of raw logs into daily project metrics.

**Triggers:**
- Immediately after BlockEvaluator tags logs
- On-demand via CLI/API
- Periodically every N minutes

**Execution Model:**
```
BEGIN TRANSACTION
  1. EXTRACT
     - Query: is_aggregated=0 AND context_state IS NOT NULL
     - Get all tagged logs ready for aggregation
  
  2. TRANSFORM
     - Normalize project names (handle None/null cases)
     - Split logs crossing daily boundaries (midnight)
     - Override with manual verification labels (ESM ground truth)
     - Clean app names (code.exe → "VS Code")
     - Resolve project paths
  
  3. DELEGATE
     - Pass clean batch to 6 aggregators:
       * ProjectAggregator    → projects table
       * AppAggregator        → daily_project_apps
       * LanguageAggregator   → daily_project_languages
       * SkillAggregator      → project_skills
       * ContextAggregator    → daily_project_context
       * BehaviorAggregator   → daily_project_behavior
     - Each aggregator generates SQL INSERT OR REPLACE statements
  
  4. LOAD
     - Execute all SQL statements in single transaction
     - Guarantee: All-or-nothing (no partial updates)
     - Mark logs: is_aggregated = 1, aggregated_at = NOW()
  
COMMIT TRANSACTION
```

**Key Methods:**
```python
ETLPipeline.run()              # Main ETL execution
ETLPipeline._extract_logs()    # Query pending logs
ETLPipeline._transform_logs()  # Normalize and clean data
ETLPipeline._delegate_to_aggregators()  # Distribute work
ETLPipeline._load_into_db()    # Execute atomic transaction
```

#### **6 Aggregators**

##### **1. ProjectAggregator**
Maintains unique projects and metadata.

**Logic:**
```sql
INSERT OR REPLACE INTO projects (
  project_name, project_path, first_seen_at, last_active_at, needs_sync
)
SELECT DISTINCT
  project_name, project_path,
  COALESCE(
    (SELECT first_seen_at FROM projects WHERE project_name = ?),
    NOW()
  ),
  NOW(), 1
FROM transformed_logs
WHERE project_name IS NOT NULL
```

**Outputs:** projects table

##### **2. AppAggregator**
Daily app usage per project.

**Logic:**
```
For each (date, project_name, app_name):
  SUM(duration_sec) grouped by these fields
  UPDATE daily_project_apps (date, project_name, app_name, duration_sec)
```

**Example Output:**
```
Date: 2026-04-10
Project: "desktop-agent"
  VS Code: 3600 sec (1 hour)
  Chrome: 1200 sec (20 min)
  Terminal: 600 sec (10 min)
```

**Outputs:** daily_project_apps table

##### **3. LanguageAggregator**
Daily language time per project.

**Logic:**
```
For each (date, project_name, detected_language):
  SUM(duration_sec) where language is detected
  Detected language derived from file extension (config.yaml mapping)
  
For generic apps (browser, terminal): language = None (NULL)
For IDE: language extracted from active file (.py → Python, .ts → TypeScript)
```

**Example Output:**
```
Date: 2026-04-10
Project: "desktop-agent"
  Python: 2400 sec (40 min)
  JavaScript: 1800 sec (30 min)
```

**Outputs:** daily_project_languages table

##### **4. SkillAggregator**
Cumulative skill/language time (overall, not daily).

**Logic:**
```
For each (project_name, skill_name):
  SUM all duration_sec across all days for this skill
  Use: language_to_skill_mapping (config.yaml)
    Python → Backend
    JavaScript → Frontend
    SQL → Data
    etc.
  
  UPDATE project_skills (project_name, skill_name, duration_sec)
  SET duration_sec += new_duration
```

**Example Output:**
```
Project: "desktop-agent"
  Backend: 5400 sec (total across all time)
  Frontend: 3200 sec
  DevOps: 900 sec
```

**Outputs:** project_skills table

##### **5. ContextAggregator**
Daily mental state distribution per project.

**Logic:**
```
For each (date, project_name, context_state):
  SUM(duration_sec) where context_state IN (Flow, Debugging, Research, Communication, Distracted)
```

**Example Output:**
```
Date: 2026-04-10
Project: "desktop-agent"
  Flow: 4000 sec (67%)
  Debugging: 1200 sec (20%)
  Research: 600 sec (10%)
  Communication: 200 sec (3%)
  Distracted: 0 sec (0%)
```

**Outputs:** daily_project_context table

##### **6. BehaviorAggregator**
Daily behavioral metrics per project (averages/sums).

**Logic:**
```
For each (date, project_name):
  typing_intensity_kpm = AVERAGE(typing_intensity) weighted by duration
  mouse_click_rate_cpm = AVERAGE(mouse_click_rate) weighted by duration
  total_deletion_key_presses = SUM(deletion_key_presses)
  total_idle_sec = SUM(idle_duration_sec)
  total_mouse_movement_distance = SUM(mouse_movement_distance)
```

**Example Output:**
```
Date: 2026-04-10
Project: "desktop-agent"
  typing_intensity_kpm: 45.2 (average)
  mouse_click_rate_cpm: 8.1 (average)
  total_deletion_key_presses: 542
  total_idle_sec: 1200
  total_mouse_movement_distance: 125000 px
```

**Outputs:** daily_project_behavior table

---

### Phase 4: Sync Components

#### **ActivitySyncer (sync/activity_syncer.py)**
Uploads aggregated data to backend API.

**Execution Model:**
- Runs every N minutes (configurable, default 3600s = 1 hour)
- Queries tables with needs_sync = 1
- Uploads JSON payloads to API
- On success: Update needs_sync = 0
- On failure: Retry with exponential backoff

**Tables Synced:**
- projects
- daily_project_apps
- daily_project_languages
- daily_project_context
- daily_project_behavior
- project_skills
- project_loc_snapshots

**Key Methods:**
```python
ActivitySyncer._sync_loop()         # Main sync loop
ActivitySyncer._upload_table()      # Send table data to API
ActivitySyncer._handle_retry()      # Exponential backoff on failure
```

---

## Database Schema

### Overview
**Database**: SQLite (data/db/desktop-agent.db)  
**Mode**: WAL (Write-Ahead Logging) for concurrency  
**Foreign Keys**: Enabled (PRAGMA foreign_keys=ON)  
**Timezone**: Local system timezone

### Table 1: raw_activity_logs
**Purpose**: Raw heartbeat records with behavioral signals (Phase 1 collection)  
**Insertion**: Continuous by DesktopAgent  
**Tagging**: BlockEvaluator adds context_state  
**Aggregation**: ETLPipeline processes & marks as aggregated

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| log_id | INTEGER | NO | Primary key, auto-increment |
| start_time | TEXT | NO | ISO format timestamp (local time) |
| end_time | TEXT | NO | ISO format timestamp (local time) |
| app_name | TEXT | NO | Executable name (e.g., "code.exe") |
| window_title | TEXT | YES | Full window title |
| duration_sec | INTEGER | NO | Session duration in seconds (min 1) |
| project_name | TEXT | YES | Project name (detected from IDE/sticky) |
| project_path | TEXT | YES | Full project path |
| active_file | TEXT | YES | Active file or URL |
| detected_language | TEXT | YES | Language from file extension |
| typing_intensity | REAL | NO (Default 0.0) | KPM (keystrokes per minute) |
| mouse_click_rate | REAL | NO (Default 0.0) | CPM (clicks per minute) |
| deletion_key_presses | INTEGER | NO (Default 0) | Count of backspace/delete keys |
| mouse_movement_distance | REAL | NO (Default 0.0) | Total pixels moved |
| idle_duration_sec | INTEGER | NO (Default 0) | Time inactive in session |
| context_state | TEXT | YES | Classification (Flow/Debug/Research/Comms/Distracted) - NULL until BlockEvaluator tags |
| confidence_score | REAL | YES | ML/heuristic confidence (0.0-1.0) |
| manually_verified_label | TEXT | YES | User verification from ESM popup (overrides ML) |
| verified_at | TIMESTAMP | YES | Timestamp of user verification |
| is_aggregated | INTEGER | NO (Default 0) | 0=pending aggregation, 1=processed |
| aggregated_at | TEXT | YES | Timestamp when aggregated |
| aggregation_version | INTEGER | NO (Default 1) | Schema version for compatibility |

**Indexes:**
```sql
idx_raw_agg_pending     -- for quick query of pending logs
idx_raw_end_time        -- for time-range queries
```

**Lifecycle Example:**
```
1. DesktopAgent inserts new log (context_state = NULL, is_aggregated = 0)
2. BlockEvaluator tags log (context_state = "Flow", confidence_score = 0.92)
3. ETLPipeline processes log (is_aggregated = 1, aggregated_at = NOW())
```

### Table 2: projects
**Purpose**: Directory of all projects (unique projects only)  
**Insertion**: ProjectAggregator upserts during aggregation  
**Usage**: Foreign key reference for all daily project tables

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| project_name | TEXT | NO | Primary key (e.g., "desktop-agent") |
| project_path | TEXT | YES | Full path to project root |
| first_seen_at | TEXT | NO | Timestamp of first activity |
| last_active_at | TEXT | NO | Timestamp of most recent activity |
| needs_sync | INTEGER | NO (Default 1) | 0=synced, 1=pending sync |

**Lifecycle:**
```
1. ProjectAggregator detects new project in raw logs
2. Inserts row with first_seen_at = NOW()
3. On each activity: Updates last_active_at
4. ActivitySyncer uploads (needs_sync=1) then marks (needs_sync=0)
```

### Table 3: daily_project_languages
**Purpose**: Daily language time per project (granular tracking)  
**Primary Key**: (date, project_name, language_name)  
**Insertion**: LanguageAggregator aggregates by date

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| date | TEXT | NO | Date in YYYY-MM-DD format (local timezone) |
| project_name | TEXT | NO | Project name (FK: projects) |
| language_name | TEXT | NO | Language (Python, JavaScript, TypeScript, etc.) |
| duration_sec | INTEGER | NO (Default 0) | Total time spent in this language |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Date | Project | Language | Duration (sec)
-----+---------+----------+----------------
2026-04-10 | desktop-agent | Python | 2400
2026-04-10 | desktop-agent | JavaScript | 1800
2026-04-10 | web-dashboard | Python | 1200
2026-04-10 | web-dashboard | React | 3600
```

### Table 4: daily_project_apps
**Purpose**: Daily app usage per project  
**Primary Key**: (date, project_name, app_name)  
**Insertion**: AppAggregator aggregates by app

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| date | TEXT | NO | Date in YYYY-MM-DD format |
| project_name | TEXT | NO | Project name (FK: projects) |
| app_name | TEXT | NO | App name (VS Code, Chrome, Terminal, etc.) |
| duration_sec | INTEGER | NO (Default 0) | Time spent in this app |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Date | Project | App | Duration (sec)
-----+---------+-----+---------------
2026-04-10 | desktop-agent | VS Code | 3600
2026-04-10 | desktop-agent | Chrome | 1200
2026-04-10 | desktop-agent | Terminal | 600
```

### Table 5: daily_project_context
**Purpose**: Daily mental state distribution per project  
**Primary Key**: (date, project_name, context_state)  
**Insertion**: ContextAggregator aggregates by context state

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| date | TEXT | NO | Date in YYYY-MM-DD format |
| project_name | TEXT | NO | Project name (FK: projects) |
| context_state | TEXT | NO | Mental state (Flow/Debugging/Research/Communication/Distracted) |
| duration_sec | INTEGER | NO (Default 0) | Time in this state |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Date | Project | Context | Duration (sec)
-----+---------+---------+---------------
2026-04-10 | desktop-agent | Flow | 4000
2026-04-10 | desktop-agent | Debugging | 1200
2026-04-10 | desktop-agent | Research | 600
```

### Table 6: daily_project_behavior
**Purpose**: Daily behavioral metrics per project (averages/aggregates)  
**Primary Key**: (date, project_name)  
**Insertion**: BehaviorAggregator aggregates metrics

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| date | TEXT | NO | Date in YYYY-MM-DD format |
| project_name | TEXT | NO | Project name (FK: projects) |
| typing_intensity_kpm | REAL | NO (Default 0.0) | Average keystrokes per minute |
| mouse_click_rate_cpm | REAL | NO (Default 0.0) | Average clicks per minute |
| total_deletion_key_presses | INTEGER | NO (Default 0) | Total backspace/delete presses |
| total_idle_sec | INTEGER | NO (Default 0) | Total inactive time |
| total_mouse_movement_distance | REAL | NO (Default 0.0) | Total pixels moved |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Date | Project | Typing (KPM) | Clicks (CPM) | Deletions | Idle (sec) | Mouse (px)
-----+---------+--------------+--------------+-----------+------------+----------
2026-04-10 | desktop-agent | 45.2 | 8.1 | 542 | 1200 | 125000
```

### Table 7: project_skills
**Purpose**: Cumulative programming skill/language time per project (across all dates)  
**Primary Key**: (project_name, skill_name)  
**Insertion**: SkillAggregator accumulates across dates

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| project_name | TEXT | NO | Project name (FK: projects) |
| skill_name | TEXT | NO | Skill category (Backend/Frontend/Mobile/Data/DevOps/etc.) |
| duration_sec | INTEGER | NO (Default 0) | Total time spent (cumulative across all dates) |
| last_updated_at | TEXT | NO | Timestamp of last update |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Project | Skill | Duration (sec) | Last Updated
--------+-------+----------------+--------------
desktop-agent | Backend | 5400 | 2026-04-10 18:30:00
desktop-agent | Frontend | 3200 | 2026-04-10 18:30:00
desktop-agent | DevOps | 900 | 2026-04-10 18:30:00
```

### Table 8: project_loc_snapshots
**Purpose**: Lines of code per language (periodic snapshots)  
**Primary Key**: (project_name, language_name)  
**Insertion**: LOCScanner updates during scan, ProjectAggregator reads

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| project_name | TEXT | NO | Project name (FK: projects) |
| language_name | TEXT | NO | Language (Python, JavaScript, etc.) |
| lines_of_code | INTEGER | NO (Default 0) | LOC count |
| file_count | INTEGER | NO (Default 0) | Number of source files |
| last_scanned_at | TEXT | NO | Timestamp of last scan |
| needs_sync | INTEGER | NO (Default 1) | Sync status |

**Example Data:**
```
Project | Language | LOC | Files | Last Scanned
--------+----------+-----+-------+--------------------
desktop-agent | Python | 5234 | 15 | 2026-04-10 12:00:00
desktop-agent | JavaScript | 2891 | 8 | 2026-04-10 12:00:00
```

### Table 9: local_user
**Purpose**: Authenticated user profile (single row)  
**Primary Key**: id = 1 (only one user)  
**Insertion**: AuthBridge on successful login  
**Usage**: Include backend_user_id in API calls

| Column | Type | Nullable | Description |
|--------|------|----------|-------------|
| id | INTEGER | NO | Primary key (always 1) |
| backend_user_id | TEXT | NO | Unique user ID from backend |
| email | TEXT | NO | User email |
| name | TEXT | NO | User display name |
| profile_photo | TEXT | YES | URL to profile photo |
| is_verified | INTEGER | NO (Default 0) | Email verification status |
| role | TEXT | NO (Default "user") | User role (user/admin) |
| created_at | TEXT | YES | Account creation timestamp |
| updated_at | TEXT | YES | Last profile update timestamp |

---

## Data Flow & Workflows

### Workflow 1: Real-Time Activity Capture

```
┌─────────────────────────────────────────────────────────────┐
│ DesktopAgent Main Loop (agent.py)                           │
│ Interval: 2-5 seconds (sample_interval_sec)                │
└─────────────────────────────────────────────────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────┐
│ Call: get_active_window()                                   │
│ Returns: (app_name, window_title, pid)                      │
│ Example: ("code.exe", "main.py - /home/user/project - ...", 1234) │
└─────────────────────────────────────────────────────────────┘
                        ▼
       ┌────────────────┴────────────────┐
       │                                 │
       ▼                                ▼
    App Changed?                   Same App?
       │                                 │
       │ YES                             │ NO
       ▼                                ▼
  ┌─────────────────┐          ┌──────────────────┐
  │ Flush current   │          │ Continue tracking│
  │ ActivitySession │          │ current session  │
  │ to DB           │          └──────────────────┘
  └─────────────────┘
       ▼
  ┌──────────────────────────────────────┐
  │ Insert to raw_activity_logs:         │
  │ - start_time, end_time, duration_sec │
  │ - app_name, window_title             │
  │ - behavioral signals (KPM, CPM, etc.)│
  │ - project_name (detected or NULL)    │
  │ - context_state = NULL (unevaluated) │
  │ - is_aggregated = 0                  │
  └──────────────────────────────────────┘
       ▼
  ┌──────────────────────────────────────┐
  │ Create new ActivitySession           │
  │ - Reset behavioral metrics           │
  │ - Detect project from new window     │
  │ - Apply sticky project if browser    │
  └──────────────────────────────────────┘
```

**Key Points:**
- Each window/app change = new session flush
- Time-based flush every flush_interval_sec (300s)
- Behavioral signals accumulated during session
- Project detection happens at session start
- context_state = NULL until BlockEvaluator tags it

### Workflow 2: Project Detection from IDE

```
┌──────────────────────────────────────────┐
│ ProjectDetector.detect_project()         │
│ Input: app_name, window_title, pid       │
│ Called by: ActivitySession.__init__()    │
└──────────────────────────────────────────┘
                    ▼
         ┌──────────────────────┐
         │ Is this an IDE?      │
         │ Check config.yaml    │
         │ ides[].executable_.. │
         └──────────────────────┘
                    ▼
       ┌────────────────┴────────────────┐
       │                                 │
       ▼ YES (IDE found)               ▼ NO (Browser/generic)
    ┌──────────────────┐            ┌──────────────────┐
    │ Layer 1: PID-based           │ Layer 2: Title    │
    │ detection                    │ parsing           │
    │                              │                   │
    │ • Get process files          │ • Extract from    │
    │ • Walk up to find root       │   window title    │
    │ • Check markers (.git, etc.) │ • Browser format  │
    └──────────────────┘            └──────────────────┘
                ▼                           ▼
         ┌────────────────┐           ┌──────────────┐
         │ Found?         │           │ Found?       │
         └────────────────┘           └──────────────┘
           │         │                  │          │
       YES │         │ NO               │ YES      │ NO
           ▼         ▼                  ▼          ▼
         Return   Layer 2          Return    Layer 3
         project  Title parsing    project   Safe search
         path     (walk up trie)   name      (bounded)
```

**Layer 1 - PID-based (Most Accurate):**
```python
# Example: VS Code opened with /home/user/my-project
process = Process(pid=1234)
files = process.open_files()  # ['/path/to/file.py', ...]
while file_path:
    parent = file_path.parent
    if (parent / '.git').exists():
        return parent.name  # "my-project"
    file_path = parent
```

**Layer 2 - Title parsing:**

VS Code format:
```
Window title: "main.py - /home/user/my-project - VS Code"
              ─────────   ────────────────────   ────────
               File              Project          IDE
```

PyCharm format:
```
Window title: "my-project - [main.py] - PyCharm"
              ──────────    ────────   ────────
               Project        File      IDE
```

**Layer 3 - Safe search (Fallback):**
```
Search constraints:
- Time limit: 1.0 second max
- Depth limit: 6 levels deep from drive root
- Skip expensive dirs: node_modules, __pycache__, Windows, etc.
```

**Sticky Project Attribution (Browser Sessions):**
```
DesktopAgent tracks: sticky_project_name (from IDE), sticky_project_time (started)
TTL: 15 minutes (sticky_project_ttl_sec=900)

When browser/terminal opened with no project detected:
1. If sticky_project_name exists AND (NOW - sticky_project_time) < 900 sec:
   → Assign sticky_project_name to this session
2. Else:
   → project_name = NULL (generic, not related to any project)
```

### Workflow 3: Block Evaluation & Context Detection

```
┌────────────────────────────────────────────┐
│ BlockEvaluator._run_loop()                 │
│ Background thread, wakes every 300 seconds │
└────────────────────────────────────────────┘
                      ▼
         ┌────────────────────────┐
         │ Wait for next block    │
         │ boundary (0s, 300s,    │
         │ 600s, etc.)            │
         └────────────────────────┘
                      ▼
  ┌─────────────────────────────────────────────┐
  │ Extract unevaluated logs:                   │
  │ SELECT * FROM raw_activity_logs             │
  │ WHERE is_aggregated=0                       │
  │   AND context_state IS NULL                 │
  │   AND end_time > NOW() - block_duration_sec │
  └─────────────────────────────────────────────┘
                      ▼
┌─────────────────────────────────────────────────────┐
│ Aggregate 8 Behavioral Signals:                    │
│ • typing_kpm = SUM(typing) / (duration / 60)       │
│ • correction_ratio = DELETE_KEYS / TOTAL_KEYS      │
│ • mouse_velocity = MOUSE_DISTANCE / duration       │
│ • click_rate = SUM(clicks) / (duration / 60)       │
│ • app_switches = COUNT(DISTINCT app_name)          │
│ • app_score = SUM(app_productivity * duration)     │
│ • idle_ratio = SUM(idle) / total_duration          │
│ • hours_worked = TIME_SINCE(session_start)         │
└─────────────────────────────────────────────────────┘
                      ▼
         ┌──────────────────────┐
         │ is_ml_enabled?       │
         │ (config.yaml)        │
         └──────────────────────┘
            │                 │
        YES │                 │ NO
            ▼                 ▼
     ┌──────────────┐   ┌────────────────┐
     │ ML Prediction│   │ Heuristic      │
     │ (XGBoost)    │   │ Fallback       │
     └──────────────┘   └────────────────┘
            ▼                 ▼
  ┌─────────────────────────────────┐
  │ Get confidence score:           │
  │ If confidence >= threshold:     │
  │   → Use result (ML/heuristic)   │
  │ Else:                           │
  │   → Lower confidence variant    │
  └─────────────────────────────────┘
                ▼
     ┌─────────────────────────┐
     │ ESM Popup enabled?      │
     │ confidence <            │
     │ esm_popup.threshold?    │
     └─────────────────────────┘
          │                 │
        YES │                 │ NO
          ▼                 ▼
  ┌──────────────────────┐ ┌─────────────────┐
  │ Show popup to user:  │ │ Use prediction  │
  │ "I think you're in   │ │ as-is           │
  │ FLOW mode. Correct?" │ └─────────────────┘
  │ Wait: 30 seconds     │
  │ If verified:         │
  │ Use user label       │
  └──────────────────────┘
              ▼
   ┌────────────────────────────────┐
   │ Update raw_activity_logs:      │
   │ SET context_state = ?,         │
   │     confidence_score = ?,      │
   │     manually_verified_label = ?│
   │ WHERE log_id IN (...)          │
   └────────────────────────────────┘
```

### Workflow 4: Atomic ETL Aggregation

```
┌──────────────────────────────────────────────┐
│ ETLPipeline.run()                            │
│ Triggers: After BlockEvaluator, or manually  │
│ Execution: Atomic transaction (all-or-none)│
└──────────────────────────────────────────────┘
                      ▼
           BEGIN TRANSACTION
                      ▼
        ┌─────────────────────────┐
        │ 1. EXTRACT              │
        │ Query unevaluated logs: │
        │ is_aggregated=0         │
        │ context_state NOT NULL  │
        └─────────────────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 2. TRANSFORM            │
        │ • Normalize projects    │
        │ • Split midnight        │
        │ • Override with manual  │
        │ • Clean app names       │
        └─────────────────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 3. DELEGATE to 6        │
        │ Aggregators:            │
        │ • ProjectAgg            │
        │ • AppAgg                │
        │ • LanguageAgg           │
        │ • SkillAgg              │
        │ • ContextAgg            │
        │ • BehaviorAgg           │
        └─────────────────────────┘
           │     │     │     │    │
           ▼     ▼     ▼     ▼    ▼
    ┌──────────────────────────────┐
    │ Each generates SQL:          │
    │ INSERT OR REPLACE statement  │
    └──────────────────────────────┘
                      ▼
        ┌─────────────────────────┐
        │ 4. LOAD (All or Nothing)│
        │ Execute all SQL in      │
        │ single transaction      │
        │ Mark aggregated:        │
        │ is_aggregated = 1       │
        └─────────────────────────┘
                      ▼
           COMMIT TRANSACTION
```

---

## Context Detection System

### 5 Mental States

#### **1. Flow**
**Definition:** Deep focus, confident typing, productive app, locked in

**Signals:**
- ✅ High typing (50-200 KPM, but also 0 when reading code)
- ✅ Low correction ratio (<8%, fewer deletions/backspace)
- ✅ Moderate mouse (10-45 px/sec)
- ✅ Productive app (IDE, Terminal)
- ✅ Low app switches (<3/block)
- ✅ Low idle ratio (normal thinking pauses 20%)

**Duration:** Can sustain for hours with fatigue factor diminishing confidence

#### **2. Debugging**
**Definition:** Trial & error, problem-solving, scattered navigation

**Signals:**
- ✅ Variable typing (50-200 KPM with pauses)
- ❌ High correction ratio (>12%, lots of deletions)
- ✅ Scattered mouse (20-55 px/sec, jumping between files)
- ✅ Moderate app switches (>4/block, IDE + browser searching)
- ✅ IDE focused but context fragmented
- ✅ Lower confidence (manual verification recommended)

#### **3. Research**
**Definition:** Learning, reading documentation, exploring

**Signals:**
- ✅ Zero typing (0 KPM, pure reading mode)
- ✅ High scrolling (30-70 px/sec, vertical movement)
- ✅ High click rate (8-35 CPM, clicking links)
- ✅ High idle ratio (20-50%, absorbing information)
- ✅ Browser/IDE mix (reading learn-to-code, API docs)
- ✅ Low app score (neutral or negative, depends on source)

#### **4. Communication**
**Definition:** Active in messaging/meeting apps

**Signals:**
- ✅ App in communication list (Slack, Teams, Zoom, Discord)
- ✅ Variable typing (0-50 KPM, messaging)
- ✅ Presence detected (user context from app detection)
- ✅ Time-bounded (usually <1 hour blocks)
- ❌ NOT while coding (IDE not primary)

#### **5. Distracted**
**Definition:** Off-task, entertainment, social media

**Signals:**
- ✅ App in distraction list (YouTube, Reddit, Twitter, Netflix, Spotify)
- ✅ High idle ratio (40-80%, staring at screen)
- ✅ Variable mouse/scroll (5-67 px/sec, flipping between content)
- ✅ No project context
- ❌ App score very negative (<-0.5)

### Heuristic Decision Tree (8 Signals)

```
Signal Analysis → Context Classification

IF app in distraction_apps:
    RETURN (Distracted, confidence=0.75)

IF app in communication_apps:
    IF user_context_present:
        RETURN (Communication, confidence=0.80)
    ELSE if time_in_app > 10_min:
        RETURN (Communication, confidence=0.75)

IF app in productive_apps OR IDE detected:
    IF typing_kpm > 0 AND correction_ratio < 8%:
        IF app_switches < 3 AND app_score > 0.5:
            IF idle_ratio < 30%:
                context = Flow
            ELSE if idle_ratio < 50%:
                context = Flow  (with thinking pauses)
        ELIF app_switches > 4:
            context = Debugging
        ELSE:
            context = Flow
    ELIF typing_kpm == 0 AND (click_rate > 8 OR mouse_velocity > 30):
        context = Research
    ELIF typing_kpm > 80 AND correction_ratio > 12%:
        context = Debugging
    ELSE:
        context = Flow (ambiguous, default to productive state)
    
    # Fatigue factor
    IF hours_worked > 6:
        confidence -= 0.17  (Flow: 0.92 → 0.75)
    ELIF hours_worked > 4:
        confidence -= 0.07  (Flow: 0.92 → 0.85)

IF app in neutral_apps (browser):
    IF browser_tab_contains("documentation", "api", "stackoverflow"):
        context = Research
    ELIF browser_tab_contains("youtube", "reddit", "twitter"):
        context = Distracted  (if long idle_ratio)
    ELIF user has active IDE in sticky context:
        context = Research  (reading for current project)
    ELSE:
        context = Ambiguous

CONFIDENCE MAPPING:
  Flow (fresh):           confidence = 0.92
  Flow (4h fatigue):      confidence = 0.85
  Flow (6h fatigue):      confidence = 0.75
  Debugging:              confidence = 0.85
  Research:               confidence = 0.80
  Communication:          confidence = 0.80
  Distracted:             confidence = 0.75
  Ambiguous:              confidence = 0.55
```

---

## Aggregation Pipeline (ETL)

### Atomic Transaction Guarantee

**Key Feature:** All aggregations happen in single database transaction

```python
BEGIN TRANSACTION
  
  # All 6 aggregators generate SQL
  sql_list = [
      aggregator1.generate_sql(),   # ProjectAgg
      aggregator2.generate_sql(),   # AppAgg
      ... (remaining 4)
  ]
  
  # Execute ALL at once
  for sql in sql_list:
      cursor.execute(sql)
  
  # Mark as aggregated (ONLY if above succeeds)
  cursor.execute("""
      UPDATE raw_activity_logs 
      SET is_aggregated=1, aggregated_at=NOW()
      WHERE log_id IN (...)
  """)

COMMIT  # All-or-nothing
```

**Benefit:** If any aggregator fails, entire transaction rolls back. Raw logs remain unaggregated (safe to retry).

### Aggregation Sequence

1. **ProjectAggregator**: Ensure all projects exist
2. **AppAggregator**: Daily app usage per project
3. **LanguageAggregator**: Daily language time per project
4. **SkillAggregator**: Cumulative skill time per project
5. **ContextAggregator**: Daily mental state distribution
6. **BehaviorAggregator**: Daily behavioral metrics

### Example: Full Aggregation

**Raw Logs (3 entries for desktop-agent on 2026-04-10):**
```
log_id | start_time | end_time | app_name | duration | context_state | language
-------|------------|----------|----------|----------|---------------|---------
1      | 09:00:00   | 09:15:00 | code.exe | 900      | Flow          | Python
2      | 09:15:00   | 09:30:00 | chrome.  | 900      | Research      | NULL
3      | 09:30:00   | 09:45:00 | code.exe | 900      | Debugging     | Python
```

**Aggregated Results:**

projects:
```
project_name: "desktop-agent"
last_active_at: "2026-04-10 09:45:00"
```

daily_project_apps:
```
date: "2026-04-10"
project: "desktop-agent"
VS Code: 1800 sec (900 + 900)
Chrome: 900 sec
```

daily_project_languages:
```
date: "2026-04-10"
project: "desktop-agent"
Python: 1800 sec (flow 900 + debugging 900)
```

daily_project_context:
```
date: "2026-04-10"
project: "desktop-agent"
Flow: 900 sec
Research: 900 sec
Debugging: 900 sec
```

project_skills:
```
project: "desktop-agent"
Backend (Python): 1800 sec added
```

---

## Configuration System

### config.yaml Structure

**Location:** `config/config.yaml`

**Sections:**

#### **1. Phase 1: Real-time Monitoring**
```yaml
sample_interval_sec: 5          # How often to sample active window
flush_interval_sec: 300         # How often to flush sessions
idle_threshold_sec: 10          # Inactivity threshold for idle detection

behavioral_metrics:
  click_debounce_ms: 50         # Ignore clicks < 50ms apart
  max_typing_intensity_kpm: 200 # Cap typing rate
  max_mouse_click_rate_cpm: 200 # Cap click rate
```

#### **2. Phase 2: Block Evaluation**
```yaml
block_duration_sec: 300         # Block duration (5 min)

block_evaluator:
  startup_delay_sec: 300        # Delay before first evaluation

heuristics:
  typing_kpm_flow_min: 0        # Minimum typing for Flow
  correction_ratio_debug_min: 0.12  # Debugging threshold
  mouse_velocity_flow_max: 45   # Max velocity for Flow
  ... (many more thresholds)
```

#### **3. Phase 3: Machine Learning**
```yaml
ml_enabled: true                # Enable/disable ML
ml_model_path: ./data/models/context_detector.pkl
ml_confidence_threshold: 0.5    # Use ML if confidence >= threshold
```

#### **4. IDE Detection**
```yaml
project_detector:
  ides:
    - name: "VS Code"
      executable_names: [...]
      title_suffixes: [...]
      title_format: "vscode"
  
  project_markers:
    - .git
    - package.json
    - requirements.txt
    ...
  
  language_extensions:
    .py: "Python"
    .ts: "TypeScript"
    ...
```

#### **5 App Scoring**
```yaml
ml_app_scoring:
  productive_apps: [vscode, code, cursor, pycharm, ...]
  communication_apps: [slack, teams, zoom, ...]
  distraction_apps: [youtube, reddit, netflix, ...]
  neutral_apps: [chrome, firefox, notion, ...]
```

#### **6. ETL Pipeline**
```yaml
etl_pipeline:
  sticky_project_ttl_sec: 900   # 15 min
  app_name_mapping:
    code.exe: "VS Code"
    chrome.exe: "Chrome"
```

---

## IDE Detection & Project Identification

### Supported IDEs

| IDE | Executable Names | Title Format | Config Location |
|-----|------------------|--------------|-----------------|
| VS Code | code.exe, code | `file - /path - VS Code` | `project_detector.ides[0]` |
| Cursor | cursor.exe, cursor-win | `file - /path - Cursor` | `project_detector.ides[1]` |
| PyCharm | pycharm64.exe, pycharm | `project - [file.py]` | `project_detector.ides[2]` |
| IntelliJ IDEA | idea64.exe, idea | `project - [file.java]` | `project_detector.ides[3]` |
| WebStorm | webstorm64.exe | `project - [file.js]` | `project_detector.ides[4]` |
| Sublime Text | sublime.exe | Varies | `project_detector.ides[5]` |
| Vim/Neovim | vim.exe, nvim | Varies | `project_detector.ides[6]` |
| Emacs | emacs.exe | Varies | `project_detector.ides[7]` |
| Antigravity | antigravity.exe | Generic | `project_detector.ides[8]` |

### Adding a New IDE

**Step 1:** Add to config.yaml
```yaml
- name: "RustRover"
  executable_names: ["rustrover.exe", "rustrover"]
  title_suffixes: ["RustRover"]
  title_format: "pycharm"  # or "vscode" or "generic"
```

**Step 2:** No code changes needed! ✅

---

## Performance & Safety

### CPU Constraints
- **Phase 1:** < 2% CPU (window sampling is negligible)
- **Phase 2:** < 5% CPU (BlockEvaluator background thread)
- **Phase 3:** < 5% CPU (ETL pipeline atomic transaction, fast)
- **Overall:** < 10% CPU at peak

### Memory Constraints
- **Base footprint:** ~50 MB (process, DB connection)
- **Per-session:** ~1 KB (ActivitySession object)
- **Behavioral signals:** ~100 bytes accumulated per 5-min block
- **No memory leaks:** Sessions cleared after flush

### Database Safety
- **WAL mode:** Write-Ahead Logging for concurrency
- **Foreign keys:** Enabled (PRAGMA foreign_keys=ON)
- **Transactions:** Atomic ETL guarantees
- **Indexes:** Optimized query paths
- **Backups:** DB file persists locally (sync to backend hourly)

### Data Privacy
- **Local-only:** No sensitive data in logs
- **Anonymization:** Project names only (no credentials)
- **Encryption:** In transit (HTTPS to backend)
- **User control:** Manual verification (ESM popups)

---

## Example: Complete Day's Workflow

**Timeline:**

```
09:00 - DesktopAgent starts
│       ├─ Sample active window: VS Code
│       ├─ Create ActivitySession(app="code.exe", project="desktop-agent")
│       └─ Track behavioral: User typing, coding Flow state
│
09:15 - Window change
│       ├─ Flush session (900 sec)
│       ├─ Insert raw_activity_logs (context_state=NULL)
│       ├─ Create new session: Chrome (project=sticky["desktop-agent"])
│       └─ Start tracking: Web research
│
09:30 - BlockEvaluator wakes (300 sec boundary)
│       ├─ Query unevaluated logs from last 300s
│       ├─ Aggregate signals:
│       │   ├─ typing_kpm = 12.0
│       │   ├─ click_rate_cpm = 15.0
│       │   ├─ app_score = 0.25 (browser)
│       │   └─ context_state = Research
│       ├─ Update raw_activity_logs with context_state
│       └─ (Second entry still NULL, waits for block complete)
│
09:45 - Window change → VS Code again
│       ├─ Flush Chrome session (900 sec)
│       ├─ Insert raw_activity_logs for Chrome
│       ├─ Create new session: VS Code (project="desktop-agent")
│       └─ Track: Debugging (high deletion keys)
│
10:00 - BlockEvaluator wakes (second block)
│       ├─ Query unevaluated logs from 09:45-10:00

│       ├─ Aggregate signals:
│       │   ├─ typing_kpm = 95.0
│       │   ├─ correction_ratio = 0.15 (debugging!)
│       │   └─ context_state = Debugging
│       ├─ Update raw_activity_logs
│       └─ ETLPipeline triggered
│           ├─ EXTRACT: All logs with context_state populated
│           ├─ TRANSFORM: Normalize project "desktop-agent"
│           ├─ DELEGATE: 6 aggregators generate SQL
│           ├─ LOAD: Execute atomically
│           │   ├─ projects: upsert "desktop-agent"
│           │   ├─ daily_project_apps: VS Code 1800+900, Chrome 900
│           │   ├─ daily_project_languages: Python 1800
│           │   ├─ daily_project_context: Flow 900, Research 900, Debug 900
│           │   └─ daily_project_behavior: typing_kpm=50, mouse_clicks=5, ...
│           └─ Mark logs: is_aggregated=1
│
18:00 - End of day
│       ├─ Multiple flows, researches, debuggings tracked
│       ├─ All sessions flushed to raw_activity_logs
│       ├─ BlockEvaluator tagged with context_state
│       └─ ETLPipeline aggregated daily metrics
│
23:00 - ActivitySyncer wakes (hourly)
        └─ Upload to backend:
            ├─ projects
            ├─ daily_project_apps
            ├─ daily_project_languages
            ├─ daily_project_context
            ├─ daily_project_behavior
            ├─ project_skills
            └─ Mark needs_sync=0
```

---

## Troubleshooting

### Issue: Project not detected
**Solution:**
1. Check IDE is in config.yaml `ides` section
2. Verify window title format matches
3. Check project markers exist in root (.git, package.json, etc.)
4. Enable debug logging to see detection layer

### Issue: Wrong context state
**Solution:**
1. Check heuristic thresholds in config.yaml (e.g., `typing_kpm_flow_min`)
2. Enable ESM popups to manually verify low-confidence predictions
3. Check `ml_confidence_threshold` - may need adjustment

### Issue: High CPU usage
**Solution:**
1. Increase `sample_interval_sec` (less frequent sampling)
2. Check BlockEvaluator `startup_delay_sec` - wait longer before first eval
3. Disable LOC scanning or increase scan interval

### Issue: Database growing too large
**Solution:**
1. Archive old logs (before 30 days) to separate DB
2. Truncate raw_activity_logs after aggregation (marked is_aggregated=1)
3. Check for stuck logs (context_state still NULL after 24h)

---

## Architecture Diagrams (Text)

### Component Interaction Diagram
```
┌──────────────┐
│ DesktopAgent │
└──────┬───────┘
       │ samples every 2-5s
       ▼
┌──────────────────────────────────────┐
│ ActivitySession                      │
│ ├─ BehavioralMetrics                 │
│ │  ├─ KPM (keyboard tracking)        │
│ │  ├─ CPM (mouse tracking)           │
│ │  ├─ Deletions                      │
│ │  └─ Mouse movement                 │
│ ├─ IdleDetector                      │
│ └─ ProjectDetector                   │
│    ├─ Layer 1: PID-based             │
│    ├─ Layer 2: Window title parsing  │
│    └─ Layer 3: Safe search           │
└──────┬───────────────────────────────┘
       │ flushes every 300s or on window change
       ▼
┌──────────────────────┐
│ raw_activity_logs    │ (context_state = NULL)
│ (Phase 1 Collection) │
└──────────┬───────────┘
           │
           │ background thread, every 300s
           ▼
┌──────────────────────────────────────┐
│ BlockEvaluator                       │
│ ├─ 8-signal aggregation              │
│ ├─ FeatureExtractor → ML model       │
│ ├─ ContextDetector → Heuristics      │
│ ├─ ESM Popup (optional)              │
│ └─ Update context_state              │
└──────────┬───────────────────────────┘
           │
           │ logs now have context_state
           ▼
┌──────────────────────────────────────┐
│ ETLPipeline (Atomic Transaction)     │
│ ├─ 1. EXTRACT                        │
│ ├─ 2. TRANSFORM                      │
│ ├─ 3. DELEGATE to 6 Aggregators:     │
│ │    ├─ ProjectAggregator            │
│ │    ├─ AppAggregator                │
│ │    ├─ LanguageAggregator           │
│ │    ├─ SkillAggregator              │
│ │    ├─ ContextAggregator            │
│ │    └─ BehaviorAggregator           │
│ └─ 4. LOAD (all-or-nothing)          │
└──────────┬───────────────────────────┘
           │
           ▼
╔══════════════════════════════════════╗
║ 7 Daily Aggregated Tables            ║
║ ├─ projects                          ║
║ ├─ daily_project_apps                ║
║ ├─ daily_project_languages           ║
║ ├─ daily_project_context             ║
║ ├─ daily_project_behavior            ║
║ ├─ project_skills                    ║
║ └─ project_loc_snapshots             ║
└──────────┬───────────────────────────────┘
           │
           │ hourly, background thread
           ▼
┌──────────────────────────────────────┐
│ ActivitySyncer                       │
│ └─ Upload to backend API             │
└──────────────────────────────────────┘
```

---

**Document Version:** 1.0  
**Last Updated:** April 10, 2026  
**Maintainers:** Zenno FYP Team
