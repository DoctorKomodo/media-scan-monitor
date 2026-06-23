# Consolidated Server Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the server detail page's two save buttons ("Save changes" + "Save folders") with one "Save changes" that persists server settings **and** the whole folder list atomically, with Save + Delete moved to the page bottom.

**Architecture:** Mirror the existing server-*create* flow on the *update* flow at every layer. The detail page becomes one `<form>` (settings + folder editor) posting to `POST /ui/servers/{id}/update`; that handler parses both halves and calls a new atomic write-core `apply_server_update_with_folders`, backed by a new repo method `update_server_with_folders` that updates fields and replaces folders in one transaction. Folder-persistence logic is centralized in a shared `_set_server_folders` helper so create and update can't drift. The now-orphaned `/ui/.../folders` route and its `apply_folders_sync`/`replace_folders` plumbing are removed.

**Tech Stack:** Python 3.14, FastAPI + Starlette, SQLModel/SQLAlchemy, Pydantic, Jinja2 + htmx, pytest (+ pytest-asyncio), ruff, mypy --strict.

**Design spec:** `docs/superpowers/specs/2026-06-23-consolidated-server-save-design.md`

## Global Constraints

Copied verbatim from `CLAUDE.md` / the spec — every task's requirements implicitly include these:

- **PEP 649 annotations:** never add `from __future__ import annotations`; leave forward refs unquoted.
- **Enums:** subclass `StrEnum`, never `(str, Enum)` (not relevant to new code here, but don't regress).
- **Typing:** full type hints; `mypy --strict` must be clean. Validate every external boundary with Pydantic/SQLModel — no raw dicts across boundaries.
- **Lint/format:** `ruff` select set is exactly `E, F, I, UP, B, C4, SIM, RUF` (per-file-ignore: `B` under `tests/**`). First-party import is `mediascanmonitor` (blank line between third-party and first-party). After editing, run `ruff format` then `ruff check --fix` on touched files before the gate. Do **not** add `# noqa` for unselected rules.
- **Atomic writes + single rebuild:** each UI mutation does one transactional repo write then exactly one `rebuild_engine`. All-or-nothing: a rejected save persists nothing.
- **Async discipline:** sync repo calls run via `asyncio.to_thread`.
- **Gate:** `ruff check .` + `ruff format --check .` + `mypy mediascanmonitor` + `pytest` must all be green before a task is done.
- **Commit footer:** every commit message ends with these two lines:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
  ```
- **Tooling note:** the project venv may have a stale interpreter path; run tools via `./.venv/bin/<tool>` (rebuild with `uv sync --extra dev` if missing). Tests are async — `pytest-asyncio` is configured; write `async def test_*` without decorators (the repo already does).

## File Structure

This change keeps each layer's responsibility intact and adds parallel-twin functions:

- `mediascanmonitor/db/repo.py` — add module-level `_set_server_folders(server, folders)` helper + `Repo.update_server_with_folders(...)`; route `create_server_with_folders` through the helper; (Task 4) remove `replace_folders`.
- `mediascanmonitor/web/writes.py` — add `apply_server_update_with_folders(...)`; (Task 4) remove `apply_folders_sync`; extend module docstring (parallel-twin note).
- `mediascanmonitor/web/pages.py` — rewire `ui_update_server` to parse folders + call the new core; (Task 4) remove `ui_sync_folders` route + its import.
- `mediascanmonitor/web/templates/server_detail.html` — one form (settings + folders) + bottom Save/Delete, mirroring `server_new.html`.
- Tests: `tests/db/test_repo.py`, `tests/web/test_writes.py`, `tests/web/test_ui_forms.py`, `tests/web/test_pages.py`.

**Disjoint from PR #7** (`feat/folder-extension-presets`, which touches `app.py`, `app.css`, `_folder_editor.html`, `_folder_rows_script.html`, docs) — no shared files, so the two branches compose without conflict. This plan reuses the existing `.form-actions`/`.danger-zone`/`.form-status` CSS, so **no `app.css` change**.

**Task ordering keeps the app working after every task:** Tasks 1–2 are purely additive; Task 3 rewires the handler + template together (the one coupled change); Task 4 deletes the now-orphaned old path. Removals are interdependent (`replace_folders` ← `apply_folders_sync` ← `ui_sync_folders` ← template), so they all happen last, in Task 4.

---

### Task 1: Repo — shared folder helper + `update_server_with_folders`

**Files:**
- Modify: `mediascanmonitor/db/repo.py` (add helper near top; add method after `update_server` ~line 127; refactor `create_server_with_folders` ~lines 101-109)
- Test: `tests/db/test_repo.py`

**Interfaces:**
- Consumes: `Server`, `Folder`, `FileType` (already imported in repo.py), `FolderCreate`, `ServerUpdate` (already imported).
- Produces:
  - `_set_server_folders(server: Server, folders: list[FolderCreate]) -> None` (module-level private)
  - `Repo.update_server_with_folders(server_id: int, data: ServerUpdate, folders: list[FolderCreate]) -> Server` — updates fields (secret tri-state) + replaces folders in one transaction; raises `KeyError` if the server is gone.

- [ ] **Step 1: Write the failing tests**

Add to `tests/db/test_repo.py` (the file already imports `pytest`, `FileType`, `Folder`, `ServerType`, `Repo`, `FolderCreate`, `ServerCreate`, `ServerUpdate`, and defines `make_server`). Append after `test_replace_folders_unknown_server_raises`:

```python
def test_update_server_with_folders_changes_fields_and_swaps_folders(repo: Repo) -> None:
    server = repo.create_server_with_folders(
        make_server(name="combo"), [FolderCreate(path="/old", extensions=["avi"])]
    )
    assert server.id is not None
    updated = repo.update_server_with_folders(
        server.id,
        ServerUpdate(enabled=False),
        [
            FolderCreate(path="/data/tv", extensions=["mkv", "MP4"]),
            FolderCreate(path="/data/movies", extensions=["mkv"]),
        ],
    )
    assert updated.enabled is False
    folders = repo.list_folders(server.id)
    assert {f.path for f in folders} == {"/data/tv", "/data/movies"}  # /old replaced wholesale
    tv = next(f for f in folders if f.path == "/data/tv")
    assert sorted(ft.extension for ft in tv.filetypes) == ["mkv", "mp4"]  # normalized + deduped


def test_update_server_with_folders_empty_clears_all(repo: Repo) -> None:
    server = repo.create_server_with_folders(
        make_server(name="clear2"), [FolderCreate(path="/x", extensions=["mkv"])]
    )
    assert server.id is not None
    repo.update_server_with_folders(server.id, ServerUpdate(), [])
    assert repo.list_folders(server.id) == []


def test_update_server_with_folders_unknown_server_raises(repo: Repo) -> None:
    with pytest.raises(KeyError):
        repo.update_server_with_folders(
            9999, ServerUpdate(), [FolderCreate(path="/data/tv", extensions=["mkv"])]
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/pytest tests/db/test_repo.py -k update_server_with_folders -v`
Expected: FAIL — `AttributeError: 'Repo' object has no attribute 'update_server_with_folders'`.

- [ ] **Step 3: Add the shared helper**

In `mediascanmonitor/db/repo.py`, add this module-level function just above `class Repo:` (after the imports):

```python
def _set_server_folders(server: Server, folders: list[FolderCreate]) -> None:
    """Replace ``server.folders`` with fresh rows built from ``folders``.

    Shared by create_server_with_folders and update_server_with_folders so the two flows
    persist folders identically (anti-drift). ``clear()`` is the delete-orphan-safe idiom
    (it also drops the old filetypes); appended rows are always brand-new instances. The
    schema validators already normalized/deduped each FolderCreate, so they're stored as-is.
    For a freshly built server ``server.folders`` is empty, so the leading clear() is a no-op.
    """
    server.folders.clear()
    for data in folders:
        folder = Folder(path=data.path, library_id=data.library_id, enabled=data.enabled)
        for ext in data.extensions:
            folder.filetypes.append(FileType(extension=ext))
        server.folders.append(folder)
```

- [ ] **Step 4: Route `create_server_with_folders` through the helper**

In `create_server_with_folders`, replace its inline folder loop (currently the `for folder_data in folders:` block, ~lines 101-109) so the method body's tail reads:

```python
            )
            _set_server_folders(server, folders)
            session.add(server)
            session.commit()
            return server
```

(The `Server(...)` construction above it is unchanged; only the inline `for folder_data ...` loop is replaced by the single `_set_server_folders(server, folders)` call.)

- [ ] **Step 5: Add `update_server_with_folders`**

In `mediascanmonitor/db/repo.py`, add this method immediately after `update_server` (before `delete_server`):

```python
    def update_server_with_folders(
        self, server_id: int, data: ServerUpdate, folders: list[FolderCreate]
    ) -> Server:
        """Update a server's fields AND replace its whole folder set in ONE transaction.

        Combines update_server's field/secret tri-state with _set_server_folders so the detail
        page's single "Save changes" persists both atomically (all-or-nothing) and the caller
        rebuilds once. An empty ``folders`` clears them. Raises KeyError if the server is gone.
        Mirror of create_server_with_folders.
        """
        with self._session_factory() as session:
            server = session.get(Server, server_id)
            if server is None:
                raise KeyError(f"server {server_id} not found")
            fields = data.model_dump(exclude_unset=True)
            if "secret" in fields:
                secret = fields.pop("secret")
                server.secret_encrypted = self._box.encrypt(secret) if secret is not None else None
            for key, value in fields.items():
                setattr(server, key, value)
            _set_server_folders(server, folders)
            session.add(server)
            session.commit()
            return server
```

- [ ] **Step 6: Run the new tests + the existing repo tests to verify all pass**

Run: `./.venv/bin/pytest tests/db/test_repo.py -v`
Expected: PASS — the 3 new tests pass and every pre-existing repo test (incl. `test_create_server_with_folders_persists_both` and the `replace_folders` tests, untouched this task) still passes.

- [ ] **Step 7: Lint, type-check, commit**

Run:
```bash
./.venv/bin/ruff format mediascanmonitor/db/repo.py tests/db/test_repo.py
./.venv/bin/ruff check --fix mediascanmonitor/db/repo.py tests/db/test_repo.py
./.venv/bin/mypy mediascanmonitor
```
Expected: ruff clean, mypy "Success".

```bash
git add mediascanmonitor/db/repo.py tests/db/test_repo.py
git commit -m "$(cat <<'EOF'
feat(db): add update_server_with_folders + shared _set_server_folders

Atomic single-transaction update of server fields + wholesale folder replace,
mirroring create_server_with_folders. Folder-build loop is centralized in a
shared _set_server_folders helper (create now routes through it too) so the
create and update flows can't drift.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
EOF
)"
```

---

### Task 2: Write core — `apply_server_update_with_folders`

**Files:**
- Modify: `mediascanmonitor/web/writes.py` (add core after `apply_server_update` ~line 88; extend module docstring)
- Test: `tests/web/test_writes.py`

**Interfaces:**
- Consumes: `Repo.update_server_with_folders` (Task 1); `_require_secret_or_422`, `_name_conflict`, `rebuild_engine`, `IntegrityError`, `ServerUpdate`, `FolderCreate`, `Server`, `Engine`, `Repo` (all already imported in writes.py).
- Produces: `apply_server_update_with_folders(repo: Repo, engine: Engine, server_id: int, data: ServerUpdate, folders: list[FolderCreate]) -> Server` — secret gate → atomic write (`IntegrityError`→409) → one rebuild; raises `KeyError` if gone, `HTTPException` 422 (secret) / 409 (dup name).

- [ ] **Step 1: Write the failing tests**

In `tests/web/test_writes.py`, add `apply_server_update_with_folders` to the `from mediascanmonitor.web.writes import (...)` block (keep it alphabetical-ish; ruff will fix ordering). Then append these tests:

```python
async def test_update_with_folders_persists_fields_and_folders_one_rebuild(
    repo: Repo, engine: Engine
) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="hook", type=ServerType.webhook)
    )
    assert server.id is not None
    before = engine.rebuild_calls  # type: ignore[attr-defined]
    updated = await apply_server_update_with_folders(
        repo,
        engine,
        server.id,
        ServerUpdate(enabled=False),
        [FolderCreate(path="/data/tv", extensions=["mkv"])],
    )
    assert updated.enabled is False
    assert {f.path for f in repo.list_folders(server.id)} == {"/data/tv"}
    assert engine.rebuild_calls == before + 1  # type: ignore[attr-defined]


async def test_update_with_folders_secret_gate_enforced(repo: Repo, engine: Engine) -> None:
    server = await apply_server_create(
        repo, engine, ServerCreate(name="plex", type=ServerType.plex, secret="tok")
    )
    assert server.id is not None
    with pytest.raises(HTTPException) as exc:
        await apply_server_update_with_folders(
            repo, engine, server.id, ServerUpdate(secret=None), []
        )
    assert exc.value.status_code == 422


async def test_update_with_folders_missing_server_raises_keyerror(
    repo: Repo, engine: Engine
) -> None:
    with pytest.raises(KeyError):
        await apply_server_update_with_folders(repo, engine, 999, ServerUpdate(), [])


async def test_update_with_folders_duplicate_name_raises_409(
    repo: Repo, engine: Engine
) -> None:
    await apply_server_create(repo, engine, ServerCreate(name="alpha", type=ServerType.webhook))
    b = await apply_server_create(
        repo, engine, ServerCreate(name="beta", type=ServerType.webhook)
    )
    assert b.id is not None
    with pytest.raises(HTTPException) as exc:
        await apply_server_update_with_folders(
            repo, engine, b.id, ServerUpdate(name="alpha"), []
        )
    assert exc.value.status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/pytest tests/web/test_writes.py -k update_with_folders -v`
Expected: FAIL — `ImportError: cannot import name 'apply_server_update_with_folders'`.

- [ ] **Step 3: Implement the write core**

In `mediascanmonitor/web/writes.py`, add this function immediately after `apply_server_update` (before `apply_server_delete`):

```python
async def apply_server_update_with_folders(
    repo: Repo,
    engine: Engine,
    server_id: int,
    data: ServerUpdate,
    folders: list[FolderCreate],
) -> Server:
    """Update a server and replace its folder set atomically, then rebuild once.

    The detail page's single "Save changes" persists the server fields and the whole folder
    list in one request. Same secret-required gate as apply_server_update; a duplicate-name
    rename raises IntegrityError, translated to a 409 (mirroring apply_server_create_with_folders)
    so the UI shows an inline error instead of a 500. Folders carry no secret. Twin of
    apply_server_create_with_folders — keep the two in lockstep.
    """
    existing = await asyncio.to_thread(repo.get_server, server_id)
    if existing is None:
        raise KeyError(f"server {server_id} not found")
    dumped = data.model_dump(exclude_unset=True)
    resulting_type = data.type if data.type is not None else existing.type
    if "secret" in dumped:
        resulting_has_secret = bool(dumped["secret"])
    else:
        resulting_has_secret = existing.secret_encrypted is not None
    _require_secret_or_422(resulting_type, resulting_has_secret)
    try:
        server = await asyncio.to_thread(
            repo.update_server_with_folders, server_id, data, folders
        )
    except IntegrityError as exc:
        raise _name_conflict(data.name or existing.name) from exc
    await rebuild_engine(engine)
    return server
```

- [ ] **Step 4: Extend the module docstring (anti-drift seam)**

In `mediascanmonitor/web/writes.py`, the module docstring already explains that `/api` and `/ui` share these cores so they "can never drift". Append one sentence to that docstring (inside the existing `"""..."""` at the top of the file):

```
The ``*_with_folders`` create/update pair are parallel twins — change both together so the
add-server and edit-server flows stay aligned.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/bin/pytest tests/web/test_writes.py -v`
Expected: PASS — the 4 new tests pass; all pre-existing write-core tests still pass.

- [ ] **Step 6: Lint, type-check, commit**

Run:
```bash
./.venv/bin/ruff format mediascanmonitor/web/writes.py tests/web/test_writes.py
./.venv/bin/ruff check --fix mediascanmonitor/web/writes.py tests/web/test_writes.py
./.venv/bin/mypy mediascanmonitor
```
Expected: ruff clean, mypy "Success".

```bash
git add mediascanmonitor/web/writes.py tests/web/test_writes.py
git commit -m "$(cat <<'EOF'
feat(web): add apply_server_update_with_folders write-core

Secret gate + atomic update_server_with_folders + one rebuild, twin of
apply_server_create_with_folders. Translates a duplicate-name IntegrityError to
a 409 (the update path previously had no guard and 500'd on rename collisions).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
EOF
)"
```

---

### Task 3: Consolidate the detail save (handler + template)

This is the one coupled change: the handler must parse folders **and** the template must put the folder editor inside the settings form, landing together so the page stays functional.

**Files:**
- Modify: `mediascanmonitor/web/pages.py` (import block ~lines 55-60; `ui_update_server` ~lines 451-506)
- Modify: `mediascanmonitor/web/templates/server_detail.html` (full restructure)
- Test: `tests/web/test_ui_forms.py`, `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `apply_server_update_with_folders` (Task 2); `_parse_folder_rows` (already in pages.py); `_error_partial` (already in pages.py).
- Produces: `POST /ui/servers/{id}/update` now saves settings **and** folders; renders `_saved.html` ("Saved.") into `#save-status`, errors retarget to `#save-error`.

- [ ] **Step 1: Write the failing UI tests**

In `tests/web/test_ui_forms.py` (already imports `httpx`, `respx`, `ServerType`, `FolderCreate`, `ServerCreate`, and defines `_seed_plex`), append:

```python
def test_ui_update_saves_fields_and_folders_together(
    auth_client: httpx.Client,
    repo,
    engine,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    repo.create_folder(sid, FolderCreate(path="/old", extensions=["mkv"]))  # replaced wholesale
    before = engine.rebuild_calls
    resp = auth_client.post(
        f"/ui/servers/{sid}/update",
        data={
            "name": "Plex Renamed",
            "base_url": "http://plex:32400",
            "secret": "",  # blank keeps the stored token
            "scan_mode": "targeted",
            "debounce_mode": "trailing",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
            "verify_tls": "on",
            "enabled": "on",
            "folder-0-path": "/data/tv",
            "folder-0-library_id": "2",
            "folder-0-extensions": "mkv, mp4",
            "folder-0-enabled": "on",
            "folder-1-path": "",  # blank row skipped
            "folder-2-path": "/data/movies",
            "folder-2-extensions": "mkv",
        },
    )
    assert resp.status_code == 200
    assert engine.rebuild_calls == before + 1  # one rebuild for the whole save
    saved = repo.get_server(sid)
    assert saved.name == "Plex Renamed"
    assert saved.secret_encrypted is not None  # blank secret left the token intact
    assert {f.path for f in repo.list_folders(sid)} == {"/data/tv", "/data/movies"}  # /old gone


def test_ui_update_empty_folder_rows_clears_all(
    auth_client: httpx.Client,
    repo,  # type: ignore[no-untyped-def]
) -> None:
    sid = _seed_plex(repo)
    repo.create_folder(sid, FolderCreate(path="/data/tv", extensions=["mkv"]))
    resp = auth_client.post(
        f"/ui/servers/{sid}/update",
        data={
            "name": "Plex",
            "scan_mode": "targeted",
            "debounce_mode": "trailing",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
            "folder-0-path": "",  # all blank → no folders
        },
    )
    assert resp.status_code == 200
    assert repo.list_folders(sid) == []  # an all-blank save is a valid "no folders"


def test_ui_update_missing_server_returns_200_inline_error(
    auth_client: httpx.Client,
) -> None:
    resp = auth_client.post(
        "/ui/servers/9999/update",
        data={
            "name": "ghost",
            "scan_mode": "targeted",
            "debounce_mode": "trailing",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
            "folder-0-path": "/data/tv",
        },
    )
    assert resp.status_code == 200  # softened so htmx swaps the message
    assert resp.headers.get("hx-retarget") == "#save-error"


def test_ui_update_duplicate_name_returns_inline_409(
    auth_client: httpx.Client,
    repo,  # type: ignore[no-untyped-def]
) -> None:
    _seed_plex(repo)  # name "Plex"
    other = repo.create_server(ServerCreate(name="Other", type=ServerType.webhook))
    oid = int(other.id)
    resp = auth_client.post(
        f"/ui/servers/{oid}/update",
        data={
            "name": "Plex",  # collides with the seeded server
            "scan_mode": "library",
            "debounce_mode": "off",
            "debounce_window_seconds": "30",
            "retry_attempts": "1",
            "timeout_seconds": "10",
            "webhook_method": "POST",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("hx-retarget") == "#save-error"
    assert "already exists" in resp.text.lower()
    assert repo.get_server(oid).name == "Other"  # nothing persisted (rolled back)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `./.venv/bin/pytest tests/web/test_ui_forms.py -k "update_saves_fields_and_folders or update_empty_folder_rows or update_missing_server or update_duplicate_name" -v`
Expected: FAIL — `test_ui_update_saves_fields_and_folders_together` fails (folders NOT replaced — old handler ignores folder rows), and the dup-name test 500s / wrong status (no `IntegrityError` handling yet). (`missing_server` may already 200 via the old KeyError path but retarget is `#edit-error`, so it fails the `#save-error` assertion.)

- [ ] **Step 3: Update the `pages.py` import block**

In `mediascanmonitor/web/pages.py`, change the `from mediascanmonitor.web.writes import (...)` block to drop `apply_server_update` and add `apply_server_update_with_folders` (keep `apply_folders_sync` — `ui_sync_folders` still uses it until Task 4):

```python
from mediascanmonitor.web.writes import (
    apply_folders_sync,
    apply_server_create_with_folders,
    apply_server_delete,
    apply_server_update_with_folders,
)
```

- [ ] **Step 4: Rewire `ui_update_server`**

In `mediascanmonitor/web/pages.py`, replace the body of `ui_update_server` (keep the decorator and the full `Form(...)` parameter signature exactly as-is) so it parses folders and calls the combined core. The new body:

```python
    # Build the schema INSIDE the try: enum/validator failures -> ValueError -> inline error.
    # The folder editor now lives in the SAME form (combined save), so parse its rows here and
    # persist settings + folders atomically. apply_server_update_with_folders raises KeyError if
    # the server was deleted concurrently, HTTPException 422 (secret gate) / 409 (duplicate name).
    form = await request.form()
    try:
        # Secret tri-state via exclude_unset: omit when blank (keep), set None when "clear" ticked.
        fields: dict[str, Any] = {
            "name": name,
            "base_url": base_url,
            "scan_mode": ScanMode(scan_mode),
            "debounce_mode": DebounceMode(debounce_mode),
            "debounce_window_seconds": debounce_window_seconds,
            "retry_attempts": retry_attempts,
            "timeout_seconds": timeout_seconds,
            "verify_tls": verify_tls,
            "enabled": enabled,
            "webhook_method": webhook_method or None,
            "webhook_headers_json": webhook_headers_json or None,
            "webhook_body_template": webhook_body_template or None,
        }
        if clear_secret:
            fields["secret"] = None
        elif secret:
            fields["secret"] = secret
        data = ServerUpdate(**fields)
        folders = _parse_folder_rows(form)
        await apply_server_update_with_folders(repo, engine, server_id, data, folders)
    except HTTPException as exc:
        return _error_partial(request, templates, str(exc.detail), "#save-error")
    except (ValueError, KeyError) as exc:
        return _error_partial(request, templates, str(exc), "#save-error")
    return templates.TemplateResponse(
        request=request, name="_saved.html", context={"message": "Saved."}
    )
```

(The `request: Request` parameter already exists on `ui_update_server`, so `await request.form()` works — same dual-read `ui_create_server_with_folders` uses.)

- [ ] **Step 5: Restructure `server_detail.html`**

Replace the entire contents of `mediascanmonitor/web/templates/server_detail.html` with:

```html
{% extends "base.html" %}
{% block title %}{{ server.name }} — media-scan-monitor{% endblock %}
{% block content %}
{% include "_nav.html" %}
<p><a href="/servers">&larr; Servers</a></p>
<h1>{{ server.name }} <span class="badge">{{ server.type }}</span></h1>

<section class="card">
  <h2>Connection test</h2>
  <div class="test-row">
    <button hx-post="/ui/servers/{{ server.id }}/test" hx-target="#test-result" hx-swap="innerHTML">
      Test
    </button>
    <span class="test-hint muted">Sends a no-op request to confirm the URL and token work.</span>
  </div>
  <div id="test-result" class="test-result-slot"></div>
</section>

<section class="card">
  <div id="save-error"></div>
  <form class="settings-form" hx-post="/ui/servers/{{ server.id }}/update"
        hx-target="#save-status" hx-swap="innerHTML">
    {% include "_server_form_fields.html" %}

    <fieldset class="form-section">
      <legend>Folders</legend>
      <p class="field-hint nf-intro">Each folder declares a host path to watch and which file
        types count. Leave a row blank to skip it. Saved together with the settings above.</p>
      {% with folders = server.folders %}{% include "_folder_editor.html" %}{% endwith %}
    </fieldset>

    <div class="form-actions">
      <button type="submit">Save changes</button>
      <span id="save-status" class="form-status"></span>
    </div>
  </form>

  <div class="danger-zone">
    <p>Delete this server and all of its folders. This can't be undone.</p>
    <form hx-post="/ui/servers/{{ server.id }}/delete" hx-target="body" hx-swap="innerHTML"
          hx-confirm="Delete this server and all its folders?">
      <button type="submit" class="ghost-danger">Delete server</button>
    </form>
  </div>
</section>

<script>
  // Token field control: turn the plain input + clear_secret checkbox into a
  // keep / replace / clear affordance. No-JS users keep the input + checkbox.
  (function () {
    const field = document.querySelector(".token-field");
    if (!field || field.dataset.hasSecret !== "true") return;

    // Some nodes are absent when the token can't be cleared (required-secret types):
    // only Replace/Cancel exist then, so every reference below is null-guarded.
    const states = {
      stored: field.querySelector(".token-stored"),
      cleared: field.querySelector(".token-cleared"),
      input: field.querySelector(".token-input"),
    };
    const secret = field.querySelector('input[name="secret"]');
    const cancel = field.querySelector(".token-cancel");
    const clearBox = field.querySelector('input[name="clear_secret"]');
    const fallback = field.querySelector(".token-clear-fallback");

    if (fallback) fallback.hidden = true; // the buttons drive clear_secret now

    function show(state) {
      for (const [name, el] of Object.entries(states)) {
        if (el) el.hidden = name !== state;
      }
    }

    field.addEventListener("click", (event) => {
      const action = event.target.closest("[data-token-action]")?.dataset.tokenAction;
      if (!action) return;
      if (action === "replace") {
        if (clearBox) clearBox.checked = false;
        secret.value = "";
        show("input");
        cancel.hidden = false;
        secret.focus();
      } else if (action === "clear") {
        if (clearBox) clearBox.checked = true;
        secret.value = "";
        show("cleared");
      } else {
        // undo / cancel both return to the stored readout
        if (clearBox) clearBox.checked = false;
        secret.value = "";
        cancel.hidden = true;
        show("stored");
      }
    });

    show("stored"); // enhanced initial state
  })();
</script>
{% include "_folder_rows_script.html" %}
{% endblock %}
```

(Changes vs. the old file: the Edit `<form>` now also wraps the Folders `<fieldset>` and posts to `/update` with `hx-target="#save-status"`; the error slot is `#save-error`; the Folders section's own `<form>`/"Save folders" button is gone; Save + Delete sit at the bottom of the card. The token-field `<script>` and `_folder_rows_script.html` include are unchanged.)

- [ ] **Step 6: Update the page-render test**

In `tests/web/test_pages.py`, in `test_server_detail_shows_folders_and_test_button`, replace the stale comment (lines ~79-80) and the `/folders` assertion (line ~83) so the block reads:

```python
    assert "Test" in resp.text  # Test button present
    # The existing folder is pre-loaded into the unified editor, and settings + folders now
    # save together via ONE form posting to /update (no separate "Save folders" form).
    assert 'value="/data/tv"' in resp.text
    assert "data-folder-editor" in resp.text
    assert f"/ui/servers/{sid}/update" in resp.text  # the one consolidated save form
    assert f"/ui/servers/{sid}/folders" not in resp.text  # separate folder-sync form is gone
    assert "Save changes" in resp.text
    assert "Delete server" in resp.text
```

- [ ] **Step 7: Run the UI + page tests to verify they pass**

Run: `./.venv/bin/pytest tests/web/test_ui_forms.py tests/web/test_pages.py -v`
Expected: PASS — the 4 new UI tests pass; the existing `test_ui_update_server_keeps_secret_when_blank_and_rebuilds` and `test_ui_update_persists_webhook_fields` still pass (they post no folder rows → empty set replaces an already-empty set → no-op); the old `test_ui_sync_folders_*` tests still pass (route not yet removed); the updated page-render test passes.

- [ ] **Step 8: Lint, type-check, commit**

Run:
```bash
./.venv/bin/ruff format mediascanmonitor/web/pages.py tests/web/test_ui_forms.py tests/web/test_pages.py
./.venv/bin/ruff check --fix mediascanmonitor/web/pages.py tests/web/test_ui_forms.py tests/web/test_pages.py
./.venv/bin/mypy mediascanmonitor
```
Expected: ruff clean, mypy "Success".

```bash
git add mediascanmonitor/web/pages.py mediascanmonitor/web/templates/server_detail.html tests/web/test_ui_forms.py tests/web/test_pages.py
git commit -m "$(cat <<'EOF'
feat(web): one "Save changes" saves server settings + folders together

The detail page is now a single form (settings + folder editor) posting to
/update; the handler parses both halves and persists them atomically via
apply_server_update_with_folders (one rebuild). Save + Delete move to the page
bottom; the separate "Save folders" form is gone. A duplicate-name rename now
returns an inline 409 instead of a 500.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
EOF
)"
```

---

### Task 4: Remove the orphaned folder-sync path

Now nothing references `ui_sync_folders` / `apply_folders_sync` / `replace_folders`, so delete them and their now-duplicate tests.

**Files:**
- Modify: `mediascanmonitor/web/pages.py` (remove `ui_sync_folders` route ~lines 525-545; remove `apply_folders_sync` from the import block)
- Modify: `mediascanmonitor/web/writes.py` (remove `apply_folders_sync` ~lines 111-120)
- Modify: `mediascanmonitor/db/repo.py` (remove `replace_folders` ~lines 154-175)
- Test: `tests/web/test_ui_forms.py` (remove 3 `test_ui_sync_folders_*`), `tests/db/test_repo.py` (remove 3 `test_replace_folders_*`)

**Interfaces:**
- Consumes: nothing new.
- Produces: removes `Repo.replace_folders`, `writes.apply_folders_sync`, route `POST /ui/servers/{id}/folders`.

- [ ] **Step 1: Remove the orphaned tests**

In `tests/web/test_ui_forms.py`, delete the three functions `test_ui_sync_folders_replaces_whole_set`, `test_ui_sync_folders_empty_clears_all`, and `test_ui_sync_folders_missing_server_returns_200_inline_error` (their behavior is now covered by the `test_ui_update_*` tests added in Task 3).

In `tests/db/test_repo.py`, delete the three functions `test_replace_folders_swaps_whole_set`, `test_replace_folders_empty_clears_all`, and `test_replace_folders_unknown_server_raises` (covered by `test_update_server_with_folders_*` from Task 1).

- [ ] **Step 2: Run the suite to confirm those tests are gone and nothing else references them**

Run: `./.venv/bin/pytest tests/web/test_ui_forms.py tests/db/test_repo.py -q`
Expected: PASS — the remaining tests pass; the deleted test names no longer collected.

- [ ] **Step 3: Remove `ui_sync_folders` + its import in `pages.py`**

In `mediascanmonitor/web/pages.py`:
- Delete the entire `ui_sync_folders` route function (the `@router.post("/ui/servers/{server_id}/folders")` block, ~lines 525-545).
- Remove `apply_folders_sync,` from the `from mediascanmonitor.web.writes import (...)` block, leaving:

```python
from mediascanmonitor.web.writes import (
    apply_server_create_with_folders,
    apply_server_delete,
    apply_server_update_with_folders,
)
```

- [ ] **Step 4: Remove `apply_folders_sync` from `writes.py`**

In `mediascanmonitor/web/writes.py`, delete the entire `apply_folders_sync` async function (~lines 111-120). Verify no remaining reference: `grep -rn "apply_folders_sync" mediascanmonitor tests` returns nothing.

- [ ] **Step 5: Remove `replace_folders` from `repo.py`**

In `mediascanmonitor/db/repo.py`, delete the entire `replace_folders` method (~lines 154-175). Verify: `grep -rn "replace_folders" mediascanmonitor tests` returns nothing.

- [ ] **Step 6: Run the full gate**

Run:
```bash
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/mypy mediascanmonitor
./.venv/bin/pytest -q
```
Expected: ruff clean (no unused imports — `apply_folders_sync`/`replace_folders` fully gone), mypy "Success", all tests pass.

- [ ] **Step 7: Commit**

```bash
git add mediascanmonitor/web/pages.py mediascanmonitor/web/writes.py mediascanmonitor/db/repo.py tests/web/test_ui_forms.py tests/db/test_repo.py
git commit -m "$(cat <<'EOF'
refactor: remove orphaned folder-sync path

Delete the now-unused POST /ui/servers/{id}/folders route, apply_folders_sync,
and repo.replace_folders (folder replace is centralized in _set_server_folders;
the detail page saves via the consolidated /update). Their behavioral coverage
moved to the update_server_with_folders / consolidated-/update tests.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_013PMvSqE8mRcmS5sQCpXukc
EOF
)"
```

---

## Manual verification (after Task 4)

Beyond the automated gate, spot-check the UI on the dev server (`scripts/dev_serve.sh`, then log in with `dev`):

1. Open a server detail page → there is **one** "Save changes" button and a "Delete server" button at the bottom; no "Save folders" button.
2. Edit a setting **and** add/remove a folder row, click **Save changes** → both persist; "Saved." appears; one engine rebuild in the logs.
3. Rename the server to an existing server's name → inline "already exists" error at `#save-error`, **not** a 500, and the folder edits/settings are unchanged on reload.
4. Delete server → confirm prompt → returns to the servers list.

## Self-Review

- **Spec coverage:** Frontend restructure → Task 3 (template). Atomic combined write → Task 1 (repo) + Task 2 (core). `_set_server_folders` anti-drift helper → Task 1. `IntegrityError`→409 drift fix → Task 2 (core) + Task 3 (UI test). Remove `/ui/.../folders` + `apply_folders_sync` + `replace_folders` → Task 4. CSS reuse (no new class) → Task 3 (template uses `.form-actions`/`.danger-zone`). Test migrations (3 repo + 3 UI + 1 page assertion + write-core/UI happy-path + dup-name) → covered across Tasks 1–4. Migration trap (full server payload on migrated `/update` tests; `#save-error` not `#folder-error`) → the Task 3 UI tests carry full payloads and assert `#save-error`. All spec sections map to a task.
- **Placeholder scan:** no TBD/TODO; every code step shows complete code; every command shows expected output.
- **Type consistency:** `_set_server_folders(server, folders)`, `update_server_with_folders(server_id, data, folders)`, and `apply_server_update_with_folders(repo, engine, server_id, data, folders)` are used with identical names/signatures everywhere they appear (Tasks 1→2→3). The handler keeps catching `(HTTPException, ValueError, KeyError)`; the core's 409/422 are `HTTPException` subtypes, so they land inline.
