# Folder picker for the add/edit flow

**Date:** 2026-06-22
**Status:** Approved design — pending implementation plan
**Branch:** `app-v2`

## Problem

Watched-folder paths are entered as free text in the shared folder editor
(`_folder_editor.html`, used by both `/servers/new` and `/servers/{id}`). Typing
absolute container paths by hand is error-prone — a typo silently produces a folder
that watches nothing. Users want to **browse and pick** a directory, while still being
able to **type or paste** a path by hand.

## Goals

- Add a directory browser to each folder row, reachable from a **Browse** button.
- Keep free-text entry fully intact — the picker only *fills* the path input.
- Match the existing "Signal Room" visual language (no new design vocabulary).
- Stay within the app's grain: htmx-rendered partials + minimal vanilla JS, system
  fonts only (offline-on-NAS), async I/O off the event loop, Pydantic at boundaries.

## Non-goals

- No JSON `/api/fs` twin. The `/ui`↔`/api` twin rule covers domain *writes*; this is a
  read-only UI helper.
- No file selection (you pick a directory to watch, not a file).
- No multi-select, no "create folder", no favorites/bookmarks (YAGNI).
- No host↔container path translation: the watcher runs **inside** the container and
  watches **container** paths, which is exactly what `Folder.path` stores — so browsing
  the container's own filesystem returns precisely the right strings.

## Decisions (settled during brainstorming)

1. **Browse scope:** the whole container filesystem from `/`. This matches what the app
   can already watch and what it stores. The app is a single-password admin tool that
   already reads arbitrary paths; a read-only directory listing adds no capability it
   doesn't already have. The picker may start deeper (the row's current path) but can
   navigate up to `/`.
2. **Picker UI:** a **modal overlay** (native `<dialog>`) opened per row, reused as a
   single shared instance — not an inline tree (which would shove the form around with
   several rows open).
3. **Folders only:** files are not listed.
4. **Hide ignore dirs:** `@eaDir` and `#snapshot` (the watcher's legacy ignore set) are
   never valid watch targets, so the browser omits them. All other directories are
   shown, including dotfiles.

## Architecture

### Backend — `mediascanmonitor/web/fsbrowse.py` (new, pure core)

A small, dependency-free, unit-testable module. No FastAPI, no DB.

```python
class DirEntry(BaseModel):
    name: str   # basename, e.g. "tv"
    path: str   # absolute, e.g. "/data/tv"

class DirListing(BaseModel):
    path: str               # resolved absolute dir being listed, e.g. "/data"
    parent: str | None      # absolute parent, or None when path == "/"
    entries: list[DirEntry] # immediate subdirectories, sorted

IGNORED_DIR_NAMES = frozenset({"@eaDir", "#snapshot"})

def list_directory(path: str) -> DirListing: ...
```

Behaviour:
- Resolve the input with `Path(path or "/").resolve()` to normalize `..` and symlinks
  to an absolute path. An empty/blank path means `/`.
- `os.scandir` the resolved dir; keep entries where `entry.is_dir(follow_symlinks=True)`
  and `name not in IGNORED_DIR_NAMES`. Files are dropped.
- Sort entries case-insensitively by name.
- `parent` is `None` when the resolved path is the filesystem root, else its parent.
- Raises the underlying `OSError` subclass on failure: `FileNotFoundError` (gone),
  `NotADirectoryError` (path is a file), `PermissionError` (unreadable). The route maps
  these to a friendly inline message — the core does not format errors.
- Per-entry `is_dir`/scan errors (e.g. a single unreadable child) are swallowed so one
  bad child doesn't blank the whole listing.

### Backend — route in `mediascanmonitor/web/pages.py`

```
GET /ui/fs?path=<dir>  ->  renders _fs_listing.html  (page-auth guarded)
```

- `path: str = ""` query param (defaults to `/` inside `list_directory`).
- `await asyncio.to_thread(list_directory, path)` — directory listing is blocking I/O,
  so it runs off the event loop (rule 4).
- On `OSError`, render `_fs_listing.html` in an **error state**: the breadcrumb for the
  *requested* path still renders (so the user can climb back out via a parent segment),
  the entry list is replaced by an inline error line, and Select is disabled for that
  view. Status stays 200 so htmx swaps it (same convention as the existing `/ui` error
  partials).

### Templates

- **`_fs_listing.html`** — the swappable inner body of the dialog (`#fs-listing`):
  - **Breadcrumb**: `/ › data › tv`. Each segment is an `hx-get="/ui/fs?path=<segment>"`
    targeting `#fs-listing` with `hx-swap="innerHTML"`. The leading `/` is its own
    segment (lists root). The current (last) segment is inert text.
  - **Parent entry**: a `..` row, present unless `parent is None`, `hx-get`ting the
    parent. Omitted at `/`.
  - **Entry list**: one row per subdirectory, each `hx-get`ting its own `path` into
    `#fs-listing`.
  - **Current path + Select**: the resolved `path` is shown above the actions and
    carried on a `data-current-path` attribute (read by the Select handler). In the
    error state this region shows the inline error and Select is disabled.
- **`_folder_picker.html`** — the `<dialog data-folder-picker>` shell: title
  ("Browse folders"), a close `✕`, the `#fs-listing` slot, and the footer
  (Cancel / Select this folder). Rendered **once** per page.
- **`_folder_editor.html`** — each row (and the `<template>` row used by the add-row
  script) gains a **Browse** button next to the path input, carrying
  `data-browse` so the script can wire it. `_folder_picker.html` is `{% include %}`d
  once at the bottom of the editor.

### JS — extend `_folder_rows_script.html`

A second IIFE alongside the existing add/remove logic:
- On a `data-browse` click: record that row's path `<input>` as the active target,
  open the dialog (`dialog.showModal()`), and trigger an htmx GET to load the listing
  for the input's current value (or `/` if blank).
- **Select**: read `data-current-path` from `#fs-listing`, write it into the recorded
  input, `dialog.close()`. (No-op + keep dialog open if the listing is in the error
  state / has no current path.)
- **Cancel / `✕` / backdrop click / Esc**: close without changing the input.
- **Progressive enhancement**: the Browse button is `hidden` in markup and revealed by
  the script, so no-JS users keep a fully working free-text input — identical to the
  token-field pattern. The `<dialog>` degrades to inert when unsupported/JS-off.

### Visual language ("Signal Room", reusing existing tokens)

The picker is machine voice — paths and listings — so it rides the **mono** stack and
existing CSS variables; no new palette.

- **Dialog surface**: `background: var(--panel)`, `1px solid var(--line)`,
  `border-radius: var(--r)`; `::backdrop` a dark translucent wash over the ink.
  Constrained width (~min(560px, 92vw)) and a max-height with the listing scrolling.
- **Title**: styled like `h2` (mono, uppercase, `letter-spacing`, `--muted`).
- **Close `✕`**: a quiet ghost button (`.ghost-danger`-like muted treatment).
- **Breadcrumb**: mono; segment separators use `--signal` (echoing the channel route
  arrow); clickable segments are `--signal`/hover-underline, the current segment
  `--text`.
- **Entry rows**: mono, separated by `1px solid var(--line-2)` (like `.nf-row` /
  `.event`); hover background `var(--panel-2)` (like `.channel:hover`). A small mono
  directory marker precedes the name (a glyph, not emoji, to stay on-brand); `..` uses
  the same marker.
- **Listing area**: `background: var(--inset)`, inset border, scrollable
  (`overflow:auto`, capped height).
- **Current-path readout**: mono `--text` on an inset strip (like `.field-static`),
  with the leading accent in `--signal`.
- **Footer actions**: Cancel = muted ghost (`.token-btn-muted` style), Select = primary
  signal action (`button[type="submit"]` / `.cta` treatment).
- **Browse button (in row)**: a compact mono control consistent with the row's Remove /
  add-row buttons (e.g. `.add-row-btn`-adjacent sizing), not a heavy primary button.
- **Motion**: any open/scroll affordance respects the global
  `prefers-reduced-motion: reduce` rule already in `app.css`.

## Data flow

```
Browse click (row R)
  └─ JS records R's path input, dialog.showModal(), htmx GET /ui/fs?path=<R's value or />
       └─ route: to_thread(list_directory) → DirListing → _fs_listing.html → #fs-listing
Navigate (click segment / .. / subdir)
  └─ htmx GET /ui/fs?path=<target> → _fs_listing.html → swaps #fs-listing
Select
  └─ JS reads #fs-listing[data-current-path] → R's input.value = path → dialog.close()
Cancel / Esc / backdrop
  └─ dialog.close(), input unchanged
```

## Error handling

- Missing dir / file-not-dir / permission denied → 200 with `_fs_listing.html` in error
  state (breadcrumb to requested path preserved; Select disabled). No 500s reach htmx.
- Unreadable child during scan → that child is skipped; the listing still renders.
- Server gone / auth lost → handled by the existing page-auth guard (303), unchanged.

## Security

- Same trust model as today: every `/ui/*` route, including `/ui/fs`, is behind
  `require_page_auth`. The listing is read-only (directory names only — no contents, no
  files, no sizes). It exposes nothing the app couldn't already read to watch a path.
- `Path.resolve()` normalizes traversal to a real absolute path before listing; there is
  no "escape" to defend because the intended scope *is* the whole filesystem.

## Testing (pyramid, rule 6)

- **Unit — `tests/web/test_fsbrowse.py`** (`tmp_path`):
  - lists only subdirectories, files excluded;
  - entries sorted case-insensitively;
  - `parent` computed correctly; `/` (or root) yields `parent is None`;
  - `@eaDir` / `#snapshot` skipped, other/dot dirs kept;
  - `..` / relative / symlinked input resolves to an absolute path;
  - raises `FileNotFoundError` / `NotADirectoryError` (file input); permission case
    where feasible.
- **Route — `tests/web/test_pages.py`**:
  - authed `GET /ui/fs?path=<tmp>` → 200 listing the dir's subfolder names;
  - anon → 303 (guard);
  - bad path → 200 with inline error text (htmx-swappable, not 500);
  - resolves `..` to the expected parent.
- **Render smoke**: the folder editor renders the `data-browse` Browse marker and the
  `data-folder-picker` dialog on both `/servers/new` and `/servers/{id}`.
- **Existing suites** (`test_ui_forms.py`, `test_repo.py`) remain green — the picker only
  fills the existing path input; the parse/sync path is unchanged.

## Files touched

| File | Change |
| --- | --- |
| `mediascanmonitor/web/fsbrowse.py` | new — `DirEntry`/`DirListing`/`list_directory` |
| `mediascanmonitor/web/pages.py` | new `GET /ui/fs` route + error mapping |
| `mediascanmonitor/web/templates/_fs_listing.html` | new — listing partial |
| `mediascanmonitor/web/templates/_folder_picker.html` | new — `<dialog>` shell |
| `mediascanmonitor/web/templates/_folder_editor.html` | add Browse button + include picker |
| `mediascanmonitor/web/templates/_folder_rows_script.html` | add picker IIFE |
| `mediascanmonitor/web/static/app.css` | picker styles (reusing tokens) |
| `tests/web/test_fsbrowse.py` | new — unit tests |
| `tests/web/test_pages.py` | route + render tests |

## Out of scope / possible follow-ups

- Showing files (greyed, non-selectable) for orientation.
- A "this folder is already watched" hint in the listing.
- Typeahead/jump-to-path inside the dialog.
