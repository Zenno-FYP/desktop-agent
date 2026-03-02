"""Launch the pywebview authentication window."""
import os
import json
import logging
import webview
from pathlib import Path
from http.server import SimpleHTTPRequestHandler
from socketserver import ThreadingTCPServer
from functools import partial
import threading
from dotenv import load_dotenv

from auth.bridge import AuthBridge

# Load .env once at import time
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger(__name__)

_UI_DIR = Path(__file__).resolve().parent / "ui"


def _start_ui_server() -> ThreadingTCPServer:
    """Serve the auth UI over http://localhost to satisfy Firebase OAuth origin rules."""

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            # Silence default request logging
            return

    handler = partial(QuietHandler, directory=str(_UI_DIR))
    server = ThreadingTCPServer(("localhost", 0), handler)
    server.daemon_threads = True

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _firebase_js_config() -> dict:
    """Build Firebase JS SDK config directly from .env vars."""
    return {
        "apiKey": os.getenv("FIREBASE_API_KEY", ""),
        "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
        "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
    }


def run_auth_window(db) -> dict | None:
    """Open the sign-in WebView and block until the user authenticates.

    Args:
        db: Database instance (connected, tables created)

    Returns:
        User data dict on success, or None if the window was closed without auth.
    """
    window_holder: dict = {}
    bridge = AuthBridge(db, window_holder)

    # Keep OAuth inside the WebView. Otherwise Firebase popups can open in the system browser
    # and will not be able to message back to the embedded window.
    try:
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    except Exception:
        pass

    # Serve UI via localhost so Firebase OAuth providers work.
    ui_server = _start_ui_server()
    port = ui_server.server_address[1]
    ui_url = f"http://localhost:{port}/index.html"

    # Inject Firebase config as a JS global before the page loads
    firebase_cfg = _firebase_js_config()

    window = webview.create_window(
        title="Zenno — Sign In",
        url=ui_url,
        js_api=bridge,
        width=480,
        height=640,
        resizable=False,
        text_select=False,
    )
    window_holder["window"] = window

    # After DOM is ready, inject the Firebase config as a JS global
    def _on_loaded():
        try:
            window.evaluate_js(
                f"window.__FIREBASE_CONFIG__ = {json.dumps(firebase_cfg)};"
                "if(typeof initFirebase === 'function') initFirebase();"
            )
        except Exception:
            logger.exception("[WebView] Failed to inject Firebase config")

    window.events.loaded += _on_loaded

    # Blocks until the window is closed (by bridge.run_agent or user X)
    try:
        webview.start(debug=False)
    finally:
        try:
            ui_server.shutdown()
            ui_server.server_close()
        except Exception:
            pass

    return bridge.user_data
