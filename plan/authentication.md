# Zenno Desktop Agent — Authentication Plan (Firebase + Backend Sync)

## Goal
Add **sign-in only** authentication to the Windows desktop agent using **Firebase Authentication** with three methods:

1. Email + password
2. Google
3. GitHub

After a successful sign-in:
- Fetch or create the user via backend `/api/v1/user/me` using `Authorization: Bearer <firebase-id-token>`
- Store the returned user profile in a **local SQLite user table**
- Show a **Welcome** dialog: “Welcome to Zenno, <UserName>” with a **large `Run Agent`** button
- On `Run Agent`, close all UI and start the existing agent loop

## Non-Goals (explicitly out of scope)
- Sign-up / password reset (handled by the website)
- Remember-me / silent login across restarts (unless added later)
- Any additional screens (settings, profile edit, etc.)

## Current Repo Notes (baseline)
- Entry point: `agent.py` runs the monitoring loop directly.
- Local DB: `database/db.py` already manages schema and a SQLite connection.
- There is an `auth/` folder, currently empty.
- No UI framework is present in `requirements.txt`.

## Proposed Architecture (minimal + matches required UX)
### Why a WebView-based UI
Firebase **client SDK** support for Google/GitHub sign-in is strongest in the **JavaScript SDK** (popup/redirect flows). In Python, provider OAuth flows are non-trivial and typically require implementing OAuth + PKCE manually.

To keep the UX simple (one attractive sign-in screen) and to support **Email/Password + Google + GitHub** reliably, use:
- **pywebview**: lightweight native desktop WebView window
- A small **HTML/JS UI** inside the WebView
- **Firebase JS SDK** inside that HTML to perform authentication
- A tiny **Python bridge API** (pywebview `js_api`) for:
  - receiving the Firebase ID token from JS
  - calling the backend
  - persisting local user
  - controlling the “close UI → run agent” transition

This keeps all auth logic in the Firebase client SDK, consistent with your requirement.

## User Flow (exact UX)
### 1) App start
- Desktop agent starts in “auth mode”.
- Opens a single window with an attractive sign-in UI:
  - Email input
  - Password input
  - `Sign in` button
  - Underneath: two buttons `Continue with Google` and `Continue with GitHub`

### 2A) Email + Password sign-in
- JS calls Firebase `signInWithEmailAndPassword(email, password)`
- On success, JS obtains `idToken` via `user.getIdToken()`
- JS passes token to Python bridge: `window.pywebview.api.on_login_success(idToken, "password")`

Python then:
- Calls backend:
  - `GET {BACKEND_BASE_URL}/user/me`
  - `Authorization: Bearer <idToken>`
- On 200, saves response `data` to local DB `local_user` table
- Shows a welcome dialog/screen with:
  - “Welcome to Zenno, <name>”
  - large `Run Agent` button

### 2B) Google / GitHub sign-in
- JS calls Firebase `signInWithPopup(provider)` for Google and GitHub
- On success, JS obtains `idToken`
- JS passes token to Python bridge: `window.pywebview.api.on_login_success(idToken, "google")` or `"github"`

Python then:
- Calls backend:
  - `PUT {BACKEND_BASE_URL}/user/me`
  - `Authorization: Bearer <idToken>`
  - `multipart/form-data` with `email` + `name` (from Firebase user) and optional `profilePhoto` omitted
  - Note: endpoint is idempotent per your docs; safe to call.
- Saves response `data` to local DB
- Shows the same welcome dialog and `Run Agent`

### 3) Run Agent
- When user clicks `Run Agent`:
  - Close the WebView window
  - Start the existing agent loop (`DesktopAgent.start()`) in the same Python process

## Backend Integration Details
### Base URL
- Read from `.env`: `BACKEND_BASE_URL` (example: `http://localhost:3000/api/v1`)

### Requests
- Email/password path: `GET /user/me`
- Google/GitHub path: `PUT /user/me`

### Error handling
- If backend returns `401`: show a user-friendly error and force re-login (token invalid/expired)
- If backend unreachable (connection error / timeout): show error with “Retry”
- If `404 User not found` on GET (possible when profile not created yet):
  - Easiest rule: fall back to calling `PUT /user/me` with name/email from Firebase user, then continue.
  - This keeps the app robust while still following your intended flow.

## Local SQLite User Table
### Schema
Add a simple single-row table (or allow multiple users, but default to “current user = last login”).

Recommended minimal fields to store (based on backend response):
- `backend_user_id` (maps to `_id`)
- `email`
- `name`
- `profile_photo`
- `is_verified`
- `role`
- `created_at`
- `updated_at`
- `last_login_at`

Notes:
- Do **not** persist the Firebase ID token to disk by default (expires ~1 hour and is sensitive).
- Keep token in memory for the current run only.

### DB changes
- Update `Database.create_tables()` in `database/db.py` to create `local_user`.
- Add helper methods:
  - `upsert_local_user(user_dict)`
  - `get_local_user()`
  - (optional) `clear_local_user()`

## Configuration / Environment
### Required env vars (already present)
- `FIREBASE_API_KEY`
- `FIREBASE_PROJECT_ID`
- `BACKEND_BASE_URL`

### Derived Firebase config in JS
Firebase JS typically needs:
- `apiKey`: from env
- `authDomain`: `${FIREBASE_PROJECT_ID}.firebaseapp.com` (derived)
- `projectId`: from env

If you later add hosting/custom domain, this may change, but the derived domain is standard for Firebase projects.

### Firebase Console prerequisites
- Enable providers:
  - Email/Password
  - Google
  - GitHub
- For GitHub provider, configure OAuth app and add callback URL as required by Firebase.

## UI Implementation Plan
### UI technology
- Add dependency: `pywebview`
- Bundle a small HTML page under something like `auth/ui/index.html`.

### UI states (single window)
1. **SignIn state**
   - Email/password inputs and buttons
   - Validation (empty fields)
   - Loading spinner/disabled buttons during auth
2. **Welcome state**
   - “Welcome to Zenno, <name>”
   - Large `Run Agent`
   - Optional: small “Sign out” link (only if needed; otherwise omit to match spec)

### Avoiding extra UX
- No sign-up links
- No password reset
- No extra pages

## Python Code Structure (new modules)
Suggested new modules:
- `auth/env.py`
  - Load `.env` (use `python-dotenv`) and provide typed accessors
- `auth/webview_app.py`
  - Creates the WebView window, registers bridge API, loads HTML
- `auth/bridge.py`
  - `on_login_success(id_token, provider)`
  - Calls backend and persists local user
  - Exposes `run_agent()` called by the welcome UI button
- `auth/backend_client.py`
  - Uses `requests` to call GET/PUT `/user/me`
- `auth/state.py` (optional)
  - Simple in-memory state: current token, current user

Entry point options:
- Option A (preferred): create a new `main.py` and keep `agent.py` as pure agent runtime.
- Option B: modify `agent.py` so `if __name__ == '__main__'` runs auth first.

## Dependencies to add
Update `requirements.txt` with:
- `pywebview` (desktop UI)
- `python-dotenv` (read `.env`)
- `requests` (backend calls)

(If you later need packaging)
- `pyinstaller` (optional)

## Implementation Steps (work breakdown)
### Phase 0 — Prep
1. Add env loader (`python-dotenv`) and a small config accessor.
2. Add a backend client with timeouts and clean error messages.

### Phase 1 — Local user persistence
1. Add `local_user` table in `database/db.py`.
2. Add upsert + get helpers.
3. Sanity test by inserting a dummy user.

### Phase 2 — Auth UI
1. Add `pywebview` window creation.
2. Create `auth/ui/index.html` with:
   - Email/password form
   - Google and GitHub buttons
   - Basic styling (clean, centered card)
3. Add Firebase JS SDK initialization from env values (passed from Python).
4. Implement JS handlers:
   - `signInWithEmailAndPassword`
   - `signInWithPopup(new GoogleAuthProvider())`
   - `signInWithPopup(new GithubAuthProvider())`
5. On success, call python bridge with `idToken` and provider name.

### Phase 3 — Backend sync + welcome
1. Implement provider branching:
   - password → backend `GET /user/me`
   - google/github → backend `PUT /user/me` (multipart with email+name)
2. Persist backend user in `local_user`.
3. Show welcome state + Run Agent button.

### Phase 4 — Handoff to agent runtime
1. When `Run Agent` is clicked, close WebView.
2. Start `DesktopAgent.start()`.
3. Ensure keyboard/mouse hooks initialize only after auth UI closes (prevents focus/input issues).

### Phase 5 — QA checklist
- Email/password valid → welcome → Run Agent starts monitoring
- Google valid → welcome → Run Agent
- GitHub valid → welcome → Run Agent
- Invalid password → error in UI
- Backend down → retry works
- Token invalid → forces re-login

## Risks & Mitigations
- **Google/GitHub popup behavior inside WebView**: some WebView environments can block popups or third-party cookies.
  - Mitigation: implement Google/GitHub with `signInWithPopup()` first; if it fails, fall back to `signInWithRedirect()`.
  - If redirect inside WebView is unreliable, last-resort fallback is to open the system browser to a hosted login page and pass the ID token back to the agent (requires additional plumbing; not part of the default implementation unless needed).
- **Firebase provider configuration**: GitHub requires correct OAuth app + callback URL in Firebase.
  - Mitigation: document a setup checklist and validate with a smoke test account early.
- **Token expiration**: ID tokens expire ~1 hour.
  - Mitigation: keep the token in-memory; if you later add backend sync during runtime, refresh via Firebase JS SDK and re-send a fresh token when needed.
- **Sensitive data storage**: storing tokens locally is a security risk.
  - Mitigation: store only the backend user profile in SQLite; do not persist tokens by default.

## Testing Strategy (pragmatic)
- Manual tests for sign-in flows (providers require real accounts).
- Add a small `auth/backend_client` unit-ish test (optional) that mocks HTTP responses.
- Verify DB `local_user` row updates.

## Open Questions (need confirmation)
1. If `GET /user/me` returns 404 for email/password (user never created), should desktop call `PUT /user/me` automatically?
2. Should the app re-authenticate every time it starts, or do you want a “stay signed in” experience later?

---
Last updated: 2026-02-28
