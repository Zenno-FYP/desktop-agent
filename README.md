# Zenno Desktop Agent (Windows)

A Windows desktop activity monitor that logs active applications/window titles, captures basic input intensity signals (typing/clicks/scrolls/idle), detects project context from IDEs, and produces both raw and aggregated SQLite datasets.

It runs fully locally (no network upload). Context labels can be inferred via heuristics or an optional ML model, and low-confidence predictions can be verified via ESM popups.

## Quick Start

### 1) Install

Recommended: use a virtual environment.

```bash
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

### 2) Configure

Edit [config/config.yaml](config/config.yaml). The default DB path in the config is:

- `db.path: ./data/db/zenno.db`

### 3) Run

```bash
python agent.py
```

## What It Does

### Pipeline (Collector → Tagger → Aggregator)

1. **Collect (real time)**
   - Reads the active window (app name, window title, PID)
   - Tracks activity intensity (KPM/CPM/scrolls) + idle duration
   - Detects project name/file/language when possible
   - Writes rows to `raw_activity_logs` with `context_state = NULL`

2. **Evaluate in blocks (background thread)**
   - Every `block_duration_sec` seconds, queries recent unevaluated logs
   - Aggregates block metrics and predicts a context label
   - Uses ML when enabled and available; falls back to heuristics when needed
   - Updates the block’s logs with `context_state` + `confidence_score`

3. **Aggregate (ETL Maestro)**
   - After tagging, runs the ETL pipeline to upsert daily project/app/language/skill/context/behavior rollups
   - Applies manual verification override: `manually_verified_label` wins over ML/heuristic
   - Buckets by local date (auto-detected from system timezone)

4. **LOC snapshots (idle-triggered)**
   - Periodically scans active projects while you’re idle
   - Stores per-language LOC + file count in `project_loc_snapshots`

## Project Structure

```
agent.py               Entry point (main loop)
config/                YAML config + loader
monitor/               Window + input + idle + project detection
analyze/               Block evaluator + heuristic context detector
ml/                    Feature extraction, model training, predictor, ESM popup
aggregate/             ETL pipeline + aggregators + LOC scanner
database/              SQLite schema + queries
data/                  Models, datasets, SQLite DB (default)
logs/                  Log file output (optional)
```

## Configuration (Key Settings)

All settings live in [config/config.yaml](config/config.yaml) and are read via dot-keys.

Minimal example:

```yaml
sample_interval_sec: 2
flush_interval_sec: 300
idle_threshold_sec: 10

behavioral_metrics:
  click_debounce_ms: 50
  max_typing_intensity_kpm: 200
  max_mouse_click_rate_cpm: 200

block_duration_sec: 300

ml_enabled: true
ml_model_path: ./data/models/context_detector.pkl
ml_confidence_threshold: 0.5

esm_popup:
  enabled: true
  confidence_threshold: 0.70

etl_pipeline:
  sticky_project_ttl_sec: 900

loc_scanner:
  scan_interval_sec: 3600
  idle_ratio_threshold: 0.3

db:
  path: ./data/db/zenno.db
  check_same_thread: false
  timeout: 10.0
  journal_mode: WAL

logging:
  level: INFO
  file: ./logs/agent.log
```

## Database (SQLite)

### Main tables

- `raw_activity_logs`: session-level logs (source of truth)
- `projects`: known projects + activity timestamps
- `daily_project_apps`, `daily_project_languages`, `daily_project_skills`
- `daily_project_context`, `daily_project_behavior`
- `project_loc_snapshots`: per-project per-language LOC + file counts

### Quick checks

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

## ML Model (Optional)

When `ml_enabled: true`, the evaluator will try to load the model at `ml_model_path`.

- If the file is missing or the model fails to load, the agent continues using heuristic fallback.
- If ML confidence is below `ml_confidence_threshold`, it falls back to heuristics.

### Training / retraining

```bash
python -m ml.synthetic_data_generator
python -m ml.train_model
```

Then update `ml_model_path` in [config/config.yaml](config/config.yaml) to point at the generated `.pkl`.

## ESM Popups (Verification)

If enabled, low-confidence blocks can be queued for verification. Verified labels are stored in:

- `raw_activity_logs.manually_verified_label`
- `raw_activity_logs.verified_at`

## Privacy Notes

- This project **does not capture keystroke contents** (it only counts events).
- Window titles and file paths may still contain sensitive information (depending on apps and your workflow).
- Data is stored locally in SQLite by default; review your retention/backups accordingly.

## Troubleshooting

- **No context tagging happening**: keep the agent running past one full `block_duration_sec` interval and check `logging.level`.
- **DB locked errors**: increase `db.timeout` and ensure `db.journal_mode: WAL`.
- **High CPU during LOC scans**: increase `loc_scanner.scan_interval_sec`, increase `loc_scanner.idle_ratio_threshold`, and expand `loc_scanner.skip_directories`.

## License

Private project - Desktop Agent FYP

## Author

Zubair Abbas

---

Last Updated: 2026-02-26
