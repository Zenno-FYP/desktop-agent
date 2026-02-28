"""Bridge between the WebView JS layer and Python backend logic.

Exposed to JS via ``window.pywebview.api.*``
"""
import os
import json
import logging
import requests
import secrets
import threading
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv

from auth.tokens import set_initial_tokens

logger = logging.getLogger(__name__)

# Load .env
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_BACKEND_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v1")
_TIMEOUT = 15  # seconds


class AuthBridge:
    """pywebview js_api class — every public method is callable from JS."""

    def __init__(self, db, window_ref_holder: dict):
        self._db = db
        self._win = window_ref_holder  # {'window': <webview.Window>}
        self._user_data: dict | None = None
        self._id_token: str | None = None

    # ── firebase config (used for external browser flow) ───────────────

    def _firebase_js_config(self) -> dict:
        return {
            "apiKey": os.getenv("FIREBASE_API_KEY", ""),
            "authDomain": os.getenv("FIREBASE_AUTH_DOMAIN", ""),
            "projectId": os.getenv("FIREBASE_PROJECT_ID", ""),
        }

    # ── backend helpers (inline — no separate file needed) ──────────────

    def _auth_headers(self, id_token: str) -> dict:
        return {"Authorization": f"Bearer {id_token}"}

    def _get_user(self, id_token: str) -> dict:
        """GET /user/me — fetch existing user profile."""
        url = f"{_BACKEND_URL}/user/me"
        resp = requests.get(url, headers=self._auth_headers(id_token), timeout=_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get("data", resp.json())
        raise requests.HTTPError(response=resp)

    def _put_user(self, id_token: str, email: str, name: str) -> dict:
        """PUT /user/me — create / upsert user profile (idempotent)."""
        url = f"{_BACKEND_URL}/user/me"
        resp = requests.put(
            url,
            headers=self._auth_headers(id_token),
            data={"email": email, "name": name},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("data", resp.json())
        raise requests.HTTPError(response=resp)

    # ── called from JS after Firebase auth success ──────────────────────

    def on_login_success(
        self,
        id_token: str,
        provider: str,
        email: str = "",
        name: str = "",
        refresh_token: str | None = None,
    ):
        """Handle successful Firebase authentication.

        Returns:
            JSON string: {"ok": true, "user": {...}} or {"ok": false, "error": "..."}
        """
        try:
            logger.info("[AuthBridge] on_login_success  provider=%s email=%s", provider, email)
            self._id_token = id_token

            existing_email = ""
            try:
                existing_user = self._db.get_local_user()
                if existing_user and isinstance(existing_user, dict):
                    existing_email = str(existing_user.get("email") or "").strip().lower()
            except Exception:
                # If reading local_user fails, do not block sign-in.
                existing_email = ""

            # Seed token cache and persist refresh token for future API calls.
            set_initial_tokens(id_token=id_token, refresh_token=refresh_token)

            if provider == "password":
                try:
                    user_data = self._get_user(id_token)
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        user_data = self._put_user(id_token, email, name)
                    else:
                        raise
            else:
                # Google / GitHub → PUT (idempotent)
                user_data = self._put_user(id_token, email, name)

            # Reset local DB only if the signed-in user changed.
            new_email = str(user_data.get("email") or email or "").strip().lower()
            if existing_email and new_email and existing_email != new_email:
                logger.info(
                    "[AuthBridge] User changed (%s -> %s). Resetting local DB.",
                    existing_email,
                    new_email,
                )
                self._db.reset_database(recreate_tables=True)

            self._db.upsert_local_user(user_data)
            self._user_data = user_data

            logger.info("[AuthBridge] User synced: %s", user_data.get("name"))
            return json.dumps({"ok": True, "user": user_data})

        except requests.ConnectionError:
            msg = "Cannot reach the Zenno server. Check your connection and try again."
            logger.error("[AuthBridge] %s", msg)
            return json.dumps({"ok": False, "error": msg})

        except requests.Timeout:
            msg = "Server request timed out. Please try again."
            logger.error("[AuthBridge] Timeout")
            return json.dumps({"ok": False, "error": msg})

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            msg = None
            if e.response is not None:
                try:
                    body = e.response.json()
                    msg = body.get("message") if isinstance(body, dict) else None
                except Exception:
                    msg = None
            if not msg:
                msg = (e.response.text[:200] if e.response is not None else str(e))
            msg = f"Server error ({status}): {msg}"
            logger.error("[AuthBridge] %s", msg)
            return json.dumps({"ok": False, "error": msg})

        except Exception as e:
            logger.exception("[AuthBridge] Unexpected error")
            return json.dumps({"ok": False, "error": f"Unexpected error: {e}"})

    # ── external browser OAuth (Google/GitHub) ─────────────────────────

    def external_oauth(self, provider: str):
        """Run Google/GitHub sign-in in the system browser and return the same payload
        as `on_login_success`.

        The browser completes Firebase OAuth and POSTs the ID token back to a loopback
        server running on 127.0.0.1.
        """
        provider = (provider or "").strip().lower()
        if provider not in {"google", "github"}:
            return json.dumps({"ok": False, "error": "Invalid provider."})

        firebase_cfg = self._firebase_js_config()
        if not firebase_cfg.get("apiKey") or not firebase_cfg.get("projectId"):
            return json.dumps({"ok": False, "error": "Firebase configuration missing. Check your .env file."})

        state = secrets.token_urlsafe(24)
        done = threading.Event()
        result_holder: dict = {}

        html = self._external_oauth_html(firebase_cfg=firebase_cfg, provider=provider, state=state)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                return

            def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path in {"/", "/external-auth"}:
                    self._send(200, html.encode("utf-8"))
                    return
                if parsed.path == "/health":
                    self._send(200, b"ok", content_type="text/plain; charset=utf-8")
                    return
                self._send(404, b"Not found", content_type="text/plain; charset=utf-8")

            def do_POST(self):
                parsed = urlparse(self.path)
                if parsed.path != "/token":
                    self._send(404, b"Not found", content_type="text/plain; charset=utf-8")
                    return

                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length) if length > 0 else b"{}"
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except Exception:
                    self._send(400, b"Bad request", content_type="text/plain; charset=utf-8")
                    return

                if payload.get("state") != state:
                    self._send(400, b"Invalid state", content_type="text/plain; charset=utf-8")
                    return

                id_token = payload.get("idToken") or ""
                refresh_token = payload.get("refreshToken")
                email = payload.get("email") or ""
                name = payload.get("name") or ""

                if not id_token:
                    self._send(400, b"Missing token", content_type="text/plain; charset=utf-8")
                    return

                result_holder.update(
                    {
                        "idToken": id_token,
                        "refreshToken": refresh_token,
                        "email": email,
                        "name": name,
                    }
                )
                done.set()
                self._send(200, b"{\"ok\":true}", content_type="application/json")

        server = ThreadingTCPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        port = server.server_address[1]
        url = f"http://localhost:{port}/external-auth?provider={provider}&state={state}"

        try:
            logger.info("[AuthBridge] External OAuth start provider=%s url=%s", provider, url)
            try:
                webbrowser.open(url, new=2)
            except Exception:
                logger.exception("[AuthBridge] Failed to open system browser")

            timeout_sec = 180
            if not done.wait(timeout=timeout_sec):
                return json.dumps({"ok": False, "error": "Sign-in timed out. Please try again."})

            return self.on_login_success(
                result_holder.get("idToken", ""),
                provider,
                result_holder.get("email", ""),
                result_holder.get("name", ""),
                result_holder.get("refreshToken"),
            )
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass

    def _external_oauth_html(self, *, firebase_cfg: dict, provider: str, state: str) -> str:
        cfg_json = json.dumps(firebase_cfg)
        safe_provider = "github" if provider == "github" else "google"
        provider_label = safe_provider.title()
        return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Zenno — Sign In</title>
  <style>
    body {{ font-family: Segoe UI, system-ui, sans-serif; padding: 28px; background: #0f0f13; color: #e8e8f0; }}
    .card {{ max-width: 560px; margin: 0 auto; padding: 22px; border: 1px solid #2a2a3a; border-radius: 12px; background: #1a1a24; }}
    h1 {{ margin: 0 0 6px; font-size: 20px; }}
    p {{ margin: 0 0 16px; color: #b5b5c8; line-height: 1.4; }}
    .muted {{ color: #8888a0; font-size: 12px; }}
    .err {{ color: #ffb3b3; white-space: pre-wrap; word-break: break-word; }}
    .done {{
      display: none;
      margin-top: 14px;
      padding: 14px;
      border: 1px solid #2a2a3a;
      border-radius: 12px;
      font-size: 18px;
      font-weight: 800;
      line-height: 1.35;
    }}
    .done.show {{ display: block; }}
        button {{
            display: inline-flex; align-items: center; justify-content: center;
            padding: 12px 14px; border-radius: 10px; border: 1px solid #2a2a3a;
            background: #6c5ce7; color: #fff; font-weight: 700; cursor: pointer;
        }}
        button:disabled {{ opacity: .6; cursor: not-allowed; }}
  </style>
</head>
<body>
  <div class=\"card\">
        <h1>{provider_label} sign-in</h1>
        <p>Click continue to sign in. When finished, this page will confirm and you can close the tab.</p>
        <p class=\"muted\">If you see an \"unauthorized domain\" error, add localhost in Firebase Auth → Settings → Authorized domains.</p>
        <button id=\"go\">Continue with {provider_label}</button>
                <div id="done" class="done">Sign-in complete. Close this tab and return to the Zenno app.</div>
        <div style=\"height:12px\"></div>
        <div id=\"status\" class=\"muted\">Waiting…</div>
  </div>

  <script src=\"https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js\"></script>
  <script src=\"https://www.gstatic.com/firebasejs/10.12.0/firebase-auth-compat.js\"></script>
  <script>
    const FIREBASE_CFG = {cfg_json};
    const PROVIDER = {json.dumps(safe_provider)};
    const STATE = {json.dumps(state)};

    const $status = document.getElementById('status');
    function setStatus(msg, isErr=false) {{
      $status.textContent = msg;
      $status.className = isErr ? 'err' : 'muted';
    }}

    function friendly(code, message) {{
      if (code === 'auth/unauthorized-domain') return 'Unauthorized domain. Add localhost (and/or 127.0.0.1) to Firebase authorized domains.';
      if (code === 'auth/operation-not-allowed') return 'Provider not enabled in Firebase Console (Authentication → Sign-in method).';
      return 'Auth error (' + (code || 'unknown') + '): ' + (message || '');
    }}

    async function postToken(payload) {{
      const resp = await fetch('/token', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(payload)
      }});
      if (!resp.ok) throw new Error('Token handoff failed');
    }}

        async function finalizeWithUser(user) {{
            setStatus('Finalizing…');
            const idToken = await user.getIdToken();
            const refreshToken = user.refreshToken;
            const email = user.email || '';
            const name = user.displayName || (email ? email.split('@')[0] : 'User');
            await postToken({{ state: STATE, idToken, refreshToken, email, name }});

            const btn = document.getElementById('go');
            if (btn) btn.style.display = 'none';
            const done = document.getElementById('done');
            if (done) done.classList.add('show');

            setStatus('Done. You can close this tab now.');
        }}

        async function checkRedirectResult() {{
            const auth = firebase.auth();
            const result = await auth.getRedirectResult();
            if (result && result.user) {{
                await finalizeWithUser(result.user);
                return true;
            }}
            return false;
        }}

        async function startSignIn() {{
            const btn = document.getElementById('go');
            btn.disabled = true;
            try {{
                const auth = firebase.auth();
                const provider = (PROVIDER === 'github') ? new firebase.auth.GithubAuthProvider() : new firebase.auth.GoogleAuthProvider();

                // Popup requires a user gesture, so we only call this from the button click.
                setStatus('Opening sign-in…');
                try {{
                    const popupResult = await auth.signInWithPopup(provider);
                    if (popupResult && popupResult.user) {{
                        await finalizeWithUser(popupResult.user);
                        return;
                    }}
                }} catch (popupErr) {{
                    const code = popupErr && popupErr.code;
                    // Fallback to redirect on popup blocked.
                    if (!(code && code.includes('popup'))) throw popupErr;
                }}

                // Redirect fallback (guard against infinite bouncing)
                const key = 'zenno_redirect_attempted_' + PROVIDER;
                if (sessionStorage.getItem(key) === '1') {{
                    setStatus('Sign-in could not be completed (redirect loop). Try again in a normal browser window (not incognito) and allow cookies for localhost.', true);
                    return;
                }}
                sessionStorage.setItem(key, '1');
                await auth.signInWithRedirect(provider);
            }} catch (e) {{
                console.error(e);
                const code = e && e.code;
                const msg = e && e.message;
                setStatus(friendly(code, msg), true);
            }} finally {{
                btn.disabled = false;
            }}
        }}

        async function main() {{
      try {{
        if (!firebase.apps.length) firebase.initializeApp(FIREBASE_CFG);
                // If we returned from a redirect, finalize immediately.
                const completed = await checkRedirectResult();
                if (completed) return;

                const btn = document.getElementById('go');
                btn.addEventListener('click', () => startSignIn());
                setStatus('Waiting for you to click Continue…');
      }} catch (e) {{
        console.error(e);
        const code = e && e.code;
        const msg = e && e.message;
        setStatus(friendly(code, msg), true);
      }}
    }}

    main();
  </script>
</body>
</html>"""

    # ── called when user clicks "Run Agent" ─────────────────────────────

    def run_agent(self):
        """Signal the WebView to close so agent.py loop can start."""
        logger.info("[AuthBridge] run_agent requested — closing UI")
        win = self._win.get("window")
        if win:
            win.destroy()

    @property
    def user_data(self) -> dict | None:
        return self._user_data

    @property
    def id_token(self) -> str | None:
        return self._id_token
