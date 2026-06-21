# Auto-generated first-run password (design spec)

**Date:** 2026-06-21
**Branch:** `app-v2` (no `main` merge — first app-code change since the rewrite landed)
**Status:** approved design → input to the implementation plan

## Goal

On first boot with **no** password configured, the app generates a strong random admin password
instead of leaving the instance at the first-run `/setup` screen. It writes the password to a file
in the config volume (never logs the value), and **forces the admin to change it on first login**.

## Why

The deployment ships an empty-config image; today a first run requires the operator to either preset
`MSM_PASSWORD`/`MSM_PASSWORD_FILE` or complete the `/setup` screen. Auto-generation removes that
step for the common case (just `docker compose up`), while the forced change ensures the random
bootstrap value is never the long-lived credential.

## Locked decisions

1. **Retrieval:** write the generated password to `/config/initial_password.txt` (mode `0600`); log
   only the **path** at startup, never the value (rule 5). The value lives in the same volume as
   `secret.key`/`app.db` — already the trust boundary.
2. **Lifecycle:** **force a password change on first login.** The generated password works once to
   log in; the admin is then funneled to a change-password screen and cannot use the app until they
   set a new password. A `must_change_password` flag tracks this state.
3. **Admin-supplied passwords are final:** `MSM_PASSWORD` / `MSM_PASSWORD_FILE` are taken as-is — no
   force-change, no generated file. Only the auto-generated case sets the flag and writes the file.

## Bootstrap precedence (`web/auth.py:bootstrap_password`, runs at web startup)

Extends the current precedence with a new final fallback. Idempotent; runs every startup.

1. Password already set (`is_password_set`) → return (no-op).
2. `MSM_PASSWORD_FILE` (path; contents whitespace-stripped) non-empty → `set_password`; return.
3. `MSM_PASSWORD` non-empty → `set_password`; return.
4. **Neither set → auto-generate:**
   - `generated = generate_password()` — `secrets.token_urlsafe(18)` (~24 chars, URL-safe).
   - `set_password(repo, generated)` (stores the Argon2 hash; plaintext never persisted in the DB).
   - `repo.set_setting(MUST_CHANGE_KEY, "1")` where `MUST_CHANGE_KEY = "must_change_password"`.
   - `_write_initial_password_file(path, generated)` → write `generated + "\n"`, `chmod 0o600`.
   - `log.info("auth.bootstrap.generated", path=str(path))` — **path only, never the value**.

`generate_password()` and `_write_initial_password_file()` are new helpers in `web/auth.py`.
`set_password` stays pure (no flag/file side effects); the flag is set explicitly in the auto-gen
branch and cleared explicitly in the change-password flow.

### Initial-password file path resolution

`bootstrap_password(repo, *, initial_password_path: Path | None = None)`. When `None`, resolve from
`MSM_INITIAL_PASSWORD_FILE` (env) else `<parent of MSM_DB_PATH>/initial_password.txt` else
`/config/initial_password.txt`. Tests pass an explicit `tmp_path`. `web/server.py` passes the
resolved path (or lets it default).

## Force-change flow

- Admin retrieves the password: `docker exec <c> cat /config/initial_password.txt` (or via the
  `./config` bind mount). Logs in at `/login` normally (sets `session["authed"] = True`).
- **Guard gating** (`web/deps.py`):
  - `require_page_auth`: if authenticated **and** `must_change_password` is set **and** the request
    path is **not** in the allowlist → `303` to `/account/password`. Allowlist (paths that must stay
    reachable to avoid a redirect loop / let the user escape): `/account/password`, `/logout`, and
    `/static/*` (static is already unauthenticated). Unauthenticated users still go to `/login` as
    today.
  - `require_api_auth`: if authenticated and `must_change_password` is set → `403`
    (`detail="password change required"`). The change-password endpoints are page routes (not under
    `require_api_auth`), so they remain reachable.
  - The flag is read via `asyncio.to_thread(repo.get_setting, MUST_CHANGE_KEY)` — one small indexed
    `Setting` lookup; only relevant until the first change. (Acceptable cost; not cached.)
- **Change-password page** (new — also closes the deferred Phase 3 FOLLOWUPS item: no change-pw
  template existed, and `/auth/password`'s error path wrongly re-rendered `login.html`):
  - `GET /account/password` (`require_page_auth`) → render `change_password.html` (a form: current,
    new, confirm). A banner when `must_change_password` is set: "You must change the auto-generated
    password before continuing."
  - `POST /account/password` (`require_page_auth`) → validate: `check_password(current)`, `new`
    non-empty, `new == confirm`. On failure re-render `change_password.html` with the error (HTTP
    400) — **not** `login.html`. On success: `set_password(new)` → `clear_initial_password(repo, path)`
    (delete `MUST_CHANGE_KEY` setting + best-effort delete the file, suppress `OSError`) → `303` to
    `/`. No engine `rebuild()` (auth is not engine config).
  - The legacy `POST /auth/password` handler (change-password, current+new, no GET page) is **moved
    to `POST /account/password`** — single route, GET form + POST submit. Its existing Phase 3 tests
    are updated to the new path. So the allowlist needs only `/account/password` (+ `/logout`,
    `/static/*`), not a second auth path.
- After a normal login, the dashboard route (`/`, under `require_page_auth`) triggers the redirect
  automatically — no special-casing in the login handler.
- A link to `/account/password` is added to the Settings page (so a non-forced change is reachable).

## Deployment / docs impact

- **`docker-compose.yml`:** the `secrets:` block + `MSM_PASSWORD_FILE` env are currently active,
  which makes `./msm_password.txt` mandatory (`up` fails without it). Comment them out by default
  with a note: "Optional — preset a password; otherwise one is auto-generated on first boot (see the
  README)." Auto-generation becomes the default path.
- **`README.md`:** update the quickstart — no need to create `msm_password.txt`; after `up`, read the
  generated password from `/config/initial_password.txt` (`docker exec … cat …`); you'll be prompted
  to change it on first login. Document `MSM_INITIAL_PASSWORD_FILE` in the env table and that
  `MSM_PASSWORD`/`_FILE` (if set) skip generation and the forced change.

## Files

| File | Change |
| --- | --- |
| `mediascanmonitor/web/auth.py` | `generate_password()`, `_write_initial_password_file()`, `_resolve_initial_password_path()`, extend `bootstrap_password` (auto-gen branch + flag + file + log), `clear_initial_password()`, `MUST_CHANGE_KEY`, `GET/POST /account/password` handlers |
| `mediascanmonitor/web/deps.py` | must-change gating in `require_page_auth` (allowlist) and `require_api_auth` (403) |
| `mediascanmonitor/web/templates/change_password.html` | new template (form + forced banner) |
| `mediascanmonitor/web/templates/settings.html` | link to `/account/password` |
| `mediascanmonitor/web/server.py` | resolve + pass the initial-password path to `bootstrap_password` |
| `docker-compose.yml` | make the password secret optional (comment out by default) |
| `README.md` | quickstart + env table updates |

## Testing

- **Auto-gen:** no env, no existing password → password hash stored; `MUST_CHANGE_KEY` set; file
  written at the given path with mode `0600` and contents that successfully `check_password`; the
  log record carries the path and **not** the value.
- **Precedence:** `MSM_PASSWORD` set → that password used; **no** flag, **no** file written.
  `MSM_PASSWORD_FILE` likewise. Existing password → full no-op (no flag, no file, no overwrite).
- **Guard gating:** authenticated session + `must_change_password` set → `GET /` returns `303` to
  `/account/password`; `GET /account/password`, `/logout`, `/static/...` are **not** redirected;
  a JSON API call returns `403`. With the flag cleared, `/` renders normally.
- **Change-password:** valid current+new+confirm → hash updated, `MUST_CHANGE_KEY` deleted, file
  deleted, `303` to `/`. Wrong current → re-renders `change_password.html` (HTTP 400), flag/file
  untouched. `new != confirm` / empty new → re-render with error.
- **File deletion is best-effort:** clearing when the file is already gone does not raise.

## Out of scope

Password complexity policy / rotation reminders / multi-user accounts. The generated password is
strong by construction (`token_urlsafe(18)`); the forced change covers the bootstrap-rotation goal.
