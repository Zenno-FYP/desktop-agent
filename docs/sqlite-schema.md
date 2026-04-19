# Zenno Desktop Agent SQLite Schema

- Database file: `E:\Zenno\desktop-agent\data\db\zenno.db`
- Schema sources: `database/db.py`, `nudge/nudge_syncer.py`
- Foreign keys are enabled by the app with `PRAGMA foreign_keys=ON`.
- This document reflects the current live local desktop SQLite schema and is intended for ERD generation.

## daily_project_apps

Daily per-project aggregated app activity.

```sql
CREATE TABLE daily_project_apps (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                app_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, app_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `date` | `TEXT` | Yes | `` | Yes | 1 | No | `` |  |
| `project_name` | `TEXT` | Yes | `` | Yes | 2 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `app_name` | `TEXT` | Yes | `` | Yes | 3 | No | `` |  |
| `duration_sec` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `date`, `project_name`, `app_name`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_dpa_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_daily_project_apps_1` (`date`, `project_name`, `app_name`) [unique, from primary key]

## daily_project_behavior

Daily per-project aggregated behavior metrics.

```sql
CREATE TABLE daily_project_behavior (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                typing_intensity_kpm REAL NOT NULL DEFAULT 0.0,
                mouse_click_rate_cpm REAL NOT NULL DEFAULT 0.0,
                total_deletion_key_presses INTEGER NOT NULL DEFAULT 0,
                total_idle_sec INTEGER NOT NULL DEFAULT 0,
                total_mouse_movement_distance REAL NOT NULL DEFAULT 0.0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `date` | `TEXT` | Yes | `` | Yes | 1 | No | `` |  |
| `project_name` | `TEXT` | Yes | `` | Yes | 2 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `typing_intensity_kpm` | `REAL` | Yes | `0.0` | No |  | No | `` |  |
| `mouse_click_rate_cpm` | `REAL` | Yes | `0.0` | No |  | No | `` |  |
| `total_deletion_key_presses` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `total_idle_sec` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `total_mouse_movement_distance` | `REAL` | Yes | `0.0` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `date`, `project_name`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_dpb_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_daily_project_behavior_1` (`date`, `project_name`) [unique, from primary key]

## daily_project_context

Daily per-project aggregated context-state activity.

```sql
CREATE TABLE daily_project_context (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                context_state TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, context_state),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `date` | `TEXT` | Yes | `` | Yes | 1 | No | `` |  |
| `project_name` | `TEXT` | Yes | `` | Yes | 2 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `context_state` | `TEXT` | Yes | `` | Yes | 3 | No | `` |  |
| `duration_sec` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `date`, `project_name`, `context_state`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_dpc_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_daily_project_context_1` (`date`, `project_name`, `context_state`) [unique, from primary key]

## daily_project_languages

Daily per-project aggregated language activity.

```sql
CREATE TABLE daily_project_languages (
                date TEXT NOT NULL,
                project_name TEXT NOT NULL,
                language_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (date, project_name, language_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `date` | `TEXT` | Yes | `` | Yes | 1 | No | `` |  |
| `project_name` | `TEXT` | Yes | `` | Yes | 2 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `language_name` | `TEXT` | Yes | `` | Yes | 3 | No | `` |  |
| `duration_sec` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `date`, `project_name`, `language_name`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_dpl_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_daily_project_languages_1` (`date`, `project_name`, `language_name`) [unique, from primary key]

## local_user

Locally cached authenticated backend user profile.

```sql
CREATE TABLE local_user (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                backend_user_id TEXT NOT NULL,
                email TEXT NOT NULL,
                name TEXT NOT NULL,
                profile_photo TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT,
                updated_at TEXT
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | `INTEGER` | No | `` | Yes | 1 | No | `` | CHECK (id = 1); singleton row |
| `backend_user_id` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `email` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `name` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `profile_photo` | `TEXT` | No | `` | No |  | No | `` |  |
| `is_verified` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `role` | `TEXT` | Yes | `'user'` | No |  | No | `` |  |
| `created_at` | `TEXT` | No | `` | No |  | No | `` |  |
| `updated_at` | `TEXT` | No | `` | No |  | No | `` |  |

Primary Key: `id`
Foreign Keys: None
Indexes: None

## nudge_log

History of generated or suppressed nudges.

```sql
CREATE TABLE nudge_log (
                nudge_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at      TEXT    NOT NULL,
                nudge_type        TEXT    NOT NULL,
                nudge_text        TEXT    NOT NULL DEFAULT '',
                rationale         TEXT,
                fatigue_score     REAL,
                fatigue_level     TEXT,
                flow_ratio_today  REAL,
                active_min_today  REAL,
                min_since_break   REAL,
                top_project       TEXT,
                was_suppressed    INTEGER NOT NULL DEFAULT 0,
                suppression_reason TEXT,
                llm_used          INTEGER NOT NULL DEFAULT 0,
                context_snapshot  TEXT
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `nudge_id` | `INTEGER` | No | `` | Yes | 1 | No | `` | AUTOINCREMENT |
| `generated_at` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `nudge_type` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `nudge_text` | `TEXT` | Yes | `''` | No |  | No | `` |  |
| `rationale` | `TEXT` | No | `` | No |  | No | `` |  |
| `fatigue_score` | `REAL` | No | `` | No |  | No | `` |  |
| `fatigue_level` | `TEXT` | No | `` | No |  | No | `` |  |
| `flow_ratio_today` | `REAL` | No | `` | No |  | No | `` |  |
| `active_min_today` | `REAL` | No | `` | No |  | No | `` |  |
| `min_since_break` | `REAL` | No | `` | No |  | No | `` |  |
| `top_project` | `TEXT` | No | `` | No |  | No | `` |  |
| `was_suppressed` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `suppression_reason` | `TEXT` | No | `` | No |  | No | `` |  |
| `llm_used` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `context_snapshot` | `TEXT` | No | `` | No |  | No | `` |  |

Primary Key: `nudge_id`
Foreign Keys: None
Indexes: `idx_nudge_suppressed` (`was_suppressed`, `generated_at`) [non-unique, explicit index]; `idx_nudge_generated` (`generated_at`) [non-unique, explicit index]

## nudge_sync_cursor

Singleton cursor tracking the last synced nudge row.

```sql
CREATE TABLE nudge_sync_cursor (
                    id                  INTEGER PRIMARY KEY DEFAULT 1,
                    last_synced_nudge_id INTEGER DEFAULT 0
                )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | `INTEGER` | No | `1` | Yes | 1 | No | `` | Singleton row key; default 1; runtime-created by nudge_syncer |
| `last_synced_nudge_id` | `INTEGER` | No | `0` | No |  | No | `` |  |

Primary Key: `id`
Foreign Keys: None
Indexes: None
Source note: This table is created lazily by `_ensure_cursor_table()` in `nudge/nudge_syncer.py`.

## project_loc_snapshots

Latest LOC and file-count snapshot by project and language.

```sql
CREATE TABLE project_loc_snapshots (
                project_name TEXT NOT NULL,
                language_name TEXT NOT NULL,
                lines_of_code INTEGER NOT NULL DEFAULT 0,
                file_count INTEGER NOT NULL DEFAULT 0,
                last_scanned_at TEXT NOT NULL,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (project_name, language_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `project_name` | `TEXT` | Yes | `` | Yes | 1 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `language_name` | `TEXT` | Yes | `` | Yes | 2 | No | `` |  |
| `lines_of_code` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `file_count` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `last_scanned_at` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `project_name`, `language_name`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_pls_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_project_loc_snapshots_1` (`project_name`, `language_name`) [unique, from primary key]

## project_skills

Per-project cumulative skill durations.

```sql
CREATE TABLE project_skills (
                project_name TEXT NOT NULL,
                skill_name TEXT NOT NULL,
                duration_sec INTEGER NOT NULL DEFAULT 0,
                last_updated_at TEXT NOT NULL,
                needs_sync INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (project_name, skill_name),
                FOREIGN KEY (project_name) REFERENCES projects(project_name) ON DELETE CASCADE
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `project_name` | `TEXT` | Yes | `` | Yes | 1 | Yes | `projects(project_name)` | ON DELETE CASCADE |
| `skill_name` | `TEXT` | Yes | `` | Yes | 2 | No | `` |  |
| `duration_sec` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `last_updated_at` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `project_name`, `skill_name`
Foreign Keys: `project_name` -> `projects(project_name)` (ON DELETE CASCADE)
Indexes: `idx_ps_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_project_skills_1` (`project_name`, `skill_name`) [unique, from primary key]

## projects

Master list of locally tracked projects.

```sql
CREATE TABLE projects (
                project_name TEXT PRIMARY KEY,
                project_path TEXT,
                first_seen_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL,
                needs_sync INTEGER NOT NULL DEFAULT 1
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `project_name` | `TEXT` | No | `` | Yes | 1 | No | `` |  |
| `project_path` | `TEXT` | No | `` | No |  | No | `` |  |
| `first_seen_at` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `last_active_at` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `needs_sync` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `project_name`
Foreign Keys: None
Indexes: `idx_projects_needs_sync` (`needs_sync`) [non-unique, explicit index]; `sqlite_autoindex_projects_1` (`project_name`) [unique, from primary key]

## raw_activity_logs

Raw desktop activity sessions before aggregation.

```sql
CREATE TABLE raw_activity_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                app_name TEXT NOT NULL,
                window_title TEXT,
                duration_sec INTEGER NOT NULL,

                project_name TEXT,
                project_path TEXT,
                active_file TEXT,
                detected_language TEXT,

                typing_intensity REAL DEFAULT 0.0,
                mouse_click_rate REAL DEFAULT 0.0,
                deletion_key_presses INTEGER DEFAULT 0,
                mouse_movement_distance REAL NOT NULL DEFAULT 0.0,
                idle_duration_sec INTEGER DEFAULT 0,

                context_state TEXT,
                confidence_score REAL,

                manually_verified_label TEXT NULL,
                verified_at TIMESTAMP NULL,

                is_aggregated INTEGER NOT NULL DEFAULT 0,
                aggregated_at TEXT NULL,
                aggregation_version INTEGER NOT NULL DEFAULT 1
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `log_id` | `INTEGER` | No | `` | Yes | 1 | No | `` | AUTOINCREMENT |
| `start_time` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `end_time` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `app_name` | `TEXT` | Yes | `` | No |  | No | `` |  |
| `window_title` | `TEXT` | No | `` | No |  | No | `` |  |
| `duration_sec` | `INTEGER` | Yes | `` | No |  | No | `` |  |
| `project_name` | `TEXT` | No | `` | No |  | No | `` |  |
| `project_path` | `TEXT` | No | `` | No |  | No | `` |  |
| `active_file` | `TEXT` | No | `` | No |  | No | `` |  |
| `detected_language` | `TEXT` | No | `` | No |  | No | `` |  |
| `typing_intensity` | `REAL` | No | `0.0` | No |  | No | `` |  |
| `mouse_click_rate` | `REAL` | No | `0.0` | No |  | No | `` |  |
| `deletion_key_presses` | `INTEGER` | No | `0` | No |  | No | `` |  |
| `mouse_movement_distance` | `REAL` | Yes | `0.0` | No |  | No | `` |  |
| `idle_duration_sec` | `INTEGER` | No | `0` | No |  | No | `` |  |
| `context_state` | `TEXT` | No | `` | No |  | No | `` |  |
| `confidence_score` | `REAL` | No | `` | No |  | No | `` |  |
| `manually_verified_label` | `TEXT` | No | `` | No |  | No | `` |  |
| `verified_at` | `TIMESTAMP` | No | `` | No |  | No | `` |  |
| `is_aggregated` | `INTEGER` | Yes | `0` | No |  | No | `` |  |
| `aggregated_at` | `TEXT` | No | `` | No |  | No | `` |  |
| `aggregation_version` | `INTEGER` | Yes | `1` | No |  | No | `` |  |

Primary Key: `log_id`
Foreign Keys: None
Indexes: `idx_raw_end_time` (`end_time`) [non-unique, explicit index]; `idx_raw_agg_pending` (`is_aggregated`, `end_time`) [non-unique, explicit index]

## user_preferences

Local onboarding and user preference singleton row.

```sql
CREATE TABLE user_preferences (
                id                      INTEGER PRIMARY KEY DEFAULT 1,
                work_schedule           TEXT    DEFAULT 'standard',
                focus_style             TEXT    DEFAULT 'moderate',
                wellbeing_goal          TEXT    DEFAULT 'focused',
                has_meetings            INTEGER DEFAULT 0,
                onboarding_completed_at TEXT,
                onboarding_version      INTEGER DEFAULT 1
            )
```

| Column | Type | NOT NULL | Default | Primary Key | PK Order | Foreign Key | References | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `id` | `INTEGER` | No | `1` | Yes | 1 | No | `` | Singleton row key; default 1 |
| `work_schedule` | `TEXT` | No | `'standard'` | No |  | No | `` |  |
| `focus_style` | `TEXT` | No | `'moderate'` | No |  | No | `` |  |
| `wellbeing_goal` | `TEXT` | No | `'focused'` | No |  | No | `` |  |
| `has_meetings` | `INTEGER` | No | `0` | No |  | No | `` |  |
| `onboarding_completed_at` | `TEXT` | No | `` | No |  | No | `` |  |
| `onboarding_version` | `INTEGER` | No | `1` | No |  | No | `` |  |

Primary Key: `id`
Foreign Keys: None
Indexes: None

## Relationships

Parent table: `projects`

Child tables that reference `projects(project_name)`:
- `daily_project_languages` via `project_name` with `ON DELETE CASCADE`
- `daily_project_apps` via `project_name` with `ON DELETE CASCADE`
- `daily_project_context` via `project_name` with `ON DELETE CASCADE`
- `daily_project_behavior` via `project_name` with `ON DELETE CASCADE`
- `project_skills` via `project_name` with `ON DELETE CASCADE`
- `project_loc_snapshots` via `project_name` with `ON DELETE CASCADE`

Tables with no foreign keys:
- `projects`
- `raw_activity_logs`
- `local_user`
- `user_preferences`
- `nudge_log`
- `nudge_sync_cursor`
