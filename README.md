# Zenno Desktop Agent (Windows)

Zenno Desktop Agent is the local telemetry and nudge runtime for the Zenno platform. It captures app/window activity, behavioral intensity, and project context, then transforms those signals into analytics-ready aggregates and personalized nudge workflows.

## Product Overview

The desktop agent is responsible for collecting and processing development activity on the user machine. It is designed to be resilient and mostly autonomous:

- authenticates users via Firebase-backed desktop login
- tracks activity in near real-time
- infers context states (Flow/Debugging/Research/Communication/Distracted)
- aggregates project, app, language, and behavior metrics to SQLite
- syncs rollups to backend APIs
- runs nudge scheduling and desktop notifications

This component bridges local runtime data with cloud-backed user experiences on website/mobile.

**Operators:** workspace-wide metrics in the **website admin console** include **desktop active (last hour)**, derived from recent activity sync timestamps on user records in the backend — keep sync healthy so admin dashboards stay accurate.

## Tech Stack

- Python
- SQLite (local storage)
- pywebview (desktop auth UI)
- Requests + keyring
- Firebase auth integration (token verification flow)
- scikit-learn / XGBoost (optional context model path)

## Core Features

### Authentication and Startup

- embedded sign-in flow with pywebview
- local token caching/refresh using Windows Credential Manager (`keyring`)
- onboarding and preference bootstrap from backend

### Activity Collection

- active window monitoring (app, title, PID)
- behavioral metrics (typing intensity, click rate, deletions, mouse movement)
- idle detection and session segmentation
- IDE-aware project/file/language detection

### Context Classification

- block-level inference pipeline
- ML prediction path with confidence thresholds
- heuristic fallback path when ML is unavailable or low confidence
- optional ESM verification popup for uncertain predictions

### Aggregation and Sync

- ETL pipeline for daily summaries
- sticky-project logic across generic apps (browser/terminal)
- LOC snapshots by language/project
- periodic backend sync of activity and nudge records

### Nudge System

- scheduler with suppression rules and quiet constraints
- NLP API integration for generated nudge copy
- deterministic fallback templates when API fails
- user preference polling from backend

## Runtime Pipeline

1. **Authenticate** user and load preferences.
2. **Collect** raw sessions into `raw_activity_logs`.
3. **Evaluate** blocks into context labels + confidence.
4. **Aggregate** into project/app/language/context/behavior rollups.
5. **Sync** pending records to backend.
6. **Schedule nudges** and notify user.

## Repository Structure

- `main.py` - auth window + onboarding + agent bootstrap
- `agent.py` - long-running monitoring and orchestration loop
- `config/` - YAML configuration and loader
- `auth/` - auth bridge/webview/token management
- `monitor/` - app focus, input, idle, project detection
- `analyze/` - block evaluator and context detector
- `ml/` - model training/inference utilities and ESM UI
- `aggregate/` - ETL orchestrator and LOC scanner
- `sync/` - backend sync workers
- `nudge/` - scheduler, generator, notifier, poller, syncer
- `database/` - SQLite schema and query layer
- `data/` - local DB/models/artifacts

## Requirements

- Windows (primary target platform)
- Python 3.10+ (3.11 recommended)
- SQLite (bundled with Python stdlib)
- Firebase project + backend + nlp services for full online flow

Install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment Variables

Create `.env` from `.env.example`:

```powershell
Copy-Item .env.example .env
```

### Required

- `FIREBASE_API_KEY`
- `FIREBASE_AUTH_DOMAIN`
- `FIREBASE_PROJECT_ID`
- `BACKEND_BASE_URL`
- `NUDGE_API_URL`

### Recommended for production/dev parity

- `NUDGE_API_SECRET`
- `NUDGE_API_TIMEOUT`
- `SYNC_TIMEOUT_SECONDS`
- `PREFS_POLL_INTERVAL_SEC`
- `NUDGE_SYNC_INTERVAL_SEC`

## Configuration (`config/config.yaml`)

Most runtime behavior is controlled by YAML config, including:

- sampling and flush intervals
- block duration and heuristic thresholds
- ML enablement/confidence threshold/model path
- ESM popup behavior and rate limits
- ETL mapping and sticky-project TTL
- LOC scanning rules
- database and logging settings
- nudge policy and suppression tuning

The config file acts as the primary tuning surface; `.env` covers integrations and external endpoints/secrets.

## Running the Agent

### Full flow (recommended)

```powershell
python main.py
```

This launches auth + onboarding and then starts `DesktopAgent`.

### Direct agent loop (advanced/local testing)

```powershell
python agent.py
```

## Data Model and Storage

Primary SQLite tables include:

- `raw_activity_logs` (source-of-truth sessions)
- `projects`
- `daily_project_apps`
- `daily_project_languages`
- `daily_project_context`
- `daily_project_behavior`
- `project_skills`
- `project_loc_snapshots`

Quick health check:

```python
import sqlite3
conn = sqlite3.connect("data/db/zenno.db")
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM raw_activity_logs")
print("raw logs:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM raw_activity_logs WHERE context_state IS NOT NULL")
print("tagged logs:", cur.fetchone()[0])
conn.close()
```

## ML and Heuristic Modes

When `ml_enabled: true`, the block evaluator attempts to load `ml_model_path`.

- if model loads and confidence is high enough: use ML
- if model missing/error/low confidence: use heuristic fallback

Train/retrain local model:

```powershell
python -m ml.synthetic_data_generator
python -m ml.train_model
```

## Integration Dependencies

For complete functionality, desktop-agent expects:

- backend API reachable at `BACKEND_BASE_URL`
- nlp API reachable at `NUDGE_API_URL`
- valid Firebase config for auth flow

## Privacy and Security Notes

- Keystroke content is not captured (event counts only).
- Window titles/file paths may still contain sensitive information.
- Local SQLite database can include sensitive metadata; secure backups accordingly.
- Keep `.env` local only; never commit real credentials or API secrets.

## Troubleshooting

- **No context labels**: wait at least one full `block_duration_sec`; check logs.
- **DB lock errors**: ensure `db.journal_mode: WAL` and raise `db.timeout`.
- **Nudges not showing**: verify backend prefs, nudge scheduler enabled, and NLP API reachability.
- **Auth failures**: check Firebase env values and backend URL.
- **High CPU**: increase `sample_interval_sec` and `loc_scanner.scan_interval_sec`.

---

Last Updated: 2026-05-01