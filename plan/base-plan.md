# Desktop Agent — Base Plan (Python + SQLite)

This document is the *starting* execution plan for the desktop agent. We’ll update it iteratively as features land.

## 0) Product scope (v0)
**Goal:** Collect privacy-safe local signals about a developer’s work habits, then generate daily profile summaries that the website can display.

**Signals to collect (raw):**
- Active app + (sanitized) window/IDE context
- Focus sessions + app switching frequency
- Typing intensity (counts only, no content)
- Idle sessions (keyboard/mouse inactivity)

**Insights to compute (derived):**
- Tool usage (IDE, browser, terminal, etc.)
- Language/stack signals (via file extensions / IDE context, privacy-safe)
- Productivity: focused vs distracted vs idle
- Trend over time (daily snapshots)

**Non-goals for v0 (explicitly not building yet):**
- Screen recording / screenshots
- Capturing keystroke content
- Network sniffing / URL logging
- ML models (we can add later)

---

## 1) Architecture (base)
### 1.1 Processes and responsibilities
- **Collector (Desktop Agent)**
  - Runs continuously (tray/service later; CLI runner first)
  - Samples OS state (active window, input activity)
  - Writes raw events into local SQLite
  - Performs minimal real-time aggregation (session boundaries)

- **Profiler (Profile Engine)**
  - Reads raw tables
  - Produces daily aggregates + JSON snapshot
  - Writes results back to SQLite and/or cache files

### 1.2 Modules (proposed)
- `agent.py` — entry point, lifecycle, orchestrates collectors
- `core/config.py` — config loading + validation (yaml)
- `core/logging.py` — structured logging
- `core/clock.py` — time helpers (UTC/local, day boundaries)
- `store/db.py` — SQLite connection, migrations, helpers
- `collectors/window_focus.py` — active app/window tracker
- `collectors/input_activity.py` — keystroke counts + idle detector
- `profiling/profile_engine.py` — daily aggregation pipeline
- `profiling/rules.py` — mapping rules (tool detection, distraction categories)
- `profiling/sanitization.py` — privacy filters (hashing/stripping)

We’ll keep it simple: a single process, periodic loop, small modules.

### 1.3 Data flow
1) Collectors write raw events → SQLite
2) Profile engine reads raw → produces derived daily summaries
3) Website later syncs snapshots (not part of v0 plan file; we’ll add when ready)

---

## 2) Privacy model 
**Principle:** store only what you need to compute stats.

- Window titles: store **sanitized** form (or none). Options:
  - Default: store only app/process name + generic category.
  - Optional (opt-in): store window title *hashed* or *project tokenized*.
- Project identifiers: store a **hash** (stable) instead of raw path/name.
- Never store:
  - full file paths
  - clipboard
  - keystroke content
  - URLs / query strings

Add a `privacy_mode` config flag with strict defaults.

---

## 3) SQLite schema (baseline)
Use WAL mode, foreign keys on, and store timestamps in ISO8601 UTC.

### 3.1 Raw session tables
**`app_sessions`** (focus sessions)
- `id INTEGER PRIMARY KEY`
- `app_name TEXT NOT NULL`
- `window_hint TEXT` (sanitized)
- `start_time TEXT NOT NULL` (UTC)
- `end_time TEXT` (UTC)
- `duration_sec INTEGER`
- `project_hash TEXT` (nullable)

**`typing_activity`** (counts per window)
- `id INTEGER PRIMARY KEY`
- `timestamp TEXT NOT NULL` (UTC, start of bucket)
- `key_count INTEGER NOT NULL`
- `window_sec INTEGER NOT NULL`
- `app_name TEXT` (optional link to context)

**`idle_sessions`**
- `id INTEGER PRIMARY KEY`
- `start_time TEXT NOT NULL`
- `end_time TEXT`
- `duration_sec INTEGER`

### 3.2 Derived tables (daily)
**`daily_tool_usage`**
- `date TEXT NOT NULL` (YYYY-MM-DD local day)
- `tool TEXT NOT NULL`
- `minutes INTEGER NOT NULL`
- PRIMARY KEY (`date`, `tool`)

**`skills_daily`**
- `date TEXT NOT NULL`
- `skill TEXT NOT NULL`
- `minutes INTEGER NOT NULL`
- `confidence REAL NOT NULL`
- PRIMARY KEY (`date`, `skill`)

**`daily_productivity`**
- `date TEXT PRIMARY KEY`
- `focus_minutes INTEGER NOT NULL`
- `distracted_minutes INTEGER NOT NULL`
- `idle_minutes INTEGER NOT NULL`
- `switch_count INTEGER NOT NULL`
- `score REAL NOT NULL`

**`daily_profile_snapshots`**
- `date TEXT PRIMARY KEY`
- `payload_json TEXT NOT NULL`
- `created_at TEXT NOT NULL`

---

## 4) OS + library choices (Windows-first)
You’re on Windows, so plan v0 around Windows support.

**Recommended approach (v0):**
- Active window: `pywin32` (Win32 APIs) or `ctypes`-based calls
- Keyboard/mouse activity:
  - If you only need idle time: Win32 `GetLastInputInfo` is enough (no hooks)
  - If you need keystroke counts: consider `pynput` (be careful: it’s more sensitive privacy-wise)

Start with idle detection via `GetLastInputInfo` (least invasive), then add keystroke counting as opt-in.

---

## 5) Milestones (step-by-step build order)
This is the *base execution order* that keeps you shipping usable increments.

### Milestone A — Agent skeleton + storage 
**Outcome:** `python agent.py` runs continuously and writes to SQLite.
- Project structure + venv
- Config (`config.yaml`) + logger
- DB layer (`store/db.py`) with migrations + WAL
- Graceful shutdown + crash-safe session close

### Milestone B — Focus tracking 
**Outcome:** app focus sessions are accurate.
- Sample active window every 1–2s
- Detect focus change → close previous session, open new
- Sanitize window context
- Daily sanity report (CLI): top apps today

### Milestone C — Idle + typing intensity 
**Outcome:** idle sessions and typing buckets fill reliably.
- Idle detection from last input time (threshold configurable)
- Optional keystroke counter (privacy gated)
- Store per-30s buckets (configurable)

### Milestone D — Profile engine v1 
**Outcome:** daily JSON snapshot generated from local DB.
- Tool detection mapping (process → tool)
- Productivity heuristics (focus/distracted/idle)
- Skills signals (file extension / IDE context, confidence)
- Store daily snapshot JSON

### Milestone E — Hardening 
**Outcome:** run-all-day reliability and privacy-proofing.
- Deduplication / duration validation
- Robust time handling (DST/local day boundaries)
- Performance checks (DB indexes, batch inserts)
- Privacy audit mode + redaction tests

---

## 6) Heuristics (initial rules; iterate later)
### 6.1 Tool detection
Maintain a mapping table/file:
- `code.exe` → VS Code
- `devenv.exe` → Visual Studio
- `pycharm64.exe` → PyCharm
- `chrome.exe`/`msedge.exe` → Browser
- `WindowsTerminal.exe`/`wt.exe` → Terminal

### 6.2 Distraction rules (starter)
- Distracted if: browser active + frequent switching + low typing
- Focused if: IDE active + typing bursts + long sessions

Keep rules transparent and configurable.

### 6.3 Skill inference (starter)
- If IDE context includes file extension (sanitized):
  - `.py` → Python, `.ts` → TypeScript, `.js` → JavaScript, `.java` → Java, etc.
- Confidence increases over multiple days with consistent signals.

---

## 7) Implementation checklist (first runnable base)
This is what you build *first* so you can iterate quickly.

1) Create folders:
   - `core/`, `store/`, `collectors/`, `profiling/`, `plan/`
2) Add minimal config (`config.yaml`) with:
   - `sample_interval_sec`, `idle_threshold_sec`, `typing_bucket_sec`, `privacy_mode`
3) Implement `store/db.py`:
   - connect, set WAL, create tables if missing
   - helper methods: start_session, end_session, insert_typing_bucket, etc.
4) Implement `collectors/window_focus.py`:
   - `get_active_app()` + `get_active_window_title()` (sanitized)
5) Implement idle detection (Win32 `GetLastInputInfo`) first
6) Wire into `agent.py` loop with graceful shutdown

---

## 8) Definition of Done (v0)
- Agent runs for 8+ hours without crashing
- SQLite has consistent sessions (no negative durations)
- Daily profile snapshot can be generated for “today”
- Privacy defaults are safe (no titles/paths/urls stored)

---

## 9) Next update to this plan
When you’re ready, we’ll extend this plan with:
- A concrete folder structure + initial files list
- Exact Windows API/library choice (pywin32 vs ctypes)
- Minimal CLI commands (`agent run`, `agent profile --date today`)
- Indexes and migration strategy
