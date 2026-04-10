# Desktop Agent Architecture & Data Flow

## System Overview

Zenno Desktop Agent is a **Windows activity monitor** that captures real-time user activity, processes it through behavioral analysis, and aggregates it into structured project/skill/context metrics. The system operates in three phases:

1. **Collection** - Real-time activity capture
2. **Analysis & Tagging** - Block-based context detection (ML + heuristics)
3. **Aggregation** - ETL pipeline that transforms raw logs into daily project metrics

---

## Architecture Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ ENTRY POINT                                                      │
│ main.py → Auth Window → DesktopAgent (agent.py)                 │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: COLLECTION (Real-time)                                  │
│ DesktopAgent → ActivitySession (per window/app)                  │
│ Monitors: Window Focus/Project/File | Behavioral Signals         │
│ Outputs: raw_activity_logs (context_state = NULL)               │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: ANALYSIS (Block-Based, Background Thread)              │
│ BlockEvaluator runs every block_duration_sec (default 300s)     │
│ 1. Extract unevaluated logs from last block                     │
│ 2. Aggregate Block Metrics (8 signals)                          │
│ 3. ML Prediction (XGBoost) or Heuristic Fallback               │
│ 4. ESM Popup (optional verification)                           │
│ 5. Update raw_activity_logs with context_state + confidence    │
│ Outputs: raw_activity_logs (context_state populated)           │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: AGGREGATION (ETL Pipeline - Atomic Transaction)        │
│ Immediately after tagging, runs ETL Pipeline Maestro           │
│ 1. EXTRACT: Query is_aggregated=0 AND context_state IS NOT NULL│
│ 2. TRANSFORM: Normalize projects, split midnight, resolve paths│
│ 3. DELEGATE: Pass to 6 specialized aggregators                 │
│ 4. LOAD: Execute all SQL in single atomic transaction          │
│ Outputs: 7 daily project tables (aggregated metrics)           │
└─────────────────────────────────────────────────────────────────┘
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: SYNC (Background Thread)                                │
│ ActivitySyncer queries tables with needs_sync=1                 │
│ Collects pending data via ActivityCollector                    │
│ POSTs to backend API after token refresh if needed             │
│ On success, marks all as needs_sync=0                          │
├─────────────────────────────────────────────────────────────────┤
│ PHASE 4B: LOC SCANNING (Idle-Triggered)                          │
│ LOCScanner runs when user idle >30 min                          │
│ Scans project directories, counts lines per language           │
│ Stores in project_loc_snapshots table                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Component Breakdown

### 1. COLLECTION PHASE

#### **agent.py - DesktopAgent (Main Loop)**
- **File**: [agent.py](agent.py)
- **Role**: Entry point; orchestrates all components
- **Key Methods**:
  - `start()` - Main loop; samples active window every `sample_interval_sec`
  - `_session_manager()` - Manages active/inactive sessions; triggers collection
  - `_sync_cycle()` - Periodic backend sync (configurable interval)
  - `_loc_scan_cycle()` - Idle-triggered LOC scanning

**Data Flow**:
```python
every sample_interval_sec:
  1. get_active_window() → (app_name, window_title, PID)
  2. Check if window changed/session ended
  3. If changed → collect_data() on old ActivitySession
  4. Create new ActivitySession or continue existing
  5. Update file context from window title
```

#### **ActivitySession - Per-Window Activity Recorder**
- **File**: [agent.py](agent.py)
- **Role**: Encapsulates single app/window session with all metrics
- **Key Attributes**:
  - `start_time`, `end_time` - Session timespan (ISO format, local time)
  - `app_name`, `window_title`, `pid` - Window identity
  - `project_name`, `project_path`, `active_file`, `detected_language` - Project context
  - `metrics` (BehavioralMetrics) - Typing/clicks/deletions tracker
  - `idle_detector` - Idle period tracking
  - `project_detector` - File/project detection

**Key Methods**:
```python
collect_data() → dict
  Returns activity_data ready for database insertion:
  - start_time, end_time, app_name, window_title, duration_sec
  - project_name, project_path, active_file, detected_language
  - typing_intensity (KPM), mouse_click_rate (CPM)
  - deletion_key_presses, mouse_movement_distance, idle_duration_sec
  - context_state: NULL (will be filled by BlockEvaluator)
```

### 2. MONITORING MODULES

#### **BehavioralMetrics - Real-time Signal Capture**
- **File**: [monitor/behavioral_metrics.py](monitor/behavioral_metrics.py)
- **Signals Tracked**:
  - **Typing Intensity** (KPM) - Keystrokes per minute
  - **Mouse Clicks** (CPM) - Clicks per minute  
  - **Deletion Keys** - Count of Delete/Backspace/Ctrl+Z/Shift+Delete
  - **Mouse Movement** - Total Euclidean distance in pixels
  - **Idle Duration** - Seconds without activity

**Implementation**:
```python
# Listener threads (pynput)
keyboard_listener   → _on_key_press/release
mouse_listener      → _on_mouse_click
mouse_movement_thread → _sample_mouse_movement (100ms intervals)

get_metrics() → {
  'typing_intensity': float (KPM),
  'mouse_click_rate': float (CPM),
  'deletion_key_presses': int,
  'mouse_movement_distance': float (pixels),
}
```

#### **ProjectDetector - IDE Context Detection**
- **File**: [monitor/project_detector.py](monitor/project_detector.py)
- **Responsibility**: Extract project/file/language from IDE window titles
- **Detection Strategy** (IDE-specific parsing):
  - **VS Code**: Parse `filename [dirname] — Visual Studio Code` → extract file path
  - **PyCharm**: Parse from title patterns
  - **IDE config-driven** from `config.yaml` with regex patterns

**Key Methods**:
```python
detect_project(app_name, window_title) 
  → (project_name: str, file_path: str)

get_detected_language(file_path)
  → language_name: str (from extension mapping)

get_project_path(app_name, window_title, pid, file_path)
  → project_path: str (root directory of project)
```

#### **IdleDetector - Inactivity Tracker**
- **File**: [monitor/idle_detector.py](monitor/idle_detector.py)
- **Role**: Measures idle periods within a session
- **Configuration**: `idle_threshold_sec` - seconds before marking idle (default: 10)

**Metrics**:
```python
get_idle_metrics() → {
  'idle_duration_sec': int,
  'idle_ratio': float (0.0 to 1.0),
  'is_currently_idle': bool,
}
```

---

### 3. DATABASE SCHEMA

#### **Core Tables**

| Table | Purpose | Primary Key | Relationships |
|-------|---------|------------|---------------|
| **raw_activity_logs** | Raw activity records (heartbeat) | log_id (auto-inc) | Input from collection |
| **projects** | Unique projects detected | project_name | FK referenced by 6 other tables |
| **daily_project_languages** | Languages worked on per day | (date, project, language) | FK → projects |
| **daily_project_apps** | Apps used per day | (date, project, app) | FK → projects |
| **daily_project_context** | Context time per day | (date, project, context) | FK → projects |
| **daily_project_behavior** | Behavioral metrics per day | (date, project) | FK → projects |
| **project_skills** | Skills (cumulative) | (project, skill) | FK → projects |
| **project_loc_snapshots** | LOC by language (snapshot) | (project, language) | FK → projects |
| **local_user** | Current authenticated user | id (1) | Used for auth |

#### **raw_activity_logs Schema**

```sql
CREATE TABLE raw_activity_logs (
    log_id INTEGER PRIMARY KEY,
    
    -- Timestamps (ISO format, local time)
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    
    -- Window Identity
    app_name TEXT NOT NULL,
    window_title TEXT,
    duration_sec INTEGER NOT NULL,
    
    -- Project Context
    project_name TEXT,              -- "__unassigned__" if unknown
    project_path TEXT,              -- NULL if no IDE session
    active_file TEXT,               -- Path to active file
    detected_language TEXT,         -- "Python", "JavaScript", "Unknown"
    
    -- Behavioral Signals
    typing_intensity REAL,          -- KPM (keystrokes/min)
    mouse_click_rate REAL,          -- CPM (clicks/min)
    deletion_key_presses INTEGER,   -- Count of deletions
    mouse_movement_distance REAL,   -- Pixels moved (Euclidean)
    idle_duration_sec INTEGER,      -- Seconds idle during session
    
    -- Context State (Phase 2 Output)
    context_state TEXT,             -- Flow|Debugging|Research|Communication|Distracted
    confidence_score REAL,          -- 0.0 to 1.0
    
    -- Manual Verification (Phase 3B)
    manually_verified_label TEXT,   -- User ground truth from ESM
    verified_at TIMESTAMP,
    
    -- Processing Flags
    is_aggregated INTEGER DEFAULT 0,  -- 0=pending, 1=aggregated
    aggregated_at TEXT,
    aggregation_version INTEGER DEFAULT 1
);
```

#### **Aggregation Tables Schema**

```sql
-- Daily per-language time tracking
daily_project_languages (
    date TEXT,
    project_name TEXT,
    language_name TEXT,           -- "Python", "JavaScript", etc.
    duration_sec INTEGER,         -- Total seconds on that language
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (date, project_name, language_name)
);

-- Daily per-app time tracking
daily_project_apps (
    date TEXT,
    project_name TEXT,
    app_name TEXT,                -- "VSCode", "Chrome", etc.
    duration_sec INTEGER,
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (date, project_name, app_name)
);

-- Daily context state tracking (mental state)
daily_project_context (
    date TEXT,
    project_name TEXT,
    context_state TEXT,           -- Flow|Debugging|Research|Communication|Distracted
    duration_sec INTEGER,
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (date, project_name, context_state)
);

-- Cumulative skill/language tracking (all time)
project_skills (
    project_name TEXT,
    skill_name TEXT,              -- Mapped from language (Backend, Frontend, etc.)
    duration_sec INTEGER,         -- Cumulative across all dates
    last_updated_at TEXT,
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (project_name, skill_name)
);

-- Daily behavioral metrics
daily_project_behavior (
    date TEXT,
    project_name TEXT,
    typing_intensity_kpm REAL,    -- Avg keystrokes/min
    mouse_click_rate_cpm REAL,    -- Avg clicks/min
    total_deletion_key_presses INTEGER,
    total_idle_sec INTEGER,
    total_mouse_movement_distance REAL,
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (date, project_name)
);

-- LOC snapshots (point-in-time count)
project_loc_snapshots (
    project_name TEXT,
    language_name TEXT,
    lines_of_code INTEGER,
    file_count INTEGER,
    last_scanned_at TEXT,
    needs_sync INTEGER DEFAULT 1,
    PRIMARY KEY (project_name, language_name)
);
```

---

## DATA FLOW: Collection to Aggregation

### Flow 1: Activity Session → raw_activity_logs

```
1. DesktopAgent detects window change every sample_interval_sec
2. If session is valid (duration > 0):
   a. Create ActivitySession instance
   b. Run behavioral monitor (keyboard, mouse, idle)
   c. Detect project/file/language
   
3. End session:
   a. Call session.collect_data()
   b. Compile activity_data dict with all metrics
   c. Call db.insert_activity_log(activity_data)
   
4. Result in database:
   INSERT INTO raw_activity_logs (
     start_time, end_time, app_name, window_title, duration_sec,
     project_name, project_path, active_file, detected_language,
     typing_intensity, mouse_click_rate, deletion_key_presses,
     mouse_movement_distance, idle_duration_sec,
     context_state=NULL, is_aggregated=0
   )
```

### Flow 2: raw_activity_logs → context_state (BlockEvaluator)

```
BlockEvaluator (background thread, runs every block_duration_sec=300s):

1. WAKE UP at aligned block boundary (e.g., :00, :05, :10 minutes)

2. EXTRACT unevaluated logs from last block:
   SELECT * FROM raw_activity_logs
   WHERE context_state IS NULL
   AND end_time >= last_block_start

3. AGGREGATE block metrics from all sessions:
   - Total duration_sec
   - Weighted typing_intensity (KPM)
   - Weighted mouse_click_rate (CPM)
   - Sum deletion_key_presses
   - Sum idle_duration_sec
   - Count of app switches
   - Time-weighted app score (productivity classifier)
   - Fatigue hours (consecutive work time)

4. FEATURE EXTRACT (8-signal vector):
   1. typing_kpm         - Keystrokes/min across block
   2. correction_ratio   - Deleted keys / total keys
   3. mouse_px_per_sec   - Pixel velocity
   4. mouse_cpm          - Clicks/min
   5. switch_freq        - App switches/min
   6. app_score          - Time-weighted productivity (-1.0 to 1.0)
   7. idle_ratio         - Idle time % in block
   8. fatigue_hrs        - Hours since last 30-min break

5. PREDICT context state:
   IF ml_enabled AND ml_model.confidence > ml_confidence_threshold:
     context_state = ml_model.predict(features)
     confidence_score = ml_model.confidence
   ELSE:
     context_state, confidence_score = heuristic_detector.detect(features, apps)

6. VERIFY (optional ESM popup):
   IF esm_popup.enabled AND confidence_score < esm_confidence_threshold:
     Show ESM dialog → user selects ground truth
     manually_verified_label = user_selection
   ELSE:
     manually_verified_label = NULL

7. UPDATE raw_activity_logs:
   UPDATE raw_activity_logs
   SET context_state = ?,
       confidence_score = ?,
       manually_verified_label = ?
   WHERE log_id IN (block_log_ids)
```

### Flow 3: raw_activity_logs → ETL Pipeline (Aggregation)

```
Triggered immediately after BlockEvaluator completes tagging:

ETLPipeline.run():

1. EXTRACT
   ├─ Query: raw_activity_logs WHERE is_aggregated=0 AND context_state IS NOT NULL
   └─ Result: List of (log_id, start_time, end_time, app_name, project_name, ..., context_state)

2. TRANSFORM (once per batch)
   ├─ Project Attribution
   │  ├─ Trust database project_name (filled by upstream sticky logic)
   │  └─ Assign "__unassigned__" for unknown projects
   │
   ├─ Manual Verification Override
   │  └─ IF manually_verified_label IS NOT NULL:
   │     final_context = manually_verified_label
   │     ELSE:
   │     final_context = ML context_state
   │
   ├─ Midnight Splitting (local time)
   │  └─ IF start_time and end_time cross midnight:
   │     Split into two logs: one for each date
   │     (Ensures daily aggregates are correct)
   │
   └─ App Name Cleaning
      ├─ Remove .exe extension
      ├─ Map to canonical names (config-driven)
      └─ Extract browser service from active_file URL (if Chrome/Firefox)

3. DELEGATE to 6 Aggregators (in order):
   ├─ ProjectAggregator
   │  └─ Groups by: project_name
   │     Upserts: projects table (path, first_seen, last_active)
   │
   ├─ AppAggregator
   │  └─ Groups by: (date, project_name, app_name)
   │     Aggregates: duration_sec
   │     Upserts: daily_project_apps
   │
   ├─ LanguageAggregator
   │  └─ Groups by: (date, project_name, language_name)
   │     Filter: only if project_path IS NOT NULL (actual coding)
   │     Aggregates: duration_sec
   │     Upserts: daily_project_languages
   │
   ├─ SkillAggregator
   │  └─ Maps language_name → skill_name (config-driven)
   │     Groups by: (project_name, skill_name) [cumulative]
   │     Aggregates: duration_sec
   │     Upserts: project_skills
   │
   ├─ ContextAggregator
   │  └─ Groups by: (date, project_name, context_state)
   │     Aggregates: duration_sec
   │     Upserts: daily_project_context
   │
   └─ BehaviorAggregator
      └─ Groups by: (date, project_name)
         Aggregates: typing_kpm, mouse_cpm, deletions, idle, mouse_distance
         Upserts: daily_project_behavior

4. LOAD (single atomic transaction)
   BEGIN TRANSACTION
   ├─ Execute all SQL commands from aggregators
   └─ Mark processed logs: UPDATE raw_activity_logs SET is_aggregated=1
   COMMIT
```

---

## ANALYSIS MODULES: Context Detection

### 1. ContextDetector - Heuristic Fallback

**File**: [analyze/context_detector.py](analyze/context_detector.py)

**5-State Classification Model**:
```
Flow          → Smooth creation, deep work (typing 80-150 KPM, low corrections)
Debugging     → Trial & error, fixing bugs (high corrections >12%, frequent switches)
Research      → Reading docs, navigation (high mouse velocity, low typing)
Communication → Chat, meetings, email (communication apps, idle time)
Distracted    → Non-work activity (distraction apps, high mouse, jumping)
```

**Signal-Based Architecture** (7 psychological signals):

| Signal | Range | Flow | Debugging | Research | Communication | Distracted |
|--------|-------|------|-----------|----------|------|---|
| **Typing KPM** | 0-200+ | 80-150 | 40-100 | <30 | <20 | <20 |
| **Correction Ratio** | 0-1.0 | <0.08 | >0.12 | 0.05-0.10 | <0.05 | <0.05 |
| **Mouse Velocity (px/s)** | 0-500 | <90 | 40-150 | >100 | 80-150 | >200 |
| **Click Rate (CPM)** | 0-200 | 3-8 | 5-15 | 8-20 | 10-30 | >20 |
| **App Switches** | 0-50/min | <1 | 4+ | 2-4 | 5-10 | 10+ |
| **App Score** | -1.0 to 1.0 | >0.5 | 0.2-0.7 | 0.3-0.7 | 0.0-0.3 | <-0.5 |
| **Idle Ratio** | 0-1.0 | <0.1 | 0.1-0.2 | 0.2-0.5 | 0.3-0.7 | <0.2 |

**Heuristic Decision Tree**:
```
Priority 0: HIGH IDLE (>70%)
├─ IF app_score < -0.5 → Distracted (Away)
├─ IF communication_app → Communication (Listening)
├─ ELSE → Research (Thinking)

Priority 1: APP CATEGORY
├─ IF communication_app → Communication
├─ IF distraction_app → Distracted
├─ IF productivity_app → Continue to signals

Priority 2: CORRECTION RATIO (Primary differentiator)
├─ IF correction_ratio > 0.12 → Debugging
├─ ELSE → Continue to other signals

Priority 3: TYPING vs MOUSE
├─ IF typing_kpm > 80 AND correction_ratio < 0.08 AND mouse_velocity < 90 → Flow
├─ IF typing_kpm < 30 AND mouse_velocity > 100 → Research
├─ IF mouse_velocity > 200 → Distracted
├─ IF mouse_cpm > 20 → Distracted

Priority 4: Fallback
└─ Ambiguous → Use confidence score <0.55
```

**Confidence Scoring**:
```python
confidence_flow = 0.92           # High confidence in Flow
confidence_debugging = 0.85      # Strong signal from corrections
confidence_research = 0.80       # Good reading signals
confidence_communication = 0.80  # App + idle combo
confidence_distracted = 0.75     # Multiple distraction signals
confidence_ambiguous = 0.55      # Unsure classification
```

### 2. BlockEvaluator - ML Integration

**File**: [analyze/block_evaluator.py](analyze/block_evaluator.py)

**Role**: Orchestrates context detection with optional ML prediction and ESM verification

**Execution Flow**:
```
BlockEvaluator thread (runs every block_duration_sec):

1. STARTUP DELAY (default 300s)
   └─ Allows agent to collect initial data

2. EXTRACT & AGGREGATE
   ├─ Query unevaluated logs from last block
   └─ Aggregate block metrics

3. ML PREDICTION (if enabled)
   ├─ Load model: XGBoost from ml_model_path
   ├─ Extract 8-signal feature vector
   ├─ Get prediction + confidence
   └─ IF confidence > ml_confidence_threshold:
      │  context_state = ml_prediction
      │  confidence_score = ml_confidence
      └─ ELSE:
         └─ Fall back to heuristic

4. HEURISTIC FALLBACK
   ├─ Call context_detector.detect(signals, apps)
   └─ Get heuristic prediction + confidence

5. ESM POPUP (optional)
   ├─ IF confidence < esm_confidence_threshold:
   │  ├─ Show popup: "What are you working on?"
   │  ├─ User selects: Flow|Debug|Research|Comms|Distracted
   │  └─ Store in manually_verified_label
   └─ ELSE: Skip popup (high confidence)

6. UPDATE DATABASE
   └─ Update raw_activity_logs with context_state + confidence

7. TRIGGER ETL
   └─ Call etl_pipeline.run() to aggregate daily metrics
```

**Fatigue Tracking** (Signal 7):
```python
consecutive_work_hours → tracks unbroken work time
break_threshold_sec = 1800 (30 minutes)

# Resets when:
├─ Idle > 30 minutes
├─ OR gap between activity blocks > 30 min
│
# Used in confidence scoring:
├─ IF consecutive_hours < 4:  confidence_flow = 0.92
├─ IF consecutive_hours 4-6: confidence_flow = 0.85
└─ IF consecutive_hours > 6: confidence_flow = 0.75
```

---

## AGGREGATION: ETL Pipeline

### Maestro Pattern

The ETL Pipeline implements the **Maestro pattern**: a central orchestrator that coordinates multiple specialized aggregators.

**Architecture Benefits**:
```
❌ Anti-pattern: Each aggregator does its own TRANSFORM
✅ Pattern: One TRANSFORM, pass clean data to all aggregators

Result:
├─ Single pass through data (less memory)
├─ Consistent business logic (no duplication)
├─ Easy to add new aggregators (just add to list)
└─ Atomic transaction (all-or-nothing)
```

### ETL Stages Detailed

#### Stage 1: EXTRACT
```python
def _extract_raw_logs(self):
    cursor = self.db.conn.execute("""
        SELECT log_id, start_time, end_time, app_name, project_name,
               project_path, detected_language, context_state,
               manually_verified_label, duration_sec, typing_intensity,
               mouse_click_rate, deletion_key_presses, idle_duration_sec,
               active_file, mouse_movement_distance
        FROM raw_activity_logs
        WHERE is_aggregated = 0
          AND context_state IS NOT NULL
        ORDER BY start_time ASC
    """)
    return cursor.fetchall()
```

**Result**: List of tuples, ready for transformation

#### Stage 2: TRANSFORM
```python
def _transform_logs(self, raw_logs):
    """
    For each raw log:
    1. Trust project_name (sticky logic already ran)
    2. Override context if manually_verified_label present
    3. Split across midnight boundaries (local time)
    4. Clean app name + extract browser service
    
    Returns: List of dicts normalized for aggregators
    """
    transformed = []
    
    for row in raw_logs:
        # Parse ISO timestamps
        start_local = datetime.fromisoformat(row.start_time_iso)
        end_local = datetime.fromisoformat(row.end_time_iso)
        
        # Project resolution
        attributed_project = row.project_name or "__unassigned__"
        
        # Context priority: manual > ML
        final_context = row.manually_verified_label or row.context_state
        
        # App name cleaning
        clean_app_name = self._clean_app_name(row.app_name)
        clean_app_name = self._extract_browser_service(clean_app_name, row.active_file)
        
        # Midnight splitting
        segments = self._split_across_midnight_local(start_local, end_local)
        
        for segment in segments:
            transformed.append({
                'log_id': row.log_id,
                'date': segment.date,
                'app_name': clean_app_name,
                'project_name': attributed_project,
                'language_name': row.detected_language or "Unknown",
                'context_state': final_context,
                'duration_sec': segment.duration,
                'end_time_local': segment.end_time,
                # Raw metrics passed through
                'typing_intensity': row.typing_intensity,
                'mouse_click_rate': row.mouse_click_rate,
                'deletion_key_presses': row.deletion_key_presses,
                'idle_duration_sec': row.idle_duration_sec,
                'mouse_movement_distance': row.mouse_movement_distance,
            })
    
    return transformed
```

**Key Transformations**:
- **Project Attribution**: Trust sticky project from database
- **Context Priority**: Manual verification > ML prediction
- **Midnight Splitting**: Ensure daily buckets are accurate
- **Metrics Passthrough**: Raw metrics available to all aggregators

#### Stage 3: DELEGATE to Aggregators

Each aggregator receives the same clean batch and generates SQL commands:

```python
for aggregator in self.aggregators:
    sql_commands = aggregator.generate_upserts(transformed_logs)
    all_sql_commands.extend(sql_commands)
```

**Aggregator Responsibilities**:

1. **ProjectAggregator** - Upsert projects table
   ```python
   Unique projects
   └─ First seen: min(start_time)
   └─ Last active: max(end_time)
   └─ Path superiority: Prefer absolute paths
   ```

2. **AppAggregator** - Upsert daily app durations
   ```python
   Group by: (date, project_name, app_name)
   Aggregate: SUM(duration_sec)
   Upsert: daily_project_apps
   Skip: __unassigned__ projects
   ```

3. **LanguageAggregator** - Upsert daily language durations
   ```python
   Group by: (date, project_name, language_name)
   Filter: project_path IS NOT NULL (only IDE sessions)
   Aggregate: SUM(duration_sec)
   Upsert: daily_project_languages
   Note: Filters OUT browser time (no project_path)
   ```

4. **SkillAggregator** - Upsert cumulative skills
   ```python
   Map: language_name → skill_name (config: language_to_skill_mapping)
   Group by: (project_name, skill_name)
   Aggregate: SUM(duration_sec) [cumulative across dates]
   Upsert: project_skills
   ```

5. **ContextAggregator** - Upsert daily context time
   ```python
   Group by: (date, project_name, context_state)
   Aggregate: SUM(duration_sec)
   Upsert: daily_project_context
   Result: Time spent in each mental state
   ```

6. **BehaviorAggregator** - Upsert daily behavioral metrics
   ```python
   Group by: (date, project_name)
   
   Metrics (weighted averages):
   ├─ typing_intensity_kpm = SUM(keystrokes) / SUM(duration_minutes)
   ├─ mouse_click_rate_cpm = SUM(clicks) / SUM(duration_minutes)
   ├─ total_deletion_key_presses = SUM(deletions)
   ├─ total_idle_sec = SUM(idle_duration)
   └─ total_mouse_movement_distance = SUM(pixels)
   
   Where:
   ├─ keystrokes = typing_intensity * duration_minutes
   ├─ clicks = mouse_click_rate * duration_minutes
   └─ conversions normalize per-log rates to per-day totals
   ```

#### Stage 4: LOAD (Atomic Transaction)
```python
def _execute_batch(self, transformed_logs, all_sql_commands):
    with self.db.conn:  # Transaction context
        # Execute all aggregator SQL
        for query, params in all_sql_commands:
            self.db.conn.execute(query, params)
        
        # Mark logs as aggregated
        log_ids = [log['log_id'] for log in transformed_logs]
        self.db.conn.execute(
            "UPDATE raw_activity_logs SET is_aggregated = 1 WHERE log_id IN (?, ?, ...)",
            log_ids
        )
        # COMMIT (automatic at end of context manager)
```

**Atomicity Guarantee**: All aggregations succeed or all fail (no partial updates)

---

## AUXILIARY COMPONENTS

### 1. LOC Scanner

**File**: [aggregate/loc_scanner.py](aggregate/loc_scanner.py)

**Trigger**: Runs when user idle >30 minutes

**Operation**:
```python
def scan_project(project_name):
    1. Get project from database
    2. Get project_path from projects table
    3. Walk directory tree, counting files
    4. Group by language (file extension mapping)
    5. Store results in project_loc_snapshots:
    
    INSERT INTO project_loc_snapshots (
        project_name, language_name, 
        lines_of_code, file_count,
        last_scanned_at, needs_sync=1
    )
```

**Language Mapping** (from config):
```yaml
loc_scanner:
  language_extensions:
    .py: Python
    .js: JavaScript
    .ts: TypeScript
    .sql: SQL
    ...
  skip_directories:
    - node_modules
    - venv
    - __pycache__
    - .git
```

### 2. Activity Syncer

**File**: [sync/activity_syncer.py](sync/activity_syncer.py)

**Trigger**: Runs every sync interval (default 3600s)

**Operation**:
```python
def sync_activity():
    1. Collect pending data:
       └─ ActivityCollector.collect_pending_projects()
          (queries tables with needs_sync=1)
    
    2. Build payload:
       └─ Group by project, include LOC, daily buckets, metadata
    
    3. Get ID token:
       └─ auth/tokens.get_valid_id_token() (refresh if needed)
    
    4. POST to backend:
       └─ BACKEND_BASE_URL/v1/sync
       └─ On 401: Refresh token and retry
       └─ On 5xx: Exponential backoff retry (3 attempts)
       └─ On connection error: Exponential backoff retry
    
    5. Mark synced:
       └─ UPDATE needs_sync=0 for all tables
```

**Payload Structure**:
```json
{
  "projects": [
    {
      "project_name": "desktop-agent",
      "metadata": {
        "first_seen_at": "2026-02-27T09:00:00",
        "last_active_at": "2026-03-01T14:25:00"
      },
      "current_loc": [
        {"language": "python", "lines": 4500, "files": 12}
      ],
      "days": [
        {
          "date": "2026-02-27",
          "languages": {"python": 1200, "sql": 300},
          "apps": {"vscode": 1400, "chrome": 200},
          "contexts": {"flow": 900, "debugging": 500},
          "behavior": {
            "typing_intensity_kpm": 85.5,
            "mouse_click_rate_cpm": 12.3,
            "total_deletion_key_presses": 234,
            "total_idle_sec": 1200,
            "total_mouse_movement_distance": 45000.5
          }
        }
      ]
    }
  ]
}
```

---

## KEY DESIGN PATTERNS

### 1. Sticky Project Attribution

**Problem**: Browser tabs don't have open files; can't detect project from title alone

**Solution**: Time-decaying project memory
```python
# When detected in IDE → set sticky_project
sticky_project = "desktop-agent"  
created_at = now

# Use for next 15 minutes (sticky_project_ttl_sec)
IF time_since_set < 900:
    │  This browser tab likely belongs to desktop-agent
    └─ use sticky_project

ELSE:
    │  TTL expired
    └─ mark as "__unassigned__"
```

### 2. Path Superiority Rule

**Problem**: Detector gives partial paths sometimes (e.g., `desktop-agent/` vs `E:\Zenno\desktop-agent\`)

**Solution**: Prefer absolute paths at all levels
```python
# Python layer (memory)
├─ When storing in unique_projects dict
├─ Check: is new path absolute? is old path absolute?
└─ Rule: absolute > relative, longer > shorter

# Database layer (SQL)
├─ CASE statement in ON CONFLICT clause
├─ If DB has absolute, never overwrite with relative
└─ If both absolute, take the newest one
```

### 3. Atomic ETL Pipeline

**Problem**: If aggregation fails halfway, data is inconsistent

**Solution**: Single transaction wrapping all aggregators
```python
with self.db.conn:  # Implicit transaction
    for aggregator in self.aggregators:
        sql_commands = aggregator.generate_upserts(transformed_logs)
        for query, params in sql_commands:
            self.db.conn.execute(query, params)
    
    # Mark all as aggregated
    UPDATE raw_activity_logs SET is_aggregated=1 WHERE log_id IN (...)
    
    # COMMIT happens automatically at end of context manager
```

### 4. Manual Verification Override

**Problem**: ML/heuristics wrong for individual blocks (e.g., distracted_me during a video call)

**Solution**: ESM popup + override flag
```python
IF ml_confidence < esm_confidence_threshold:
    │  Show popup (ESM - Experience Sampling Methodology)
    ├─ User confirms or corrects context
    └─ Store in manually_verified_label

# Aggregation respects override:
final_context = manually_verified_label OR ml_context_state
```

### 5. Midnight Splitting

**Problem**: Sessions that cross midnight (e.g., 23:50-00:10) cross daily bucket boundaries

**Solution**: Pre-split during transformation
```python
def _split_across_midnight_local(start, end):
    segments = []
    
    if start.date() == end.date():
        # No split needed
        segments.append({'date': start.date(), 'duration': end - start})
    else:
        # Split at midnight boundary
        midnight = start.replace(hour=0, minute=0, second=0) + timedelta(days=1)
        segments.append({
            'date': start.date(),
            'duration': (midnight - start).total_seconds()
        })
        segments.append({
            'date': end.date(),
            'duration': (end - midnight).total_seconds()
        })
    
    return segments
```

---

## CONFIGURATION

**File**: [config/config.yaml](config/config.yaml)

**Key Settings**:
```yaml
# Collection
sample_interval_sec: 2              # How often to sample active window
flush_interval_sec: 300             # How often to save batch

# Analysis
block_duration_sec: 300             # Block size for context detection (5 min)
ml_enabled: true                    # Use XGBoost model
ml_confidence_threshold: 0.5        # Min confidence before heuristic fallback
block_evaluator:
  startup_delay_sec: 300            # Wait before first evaluation

# ESM (Experience Sampling Method)
esm_popup:
  enabled: true                     # Show verification dialogs
  confidence_threshold: 0.70        # Popup if confidence < 70%

# Sync
sync_interval_sec: 3600             # How often to upload to backend

# LOC Scanning
loc_scanner:
  scan_interval_sec: 3600           # Idle trigger interval

# ML App Scoring
ml_app_scoring:
  productive_apps:
    - vscode
    - pycharm
    - intellij
  communication_apps:
    - slack
    - zoom
    - teams
  distraction_apps:
    - spotify
    - netflix
    - youtube

# ETL
etl_pipeline:
  sticky_project_ttl_sec: 900       # 15-min TTL for sticky project
  language_to_skill_mapping:
    Python: Backend
    JavaScript: Frontend
```

---

## DATA FLOW DIAGRAM (Text)

```
┌──────────────────────────────────────────────────────────────────┐
│ DesktopAgent Main Loop (every sample_interval_sec)               │
│ get_active_window() → (app_name, window_title, PID)             │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ├─ [SessionManager: detect window change]
               │  
               ├─ If session unchanged → continue (gather more signals)
               │  
               └─ If session changed OR ended:
                  │
                  ├─ Session.collect_data()
                  │  ├─ Get BehavioralMetrics (KPM, CPM, deletions, mouse)
                  │  ├─ Get IdleDetector metrics
                  │  ├─ Get ProjectDetector (project, file, language)
                  │  └─ Compile activity_data dict
                  │
                  └─ db.insert_activity_log(activity_data)
                     │
                     ├─ context_state = NULL (NOT YET TAGGED)
                     ├─ is_aggregated = 0
                     └─ Wait for BlockEvaluator to tag...

                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│ BlockEvaluator Thread (every block_duration_sec, background)     │
│ Runs at block boundaries: :00, :05, :10 minutes                 │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ├─ Query: raw_activity_logs WHERE context_state IS NULL
               │
               ├─ Aggregate block metrics:
               │  ├─ typing_kpm (avg keystrokes/min)
               │  ├─ mouse_cpm (avg clicks/min)
               │  ├─ correction_ratio (deletions/total keys)
               │  └─ ... 5 more signals
               │
               ├─ IF ml_enabled AND model available:
               │  ├─ context_state = model.predict(features)
               │  └─ confidence = model.confidence
               │
               ├─ ELSE:
               │  ├─ context_state = heuristic.detect(features, apps)
               │  └─ confidence = heuristic_confidence
               │
               ├─ IF confidence < esm_threshold:
               │  ├─ Show ESM popup → user selects ground truth
               │  └─ manually_verified_label = user_selection
               │
               └─ UPDATE raw_activity_logs:
                  │  context_state = Flow|Debug|Research|Comms|Distracted
                  │  confidence_score = 0.75-0.92
                  │  manually_verified_label = (optional user override)
                  │
                  └─ Trigger: ETLPipeline.run()

                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│ ETLPipeline (Maestro) / Aggregation                              │
│ Atomic transaction groups all aggregations                       │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ├─ EXTRACT
               │  └─ SELECT * FROM raw_activity_logs
               │     WHERE is_aggregated=0 AND context_state IS NOT NULL
               │
               ├─ TRANSFORM (once per batch)
               │  ├─ Trust project_name (sticky logic done)
               │  ├─ Override context if manually_verified_label set
               │  ├─ Split across midnight boundaries (local time)
               │  └─ Clean app names, extract browser services
               │
               ├─ DELEGATE to 6 Aggregators:
               │  ├─ ProjectAggregator → upsert projects table
               │  ├─ AppAggregator → upsert daily_project_apps
               │  ├─ LanguageAggregator → upsert daily_project_languages
               │  ├─ SkillAggregator → upsert project_skills
               │  ├─ ContextAggregator → upsert daily_project_context
               │  └─ BehaviorAggregator → upsert daily_project_behavior
               │
               └─ LOAD (all in one transaction)
                  ├─ Execute all SQL commands
                  ├─ UPDATE raw_activity_logs SET is_aggregated=1
                  └─ COMMIT (or rollback on error)

                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│ 7 Daily Project Aggregation Tables (ready to sync)               │
├─ daily_project_languages [(date, project, language) → duration]  │
├─ daily_project_apps [(date, project, app) → duration]            │
├─ daily_project_context [(date, project, context) → duration]     │
├─ daily_project_behavior [(date, project) → KPM, CPM, idle, etc]  │
├─ project_skills [(project, skill) → duration (cumulative)]       │
├─ projects [project → metadata (first_seen, last_active)]         │
└─ project_loc_snapshots [(project, language) → LOC count]         │

                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────┐
│ ActivitySyncer (periodic, background thread)                     │
│ (every sync_interval_sec)                                        │
│ Triggered by DesktopAgent._sync_cycle()                          │
└──────────────┬───────────────────────────────────────────────────┘
               │
               ├─ ActivityCollector.collect_pending_projects()
               │  └─ Query tables WHERE needs_sync=1
               │
               ├─ Build JSON payload:
               │  ├─ Group by project
               │  ├─ Include LOC snapshots
               │  ├─ Include daily buckets (languages, apps, context, behavior)
               │  └─ Include project metadata
               │
               ├─ Get ID token:
               │  └─ auth/tokens.get_valid_id_token() (refresh if needed)
               │
               ├─ POST to backend:
               │  └─ BACKEND_BASE_URL/v1/sync
               │
               ├─ On success:
               │  └─ UPDATE all tables SET needs_sync=0
               │
               └─ On failure:
                  ├─ 401 → Refresh token and retry
                  ├─ 5xx → Exponential backoff (3 retries)
                  └─ Network error → Exponential backoff (3 retries)
```

---

## Summary Table: Components → Responsibility

| Component | Module | Input | Output | Trigger |
|-----------|--------|-------|--------|---------|
| **DesktopAgent** | agent.py | System (active window) | ActivitySession objects | Loop (every 2s) |
| **ActivitySession** | agent.py | Behavioral metrics | activity_data dict | Session end |
| **BehavioralMetrics** | monitor/behavioral_metrics.py | Keyboard/mouse events | KPM, CPM, deletions, mouse_dist | Listeners (continuous) |
| **IdleDetector** | monitor/idle_detector.py | Activity timestamps | idle_duration, idle_ratio | Session end |
| **ProjectDetector** | monitor/project_detector.py | Window title, app name | project_name, file, language | Session creation |
| **Database** | database/db.py | activity_data | raw_activity_logs row | insert_activity_log() |
| **BlockEvaluator** | analyze/block_evaluator.py | raw_activity_logs | context_state, confidence | Block boundary (every 300s) |
| **ContextDetector** | analyze/context_detector.py | Block metrics + apps | context_state, confidence | BlockEvaluator (heuristic) |
| **ML Model** | ml/feature_extractor.py, predictor.py | Features (8-signal vector) | context_state, confidence | BlockEvaluator (if enabled) |
| **ETLPipeline** | aggregate/etl_pipeline.py | raw_activity_logs (tagged) | 6 aggregators | After BlockEvaluator |
| **ProjectAggregator** | aggregate/project_aggregator.py | Transformed logs | projects UPSERT | ETL delegate |
| **AppAggregator** | aggregate/app_aggregator.py | Transformed logs | daily_project_apps UPSERT | ETL delegate |
| **LanguageAggregator** | aggregate/language_aggregator.py | Transformed logs | daily_project_languages UPSERT | ETL delegate |
| **SkillAggregator** | aggregate/skill_aggregator.py | Transformed logs | project_skills UPSERT | ETL delegate |
| **ContextAggregator** | aggregate/context_aggregator.py | Transformed logs | daily_project_context UPSERT | ETL delegate |
| **BehaviorAggregator** | aggregate/behavior_aggregator.py | Transformed logs | daily_project_behavior UPSERT | ETL delegate |
| **LOCScanner** | aggregate/loc_scanner.py | projects table | project_loc_snapshots | Idle trigger (>30 min) |
| **ActivityCollector** | sync/activity_collector.py | Tables (needs_sync=1) | JSON project payloads | ActivitySyncer |
| **ActivitySyncer** | sync/activity_syncer.py | Payloads + ID token | Backend sync | Periodic (3600s) |

---

## Execution Timeline Example

```
T=0s      User launches agent.py
          ├─ Load config.yaml
          ├─ Connect SQLite database
          ├─ Show auth window
          └─ Wait for sign-in

T=30s     User signs in
          └─ Start DesktopAgent main loop

T=32s     [COLLECT] Sample: VSCode is active
          ├─ Create ActivitySession("VSCode", "project.py - VSCode")
          ├─ Set up listeners (KPM, CPM, mouse, idle)
          └─ Store start_time

T=50s     [COLLECT] VSCode still active, user typing
          ├─ BehavioralMetrics accumulate: 85 KPM, 12 CPM
          ├─ No idle detected
          └─ Continue session

T=90s     [COLLECT] User switches to Chrome (30 seconds typing in VSCode)
          ├─ Collect VSCode session:
          │  ├─ duration_sec = 58
          │  ├─ typing_intensity = 85 KPM
          │  ├─ mouse_click_rate = 12 CPM
          │  ├─ project_name = "desktop-agent" (sticky from VSCode)
          │  ├─ detected_language = "Python"
          │  ├─ idle_duration_sec = 0
          │  └─ context_state = NULL (waiting for BlockEvaluator)
          │
          ├─ Insert into raw_activity_logs
          ├─ Create new ActivitySession("Chrome", "github.com/...")
          └─ Reset behavioral monitors

T=300s    [ANALYZE] Block boundary! BlockEvaluator wakes up
          ├─ Query: SELECT * FROM raw_activity_logs WHERE context_state IS NULL
          │  └─ Find: 5 VSCode sessions + 2 Chrome sessions from past 5 min
          │
          ├─ Aggregate block metrics:
          │  ├─ typing_kpm = 82 (mostly VSCode)
          │  ├─ mouse_cpm = 14
          │  ├─ correction_ratio = 0.06 (low corrections)
          │  ├─ mouse_velocity = 75 px/s (moderate)
          │  ├─ app_score = 0.8 (mostly VSCode, a productive app)
          │  ├─ idle_ratio = 0.05 (minimal idle)
          │  └─ fatigue_hrs = 0.1 (just started)
          │
          ├─ ML Prediction:
          │  ├─ Load model from disk
          │  ├─ Calculate features
          │  ├─ Predict: context_state = "Flow", confidence = 0.89
          │  └─ Confidence > threshold (0.5) → Use ML
          │
          ├─ Update raw_activity_logs:
          │  ├─ context_state = "Flow"
          │  ├─ confidence_score = 0.89
          │  └─ manually_verified_label = NULL
          │
          └─ Trigger: etl_pipeline.run()

T=300s    [AGGREGATE] ETL Pipeline executes (in atomic transaction):
          │
          ├─ EXTRACT:
          │  └─ Query 7 logs from last 5 minutes (all with context_state='Flow')
          │
          ├─ TRANSFORM:
          │  ├─ Project: "desktop-agent" (already in DB)
          │  ├─ Context: "Flow" (use ML prediction)
          │  ├─ Midnight split: All logs from same date (2026-03-01)
          │  └─ Clean apps: VSCode → "VSCode"
          │
          ├─ DELEGATE:
          │  ├─ ProjectAggregator: Upsert projects
          │  │  └─ projects: (desktop-agent, E:\Zenno\desktop-agent, 09:00, 09:05)
          │  │
          │  ├─ AppAggregator: Group by (date, project, app)
          │  │  ├─ (2026-03-01, desktop-agent, VSCode) → 240 sec
          │  │  └─ (2026-03-01, desktop-agent, Chrome) → 60 sec
          │  │
          │  ├─ LanguageAggregator: Only IDE sessions with project_path
          │  │  ├─ (2026-03-01, desktop-agent, Python) → 240 sec
          │  │  └─ Chrome skipped (no project_path)
          │  │
          │  ├─ SkillAggregator: Map language → skill
          │  │  └─ (desktop-agent, Backend) → +240 sec
          │  │
          │  ├─ ContextAggregator: Group by (date, project, context)
          │  │  └─ (2026-03-01, desktop-agent, Flow) → 300 sec
          │  │
          │  └─ BehaviorAggregator: Aggregate metrics
          │     └─ (2026-03-01, desktop-agent) →
          │        ├─ typing_intensity_kpm: 82
          │        ├─ mouse_click_rate_cpm: 14
          │        ├─ total_deletion_key_presses: 18
          │        ├─ total_idle_sec: 0
          │        └─ total_mouse_movement_distance: 22500
          │
          └─ LOAD:
             ├─ BEGIN TRANSACTION
             ├─ Execute all aggregator UPSERTs
             ├─ UPDATE raw_activity_logs SET is_aggregated=1
             └─ COMMIT

T=3600s   [LOC SCAN] User has been idle >30 min
          └─ LOCScanner.scan_all_projects()
             ├─ Find all projects from projects table
             ├─ For each project:
             │  ├─ Scan directory tree
             │  ├─ Count lines by language
             │  └─ Upsert project_loc_snapshots
             └─ Example: (desktop-agent, Python, 4500 lines, 45 files)

T=5400s   [SYNC] DesktopAgent._sync_cycle() triggered
          ├─ ActivitySyncer.sync_activity()
          │
          ├─ Collect pending data:
          │  └─ ActivityCollector.collect_pending_projects()
          │     └─ Query all tables WHERE needs_sync=1
          │        ├─ projects: 3 projects
          │        ├─ daily_project_apps: 12 rows
          │        ├─ daily_project_languages: 8 rows
          │        ├─ daily_project_context: 18 rows
          │        ├─ daily_project_behavior: 3 rows
          │        ├─ project_skills: 6 rows
          │        └─ project_loc_snapshots: 9 rows
          │
          ├─ Build JSON payload (structure per ActivitySyncer)
          │
          ├─ Get ID token:
          │  └─ auth/tokens.get_valid_id_token()
          │     ├─ Check if token expired
          │     ├─ If expired: POST refresh and get new token
          │     └─ Return valid token
          │
          ├─ POST /api/v1/sync with payload
          │  └─ Retry on 401 (refresh) / 5xx (backoff) / network error (backoff)
          │
          ├─ On success:
          │  └─ UPDATE all tables SET needs_sync=0
          │
          └─ Log outcome

... (pattern repeats every block_duration_sec and sync_interval_sec)
```

---

## Key Insights

### 1. **Sticky Project Inheritance**
- Browser sessions inherit project from last IDE session (within 15-min window)
- Enables tracking "research for project X" even in browser

### 2. **Context Detection is Probabilistic**
- ML provides point estimates with confidence scores
- Heuristics provide interpretable fallbacks
- ESM popups collect human ground truth to improve models

### 3. **Aggregation is Deterministic**
- Raw logs are tagged once (no re-tagging)
- Aggregation is idempotent (re-runs produce same result)
- Midnight splitting ensures daily buckets are accurate

### 4. **Atomicity Prevents Inconsistency**
- ETL transaction wraps all aggregations
- Failure mid-pipeline leaves no partial aggregates
- Simple retry logic: re-run ETL on next block

### 5. **Behavior Metrics Enable Psychology-Based Detection**
- Typing intensity + correction ratio differentiate Flow vs Debugging
- Mouse velocity differentiates Reading vs Distraction
- App categorization provides priority signals for Communication vs Distraction

### 6. **Daily Buckets Enable Time Series Analysis**
- daily_project_languages: Time spent per language per day
- daily_project_context: Mental state distribution per day
- daily_project_behavior: Effort metrics per day
- Enables trend analysis: "Is this person ramping up on Python?"
