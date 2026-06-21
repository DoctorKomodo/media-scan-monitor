# Login-page Password Reset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Forgot password?" flow on the login page that regenerates the admin password exactly the way the first-run auto-generated password works — a strong random value written to `/config/initial_password.txt` (mode `0600`, value never logged), with `must_change_password` set so the admin is forced to change it on first login.

**Architecture:** Extract the first-run auto-generate core in `web/auth.py` into one private `_generate_and_store(repo, path)` helper, called by both `bootstrap_password`'s auto-gen branch and a new public `reset_to_generated_password(repo, path)`. Two new unauthenticated routes (`GET`/`POST /auth/reset-password`) render a confirm page and perform the reset, throttled by a second `LoginRateLimiter` instance dedicated to resets. The login page gets a link and a post-reset info banner.

**Tech Stack:** FastAPI, Starlette `TestClient`, SQLModel `Setting` table, Argon2 (`argon2-cffi`), `secrets.token_urlsafe`, structlog, Jinja2, pytest. Python 3.14 (PEP 649, `StrEnum`, no `from __future__`).

## Global Constraints

- **Rule 5 — never log the value.** The generated password is written to the `0600` file and only the **path** is logged. The reset logs a **distinct** event `auth.password.reset` (path only); the first-run path keeps logging `auth.bootstrap.generated`.
- **Reuse the first-run mechanism.** The reset uses the same `generate_password` / `_write_initial_password_file` / `_resolve_initial_password_path` / `set_password` / `MUST_CHANGE_KEY` as the auto-generated first-run password. Do **not** introduce a second generation/format path.
- **Write-order safety.** In `_generate_and_store`, write the `0600` file **before** persisting the hash + flag. A write failure (`OSError`) must propagate **before** any DB mutation so the existing password and flag are left intact (no lockout).
- **Unauthenticated routes.** `GET`/`POST /auth/reset-password` are siblings of `/login` and `/setup` — **not** behind `require_page_auth` (a locked-out admin is unauthenticated). No CSRF token (consistent with `/auth/login` and `/setup`; the `same_site="lax"` cookie defense does not apply to a request with no session). The confirm page is the deliberate friction.
- **Reset rate limiter.** A **second** `LoginRateLimiter` instance, `app.state.reset_limiter`, with `max_attempts=3, window_seconds=3600.0`, keyed by client IP — separate from the login limiter so the two never entangle.
- **No takeover.** The new password's only sink is the `0600` host-readable file; the web response must never contain it.
- **Lint/format:** `ruff` select is `E,F,I,UP,B,C4,SIM,RUF`; line length 100; `mypy --strict` clean. PEP 649 — never add `from __future__ import annotations`. PROJECT QUIRK: `except A, B, C:` (no parens) is valid Python 3.14 PEP 758 and is ruff-**enforced** here — do not flag or parenthesize it. Templates use `class="error"` for errors and `class="ok"` for info (no `alert` class exists). Run `ruff format` then `ruff check --fix` then the real gate after pasting code. Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- Branch `app-v2`; **no `main` merge.** No new dependencies (all stdlib/existing).

---

## Task 1: auth.py — `_generate_and_store` extraction + `reset_to_generated_password`

**Files:**
- Modify: `mediascanmonitor/web/auth.py`
- Test: `tests/web/test_auth.py`

**Interfaces:**
- Consumes: existing `generate_password`, `_write_initial_password_file`, `_resolve_initial_password_path`, `set_password`, `is_must_change`, `check_password`, `MUST_CHANGE_KEY`, `log`.
- Produces (used by Task 2):
  - `reset_to_generated_password(repo: Repo, path: Path) -> None`
  - `_generate_and_store(repo: Repo, path: Path) -> None` (private; also now used inside `bootstrap_password`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_auth.py` (it already imports `auth`, `pytest`, `structlog`, `Path`, and defines the `repo` fixture):

```python
def test_reset_regenerates_over_existing_password(repo: Repo, tmp_path: Path) -> None:
    auth.set_password(repo, "old-pw")
    assert not auth.is_must_change(repo)
    pw_file = tmp_path / "initial_password.txt"

    with structlog.testing.capture_logs() as logs:
        auth.reset_to_generated_password(repo, pw_file)

    # the old password no longer verifies; the generated one (from the file) does
    assert not auth.check_password(repo, "old-pw")
    assert auth.is_must_change(repo)
    assert pw_file.exists()
    assert (pw_file.stat().st_mode & 0o777) == 0o600
    generated = pw_file.read_text(encoding="utf-8").strip()
    assert auth.check_password(repo, generated)
    # rule 5: a DISTINCT reset event carries the path, never the value
    events = [e for e in logs if e.get("event") == "auth.password.reset"]
    assert events and events[0]["path"] == str(pw_file)
    assert all(generated not in str(e) for e in logs)


def test_reset_write_failure_leaves_existing_password_intact(repo: Repo, tmp_path: Path) -> None:
    auth.set_password(repo, "keep-me")
    # parent is a regular FILE, so mkdir/open inside _write_initial_password_file raises OSError
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    bad_path = blocker / "initial_password.txt"

    with pytest.raises(OSError):
        auth.reset_to_generated_password(repo, bad_path)

    # nothing was mutated: the old password still verifies and no must-change flag was set
    assert auth.check_password(repo, "keep-me")
    assert not auth.is_must_change(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_auth.py -k reset -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'reset_to_generated_password'`.

- [ ] **Step 3: Implement in `mediascanmonitor/web/auth.py`**

Add this helper immediately **after** `_write_initial_password_file` (and before `clear_initial_password`):

```python
def _generate_and_store(repo: Repo, path: Path) -> None:
    """Generate a strong password, write the retrievable 0600 file, then persist it as the
    live must-change credential. The file is written BEFORE the hash + flag so a write
    failure leaves any existing password intact (no lockout). NEVER logs the value (rule 5).
    """
    generated = generate_password()
    _write_initial_password_file(path, generated)
    repo.set_setting(MUST_CHANGE_KEY, "1")
    set_password(repo, generated)


def reset_to_generated_password(repo: Repo, path: Path) -> None:
    """Unconditionally regenerate the admin password (forgot-password recovery).

    Unlike ``bootstrap_password`` this does NOT check ``is_password_set`` — it overwrites
    whatever is currently set, writes the new value to ``path`` (0600), sets the must-change
    flag, and logs a distinct ``auth.password.reset`` event (path only, never the value).
    """
    _generate_and_store(repo, path)
    log.info("auth.password.reset", path=str(path))
```

Then **refactor** `bootstrap_password`'s auto-generate branch to reuse the helper. Replace the block from the `# Nothing supplied …` comment to the end of the function with:

```python
    # Nothing supplied → auto-generate and force a change on first login.
    target = (
        initial_password_path
        if initial_password_path is not None
        else _resolve_initial_password_path()
    )
    _generate_and_store(repo, target)
    log.info("auth.bootstrap.generated", path=str(target))
```

(`_generate_and_store` holds the same write-first ordering the branch had inline, so behavior is unchanged — the existing bootstrap tests must stay green.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: PASS (the new `reset` tests AND all existing bootstrap tests).

Gate on the touched files:
Run: `uv run ruff format mediascanmonitor/web/auth.py tests/web/test_auth.py && uv run ruff check mediascanmonitor/web/auth.py tests/web/test_auth.py && uv run mypy mediascanmonitor`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/web/auth.py tests/web/test_auth.py
git commit -m "$(cat <<'EOF'
feat(auth): reset_to_generated_password helper (shared with bootstrap)

Extract the first-run auto-generate core into _generate_and_store (write the
0600 file before persisting the hash+flag, so a write failure leaves any
existing password intact) and call it from both bootstrap_password and a new
reset_to_generated_password, which unconditionally regenerates the admin
password and logs a distinct auth.password.reset event (path only, rule 5).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: reset routes + reset limiter + templates

**Files:**
- Modify: `mediascanmonitor/web/app.py` (add `app.state.reset_limiter`)
- Modify: `mediascanmonitor/web/auth.py` (`_reset_limiter`, two routes, login banner)
- Create: `mediascanmonitor/web/templates/reset_password.html`
- Modify: `mediascanmonitor/web/templates/login.html` (Forgot-password link + `?reset=1` banner)
- Test: `tests/web/test_auth_routes.py`

**Interfaces:**
- Consumes (Task 1): `reset_to_generated_password`, plus existing `_resolve_initial_password_path`, `_client_ip`, `get_repo`, `get_templates`, `LoginRateLimiter`.
- Produces: routes `GET /auth/reset-password`, `POST /auth/reset-password`; `app.state.reset_limiter`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_auth_routes.py` (it imports `TestClient`, `Repo`, and uses the `app`, `repo`, `client` fixtures from conftest):

```python
def test_get_reset_password_renders_confirm_page(client: TestClient) -> None:
    # a locked-out admin is unauthenticated — the confirm page must still be reachable
    resp = client.get("/auth/reset-password")
    assert resp.status_code == 200
    assert "Reset password" in resp.text
    assert "/config/initial_password.txt" in resp.text  # tells the admin where to read it


def test_post_reset_regenerates_and_redirects(
    app, repo: Repo, tmp_path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    from mediascanmonitor.web.auth import MUST_CHANGE_KEY, check_password, set_password

    pw_file = tmp_path / "initial_password.txt"
    monkeypatch.setenv("MSM_INITIAL_PASSWORD_FILE", str(pw_file))
    set_password(repo, "old-pw")

    client = TestClient(app)
    resp = client.post("/auth/reset-password", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?reset=1"
    assert repo.get_setting(MUST_CHANGE_KEY) == "1"
    assert pw_file.exists()
    generated = pw_file.read_text(encoding="utf-8").strip()
    assert check_password(repo, generated)
    assert not check_password(repo, "old-pw")
    assert generated not in resp.text  # rule 5: value never in the response body


def test_post_reset_is_rate_limited(
    app, repo: Repo, tmp_path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    from mediascanmonitor.web.auth import check_password, set_password

    pw_file = tmp_path / "initial_password.txt"
    monkeypatch.setenv("MSM_INITIAL_PASSWORD_FILE", str(pw_file))
    set_password(repo, "old-pw")

    client = TestClient(app)
    # reset_limiter default max_attempts=3: first 3 succeed (303), the 4th is throttled
    for _ in range(3):
        r = client.post("/auth/reset-password", follow_redirects=False)
        assert r.status_code == 303
    blocked = client.post("/auth/reset-password", follow_redirects=False)
    assert blocked.status_code == 429
    # the throttled call did NOT regenerate again: the file still holds the 3rd reset's value
    generated = pw_file.read_text(encoding="utf-8").strip()
    assert check_password(repo, generated)


def test_login_page_shows_reset_link_and_banner(client: TestClient) -> None:
    plain = client.get("/login")
    assert plain.status_code == 200
    assert "/auth/reset-password" in plain.text  # the "Forgot password?" link
    banner = client.get("/login?reset=1")
    assert "written to" in banner.text  # the post-reset info banner text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_auth_routes.py -k reset -v`
Expected: FAIL — `GET /auth/reset-password` returns 404 (route not defined yet).

- [ ] **Step 3: Wire the reset limiter in `mediascanmonitor/web/app.py`**

Immediately after the line `app.state.limiter = LoginRateLimiter()`, add:

```python
    # A SECOND limiter, dedicated to the unauthenticated /auth/reset-password POST, kept
    # separate from the login limiter so reset attempts never trip the login lockout (and
    # vice-versa). Stricter: 3 resets per hour per IP.
    app.state.reset_limiter = LoginRateLimiter(max_attempts=3, window_seconds=3600.0)
```

- [ ] **Step 4: Add the routes + login banner in `mediascanmonitor/web/auth.py`**

Add a `_reset_limiter` accessor immediately after the existing `_limiter` function:

```python
def _reset_limiter(request: Request) -> LoginRateLimiter:
    limiter: LoginRateLimiter = request.app.state.reset_limiter
    return limiter
```

**Replace** the existing `login_page` handler so it surfaces the post-reset banner:

```python
@router.get("/login")
async def login_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    info = (
        "A new password was generated and written to the initial-password file on the host. "
        "Retrieve it, then sign in — you will be asked to change it."
        if request.query_params.get("reset") == "1"
        else None
    )
    return templates.TemplateResponse(request, "login.html", {"error": None, "info": info})
```

Add the two reset routes (place them after the `logout` handler, alongside the other auth routes):

```python
@router.get("/auth/reset-password")
async def reset_password_page(
    request: Request,
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    return templates.TemplateResponse(request, "reset_password.html", {"error": None})


@router.post("/auth/reset-password")
async def reset_password(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    limiter = _reset_limiter(request)
    key = _client_ip(request)
    if not limiter.allowed(key):
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"error": "Too many reset attempts. Try again later."},
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    limiter.record_failure(key)  # the limiter counts attempts; a reset is one attempt
    try:
        await asyncio.to_thread(
            reset_to_generated_password, repo, _resolve_initial_password_path()
        )
    except OSError:
        return templates.TemplateResponse(
            request,
            "reset_password.html",
            {"error": "Could not write the new password file; the current password is unchanged."},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return RedirectResponse("/login?reset=1", status_code=status.HTTP_303_SEE_OTHER)
```

- [ ] **Step 5: Create `mediascanmonitor/web/templates/reset_password.html`**

```html
{% extends "base.html" %}
{% block title %}Reset password — media-scan-monitor{% endblock %}
{% block content %}
<h1>Reset password</h1>
<p>Resetting generates a <strong>new</strong> admin password and invalidates the current one.
   After resetting, retrieve the new password from the initial-password file on the host:</p>
<pre>docker exec media-scan-monitor cat /config/initial_password.txt</pre>
<p>Then sign in with it — you will be required to change it.</p>
<form method="post" action="/auth/reset-password">
  <button type="submit" class="danger">Reset password</button>
</form>
<p><a href="/login">Cancel</a></p>
{% endblock %}
```

(`base.html` already renders `{% if error %}…{% endif %}` at the top of `<body>`, so the rate-limit / write-failure error shows automatically — do not add a second error block here.)

- [ ] **Step 6: Add the link + banner in `mediascanmonitor/web/templates/login.html`**

Replace the contents of the `{% block content %}` so it includes the info banner and the link:

```html
{% block content %}
<h1>Sign in</h1>
{% if info %}<p class="ok" role="status">{{ info }}</p>{% endif %}
<form method="post" action="/auth/login">
  <label>Password <input type="password" name="password" autofocus required /></label>
  <button type="submit">Sign in</button>
</form>
<p><a href="/auth/reset-password">Forgot password?</a></p>
{% endblock %}
```

(The login POST handlers render `login.html` with only `error` set; `info` is then undefined, which Jinja treats as falsy, so the banner stays hidden — no change needed there.)

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_auth_routes.py -v`
Expected: PASS.

Then the full web suite (the login template + app.state change touch shared surface — confirm no regressions):
Run: `uv run pytest tests/web/ -q`
Expected: all pass.

Gate:
Run: `uv run ruff format mediascanmonitor/web/app.py mediascanmonitor/web/auth.py tests/web/test_auth_routes.py && uv run ruff check mediascanmonitor/web/app.py mediascanmonitor/web/auth.py tests/web/test_auth_routes.py && uv run mypy mediascanmonitor`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add mediascanmonitor/web/app.py mediascanmonitor/web/auth.py mediascanmonitor/web/templates/reset_password.html mediascanmonitor/web/templates/login.html tests/web/test_auth_routes.py
git commit -m "$(cat <<'EOF'
feat(auth): login-page "Forgot password?" reset flow

GET /auth/reset-password renders a confirm page; POST regenerates the admin
password the first-run way (0600 /config/initial_password.txt + must_change)
and 303-redirects to /login?reset=1, which shows a retrieval banner. Both
routes are unauthenticated (a locked-out admin has no session); POST is
throttled by a dedicated reset limiter (3/hour/IP) and a write failure leaves
the current password unchanged. The login page gains the link + banner.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: docs — README "Forgot password?" recovery note

**Files:**
- Modify: `README.md`
- (no tests — docs only)

**Interfaces:** none (documentation).

- [ ] **Step 1: Document the web recovery path in `README.md`**

In the **Troubleshooting → "Can't log in / stored secrets won't decrypt"** section, immediately after the existing "Auto-generated password not working" bullet, add:

```markdown
- **Forgot the password** — on the login page click **Forgot password?** → **Reset password**.
  This regenerates the admin password exactly like first run: it is written to
  `/config/initial_password.txt` (mode 0600) and you are forced to change it on the next login.
  Retrieve it on the host with `docker exec media-scan-monitor cat /config/initial_password.txt`.
  The reset is rate-limited (3 per hour per client) and never reveals the password in the
  browser — recovery still requires host access to read the file.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: document the login-page "Forgot password?" reset

Add a Troubleshooting note covering the web reset flow: where the regenerated
password lands (/config/initial_password.txt), the forced change, the per-IP
rate limit, and that recovery still needs host access to read the file.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Done

After Task 3: run the full gate once more —
`uv run ruff format --check . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest`
— expect green. The feature lands on `app-v2`; pushing it triggers the image rebuild + republish of the `app-v2` tag. No `main` merge.
