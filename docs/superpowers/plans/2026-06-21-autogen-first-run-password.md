# Auto-generated First-Run Password — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On first boot with no password configured, generate a strong random admin password, write it to `/config/initial_password.txt` (mode 0600, value never logged), and force the admin to change it on first login.

**Architecture:** Extend `web/auth.py:bootstrap_password` with an auto-generate fallback that also sets a `must_change_password` Setting flag and writes the password file. A new `/account/password` page (GET form + POST) lets the admin change it; the two auth guards in `web/deps.py` funnel a must-change session to that page and 403 the JSON API until the password is rotated. Compose + README updated so auto-generation is the default path.

**Tech Stack:** FastAPI, Starlette `TestClient`, SQLModel `Setting` table, Argon2 (`argon2-cffi`), `secrets.token_urlsafe`, structlog, Jinja2/htmx, pytest. Python 3.14 (PEP 649, `StrEnum`, no `from __future__`).

## Global Constraints

- **Rule 5 — never log secrets.** The generated password is written to a file (mode `0600`) and the **path** is logged, never the value.
- **Auto-generate only when nothing is supplied.** Precedence: existing password (no-op) > `MSM_PASSWORD_FILE` > `MSM_PASSWORD` > auto-generate. `MSM_PASSWORD`/`_FILE` are taken as final — **no** `must_change` flag, **no** file.
- **Flag value convention:** `Setting["must_change_password"] == "1"` means rotation is pending; `"0"` (or absent) means cleared. `Repo` has only `get_setting`/`set_setting` (no delete) — clear by setting `"0"`.
- **Single change-password route:** `/account/password` (GET form + POST). The legacy `POST /auth/password` is moved here; its tests are updated.
- **Allowlist during must-change:** an authenticated must-change session may reach only paths starting with `/account/password` or `/auth/logout`; everything else page-redirects (303) to `/account/password`, and the JSON API returns 403. (`/static/*` is served by a separate StaticFiles mount, not via the guards, so it needs no allowlist entry.)
- **Async/sync bridge:** every `Repo`/auth call from a coroutine goes through `await asyncio.to_thread(...)` (contract §C). The new `auth.py` helpers are sync.
- **Lint/format:** `ruff` select is `E,F,I,UP,B,C4,SIM,RUF`; line length 100; `mypy --strict` clean. Run `ruff format` then `ruff check --fix` then the real gate after pasting code. Commit trailer block on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
  ```
- Branch `app-v2`; **no `main` merge.** No new dependencies (all stdlib/existing).

---

## Task 1: auth.py — generate, auto-gen bootstrap, file, flag helpers

**Files:**
- Modify: `mediascanmonitor/web/auth.py`
- Test: `tests/web/test_auth.py`

**Interfaces:**
- Consumes: existing `is_password_set`, `set_password`, `check_password`, `_read_secret_file`, `Repo`.
- Produces (used by Tasks 2 & 3):
  - `MUST_CHANGE_KEY: str = "must_change_password"`
  - `generate_password() -> str`
  - `is_must_change(repo: Repo) -> bool`
  - `clear_initial_password(repo: Repo, path: Path) -> None`
  - `_resolve_initial_password_path() -> Path`
  - `bootstrap_password(repo: Repo, *, initial_password_path: Path | None = None) -> None` (extended)

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_auth.py` (it already imports `auth` and `repo`; add `from pathlib import Path` and `import structlog` if not present):

```python
def test_generate_password_is_strong_and_unique() -> None:
    a = auth.generate_password()
    b = auth.generate_password()
    assert a != b
    assert len(a) >= 20
    assert a.strip() == a  # url-safe, no surrounding whitespace


def test_bootstrap_autogenerates_when_no_env(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MSM_PASSWORD", "MSM_PASSWORD_FILE", "MSM_INITIAL_PASSWORD_FILE", "MSM_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    pw_file = tmp_path / "initial_password.txt"

    with structlog.testing.capture_logs() as logs:
        auth.bootstrap_password(repo, initial_password_path=pw_file)

    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)
    # the file holds the live password, restricted to the owner
    assert pw_file.exists()
    assert (pw_file.stat().st_mode & 0o777) == 0o600
    generated = pw_file.read_text(encoding="utf-8").strip()
    assert auth.check_password(repo, generated)
    # rule 5: the path is logged, the value never is
    events = [e for e in logs if e.get("event") == "auth.bootstrap.generated"]
    assert events and events[0]["path"] == str(pw_file)
    assert all(generated not in str(e) for e in logs)


def test_bootstrap_env_var_skips_autogen(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    monkeypatch.setenv("MSM_PASSWORD", "chosen-pw")
    pw_file = tmp_path / "initial_password.txt"
    auth.bootstrap_password(repo, initial_password_path=pw_file)
    assert auth.check_password(repo, "chosen-pw")
    assert not auth.is_must_change(repo)
    assert not pw_file.exists()  # env-supplied password writes no file


def test_clear_initial_password_clears_flag_and_deletes_file(repo: Repo, tmp_path: Path) -> None:
    repo.set_setting(auth.MUST_CHANGE_KEY, "1")
    pw_file = tmp_path / "initial_password.txt"
    pw_file.write_text("x\n", encoding="utf-8")
    auth.clear_initial_password(repo, pw_file)
    assert not auth.is_must_change(repo)
    assert not pw_file.exists()


def test_clear_initial_password_tolerates_missing_file(repo: Repo, tmp_path: Path) -> None:
    repo.set_setting(auth.MUST_CHANGE_KEY, "1")
    auth.clear_initial_password(repo, tmp_path / "nope.txt")  # must not raise
    assert not auth.is_must_change(repo)
```

Also **update the two existing tests** that asserted the old "do nothing without env" behavior. Replace `test_bootstrap_does_nothing_without_env` and `test_bootstrap_ignores_empty_values` with auto-gen assertions (the new behavior). Find them (around lines 86–100) and rewrite:

```python
def test_bootstrap_no_env_now_autogenerates(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("MSM_PASSWORD", "MSM_PASSWORD_FILE", "MSM_INITIAL_PASSWORD_FILE", "MSM_DB_PATH"):
        monkeypatch.delenv(var, raising=False)
    auth.bootstrap_password(repo, initial_password_path=tmp_path / "initial_password.txt")
    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)


def test_bootstrap_empty_env_values_autogenerate(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MSM_PASSWORD", "   ")  # whitespace-only → treated as unset
    monkeypatch.delenv("MSM_PASSWORD_FILE", raising=False)
    auth.bootstrap_password(repo, initial_password_path=tmp_path / "initial_password.txt")
    assert auth.is_password_set(repo)
    assert auth.is_must_change(repo)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: the new tests FAIL (`AttributeError: module ... has no attribute 'generate_password'` / `is_must_change` / `clear_initial_password`).

- [ ] **Step 3: Implement in `mediascanmonitor/web/auth.py`**

Add to the imports at the top (alongside the existing `import asyncio`, `import os`, `from pathlib import Path`):

```python
import contextlib
import secrets

import structlog
```

Add after the existing module-level constants (`PASSWORD_HASH_KEY = "password_hash"` / `_hasher = PasswordHasher()`):

```python
MUST_CHANGE_KEY = "must_change_password"

log = structlog.get_logger("web.auth")


def generate_password() -> str:
    """A strong, URL-safe random password (~24 chars) for first-run bootstrap."""
    return secrets.token_urlsafe(18)


def is_must_change(repo: Repo) -> bool:
    """True while the auto-generated bootstrap password still needs rotating."""
    return repo.get_setting(MUST_CHANGE_KEY) == "1"


def _resolve_initial_password_path() -> Path:
    """Where the auto-generated password file lives.

    Precedence: ``MSM_INITIAL_PASSWORD_FILE`` > ``<dir of MSM_DB_PATH>/initial_password.txt``
    > ``/config/initial_password.txt``.
    """
    explicit = os.environ.get("MSM_INITIAL_PASSWORD_FILE")
    if explicit:
        return Path(explicit)
    db_path = os.environ.get("MSM_DB_PATH")
    if db_path:
        return Path(db_path).parent / "initial_password.txt"
    return Path("/config/initial_password.txt")


def _write_initial_password_file(path: Path, value: str) -> None:
    """Write ``value`` to ``path`` owner-only (0600), creating the parent dir if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    path.chmod(0o600)  # enforce 0600 even if the file pre-existed with looser perms


def clear_initial_password(repo: Repo, path: Path) -> None:
    """Clear the must-change flag and best-effort delete the generated-password file."""
    repo.set_setting(MUST_CHANGE_KEY, "0")
    with contextlib.suppress(OSError):
        path.unlink()
```

Now **replace** the existing `bootstrap_password` with:

```python
def bootstrap_password(repo: Repo, *, initial_password_path: Path | None = None) -> None:
    """Seed a first-run password (idempotent; runs at every web startup).

    Precedence: an existing password (no-op) > ``MSM_PASSWORD_FILE`` (path; contents
    whitespace-stripped) > ``MSM_PASSWORD`` > auto-generate. Env-supplied passwords are
    taken as final. When nothing is supplied, generate a strong random password, set the
    ``must_change_password`` flag, and write the value to ``initial_password_path`` (default
    ``_resolve_initial_password_path()``) at mode 0600. NEVER logs the value (rule 5).
    """
    if is_password_set(repo):
        return
    value = ""
    file_path = os.environ.get("MSM_PASSWORD_FILE")
    if file_path:
        try:
            value = _read_secret_file(file_path)
        except OSError:
            value = ""
    if not value:
        value = (os.environ.get("MSM_PASSWORD") or "").strip()
    if value:
        set_password(repo, value)
        return
    # Nothing supplied → auto-generate and force a change on first login.
    generated = generate_password()
    set_password(repo, generated)
    repo.set_setting(MUST_CHANGE_KEY, "1")
    target = (
        initial_password_path
        if initial_password_path is not None
        else _resolve_initial_password_path()
    )
    _write_initial_password_file(target, generated)
    log.info("auth.bootstrap.generated", path=str(target))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_auth.py -v`
Expected: PASS (all, including the rewritten ones).

Then the gate on the touched file:
Run: `uv run ruff format mediascanmonitor/web/auth.py tests/web/test_auth.py && uv run ruff check mediascanmonitor/web/auth.py tests/web/test_auth.py && uv run mypy mediascanmonitor`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/web/auth.py tests/web/test_auth.py
git commit -m "$(cat <<'EOF'
feat(auth): auto-generate first-run password (flag + 0600 file)

bootstrap_password now generates a strong random password when neither
MSM_PASSWORD nor MSM_PASSWORD_FILE is set: stores the Argon2 hash, sets the
must_change_password flag, and writes the value to a 0600 file (path logged,
value never — rule 5). Env-supplied passwords stay final. Adds
generate_password / is_must_change / clear_initial_password helpers.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
EOF
)"
```

---

## Task 2: change-password page (`/account/password`) + Settings link

**Files:**
- Create: `mediascanmonitor/web/templates/change_password.html`
- Modify: `mediascanmonitor/web/auth.py` (replace `POST /auth/password` with `GET`+`POST /account/password`)
- Modify: `mediascanmonitor/web/templates/settings.html` (add a link)
- Test: `tests/web/test_auth_routes.py`

**Interfaces:**
- Consumes (Task 1): `is_must_change`, `clear_initial_password`, `_resolve_initial_password_path`, plus existing `check_password`, `set_password`, `require_page_auth`, `get_repo`, `get_templates`.
- Produces: routes `GET /account/password`, `POST /account/password`.

- [ ] **Step 1: Write the failing tests**

In `tests/web/test_auth_routes.py`, **replace** `test_change_password_success` and `test_change_password_wrong_current_is_rejected` (they POST to the old `/auth/password`) and add new cases. The `auth_client` fixture logs in with password `"pw"` (see conftest), so `current_password` is `"pw"`:

```python
def test_get_change_password_renders_form(auth_client: TestClient) -> None:
    resp = auth_client.get("/account/password")
    assert resp.status_code == 200
    assert "Change password" in resp.text


def test_change_password_success(auth_client: TestClient, repo: Repo) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "pw",
            "new_password": "brand-new-pw",
            "confirm_password": "brand-new-pw",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    from mediascanmonitor.web.auth import check_password

    assert check_password(repo, "brand-new-pw")


def test_change_password_wrong_current_rerenders_form(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "wrong",
            "new_password": "x",
            "confirm_password": "x",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "Change password" in resp.text  # the change-password template, not login.html
    assert "Current password is incorrect." in resp.text


def test_change_password_confirm_mismatch_rejected(auth_client: TestClient) -> None:
    resp = auth_client.post(
        "/account/password",
        data={
            "current_password": "pw",
            "new_password": "aaa",
            "confirm_password": "bbb",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "do not match" in resp.text


def test_change_password_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/account/password",
        data={"current_password": "pw", "new_password": "x", "confirm_password": "x"},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # redirected by require_page_auth (not authed)


def test_change_password_clears_must_change_and_deletes_file(
    app, repo: Repo, tmp_path, monkeypatch  # type: ignore[no-untyped-def]
) -> None:
    from mediascanmonitor.web.auth import MUST_CHANGE_KEY, set_password

    pw_file = tmp_path / "initial_password.txt"
    pw_file.write_text("pw\n", encoding="utf-8")
    monkeypatch.setenv("MSM_INITIAL_PASSWORD_FILE", str(pw_file))
    set_password(repo, "pw")
    repo.set_setting(MUST_CHANGE_KEY, "1")

    client = TestClient(app)
    client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    resp = client.post(
        "/account/password",
        data={"current_password": "pw", "new_password": "rotated", "confirm_password": "rotated"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert repo.get_setting(MUST_CHANGE_KEY) == "0"
    assert not pw_file.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_auth_routes.py -v`
Expected: the `/account/password` tests FAIL with 404 (route not defined yet).

- [ ] **Step 3: Create the template `mediascanmonitor/web/templates/change_password.html`**

```html
{% extends "base.html" %}
{% block title %}Change password — media-scan-monitor{% endblock %}
{% block content %}
{% if not must_change %}{% include "_nav.html" %}{% endif %}
<h1>Change password</h1>
{% if must_change %}
<p class="alert" role="alert">You must change the auto-generated password before continuing.</p>
{% endif %}
{% if error %}<p class="alert" role="alert">{{ error }}</p>{% endif %}
<form method="post" action="/account/password" class="card">
  <label>Current password
    <input type="password" name="current_password" required autocomplete="current-password">
  </label>
  <label>New password
    <input type="password" name="new_password" required autocomplete="new-password">
  </label>
  <label>Confirm new password
    <input type="password" name="confirm_password" required autocomplete="new-password">
  </label>
  <button type="submit">Change password</button>
</form>
{% endblock %}
```

- [ ] **Step 4: Replace the route in `mediascanmonitor/web/auth.py`**

Delete the existing `@router.post("/auth/password", ...)` `change_password` handler entirely and put in its place:

```python
@router.get("/account/password", dependencies=[Depends(require_page_auth)])
async def change_password_page(
    request: Request,
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    must_change = await asyncio.to_thread(is_must_change, repo)
    return templates.TemplateResponse(
        request, "change_password.html", {"error": None, "must_change": must_change}
    )


@router.post("/account/password", dependencies=[Depends(require_page_auth)])
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    repo: Repo = Depends(get_repo),
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    must_change = await asyncio.to_thread(is_must_change, repo)

    def _render_error(message: str) -> Response:
        return templates.TemplateResponse(
            request,
            "change_password.html",
            {"error": message, "must_change": must_change},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not await asyncio.to_thread(check_password, repo, current_password):
        return _render_error("Current password is incorrect.")
    if not new_password.strip():
        return _render_error("New password must not be empty.")
    if new_password != confirm_password:
        return _render_error("New password and confirmation do not match.")
    # no rebuild: auth is not engine config
    await asyncio.to_thread(set_password, repo, new_password)
    await asyncio.to_thread(clear_initial_password, repo, _resolve_initial_password_path())
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
```

- [ ] **Step 5: Add the Settings link in `mediascanmonitor/web/templates/settings.html`**

Insert this `<section>` after the closing `</section>` of the "Status" card (before `{% endblock %}`):

```html
<section class="card">
  <h2>Account</h2>
  <p><a href="/account/password">Change password</a></p>
</section>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_auth_routes.py -v`
Expected: PASS.

Gate:
Run: `uv run ruff format mediascanmonitor/web/auth.py tests/web/test_auth_routes.py && uv run ruff check mediascanmonitor/web/auth.py tests/web/test_auth_routes.py && uv run mypy mediascanmonitor`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/auth.py mediascanmonitor/web/templates/change_password.html mediascanmonitor/web/templates/settings.html tests/web/test_auth_routes.py
git commit -m "$(cat <<'EOF'
feat(auth): /account/password change page (current+new+confirm)

Move the change-password handler from POST /auth/password to a single
/account/password route with a GET form (change_password.html) and POST
submit. Wrong-current / empty / mismatch re-render the change-password
template (fixes the deferred Phase 3 bug that re-rendered login.html). On
success, clear the must_change flag and delete the generated-password file.
Add a Settings link.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
EOF
)"
```

---

## Task 3: deps.py — must-change gating in both guards

**Files:**
- Modify: `mediascanmonitor/web/deps.py`
- Test: `tests/web/test_deps.py`

**Interfaces:**
- Consumes (Task 1): `auth.is_must_change`. Existing: `auth.is_password_set`, `_is_authed`, `get_repo`.
- Produces: gating behavior (no new public symbols).

- [ ] **Step 1: Write the failing tests**

Add to `tests/web/test_deps.py` (import `TestClient` and `set_password` as the file needs; mirror conftest usage):

```python
from starlette.testclient import TestClient

from mediascanmonitor.web.auth import MUST_CHANGE_KEY, set_password


def _must_change_client(app, repo) -> TestClient:  # type: ignore[no-untyped-def]
    set_password(repo, "pw")
    repo.set_setting(MUST_CHANGE_KEY, "1")
    client = TestClient(app)
    resp = client.post("/auth/login", data={"password": "pw"}, follow_redirects=False)
    assert resp.status_code == 303
    return client


def test_must_change_redirects_pages_to_account_password(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/account/password"


def test_must_change_allows_the_change_page_itself(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/account/password", follow_redirects=False)
    assert resp.status_code == 200  # not redirected — it is on the allowlist


def test_must_change_allows_logout(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.post("/auth/logout", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"  # logout, not the change page


def test_must_change_returns_403_on_api(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    resp = client.get("/api/status")
    assert resp.status_code == 403


def test_cleared_flag_restores_normal_access(app, repo) -> None:  # type: ignore[no-untyped-def]
    client = _must_change_client(app, repo)
    repo.set_setting(MUST_CHANGE_KEY, "0")
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/web/test_deps.py -v`
Expected: the new tests FAIL (`/` returns 200 today, not 303; `/api/status` returns 200, not 403).

- [ ] **Step 3: Implement the gating in `mediascanmonitor/web/deps.py`**

Add a module-level constant after the imports:

```python
# Paths an authenticated but must-change session may still reach (everything else
# page-redirects to /account/password until the bootstrap password is rotated).
_MUST_CHANGE_ALLOWLIST = ("/account/password", "/auth/logout")
```

**Replace** `require_api_auth` and `require_page_auth` with:

```python
async def require_api_auth(request: Request) -> None:
    repo = get_repo(request)
    if _is_authed(request):
        if await asyncio.to_thread(auth.is_must_change, repo):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="password change required"
            )
        return
    if not await asyncio.to_thread(auth.is_password_set, repo):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="setup required")
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")


async def require_page_auth(request: Request) -> None:
    repo = get_repo(request)
    if _is_authed(request):
        if await asyncio.to_thread(auth.is_must_change, repo):
            path = request.url.path
            if not any(path.startswith(prefix) for prefix in _MUST_CHANGE_ALLOWLIST):
                raise HTTPException(
                    status_code=status.HTTP_303_SEE_OTHER,
                    headers={"Location": "/account/password"},
                )
        return
    location = "/login"
    if not await asyncio.to_thread(auth.is_password_set, repo):
        location = "/setup"
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": location},
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_deps.py -v`
Expected: PASS.

Then the full web suite (the guards touch every route — confirm no regressions):
Run: `uv run pytest tests/web/ -q`
Expected: all pass.

Gate:
Run: `uv run ruff format mediascanmonitor/web/deps.py tests/web/test_deps.py && uv run ruff check mediascanmonitor/web/deps.py tests/web/test_deps.py && uv run mypy mediascanmonitor`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/web/deps.py tests/web/test_deps.py
git commit -m "$(cat <<'EOF'
feat(auth): gate the app until the bootstrap password is rotated

While must_change_password is set, require_page_auth 303-redirects an
authenticated session to /account/password for everything except that page
and /auth/logout, and require_api_auth returns 403. Clearing the flag (on a
successful change) restores normal access.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
EOF
)"
```

---

## Task 4: docs — make the compose secret optional + README

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`
- (no tests — docs/config only; validate with `docker compose config`)

**Interfaces:** none (documentation).

- [ ] **Step 1: Make the password secret optional in `docker-compose.yml`**

The service currently has `MSM_PASSWORD_FILE: "/run/secrets/msm_password"` in `environment:`, a `secrets:` reference under the service, and a top-level `secrets:` block reading `./msm_password.txt`. Because a Docker secret file must exist, this makes `./msm_password.txt` **mandatory**. Auto-generation should be the default, so comment these out.

Replace the `MSM_PASSWORD_FILE: "/run/secrets/msm_password"` line in `environment:` with:

```yaml
      # Password bootstrap is OPTIONAL. By default (nothing set here) the app
      # generates a strong random admin password on first boot, writes it to
      # /config/initial_password.txt (mode 0600), and forces you to change it
      # on first login — see the README "First login" section.
      # To preset a password instead, uncomment the secret below (and the
      # service `secrets:` + top-level `secrets:` blocks) and create
      # ./msm_password.txt:
      # MSM_PASSWORD_FILE: "/run/secrets/msm_password"
```

Comment out the service-level `secrets:` reference (the `- msm_password` entry and its `secrets:` key):

```yaml
    # secrets:
    #   - msm_password
```

Comment out the top-level `secrets:` block at the bottom of the file:

```yaml
# secrets:
#   msm_password:
#     file: ./msm_password.txt
```

(Keep the existing explanatory comments around these blocks; just ensure the active default no longer requires the file.)

- [ ] **Step 2: Validate the compose still resolves**

Run: `docker compose config -q && docker compose --profile init-watches config -q`
Expected: exit 0 (no secret file required now). (If `docker` is unavailable locally, CI's compose-lint covers it.)

- [ ] **Step 3: Update `README.md`**

Make three edits:

(a) In the Quickstart, the step that tells the user to create `./msm_password.txt` is now **optional**. Reword it to:

```markdown
   - **First-run password (optional)** — by default the app generates a strong random admin
     password on first boot; you do not need to create anything. To preset your own instead,
     uncomment the `secrets:` blocks in `docker-compose.yml` and create `./msm_password.txt`
     (`echo 'your-strong-password' > ./msm_password.txt && chmod 600 ./msm_password.txt`).
```

(b) Add a **"First login"** subsection right after the "Open the UI and log in" step:

```markdown
### First login (auto-generated password)

If you did not preset a password, the app generated one on first boot and wrote it to
`/config/initial_password.txt` (owner-readable only). Retrieve it:

```bash
docker exec media-scan-monitor cat /config/initial_password.txt
# or, via the bind mount:  cat ./config/initial_password.txt
```

Log in with that password at `http://<host>:8080`. You will be **required to change it**
before you can use the app; once you do, the file is deleted automatically.
```

(c) In the environment-variable reference table, add a row and tighten the password rows:

```markdown
| `MSM_INITIAL_PASSWORD_FILE` | `/config/initial_password.txt` | Where the auto-generated first-run password is written (mode 0600). Defaults to the directory of `MSM_DB_PATH`. |
```

And append to the `MSM_PASSWORD_FILE` / `MSM_PASSWORD` descriptions: "If either is set, no password is generated and no forced change occurs."

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml README.md
git commit -m "$(cat <<'EOF'
docs: default to auto-generated password; make compose secret optional

The compose password secret is now opt-in (commented out) — by default the
app auto-generates the first-run password, so `docker compose up` works with
no msm_password.txt. README documents retrieving /config/initial_password.txt
and the forced first-login change, and adds MSM_INITIAL_PASSWORD_FILE.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016PcFMagbvJJEdNz1QWUEKU
EOF
)"
```

---

## Done

After Task 4: run the full gate once more —
`uv run ruff format --check . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest`
— expect green. The feature lands on `app-v2`; pushing it triggers the image rebuild + republish of the `app-v2` tag (so the NAS can pull the new behavior). No `main` merge.
