# Folder Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users browse and pick a watched directory from the folder editor (add/edit flows) via a modal `<dialog>`, while keeping free-text path entry.

**Architecture:** A pure, unit-tested `fsbrowse` core lists immediate subdirectories; a page-auth-guarded `GET /ui/fs` route renders an htmx listing partial off the event loop; a shared `<dialog>` (one per page) opened per row by a small vanilla-JS extension writes the chosen path back into the row's existing path input. Read-only, names-only, behind the single app password.

**Tech Stack:** Python 3.14, FastAPI, Jinja2, htmx (already global as `window.htmx`), Pydantic, pytest. No new dependencies.

**Design spec:** `docs/superpowers/specs/2026-06-22-folder-picker-design.md` (read it; this plan implements it).

## Global Constraints

Copied from `CLAUDE.md` — every task's requirements implicitly include these:

- **Python 3.14**; target current stable, no version guesses.
- **PEP 649 annotations**: never add `from __future__ import annotations`; leave forward refs unquoted.
- **Enums** subclass `StrEnum`, never `(str, Enum)`. (No new enums in this plan.)
- **Ruff `select` is exactly `E, F, I, UP, B, C4, SIM, RUF`** (`B` ignored under `tests/**`). Don't add `# noqa` for unselected rules (trips `RUF100`).
- **`mediascanmonitor` is first-party for isort** — blank line between third-party and first-party imports.
- **`mypy --strict`** clean on `mediascanmonitor` (not on `tests/`).
- **Async discipline**: no blocking calls in the event loop — directory listing runs via `asyncio.to_thread`.
- **Pydantic at every external boundary** — the listing core returns validated models, not raw dicts.
- **Security**: never log paths-as-secrets (no secrets involved here); the route is behind `require_page_auth` like every `/ui/*` route.
- **System fonts only** (offline-on-NAS) — the picker uses the existing `--mono`/`--sans` stacks; no web fonts, no emoji icons.
- **CI gate** (must be green): `ruff format --check .` → `ruff check .` → `mypy mediascanmonitor` → `pytest`. Run `ruff format .` before checking — CI enforces format separately from lint.
- **Commits**: end every commit with the repo's standard trailers (Co-Authored-By + Claude-Session) per the harness. Branch is `app-v2`; do not merge to `main`.

Dev gate command (run before each commit):

```bash
uv run ruff format . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest -q
```

---

### Task 1: `fsbrowse` core + unit tests

The pure listing core. No FastAPI, no DB — fully testable in isolation.

**Files:**
- Create: `mediascanmonitor/web/fsbrowse.py`
- Test: `tests/web/test_fsbrowse.py`

**Interfaces:**
- Consumes: nothing (stdlib + Pydantic).
- Produces:
  - `class FsEntry(BaseModel)` with `name: str`, `path: str`
  - `class DirListing(BaseModel)` with `path: str`, `parent: str | None`, `entries: list[FsEntry]`, `truncated: bool = False`
  - `IGNORED_DIR_NAMES: frozenset[str]`, `MAX_ENTRIES: int`
  - `def list_directory(path: str) -> DirListing` — normalizes (`normpath(abspath(path or "/"))`, **no** symlink resolution), lists immediate subdirectories (files + ignored names dropped), sorted case-insensitively, capped at `MAX_ENTRIES` (`truncated=True` when clipped). Raises the underlying `OSError` subclass (`FileNotFoundError` / `NotADirectoryError` / `PermissionError`) on failure.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_fsbrowse.py`:

```python
"""Unit tests for the folder-picker directory core (spec 2026-06-22)."""

import os

import pytest

from mediascanmonitor.web.fsbrowse import MAX_ENTRIES, list_directory


def test_lists_only_subdirectories(tmp_path):
    (tmp_path / "tv").mkdir()
    (tmp_path / "movies").mkdir()
    (tmp_path / "note.txt").write_text("x")
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == ["movies", "tv"]  # sorted, file excluded
    assert all(e.path == os.path.join(str(tmp_path), e.name) for e in listing.entries)


def test_entries_sorted_case_insensitively(tmp_path):
    for name in ("Zeta", "alpha", "Beta"):
        (tmp_path / name).mkdir()
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == ["alpha", "Beta", "Zeta"]


def test_parent_computed_and_none_at_root(tmp_path):
    (tmp_path / "child").mkdir()
    listing = list_directory(str(tmp_path / "child"))
    assert listing.parent == str(tmp_path)
    root = list_directory("/")
    assert root.parent is None
    assert root.path == "/"


def test_ignored_dirs_skipped_dotdirs_kept(tmp_path):
    for name in ("@eaDir", "#snapshot", ".hidden", "tv"):
        (tmp_path / name).mkdir()
    listing = list_directory(str(tmp_path))
    assert [e.name for e in listing.entries] == [".hidden", "tv"]


def test_symlinked_dir_keeps_symlink_path(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "inside").mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    listing = list_directory(str(link))
    assert listing.path == str(link)  # NOT str(real) — symlinks are not resolved (decision 5)
    assert [e.name for e in listing.entries] == ["inside"]


def test_dotdot_normalized_lexically(tmp_path):
    (tmp_path / "real").mkdir()
    listing = list_directory(str(tmp_path / "real" / ".."))
    assert listing.path == str(tmp_path)


def test_truncated_when_over_cap(tmp_path):
    for i in range(MAX_ENTRIES + 5):
        (tmp_path / f"d{i:04d}").mkdir()
    listing = list_directory(str(tmp_path))
    assert listing.truncated is True
    assert len(listing.entries) == MAX_ENTRIES


def test_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_directory(str(tmp_path / "nope"))


def test_file_path_raises_not_a_directory(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        list_directory(str(f))


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
def test_permission_denied_raises(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "child").mkdir()
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            list_directory(str(locked))
    finally:
        locked.chmod(0o755)  # restore so pytest can clean up tmp_path
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_fsbrowse.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'mediascanmonitor.web.fsbrowse'`.

- [ ] **Step 3: Write the implementation**

Create `mediascanmonitor/web/fsbrowse.py`:

```python
"""Read-only directory browser core for the folder picker (UI helper, spec 2026-06-22).

Pure and dependency-free (no FastAPI, no DB) so it unit-tests in isolation. Lists the
immediate subdirectories of a path for the add/edit folder picker.

Paths are normalized (``..`` collapsed, made absolute) but symlinks are deliberately NOT
resolved: the watcher runs inside the container and watches the path as given, so resolving
a symlinked media dir to its real target could store a path the watcher never sees.
"""

import os

from pydantic import BaseModel

IGNORED_DIR_NAMES = frozenset({"@eaDir", "#snapshot"})
MAX_ENTRIES = 1000


class FsEntry(BaseModel):
    name: str
    path: str


class DirListing(BaseModel):
    path: str
    parent: str | None
    entries: list[FsEntry]
    truncated: bool = False


def list_directory(path: str) -> DirListing:
    """List the immediate subdirectories of ``path`` (files and ignore-dirs excluded)."""
    target = os.path.normpath(os.path.abspath(path or "/"))
    entries: list[FsEntry] = []
    truncated = False
    with os.scandir(target) as it:  # raises FileNotFoundError/NotADirectoryError/PermissionError
        for entry in it:
            if entry.name in IGNORED_DIR_NAMES:
                continue
            try:
                if not entry.is_dir(follow_symlinks=True):
                    continue
            except OSError:
                continue  # a single unreadable/stale child must not blank the listing
            if len(entries) >= MAX_ENTRIES:
                truncated = True
                break
            entries.append(FsEntry(name=entry.name, path=os.path.join(target, entry.name)))
    entries.sort(key=lambda e: e.name.lower())
    parent = os.path.dirname(target)
    return DirListing(
        path=target,
        parent=None if parent == target else parent,  # dirname("/") == "/" → root has no parent
        entries=entries,
        truncated=truncated,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web/test_fsbrowse.py -q`
Expected: PASS (all tests; the permission test may show as skipped if run as root).

- [ ] **Step 5: Run the gate**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest -q`
Expected: format clean, lint clean, mypy `Success`, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/fsbrowse.py tests/web/test_fsbrowse.py
git commit -m "feat(web): directory-listing core for the folder picker"
```

---

### Task 2: `GET /ui/fs` route + listing partial

Wire the core to an htmx endpoint that renders the listing partial, with errors rendered inline (200) per the existing `/ui` convention.

**Files:**
- Modify: `mediascanmonitor/web/pages.py` (add `import os`; import `list_directory`/`DirListing`; add `_path_crumbs`, `_fs_error_message`, and the `ui_browse_fs` route)
- Create: `mediascanmonitor/web/templates/_fs_listing.html`
- Test: `tests/web/test_pages.py` (add route tests)

**Interfaces:**
- Consumes: `mediascanmonitor.web.fsbrowse.list_directory`, `DirListing` (Task 1).
- Produces: `GET /ui/fs?path=<dir>` → 200 rendering `_fs_listing.html`. Template context keys: `listing: DirListing | None`, `crumbs: list[dict[str, str]]` (each `{"name", "path"}`, cumulative), `error: str | None`. The rendered partial carries `data-current-path="<dir>"` (empty in the error state) for the picker JS (Task 4).

- [ ] **Step 1: Write the failing route tests**

Add to `tests/web/test_pages.py` (top-level functions; `tmp_path` is a pytest builtin, `auth_client`/`client` are existing fixtures):

```python
def test_ui_fs_lists_subdirs_for_authed(auth_client: httpx.Client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "movies").mkdir()
    (tmp_path / "tv").mkdir()
    (tmp_path / "note.txt").write_text("x")
    resp = auth_client.get("/ui/fs", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    assert "movies" in resp.text and "tv" in resp.text
    assert "note.txt" not in resp.text
    assert f'data-current-path="{tmp_path}"' in resp.text  # the JS hook the picker reads


def test_ui_fs_bad_path_renders_inline_error_not_500(auth_client: httpx.Client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    resp = auth_client.get("/ui/fs", params={"path": str(tmp_path / "missing")})
    assert resp.status_code == 200  # rendered inline so htmx can swap it, never a 500
    assert "no longer exists" in resp.text.lower()


def test_ui_fs_normalizes_dotdot(auth_client: httpx.Client, tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / "a").mkdir()
    resp = auth_client.get("/ui/fs", params={"path": str(tmp_path / "a" / "..")})
    assert resp.status_code == 200
    assert f'data-current-path="{tmp_path}"' in resp.text


def test_ui_fs_redirects_when_anon(client: httpx.Client) -> None:
    r = client.get("/ui/fs", params={"path": "/"}, follow_redirects=False)
    assert r.status_code == 303
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/web/test_pages.py -k ui_fs -q`
Expected: FAIL — 404 for `/ui/fs` (route not defined) / `TemplateNotFound: _fs_listing.html`.

- [ ] **Step 3: Add `import os` to `pages.py`**

In `mediascanmonitor/web/pages.py`, add `import os` to the stdlib import block (alphabetical, before `import re`):

```python
import asyncio
import dataclasses
import json
import os
import re
```

- [ ] **Step 4: Import the core in `pages.py`**

Add this import alongside the other `mediascanmonitor.web.*` imports (keep isort order — it sorts before `servertest`):

```python
from mediascanmonitor.web.fsbrowse import DirListing, list_directory
```

- [ ] **Step 5: Add the helpers + route to `pages.py`**

Add near the other `/ui` helpers (e.g. after `_servers_list_response`):

```python
def _path_crumbs(path: str) -> list[dict[str, str]]:
    """Breadcrumb segments with cumulative absolute paths: /data/tv → [/, /data, /data/tv]."""
    crumbs = [{"name": "/", "path": "/"}]
    acc = ""
    for part in (p for p in path.split("/") if p):
        acc = f"{acc}/{part}"
        crumbs.append({"name": part, "path": acc})
    return crumbs


def _fs_error_message(exc: OSError) -> str:
    """Map a listing failure to a short, user-facing line (the core raises, the route phrases)."""
    if isinstance(exc, FileNotFoundError):
        return "That folder no longer exists."
    if isinstance(exc, NotADirectoryError):
        return "That path is a file, not a folder."
    if isinstance(exc, PermissionError):
        return "Permission denied reading that folder."
    return "Couldn't read that folder."


@router.get("/ui/fs")
async def ui_browse_fs(
    request: Request,
    path: str = "",
    templates: Jinja2Templates = Depends(get_templates),
) -> Response:
    # Read-only directory browser for the folder picker (spec 2026-06-22). Lists immediate
    # subdirectories off the event loop. On any OSError we still render the listing partial in
    # an error state (200, so htmx swaps it) with the breadcrumb to the requested path so the
    # user can climb back out — same "errors render inline" convention as the /ui form handlers.
    requested = os.path.normpath(os.path.abspath(path or "/"))
    listing: DirListing | None = None
    error: str | None = None
    try:
        listing = await asyncio.to_thread(list_directory, path)
    except OSError as exc:
        error = _fs_error_message(exc)
    return templates.TemplateResponse(
        request=request,
        name="_fs_listing.html",
        context={"listing": listing, "crumbs": _path_crumbs(requested), "error": error},
    )
```

- [ ] **Step 6: Create the listing partial**

Create `mediascanmonitor/web/templates/_fs_listing.html`:

```html
{# Directory listing for the folder picker (spec 2026-06-22). htmx swaps this into #fs-listing.
   `crumbs` (cumulative breadcrumb) is always set; `listing` is a DirListing on success or None
   in the error state; `error` is the inline message then. Every emitted path is | urlencode'd —
   folder names legally contain spaces / # / & / +. Nav controls are <button type=button> so they
   never submit the surrounding folder form. #}
<nav class="fs-crumbs" aria-label="Folder path">
  {% for c in crumbs %}{% if not loop.last %}<button type="button" class="fs-crumb" hx-get="/ui/fs?path={{ c.path | urlencode }}" hx-target="#fs-listing" hx-swap="innerHTML">{{ c.name }}</button><span class="fs-crumb-sep">/</span>{% else %}<span class="fs-crumb fs-crumb-here">{{ c.name }}</span>{% endif %}{% endfor %}
</nav>
{% if error %}
<p class="fs-error">{{ error }}</p>
<div class="fs-current" data-current-path=""></div>
{% else %}
<ul class="fs-list">
  {% if listing.parent is not none %}
  <li><button type="button" class="fs-entry fs-up" hx-get="/ui/fs?path={{ listing.parent | urlencode }}" hx-target="#fs-listing" hx-swap="innerHTML"><span class="fs-icon" aria-hidden="true">&uarr;</span>..</button></li>
  {% endif %}
  {% for e in listing.entries %}
  <li><button type="button" class="fs-entry" hx-get="/ui/fs?path={{ e.path | urlencode }}" hx-target="#fs-listing" hx-swap="innerHTML"><span class="fs-icon" aria-hidden="true">&rsaquo;</span>{{ e.name }}</button></li>
  {% else %}
  <li class="fs-empty">No sub-folders here.</li>
  {% endfor %}
</ul>
{% if listing.truncated %}<p class="fs-trunc">Showing {{ listing.entries | length }} folders (list truncated) — navigate in to narrow down.</p>{% endif %}
<div class="fs-current" data-current-path="{{ listing.path }}">{{ listing.path }}</div>
{% endif %}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/web/test_pages.py -k ui_fs -q`
Expected: PASS (4 tests).

- [ ] **Step 8: Run the gate**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest -q`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add mediascanmonitor/web/pages.py mediascanmonitor/web/templates/_fs_listing.html tests/web/test_pages.py
git commit -m "feat(web): GET /ui/fs directory browser endpoint"
```

---

### Task 3: Picker dialog + editor integration (markup)

Add the shared dialog shell and the per-row Browse button, nested inside the existing first grid cell so the row stays a 5-column grid. Markup only — styling and behavior land in Task 4 (the Browse button is briefly visible-but-inert between this commit and the next; that's the only intermediate state).

**Files:**
- Create: `mediascanmonitor/web/templates/_folder_picker.html`
- Modify: `mediascanmonitor/web/templates/_folder_editor.html`
- Test: `tests/web/test_pages.py` (add a render-smoke test)

**Interfaces:**
- Consumes: `_fs_listing.html` + `GET /ui/fs` (Task 2) at runtime.
- Produces: every folder row (rendered rows, the blank starter row, **and** the `<template>` clone row) contains `<button ... data-browse>`; each page renders one `<dialog data-folder-picker>` containing `<div id="fs-listing">`, a `[data-picker-select]` button, and `[data-picker-close]` controls.

- [ ] **Step 1: Write the failing render test**

Add to `tests/web/test_pages.py`:

```python
def test_folder_picker_present_on_new_and_detail(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    sid = _seed_server(repo)
    for body in (auth_client.get("/servers/new").text, auth_client.get(f"/servers/{sid}").text):
        assert "data-browse" in body  # the per-row Browse button
        assert "data-folder-picker" in body  # the shared dialog shell
        assert 'id="fs-listing"' in body  # the htmx swap target inside the dialog
        assert "data-picker-select" in body  # the Select control
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/web/test_pages.py -k folder_picker_present -q`
Expected: FAIL — `data-browse` not in body.

- [ ] **Step 3: Create the dialog shell**

Create `mediascanmonitor/web/templates/_folder_picker.html`:

```html
{# Folder picker dialog — one shared instance per page (one folder editor per page; see spec
   2026-06-22). Opened per row by _folder_rows_script.html, which records the target path input,
   loads #fs-listing via htmx, and on Select writes the listing's data-current-path back. Footer
   controls are type=button so they never submit the surrounding folder form. #}
<dialog class="fs-dialog" data-folder-picker aria-label="Browse folders">
  <div class="fs-dialog-head">
    <h2>Browse folders</h2>
    <button type="button" class="fs-close" data-picker-close aria-label="Close">&times;</button>
  </div>
  <div id="fs-listing" class="fs-listing"></div>
  <div class="fs-dialog-foot">
    <button type="button" class="fs-cancel" data-picker-close>Cancel</button>
    <button type="button" class="fs-select" data-picker-select>Select this folder</button>
  </div>
</dialog>
```

- [ ] **Step 4: Wrap the path input + Browse button in each row**

In `mediascanmonitor/web/templates/_folder_editor.html`, replace the **three** path-input lines (the rendered `{% for %}` row, the `{% else %}` blank row, and the `<template>` row) with a `.nf-path` wrapper holding the input + a Browse button.

Rendered row (`{% for f in folders %}`) — replace:

```html
      <input type="text" name="folder-{{ loop.index0 }}-path" value="{{ f.path }}" placeholder="/data/tv" aria-label="Path">
```

with:

```html
      <div class="nf-path">
        <input type="text" name="folder-{{ loop.index0 }}-path" value="{{ f.path }}" placeholder="/data/tv" aria-label="Path">
        <button type="button" class="nf-browse" data-browse>Browse</button>
      </div>
```

Blank starter row (`{% else %}`) — replace:

```html
      <input type="text" name="folder-0-path" placeholder="/data/tv" aria-label="Path">
```

with:

```html
      <div class="nf-path">
        <input type="text" name="folder-0-path" placeholder="/data/tv" aria-label="Path">
        <button type="button" class="nf-browse" data-browse>Browse</button>
      </div>
```

`<template>` clone row — replace:

```html
      <input type="text" name="folder-__I__-path" placeholder="/data/tv" aria-label="Path">
```

with:

```html
      <div class="nf-path">
        <input type="text" name="folder-__I__-path" placeholder="/data/tv" aria-label="Path">
        <button type="button" class="nf-browse" data-browse>Browse</button>
      </div>
```

- [ ] **Step 5: Include the dialog once in the editor**

In `mediascanmonitor/web/templates/_folder_editor.html`, add the include just before the final closing `</div>` of `.folder-editor` (after the `</template>` line):

```html
  {% include "_folder_picker.html" %}
</div>
```

- [ ] **Step 6: Run the render test to verify it passes**

Run: `uv run pytest tests/web/test_pages.py -k folder_picker_present -q`
Expected: PASS.

- [ ] **Step 7: Run the gate**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest -q`
Expected: all green (templates aren't linted; existing tests still pass — the picker only *adds* markup around the unchanged `folder-<i>-path` input).

- [ ] **Step 8: Commit**

```bash
git add mediascanmonitor/web/templates/_folder_picker.html mediascanmonitor/web/templates/_folder_editor.html tests/web/test_pages.py
git commit -m "feat(web): folder picker dialog + per-row Browse button markup"
```

---

### Task 4: Picker behavior (JS) + styling (CSS) + manual verification

Make the Browse button live and style the dialog to the Signal Room theme. No new pytest tests (browser behavior isn't unit-tested in this project, matching the existing `_folder_rows_script.html` / token-field pattern); verified via the gate + a manual smoke on the dev server.

**Files:**
- Modify: `mediascanmonitor/web/templates/_folder_rows_script.html` (add a second IIFE)
- Modify: `mediascanmonitor/web/static/app.css` (append the picker block)

**Interfaces:**
- Consumes: the markup from Task 3 (`[data-folder-picker]`, `#fs-listing`, `[data-browse]`, `[data-picker-select]`, `[data-picker-close]`) and `data-current-path` from Task 2's partial; `window.htmx`.
- Produces: no Python surface. Adds the `picker-ready` class to each `[data-folder-editor]` so CSS reveals the Browse buttons.

- [ ] **Step 1: Add the picker IIFE**

In `mediascanmonitor/web/templates/_folder_rows_script.html`, add a second `<script>` block after the existing one:

```html
<script>
  // Folder picker: open the shared <dialog> from a row's Browse button, browse via htmx, and
  // write the chosen path back into that row's path input. Progressive enhancement — adds
  // .picker-ready so the CSS-hidden Browse buttons appear (cloned rows inherit the class via the
  // editor ancestor); with no JS the free-text input still works. Uses event delegation so rows
  // added after load are handled too (mirrors the delegated remove handler above).
  (function () {
    const dialog = document.querySelector("[data-folder-picker]");
    const listing = document.getElementById("fs-listing");
    if (!dialog || !listing || typeof dialog.showModal !== "function") return;

    const select = dialog.querySelector("[data-picker-select]");
    let targetInput = null;

    for (const editor of document.querySelectorAll("[data-folder-editor]")) {
      editor.classList.add("picker-ready"); // reveals the Browse buttons (incl. cloned rows)
      editor.addEventListener("click", (event) => {
        const browse = event.target.closest("[data-browse]");
        if (!browse) return;
        targetInput = browse.closest("[data-folder-row]").querySelector('input[name$="-path"]');
        const start = targetInput.value.trim() || "/";
        select.disabled = true; // re-enabled on the first afterSwap below
        dialog.showModal();
        htmx.ajax("GET", "/ui/fs?path=" + encodeURIComponent(start), {
          target: "#fs-listing",
          swap: "innerHTML",
        });
      });
    }

    // Stale-path guard: disable Select while a navigation is in flight, re-enable once swapped.
    listing.addEventListener("click", (event) => {
      if (event.target.closest("[hx-get]")) select.disabled = true;
    });
    listing.addEventListener("htmx:afterSwap", () => {
      select.disabled = false;
    });

    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        dialog.close(); // backdrop click (the target is the dialog itself, not its content)
        return;
      }
      if (event.target.closest("[data-picker-close]")) {
        dialog.close();
        return;
      }
      if (event.target.closest("[data-picker-select]")) {
        const current = listing.querySelector("[data-current-path]");
        const path = current && current.dataset.currentPath;
        if (path && targetInput) {
          targetInput.value = path;
          dialog.close();
        }
      }
    });
  })();
</script>
```

- [ ] **Step 2: Append the picker styles**

Append to `mediascanmonitor/web/static/app.css`:

```css
/* ------------------------------------------------------------- Folder picker
   A directory browser for the folder rows. Machine voice: mono listing, signal
   accents, panel/inset surfaces — reuses the Signal Room tokens. The Browse
   button stays hidden until its script enhances the editor (cloned rows inherit
   .picker-ready from the editor ancestor). */

[data-browse] { display: none; }
.nf-path { display: flex; gap: 0.4rem; align-items: center; min-width: 0; }
.nf-path input[type="text"] { flex: 1 1 auto; width: auto; min-width: 0; }

.picker-ready .nf-browse {
  display: inline-flex;
  flex: 0 0 auto;
  font: 600 0.62rem var(--mono);
  text-transform: uppercase;
  letter-spacing: 0.07em;
  padding: 0.34rem 0.55rem;
  margin: 0;
  color: var(--signal);
  background: transparent;
  border: 1px solid var(--signal-dim);
  border-radius: var(--r-sm);
}
.picker-ready .nf-browse:hover { background: var(--signal); color: #04181b; border-color: var(--signal); }

.fs-dialog {
  width: min(560px, 92vw);
  max-height: 80vh;
  padding: 0;
  color: var(--text);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--r);
  overflow: hidden;
}
.fs-dialog::backdrop { background: rgba(6, 12, 18, 0.62); }

.fs-dialog-head {
  display: flex; align-items: center; justify-content: space-between; gap: 0.6rem;
  padding: 0.85rem 1rem;
  border-bottom: 1px solid var(--line);
}
.fs-dialog-head h2 { margin: 0; }
.fs-close {
  font: 700 0.9rem var(--mono);
  padding: 0.22rem 0.5rem; margin: 0;
  color: var(--faint); background: transparent; border: 1px solid var(--line);
}
.fs-close:hover { color: #ffd2d5; background: rgba(242, 109, 116, 0.12); border-color: rgba(242, 109, 116, 0.5); }

.fs-crumbs {
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.1rem;
  padding: 0.7rem 1rem 0.55rem;
  font: 0.8rem/1.5 var(--mono);
}
.fs-crumb { font: inherit; color: var(--signal); background: transparent; border: 0; padding: 0; margin: 0; cursor: pointer; }
.fs-crumb:hover { text-decoration: underline; text-underline-offset: 2px; }
.fs-crumb-sep { color: var(--faint); margin: 0 0.2rem; }
span.fs-crumb-here { color: var(--text); cursor: default; }

.fs-listing { display: flex; flex-direction: column; min-height: 0; }
.fs-list {
  list-style: none; margin: 0; padding: 0 0.5rem;
  overflow: auto; max-height: 46vh;
  background: var(--inset);
  border-top: 1px solid var(--line-2);
  border-bottom: 1px solid var(--line-2);
}
.fs-entry {
  display: flex; align-items: center; gap: 0.55rem;
  width: 100%; margin: 0; text-align: left;
  font: 0.86rem/1.3 var(--mono);
  color: var(--text); background: transparent;
  border: 0; border-bottom: 1px solid var(--line-2); border-radius: 0;
  padding: 0.5rem 0.45rem;
}
.fs-list li:last-child .fs-entry { border-bottom: 0; }
.fs-entry:hover { background: var(--panel-2); }
.fs-icon { color: var(--signal); flex: 0 0 auto; }

.fs-empty, .fs-trunc, .fs-error { font: 0.82rem/1.45 var(--sans); color: var(--muted); padding: 0.7rem 1rem; margin: 0; }
.fs-error { color: #f6b9bd; }

.fs-current { font: 0.82rem/1.45 var(--mono); color: var(--text); padding: 0.6rem 1rem; word-break: break-all; }
.fs-current:empty { display: none; }

.fs-dialog-foot {
  display: flex; align-items: center; justify-content: flex-end; gap: 0.6rem;
  padding: 0.8rem 1rem;
  border-top: 1px solid var(--line);
}
.fs-cancel {
  font: 600 0.8rem var(--sans);
  color: var(--muted); background: transparent; border: 1px solid var(--line);
}
.fs-cancel:hover { color: var(--text); background: var(--panel-2); border-color: var(--muted); }
.fs-select {
  font: 600 0.82rem var(--sans);
  color: var(--signal); background: rgba(63, 216, 228, 0.07); border: 1px solid var(--signal-dim);
}
.fs-select:hover { background: var(--signal); color: #04181b; border-color: var(--signal); }
.fs-select:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 3: Run the gate**

Run: `uv run ruff format . && uv run ruff check . && uv run mypy mediascanmonitor && uv run pytest -q`
Expected: all green (no behavior change to Python; full suite passes).

- [ ] **Step 4: Manual smoke on the dev server**

Start the dev server (project skill / script):

```bash
scripts/dev_serve.sh   # serves on 0.0.0.0:8099, password "dev"
```

In a browser (log in with `dev`), verify on **both** `/servers/new` and an existing server's detail page:

1. Each folder row shows a **Browse** button next to the path input; rows stay aligned to the header (no column shift). Add a row with "+ Add another folder" — the new row also has a working Browse button.
2. Click **Browse** → the dialog opens and lists the starting dir (`/` when the path is blank, else the typed path).
3. Click a folder name → the listing navigates in; the breadcrumb updates; `..` and breadcrumb segments navigate back out.
4. Click **Select this folder** → the dialog closes and the row's path input holds the current path. **Cancel**, the `✕`, **Esc**, and a backdrop click all close without changing the input.
5. Type a bogus path into the input, then Browse → the dialog shows the inline error and Select does nothing.
6. Save the form and confirm the picked path persisted (folder appears on the detail page / re-opens with the value).
7. (Optional) Disable JavaScript → the Browse button is hidden and the free-text path input still works.

Stop the dev server when done (find its PID and kill it; do not `pkill -f` a pattern that matches your own shell):

```bash
pid=$(ss -ltnp | grep ':8099' | grep -o 'pid=[0-9]*' | cut -d= -f2); [ -n "$pid" ] && kill "$pid"
```

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/web/templates/_folder_rows_script.html mediascanmonitor/web/static/app.css
git commit -m "feat(web): folder picker behavior and Signal Room styling"
```

---

## Self-Review

**Spec coverage:**
- `fsbrowse.py` core (`FsEntry`/`DirListing`/`list_directory`, normpath-not-resolve, ignore dirs, MAX_ENTRIES/truncated, OSError raising) → Task 1.
- `GET /ui/fs` route, off-thread listing, inline-error-as-200, breadcrumb crumbs → Task 2.
- `_fs_listing.html` (urlencoded cumulative breadcrumb, `..`, entries, truncation note, `data-current-path`, error state) → Task 2.
- `_folder_picker.html` dialog (single instance, `type=button` footer) → Task 3.
- `_folder_editor.html` (Browse nested in first grid cell, rendered + blank + template rows, include once) → Task 3.
- JS (event delegation, class-based reveal for cloned rows, Select stale-path guard, backdrop `target===dialog`) → Task 4.
- CSS (Signal Room tokens, hide-until-`picker-ready`, dialog/list/crumb/footer styling) → Task 4.
- Tests: unit (Task 1), route + render (Tasks 2–3); JS/CSS verified via gate + manual smoke (Task 4) — matches the project's no-JS-unit-test convention.
- Security (page-auth guard, read-only) → inherited (route on the `require_page_auth` router) + Global Constraints.

**Placeholder scan:** none — every code/template/CSS step shows full content; commands have expected output.

**Type consistency:** `list_directory(path: str) -> DirListing`; `DirListing.path/parent/entries/truncated` and `FsEntry.name/path` used identically in the route and template; `_path_crumbs -> list[dict[str, str]]` with `{"name","path"}` matches the template's `c.name`/`c.path`; `data-current-path` is written by Task 2's partial and read by Task 4's JS.
