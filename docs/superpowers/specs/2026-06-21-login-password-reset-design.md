# Login-page password reset (design spec)

**Date:** 2026-06-21
**Branch:** `app-v2` (no `main` merge — continues the app-v2 rewrite line)
**Status:** approved design → input to the implementation plan
**Builds on:** [`2026-06-21-autogen-first-run-password-design.md`](2026-06-21-autogen-first-run-password-design.md)
(reuses `generate_password`, `_write_initial_password_file`, `_resolve_initial_password_path`,
`MUST_CHANGE_KEY`, the 0600 file convention, and the must-change forced-rotation gate).

## Goal

Give a locked-out admin a way back in from the **login page**: a "Forgot password?" flow that
**regenerates** the admin password exactly the way the first-run auto-generated password works —
a strong random value written to `/config/initial_password.txt` (mode `0600`, value never logged),
with the `must_change_password` flag set so the admin is forced to change it on first login.

## Why

Today the only recovery from a forgotten password is to delete `app.db` (losing all config) or hand-
edit the database. The first-run feature already generates, persists, and force-rotates a bootstrap
password; this exposes that same mechanism as an on-demand reset. The new password lands in the same
host-readable `0600` file, so recovery requires the same trust boundary (filesystem access to
`/config`) that retrieving the first-run password already does.

## Locked decisions

1. **Exposure: always-on, rate-limited, behind a confirm page.** The reset is reachable from the
   unauthenticated login page (the only place a locked-out admin can be). This is acceptable because
   the regenerated password is written **only** to the `0600` `/config/initial_password.txt` file —
   an anonymous web visitor who triggers a reset **cannot read it**, so the action enables **no
   account takeover**. The single new exposure is denial-of-service (an anonymous visitor repeatedly
   invalidating the current password); it is bounded by a per-IP rate limit and gated behind an
   explicit confirm page.
2. **Confirm page (not a JS dialog).** `GET /auth/reset-password` renders a dedicated page that
   explains the consequence and where to retrieve the new password; the actual reset is a `POST` from
   that page. No JavaScript dependency.
3. **Reuses the first-run mechanism.** The reset regenerates and force-rotates identically to the
   auto-generated first-run password (`generate_password` → write `0600` file → set
   `must_change_password` → store Argon2 hash). The admin retrieves the value from
   `/config/initial_password.txt` and is forced to change it on first login.

## Shared helper (`web/auth.py`)

`bootstrap_password` short-circuits when a password is already set, so it cannot be reused directly.
Extract the first-run auto-generate core into one private helper and call it from both places:

```
def _generate_and_store(repo, path) -> None:
    """Generate a strong password, persist it as the live (must-change) credential, and write
    the retrievable 0600 file. Write the file BEFORE persisting the hash + flag so a write
    failure leaves any existing password intact (no lockout). NEVER logs the value (rule 5)."""
    generated = generate_password()
    _write_initial_password_file(path, generated)   # write first (write-order safety)
    repo.set_setting(MUST_CHANGE_KEY, "1")
    set_password(repo, generated)
```

- `bootstrap_password`'s auto-generate branch calls `_generate_and_store(repo, target)` then logs
  `auth.bootstrap.generated` (unchanged behavior; the log event/path stays as-is).
- New public helper:

```
def reset_to_generated_password(repo, path) -> None:
    """Unconditionally regenerate the admin password (forgot-password recovery). Unlike
    bootstrap_password this does NOT check is_password_set — it overwrites whatever is set."""
    _generate_and_store(repo, path)
    log.info("auth.password.reset", path=str(path))
```

`reset_to_generated_password` logs a **distinct** event (`auth.password.reset`) from the first-run
path so the two are auditable separately. Both log the **path only**, never the value (rule 5).

**Write-order safety / failure handling:** if `_write_initial_password_file` raises `OSError`, it does
so *before* `set_setting`/`set_password`, so the existing password and flag are untouched — the admin
is not locked out. The route surfaces this as an error (below).

## Routes (`web/auth.py`, unauthenticated)

Both sit alongside `/login`, `/setup`, `/auth/login` and are **not** behind `require_page_auth`
(a locked-out admin is by definition unauthenticated). They are also outside the must-change
allowlist concern — the routes are unauthenticated, so the `deps.py` page/api guards never run on
them.

- **`GET /auth/reset-password`** → render `reset_password.html` (the confirm page). Read-only; no rate
  limit. Always available (even when no password is set yet — harmless, equivalent to first run).
- **`POST /auth/reset-password`** →
  1. Rate-limit check against the **reset limiter** keyed by client IP. If not `allowed`, re-render
     `reset_password.html` with an error at `429 Too Many Requests` and **do not** regenerate.
  2. Record the attempt on the reset limiter.
  3. `await asyncio.to_thread(reset_to_generated_password, repo, _resolve_initial_password_path())`.
     On `OSError` (file write failed), re-render `reset_password.html` with an error at `500` and a
     message that the current password was left unchanged.
  4. On success, `303 → /login?reset=1`.

## Rate limiting

A **second** `LoginRateLimiter` instance, dedicated to resets, created in `create_app` and stored on
`app.state` next to the existing login limiter (e.g. `app.state.reset_limiter`). Parameters:
`max_attempts=3`, `window_seconds=3600` (stricter than login's 5 / 5 min, because a reset is more
disruptive). Keyed by client IP via the existing `_client_ip(request)`. Kept separate so reset
attempts never trip the login-failure lockout and vice-versa.

## Templates

- **New `reset_password.html`** (extends `base.html`, no nav): a short explanation —
  > Resetting generates a **new** admin password and invalidates the current one. After resetting,
  > retrieve the new password from `/config/initial_password.txt` on the host
  > (`docker exec media-scan-monitor cat /config/initial_password.txt`), then log in; you will be
  > required to change it.
  — a `POST /auth/reset-password` **Reset password** button, and a **Cancel** link back to `/login`.
  Renders an error banner (`class="error"`) when the POST handler re-renders it (rate-limited /
  write-failed).
- **`login.html`**: add a "Forgot password?" link to `/auth/reset-password`, and an **info banner**
  shown when the page is loaded with `?reset=1`:
  > A new password was generated and written to `/config/initial_password.txt`. Retrieve it on the
  > host, then log in — you'll be asked to change it.

## Login page flash

`login_page` (`GET /login`) reads `request.query_params.get("reset")` and passes an `info` flag to
`login.html` (rendered as a distinct info banner, separate from the existing `error` slot). No
session flash / cookie is used.

## Security

- **Rule 5:** the generated value is never logged, never returned in a response, never templated.
  Only the path is logged (`auth.password.reset`, path + client IP).
- **No CSRF token:** consistent with `/auth/login` and `/setup`, which are also unauthenticated POSTs
  (the `same_site="lax"` session-cookie defense does not apply to a request that carries no session).
  The confirm page is the deliberate friction against accidental/prefetch triggering.
- **No takeover:** the only sink for the new password is the `0600` host-readable file; the web
  response never reveals it.
- **Forced rotation:** `must_change_password` is set, so the existing must-change gate forces a change
  on first login after recovery.

## Files

| File | Change |
| --- | --- |
| `mediascanmonitor/web/auth.py` | `_generate_and_store` (extracted), `reset_to_generated_password`, `GET`+`POST /auth/reset-password`; refactor `bootstrap_password` auto-gen branch to call `_generate_and_store` |
| `mediascanmonitor/web/app.py` | create + store `app.state.reset_limiter` (second `LoginRateLimiter`) |
| `mediascanmonitor/web/templates/reset_password.html` | new confirm page |
| `mediascanmonitor/web/templates/login.html` | "Forgot password?" link + `?reset=1` info banner |
| `README.md` | first-login section: document "Forgot password?" as the web recovery path |

## Testing

- **Unit (`tests/web/test_auth.py`):**
  - `reset_to_generated_password` overwrites an existing password (old no longer verifies, new does),
    sets `MUST_CHANGE_KEY`, writes the `0600` file, and the value never appears in logs.
  - Write-order safety: when the file write fails (`OSError`, e.g. unwritable path), the existing
    password and flag are unchanged (no lockout).
  - The first-run auto-generate path still works after the `_generate_and_store` extraction
    (existing bootstrap tests stay green).
- **Routes (`tests/web/test_auth_routes.py`):**
  - `GET /auth/reset-password` renders the confirm page with a `200` for an **unauthenticated**
    client (no redirect to /login).
  - `POST /auth/reset-password` regenerates: `303 → /login?reset=1`, `MUST_CHANGE_KEY == "1"`, the
    file is written, and the response never contains the generated value.
  - Rate limit: after `max_attempts` resets from one IP, the next `POST` returns `429` and does **not**
    regenerate (the stored hash is unchanged on the throttled call).
  - `GET /login` shows the "Forgot password?" link, and `GET /login?reset=1` shows the info banner.

## Out of scope

Email/SMS recovery, security questions, multi-user accounts, a UI display of the generated value
(it stays host-file-only by design). Reset audit history beyond the single structured log line.
