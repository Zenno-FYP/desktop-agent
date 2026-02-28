"""
Zenno Desktop Agent — Main Entry Point

1. Opens the authentication WebView (sign-in)
2. On success, starts the desktop activity agent
"""
import sys
import logging
from pathlib import Path

# Ensure project root is on the path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.config import Config
from database.db import Database
from auth.webview_app import run_auth_window


def main():
    # ── 1) Load config & set up logging ──────────────────────
    config = Config()

    cfg = config.get("logging", {}) or {}
    level_name = str(cfg.get("level", "INFO")).upper()
    log_level = getattr(logging, level_name, logging.INFO)
    log_format = cfg.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    log_file = cfg.get("file")

    handlers = [logging.StreamHandler()]
    if log_file:
        try:
            log_path = Path(str(log_file))
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
        except Exception:
            pass

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)
    logger = logging.getLogger("main")

    # ── 2) Connect DB (light — just for user table) ──────────
    db_path = config.get("db.path", "./data/db/zenno.db")
    db = Database(
        db_path,
        check_same_thread=config.get("db.check_same_thread", False),
        timeout=config.get("db.timeout", 10.0),
        journal_mode=config.get("db.journal_mode", "WAL"),
        config=config,
    )
    db.connect()
    db.create_tables()

    # ── 3) Show auth window (blocks until user signs in or closes) ──
    logger.info("[Main] Opening authentication window…")
    user_data = run_auth_window(db)

    if not user_data:
        logger.info("[Main] Auth window closed without sign-in — exiting.")
        db.close()
        sys.exit(0)

    logger.info("[Main] Authenticated as: %s (%s)", user_data.get("name"), user_data.get("email"))

    # Close the lightweight DB connection; DesktopAgent will open its own.
    db.close()

    # ── 4) Start the agent ───────────────────────────────────
    logger.info("[Main] Launching desktop agent…")
    from agent import DesktopAgent
    agent = DesktopAgent()
    agent.start()


if __name__ == "__main__":
    main()
