# Aggregation Plan (Phase 4) — Dashboard-Tier ETL for `desktop-agent`

## Goal
Convert fine-grained rows in `raw_activity_logs` into **small, pre-aggregated “dashboard-tier” tables** that are safe to sync to the cloud (no raw window titles, no raw file paths), while keeping the desktop agent lightweight.

**Design principles**
- **Never sync raw logs** (`raw_activity_logs` stays local only).
- Aggregation must be **idempotent** (no double counting).
- Aggregation must be **fast** (append/UPSERT, indexed queries, WAL-friendly).
- Daily tables use **composite PK** for natural UPSERT.

Non-goals (for Phase 4 MVP)
- Perfect semantic “skill” inference from any context.
- Re-aggregating historical data on label corrections (we’ll plan it, but keep MVP simple).

---

## 0) Current Inputs (Confirmed)
Your current table is created in `database/db.py` as `raw_activity_logs` with (key fields):
- `start_time`, `end_time` (ISO string, UTC)
- `duration_sec`
- `app_name` (process exe, e.g., `Code.exe`, `chrome.exe`)
- `window_title` (local only)
- `project_name`, `project_path`, `active_file`, `detected_language`
- `context_state`, `confidence_score` (filled retroactively by `analyze/block_evaluator.py`)
- `manually_verified_label`, `verified_at` (optional; Phase 3B)

Phase 4 consumes **only completed rows** (inserted on flush) and prefers rows where `context_state IS NOT NULL`.

---

## 0.5) Phase 2 Hardening ✅ COMPLETED
Phase 4 aggregation assumes **every raw row eventually gets `context_state`**. Before building aggregation, tighten Phase 2 so tagging is complete and stable.

### 0.5.1 ✅ Fix the BlockEvaluator query window (prevents "never-tagged" logs)
**Status: IMPLEMENTED and DEPLOYED**

- Modified: `Database.query_logs()` now uses `end_time` (not `start_time`) by default
- Modified: `BlockEvaluator.evaluate_block()` passes `query_by_end_time=True`
- Added parameter `query_by_end_time` to `query_logs()` for backwards compatibility

**What this fixes:** Sessions longer than 5 minutes can now be tagged reliably across block boundaries. Before, an 8-minute VS Code session might miss the evaluator entirely.

### 0.5.2 ✅ Align the 5-minute heartbeat to wall-clock boundaries (prevents drift)
**Status: IMPLEMENTED and DEPLOYED**

- Modified: `BlockEvaluator._run_loop()` now computes "seconds until next boundary"
- Changed from fixed `time.sleep(block_duration_sec)` to dynamic boundary-aligned sleeping
- Evaluator now wakes at stable UTC times: 2:00, 2:05, 2:10, etc. (not 2:00, 2:05:03, 2:10:07...)

**What this fixes:** Heartbeat no longer drifts over time. Makes aggregation scheduling predictable and deterministic.

---

## 1) Aggregated Database Schema (Dashboard Tier)
These tables live in the same local SQLite DB.

### 1.1 `projects` (Metadata Hub) ✅ IMPLEMENTED
Purpose: registry of observed projects.

**Status: COMPLETE**
- ✅ Schema created in `database/db.py`
- ✅ Helper methods implemented:
  - `upsert_project(project_name, project_path)` — INSERT or UPDATE last_active_at
  - `update_project_last_active(project_name, timestamp)` — UPDATE last_active_at only
  - `get_project(project_name)` — Query single project
  - `get_all_projects(needs_sync=None)` — Query all projects with filter
- ✅ ProjectAggregator created in `aggregate/project_aggregator.py`
- ✅ Integrated into `BlockEvaluator.evaluate_block()` → calls `project_aggregator.aggregate()` after ML tagging

**Implementation Details**
```sql
CREATE TABLE IF NOT EXISTS projects (
  project_name TEXT PRIMARY KEY,
  project_path TEXT,                 -- local-only (never synced)
  first_seen_at TEXT NOT NULL,       -- UTC ISO (set on first upsert)
  last_active_at TEXT NOT NULL,      -- UTC ISO (updated on each aggregation)
  needs_sync INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_projects_needs_sync
  ON projects(needs_sync);
```

**Data Flow (End-to-End)**
1. Phase 1 (Monitor) collects raw logs with `project_name` and `project_path`
2. Phase 2 (BlockEvaluator) tags logs retroactively with `context_state IS NOT NULL`
3. Phase 4 (ProjectAggregator) extracts unique projects:
   ```python
   SELECT DISTINCT project_name, project_path
   FROM raw_activity_logs
   WHERE is_aggregated = 0 
     AND context_state IS NOT NULL
     AND project_name IS NOT NULL
     AND project_path IS NOT NULL
   ```
4. For each project: calls `db.upsert_project(project_name, project_path)`
5. UPSERT logic:
   - If new: INSERT with `first_seen_at = now`, `last_active_at = now`, `needs_sync = 1`
   - If exists: UPDATE `last_active_at = now`, `needs_sync = 1` (preserves `first_seen_at`)
6. Projects table stays in sync, updated every 5 minutes after block evaluation

**Notes**
- `project_path` exists to support LOC scanning. The **sync worker must omit it**.
- If you ever rename projects, treat it as a new `project_name` (simplest, safe).
- `needs_sync = 1` signals cloud sync worker to include this project in next sync batch.

### 1.2 `daily_project_languages` (Syntax Tracker)
Purpose: exact languages used per day.

```sql
CREATE TABLE IF NOT EXISTS daily_project_languages (
  date TEXT NOT NULL,               -- YYYY-MM-DD (recommend local date; see §2.4)
  project_name TEXT NOT NULL,
  language_name TEXT NOT NULL,
  duration_sec INTEGER NOT NULL DEFAULT 0,
  needs_sync INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (date, project_name, language_name),
  FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dpl_needs_sync
  ON daily_project_languages(needs_sync);
```

### 1.3 `daily_project_skills` (Domain/Role Tracker)
Purpose: high-level role buckets (Backend, Frontend, Mobile, DevOps, Data/ML, etc.).

```sql
CREATE TABLE IF NOT EXISTS daily_project_skills (
  date TEXT NOT NULL,
  project_name TEXT NOT NULL,
  skill_name TEXT NOT NULL,
  duration_sec INTEGER NOT NULL DEFAULT 0,
  needs_sync INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (date, project_name, skill_name),
  FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
```

### 1.4 `daily_project_apps` (Tool Tracker)
Purpose: app usage per project/day (VS Code vs Chrome vs Terminal vs Postman).

```sql
CREATE TABLE IF NOT EXISTS daily_project_apps (
  date TEXT NOT NULL,
  project_name TEXT NOT NULL,
  app_name TEXT NOT NULL,           -- normalized friendly name
  duration_sec INTEGER NOT NULL DEFAULT 0,
  needs_sync INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (date, project_name, app_name),
  FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
```

### 1.5 `daily_project_context` (Focus Tracker)
Purpose: Focus/Reading/Distracted/Idle heatmap per project/day.

```sql
CREATE TABLE IF NOT EXISTS daily_project_context (
  date TEXT NOT NULL,
  project_name TEXT NOT NULL,
  context_state TEXT NOT NULL,      -- Focused | Reading | Distracted | Idle
  duration_sec INTEGER NOT NULL DEFAULT 0,
  needs_sync INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (date, project_name, context_state),
  FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
```

### 1.6 `project_loc_snapshots` (Structural Size)
Purpose: rolling LOC total per project and language.

```sql
CREATE TABLE IF NOT EXISTS project_loc_snapshots (
  project_name TEXT NOT NULL,
  language_name TEXT NOT NULL,
  lines_of_code INTEGER NOT NULL,
  last_scanned_at TEXT NOT NULL,
  needs_sync INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (project_name, language_name),
  FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
);
```

### 1.7 `raw_activity_logs` migration for idempotency
Add an aggregation marker so we never process the same raw row twice.

**MVP fields**
```sql
ALTER TABLE raw_activity_logs ADD COLUMN is_aggregated INTEGER NOT NULL DEFAULT 0;
```

**Recommended extra fields (helps debugging + future reaggregation)**
```sql
ALTER TABLE raw_activity_logs ADD COLUMN aggregated_at TEXT NULL;         -- UTC ISO
ALTER TABLE raw_activity_logs ADD COLUMN aggregation_version INTEGER NOT NULL DEFAULT 1;
```

Indexes (important for performance):
```sql
CREATE INDEX IF NOT EXISTS idx_raw_agg_pending
  ON raw_activity_logs(is_aggregated, end_time);

CREATE INDEX IF NOT EXISTS idx_raw_end_time
  ON raw_activity_logs(end_time);
```

**Note:** Indexes use `end_time` (not `start_time`) to match Phase 2 Hardening query optimization (section 0.5.1). The aggregator queries by `end_time` to catch long sessions spanning multiple blocks.

---

## 2) Aggregation Flow (ETL Pipeline)
Aggregator runs periodically (every ~5 minutes) and/or immediately after each block evaluation cycle.

**Architecture note:** Each table has its own dedicated aggregator module for separation of concerns.

### 2.0 Project Aggregation ✅ IMPLEMENTED
**Status: COMPLETE** (see section 1.1 for full details)

Module: `aggregate/project_aggregator.py`
Class: `ProjectAggregator`
Method: `aggregate()`

Triggered by: `BlockEvaluator.evaluate_block()` immediately after ML tagging

### 2.1 Extract (Read raw rows) — Example: Languages
For language aggregation, query:
- `is_aggregated = 0`
- `context_state IS NOT NULL` (to avoid aggregating before Phase 2 tagging)
- ORDER BY `start_time ASC`

Pseudo-SQL:
```sql
SELECT *
FROM raw_activity_logs
WHERE is_aggregated = 0
  AND context_state IS NOT NULL
  AND detected_language IS NOT NULL
ORDER BY start_time ASC;
```

**Note:** Project extraction (section 2.0) uses a simpler DISTINCT query since it only needs `(project_name, project_path)` pairs.

### 2.2 Transform (Sticky Project + normalization)
Iterate logs chronologically and maintain these state variables:
- `active_project` (string or NULL)
- `active_project_last_seen_at` (UTC datetime)

**Decision tree**
1) **Blacklist / Distraction hard break**
   - If the app/window is a known distraction (Netflix/YouTube/WhatsApp/etc):
     - Set `active_project = NULL`
     - Project attribution → `__unassigned__`
     - Force `context_state = "Distracted"` (override)

2) **Anchor check (IDE or strong project_name)**
   - If `project_name` is present (from IDE parsing):
     - Set `active_project = project_name`
     - Update `active_project_last_seen_at`
     - Attribute this log to that project

3) **Inheritor check (neutral apps like Chrome/Terminal)**
   - If `project_name` is missing but app is neutral/productive:
     - If `active_project` exists and is not stale (see TTL below), inherit it
     - Else attribute to `__unassigned__`

4) **Idle check (optional refinement)**
   - If `context_state == "Idle"` OR idle_ratio is very high for the log:
     - Optionally clear `active_project` (prevents inheriting after stepping away)

**Sticky TTL (recommended improvement)**
- Only inherit `active_project` if last anchor was within e.g. **15 minutes**.

**App normalization (recommended improvement)**
- Convert raw process exe (`Code.exe`) into friendly names (`VS Code`).
- Use a mapping dict; fallback to a cleaned title-cased name.

### 2.3 Skill mapping
MVP: map `detected_language` → `skill_name` using a config mapping.

Recommended configuration in `config/config.yaml`:
```yaml
skills:
  language_to_skill:
    Python: Backend API
    JavaScript: Frontend Web
    TypeScript: Frontend Web
    Dart: Mobile Development
    Kotlin: Mobile Development
    Swift: Mobile Development
    SQL: Data Engineering
    Bash: DevOps
```

Rules
- If language is unknown/NULL → skill = `"Unknown"`.
- If you want higher accuracy later, add project-specific overrides.

### 2.4 Daily bucketing (UTC vs Local)
You must choose what “day” means.

Recommended for dashboards: **local day**, because humans think in local dates.
- Convert `start_time/end_time` UTC → local time, then compute `date = YYYY-MM-DD`.

**Important improvement:** split logs that cross midnight.
- If a log spans multiple dates, split into segments per day before grouping.

**Implementation tip (best approach): do the split in Python using half-open intervals**
- Represent each raw log as a time interval `[start, end)`.
- Split at exact midnight boundary `00:00:00` (local time), producing segments like `[start, midnight)` and `[midnight, end)`.
- Compute each segment duration as `int((seg_end - seg_start).total_seconds())`.

This avoids off-by-one bugs that happen when you try to use `23:59:59` as an artificial endpoint.

Example (local time):
- Raw log: `23:50:00 -> 00:15:00` (1500s)
- Segment A: `23:50:00 -> 00:00:00` (600s) with date = today
- Segment B: `00:00:00 -> 00:15:00` (900s) with date = tomorrow

Pseudo-code:
```python
from datetime import datetime, time, timedelta

def split_across_midnight_local(start_local: datetime, end_local: datetime):
  segments = []
  cursor = start_local
  while cursor.date() < end_local.date():
    midnight = datetime.combine(cursor.date() + timedelta(days=1), time(0, 0, 0))
    segments.append((cursor, midnight))  # [cursor, midnight)
    cursor = midnight
  segments.append((cursor, end_local))     # [cursor, end_local)
  return segments
```

After splitting, feed each segment into the **same** grouping + UPSERT loop.

### 2.5 Load (Group + UPSERT)
After transformation, create rollups grouped by:
- `(date, project_name, language_name)`
- `(date, project_name, skill_name)`
- `(date, project_name, app_name)`
- `(date, project_name, context_state)`

Then UPSERT (additive):
```sql
INSERT INTO daily_project_apps(date, project_name, app_name, duration_sec, needs_sync)
VALUES (?, ?, ?, ?, 1)
ON CONFLICT(date, project_name, app_name)
DO UPDATE SET
  duration_sec = daily_project_apps.duration_sec + excluded.duration_sec,
  needs_sync = 1;
```

Also update `projects`:
- If missing: INSERT with `first_seen_at = now`, `last_active_at = now`
- If exists: UPDATE `last_active_at = now`, `needs_sync = 1`

### 2.6 Mark processed raw rows
In the same transaction, mark all processed `log_id`s:
```sql
UPDATE raw_activity_logs
SET is_aggregated = 1,
    aggregated_at = ?
WHERE log_id IN (...);
```

**Transaction requirement (important improvement)**
- Wrap: (UPSERT daily tables) + (UPDATE raw rows) in a single transaction.
- If the process crashes mid-way, you either commit everything or nothing.

---

## 3) Privacy & Sync Rules (Hard Requirements)
- Cloud sync payloads must contain **only** dashboard-tier rows.
- Never sync: `window_title`, `active_file`, `project_path`, raw timestamps.
- For `projects`, sync only `project_name`, `first_seen_at`, `last_active_at`.

Recommended: keep a single “serializer” that defines what is allowed to leave the device.

---

## 4) Background Workers (Architecture)
### 4.1 Aggregator thread
Runs every 5 minutes (or after each `BlockEvaluator.evaluate_block()`), processes pending raw rows.

Suggested module layout:
- `aggregate/aggregator.py` — ETL + UPSERT

### 4.2 LOC scanner (separate, async)
Do **not** compute LOC in the aggregation loop.

Schedule
- every 24h, or when the user is “Idle” for a long stretch.

Implementation notes
- Use `project_path` from `projects` table (local only).
- Store results in `project_loc_snapshots`.

Suggested module:
- `aggregate/loc_scanner.py`

### 4.3 Sync worker (separate, async)
Runs every hour:
- selects rows where `needs_sync = 1` from the 6 dashboard-tier tables
- POSTs them to backend
- sets `needs_sync = 0` on success

Suggested module:
- `aggregate/sync_worker.py`

---

## 5) Improvements Over Previous Phases (to get better results)
These are targeted upgrades that increase accuracy without heavy CPU cost.

1) **Aggregate only after context tagging**
- Filter raw rows with `context_state IS NOT NULL` to avoid “unknown state” time.

2) **Midnight splitting**
- Prevents the classic bug: late-night sessions attributed fully to one day.

3) **Sticky-project TTL + idle reset**
- Avoids incorrectly attributing next-morning browsing to yesterday’s project.

4) **Leverage existing app categorization**
- Reuse the same distraction/productivity sets already used in Phase 2.
- Improves sticky inheritance accuracy.

5) **Normalize app names**
- Better dashboard consistency (Code.exe vs vscode.exe vs “Visual Studio Code”).

6) **Indexes + WAL-friendly batching**
- Keep aggregation fast even when raw logs grow.

7) **Retention policy (optional but recommended)**
- Keep raw logs only N days (e.g., 14–30) after successful aggregation.
- Daily tables remain as long-term history.

---

## 6) Handling Verified Labels (Phase 3B → Phase 4)
When ESM verification updates `manually_verified_label`, your daily context aggregates become stale.

Two safe strategies:

### Strategy A (MVP): forward-only, no rebuild
- Keep daily aggregates as originally predicted.
- Verified labels are used only for model retraining.

### Strategy B (Recommended next step): day-level rebuild on verification
- On verification, mark affected `(date, project)` as dirty.
- Recompute daily aggregates for that day by re-scanning raw logs for that scope.
- This avoids subtraction bookkeeping and stays correct.

(Phase 4 MVP can ship with Strategy A; Strategy B is the clean upgrade.)

---

## 7) Validation & Tests
Suggested tests (fast, deterministic):
- Sticky inheritance: IDE → Chrome → Terminal inherits the same project.
- Distraction break: IDE → YouTube forces `__unassigned__` + Distracted.
- Midnight split: 23:59–00:02 becomes two daily rows.
- Idempotency: running aggregator twice adds time only once (via `is_aggregated`).
- UPSERT correctness: composite PK merges durations.

---

## 8) Implementation Checklist (when you’re ready to code)
### ✅ COMPLETED
1. ✅ Add `projects` table to `database/db.py`
   - Schema with `project_name` (PK), `project_path`, `first_seen_at`, `last_active_at`, `needs_sync`
   - Index on `needs_sync`
2. ✅ Add helper methods for projects in `database/db.py`
   - `upsert_project(project_name, project_path)`
   - `update_project_last_active(project_name, timestamp)`
   - `get_project(project_name)`
   - `get_all_projects(needs_sync=None)`
3. ✅ Implement `ProjectAggregator` in `aggregate/project_aggregator.py`
   - `aggregate()` method that extracts unique projects from tagged logs
   - Calls `db.upsert_project()` for each distinct project
4. ✅ Integrate ProjectAggregator into `BlockEvaluator.evaluate_block()`
   - Called immediately after ML tagging (after `db.update_logs_context()`)
   - Ensures projects table stays in sync every 5 minutes

### ⏳ IN PROGRESS (Next Steps)
5. ⏳ Implement `daily_project_languages` table schema + aggregator
6. ⏳ Implement `daily_project_skills` table schema + aggregator
7. ⏳ Implement `daily_project_apps` table schema + aggregator
8. ⏳ Implement `daily_project_context` table schema + aggregator
9. ⏳ Implement `project_loc_snapshots` table schema + aggregator

### ❌ NOT STARTED (Future Work)
10. Implement LOC scanner worker
11. Implement sync worker + payload serializer
12. Add retention policy for raw logs (optional)
13. Implement verified label rebuild (Strategy B)
