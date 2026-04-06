# Zenno Desktop Agent — Database Schema

This document describes all tables in the SQLite database used by the Zenno desktop agent for tracking activity and storing user context.

---

## Overview

The database contains three main categories of tables:

1. **Raw Data Table** — stores granular activity logs as they are captured
2. **Aggregated Data Tables** — store pre-aggregated metrics per project, per day
3. **Reference Tables** — store project metadata and user information
4. **Indexing** — optimizes common query patterns

---

## Raw Data

### `raw_activity_logs`

Stores detailed activity records captured in real-time from the desktop agent.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `log_id` | INTEGER | auto-increment | Primary key; unique log entry identifier |
| `start_time` | TEXT | NOT NULL | ISO 8601 timestamp when activity started (e.g., `2026-02-28T10:30:00.123456`) |
| `end_time` | TEXT | NOT NULL | ISO 8601 timestamp when activity ended |
| `duration_sec` | INTEGER | NOT NULL | Duration in seconds between start and end |
| `app_name` | TEXT | NOT NULL | Application name (e.g., `Visual Studio Code`, `Chrome`) |
| `window_title` | TEXT |  | Window/tab title (may be empty for some apps) |
| `project_name` | TEXT |  | Detected project name (detected from active file path) |
| `project_path` | TEXT |  | File system path to the project root |
| `active_file` | TEXT |  | Currently active file path (relative to project root or absolute) |
| `detected_language` | TEXT |  | Programming language detected (e.g., `python`, `javascript`, `typescript`) |
| `typing_intensity` | REAL | 0.0 | Keystroke rate in KPM (keystrokes per minute) |
| `mouse_click_rate` | REAL | 0.0 | Mouse click rate in CPM (clicks per minute) |
| `deletion_key_presses` | INTEGER | 0 | Total deletion key presses (Delete, Backspace, Ctrl+Z combined) |
| `idle_duration_sec` | INTEGER | 0 | Time spent idle (no keyboard/mouse input) |
| `context_state` | TEXT |  | Work context state (e.g., `coding`, `meeting`, `break`, `research`) |
| `confidence_score` | REAL |  | AI confidence score (0.0–1.0) for the detected context/project |
| `manually_verified_label` | TEXT |  | Manual label if user corrected the auto-detection |
| `verified_at` | TIMESTAMP |  | When the manual verification was applied |
| `is_aggregated` | INTEGER | 0 | Flag (0=pending, 1=already aggregated into daily tables) |
| `aggregated_at` | TEXT |  | ISO 8601 timestamp when this log was aggregated |
| `aggregation_version` | INTEGER | 1 | Version number of the aggregation logic used |

**Indexes:**
- `idx_raw_agg_pending` on `(is_aggregated, end_time)` — optimize querying pending logs for aggregation
- `idx_raw_end_time` on `(end_time)` — optimize time-range queries

---

## Reference Data

### `projects`

Stores metadata about unique projects detected on the system.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `project_name` | TEXT | PRIMARY KEY | Unique project identifier (e.g., `desktop-agent`, `zenno-web`) |
| `project_path` | TEXT |  | Local file system path to the project root |
| `first_seen_at` | TEXT | NOT NULL | ISO 8601 timestamp of first activity detection |
| `last_active_at` | TEXT | NOT NULL | ISO 8601 timestamp of most recent activity in this project |
| `needs_sync` | INTEGER | 1 | Flag (0=synced to backend, 1=pending sync) |

**Indexes:**
- `idx_projects_needs_sync` on `(needs_sync)` — filter projects awaiting backend sync

---

### `local_user`

Stores the currently authenticated user's profile and sync metadata.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `id` | INTEGER | PRIMARY KEY | Always `1` (single-row table); enforced by `CHECK (id = 1)` |
| `backend_user_id` | TEXT | NOT NULL | User ID from backend (matches `_id` in backend response) |
| `email` | TEXT | NOT NULL | User's email address |
| `name` | TEXT | NOT NULL | User's display name |
| `profile_photo` | TEXT |  | URL to user's profile photo |
| `is_verified` | INTEGER | 0 | Account verification flag (0=unverified, 1=verified) |
| `role` | TEXT | 'user' | User role (e.g., `user`, `admin`, `beta_tester`) |
| `created_at` | TEXT |  | ISO 8601 timestamp of account creation (from backend) |
| `updated_at` | TEXT |  | ISO 8601 timestamp of last profile update (from backend) |

**Design Notes:**
- All user sync timing and activity tracking is managed entirely by the backend.
- The desktop agent stores only user profile information locally.

---

## Aggregated Data

Aggregated tables store pre-computed metrics grouped by project and date. They are populated by the ETL pipeline from raw logs.

### `daily_project_languages`

Break down of time spent per programming language per project per day.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `date` | TEXT | NOT NULL | Date of aggregation (e.g., `2026-02-28`) in `YYYY-MM-DD` format |
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `language_name` | TEXT | NOT NULL | Programming language (e.g., `python`, `javascript`, `sql`) |
| `duration_sec` | INTEGER | 0 | Total seconds spent on this language in this project on this date |
| `needs_sync` | INTEGER | 1 | Flag (0=synced to backend, 1=pending sync) |

**Primary Key:** `(date, project_name, language_name)` — one row per language per project per day

**Indexes:**
- `idx_dpl_needs_sync` on `(needs_sync)` — filter records awaiting backend sync

**Foreign Key:** `project_name` → `projects.project_name` (ON DELETE CASCADE)

---

### `daily_project_apps`

Break down of time spent per application per project per day.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `date` | TEXT | NOT NULL | Date (YYYY-MM-DD format) |
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `app_name` | TEXT | NOT NULL | Application name (e.g., `Visual Studio Code`, `Chrome`) |
| `duration_sec` | INTEGER | 0 | Total seconds spent in this app on this project on this date |
| `needs_sync` | INTEGER | 1 | Flag (0=synced, 1=pending) |

**Primary Key:** `(date, project_name, app_name)` — one row per app per project per day

**Indexes:**
---

### `project_skills`

Cumulative skills breakdown per project (similar to `project_loc_snapshots` for language experience).
Tracks total time spent on each skill across the entire project timeline.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `skill_name` | TEXT | NOT NULL | Skill label (inferred from language, file types, context) |
| `duration_sec` | INTEGER | 0 | Total cumulative seconds spent on this skill on this project |
| `last_updated_at` | TEXT | NOT NULL | ISO timestamp of last update |
| `needs_sync` | INTEGER | 1 | Sync flag: 1 = pending cloud sync, 0 = synced |

**Primary Key:** `(project_name, skill_name)` — one row per skill per project (cumulative)

**Indexes:**
- `idx_ps_needs_sync` on `(needs_sync)`

**Foreign Key:** `project_name` → `projects.project_name` (ON DELETE CASCADE)

---

### `daily_project_context`

Break down of work context/state per project per day.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `date` | TEXT | NOT NULL | Date (YYYY-MM-DD format) |
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `context_state` | TEXT | NOT NULL | Context label (e.g., `coding`, `debugging`, `meeting`, `break`, `research`) |
| `duration_sec` | INTEGER | 0 | Total seconds in this context on this project on this date |
| `needs_sync` | INTEGER | 1 | Flag (0=synced, 1=pending) |

**Primary Key:** `(date, project_name, context_state)` — one row per context per project per day

**Indexes:**
- `idx_dpc_needs_sync` on `(needs_sync)`

**Foreign Key:** `project_name` → `projects.project_name` (ON DELETE CASCADE)

---

### `daily_project_behavior`

Aggregated behavioral metrics (keyboard, mouse, scroll, idle) per project per day.

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `date` | TEXT | NOT NULL | Date (YYYY-MM-DD format) |
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `total_keystrokes` | INTEGER | 0 | Total keyboard events (key presses) on this project on this date |
| `total_mouse_clicks` | INTEGER | 0 | Total mouse clicks on this project on this date |
| `total_scroll_events` | INTEGER | 0 | Total scroll wheel events on this project on this date |
| `total_idle_sec` | INTEGER | 0 | Total idle time (seconds) on this project on this date |
| `needs_sync` | INTEGER | 1 | Flag (0=synced, 1=pending) |

**Primary Key:** `(date, project_name)` — one row per project per day

**Indexes:**
- `idx_dpb_needs_sync` on `(needs_sync)`

**Foreign Key:** `project_name` → `projects.project_name` (ON DELETE CASCADE)

---

### `project_loc_snapshots`

Code metrics: lines of code and file count per language per project (snapshot-based).

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `project_name` | TEXT | NOT NULL | Project identifier; foreign key to `projects(project_name)` |
| `language_name` | TEXT | NOT NULL | Programming language (e.g., `python`, `javascript`) |
| `lines_of_code` | INTEGER | 0 | Total lines of code in this language in this project (at last scan) |
| `file_count` | INTEGER | 0 | Number of files in this language in this project (at last scan) |
| `last_scanned_at` | TEXT | NOT NULL | ISO 8601 timestamp of the last LOC scan (e.g., `2026-02-28T14:05:15.123456`) |
| `needs_sync` | INTEGER | 1 | Flag (0=synced, 1=pending) |

**Primary Key:** `(project_name, language_name)` — one row per language per project

**Indexes:**
- `idx_pls_needs_sync` on `(needs_sync)`

**Foreign Key:** `project_name` → `projects.project_name` (ON DELETE CASCADE)

---

## Data Flow

1. **Raw Capture** → activity logs are inserted into `raw_activity_logs` in real-time by the agent.
2. **Aggregation** → ETL pipeline reads `raw_activity_logs` marked with `is_aggregated = 0` and computes daily aggregates.
3. **Aggregation Marking** → once aggregated, `raw_activity_logs` are marked with `is_aggregated = 1` and `aggregated_at` timestamp.
4. **Backend Sync** → records in aggregated tables with `needs_sync = 1` are sent to the backend API.
5. **Sync Marking** → after successful backend upload, `needs_sync` is set to `0`.

---

## Example Queries

### Get all activity for a specific project on a specific date:
```sql
SELECT * FROM daily_project_languages 
WHERE project_name = 'desktop-agent' AND date = '2026-02-28';
```

### Get pending aggregations waiting to be synced:
```sql
SELECT * FROM daily_project_languages 
WHERE needs_sync = 1 
ORDER BY date DESC;
```

### Check when the agent last synced:
```sql
SELECT backend_user_id, email, name FROM local_user WHERE id = 1;
```

### Get the current user:
```sql
SELECT * FROM local_user WHERE id = 1;
```

### Get lines of code for all languages in a project:
```sql
SELECT language_name, lines_of_code, file_count 
FROM project_loc_snapshots 
WHERE project_name = 'desktop-agent'
ORDER BY lines_of_code DESC;
```

---

## Notes

- All timestamps are in **ISO 8601 format** (local time): `YYYY-MM-DDTHH:MM:SS.ffffff` (no timezone suffix)
- Dates in aggregated tables use **YYYY-MM-DD** format (no time component)
- Duration columns (`duration_sec`) are measured in **seconds**
- The `needs_sync` flag is a **lazy sync marker** — records are only pushed to the backend when flagged as needing sync
- Foreign keys use **ON DELETE CASCADE**, so deleting a project cascades to all its related records
- The `local_user` table always contains at most **one row** (enforced by the `id = 1` check)
