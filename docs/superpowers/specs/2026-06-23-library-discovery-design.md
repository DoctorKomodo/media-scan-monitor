# Library Discovery — Design Spec

**Date:** 2026-06-23
**Status:** Approved (brainstorming) → ready for implementation plan

## Problem

A folder row's **Library** field is a hand-typed opaque id. For Audiobookshelf this is
especially painful: library ids are long, opaque strings (e.g.
`lib_5yvub9dqvctlcrza6h` or a UUID like `312527c3-08d0-4cbc-bb95-2c37d7f5665d`) with no
human meaning, so users must hunt them down in the ABS UI and copy them by hand. Plex,
Emby, and Jellyfin have the same friction to a lesser degree (numeric section / item ids).

Every supported media backend exposes an endpoint that lists its libraries with both an id
and a human name. We can let the user **fetch and pick** a library instead of typing an id.

## Goal

Add a **general "list libraries" capability** to the server-adapter layer, surfaced in the
folder editor as a per-row **Fetch → pick from dialog** affordance. Implement it for
**Audiobookshelf** in this spec; Plex/Emby/Jellyfin become one-method follow-ups.

## Non-goals

- No change to scan/trigger behavior. This only helps *populate* the library field.
- No autodiscovery of folder paths from the backend (the library `folders[]` data ABS
  returns is ignored here).
- No backfill of `library_name` for existing rows — it stays `None` until re-picked.

## Constraints (from CLAUDE.md)

- **Rule 2 — one file per backend:** the capability is opt-in per adapter; the watcher and
  pipeline never learn about it. Adding it to another backend = flip a flag + one method in
  that backend's module.
- **Rule 3 — validate every external boundary:** the backend's JSON response is parsed
  through a Pydantic model, not raw-dict indexing. Full type hints; `mypy --strict` clean.
- **Rule 1 — verify deps/APIs at implement-time:** the ABS endpoint, auth scheme, and
  response shape are confirmed against current ABS API docs when implementing (already
  spot-checked: `GET /api/libraries`, Bearer header, `libraries[].{id,name}`).
- **Rule 5 — security:** the Bearer token stays in the header, never the URL or logs;
  reuses the adapter's existing `_headers()`.
- **Rule 7 — explicit migrations:** the new column ships as an Alembic revision, never
  `create_all`.

---

## Architecture

```
folder editor (per-row "Fetch" btn) ─htmx POST─▶ /ui/servers[/{id}]/libraries
                                                        │
                                                  run_library_listing()  (web/serverlibraries.py)
                                                        │  builds throwaway runtime + adapter,
                                                        │  always closes client (mirrors servertest.py)
                                                        ▼
                                          adapter.list_libraries() ─▶ LibraryListResult
                                                        │
                                            picker dialog body (HTML) ◀─ rendered
```

The capability rides the existing `ServerAdapter` ABC (approach A from brainstorming): a
`ClassVar` flag advertises support (parallel to `supported_scan_modes`), and a default
method returns "not supported" so non-discovery backends (webhook) inherit it for free.

---

## Component 1 — Adapter capability (`servers/base.py`, `servers/audiobookshelf.py`)

New value objects in `base.py`, alongside `TestResult`/`TriggerResult`:

```python
@dataclass(frozen=True, slots=True)
class LibraryOption:
    id: str       # opaque backend id (ABS lib_… / GUID; Plex section id; Emby item id)
    name: str     # human label, e.g. "Audiobooks"

@dataclass(frozen=True, slots=True)
class LibraryListResult:
    __test__ = False     # symmetric with TestResult; opt out of pytest collection
    ok: bool
    detail: str                              # "" on success, error reason otherwise
    libraries: tuple[LibraryOption, ...] = ()
```

On the `ServerAdapter` ABC:

```python
supports_library_discovery: ClassVar[bool] = False

async def list_libraries(self) -> LibraryListResult:
    """List selectable libraries (id + name). Default: backend has no concept of one."""
    return LibraryListResult(ok=False, detail="not supported")
```

The ABS adapter (`audiobookshelf.py`):

- sets `supports_library_discovery: ClassVar[bool] = True`
- implements `list_libraries()`:
  - `GET {base}/api/libraries` with the existing `_headers()` (Bearer), via
    `request_with_retry` (attempts=1, like `test()`).
  - On a 2xx: parse the body through a local Pydantic model
    (`_AbsLibrary(id: str, name: str)` inside an `_AbsLibrariesResponse` with
    `libraries: list[_AbsLibrary]`, `extra="ignore"`), map to `LibraryOption`s, return
    `LibraryListResult(ok=True, detail="", libraries=...)`.
  - On non-2xx: `LibraryListResult(ok=False, detail=f"HTTP {status}")`.
  - On `httpx.HTTPError`: `LibraryListResult(ok=False, detail=f"{type}: {exc}")`.
  - On a malformed body (Pydantic `ValidationError`): `ok=False,
    detail="unexpected response from Audiobookshelf"`.

The ABS module docstring's "VERIFY AT IMPLEMENT-TIME" note is extended to cover the
`/api/libraries` path and response shape.

No other adapter changes in this spec.

---

## Component 2 — Persistence (`db/models.py`, `db/schemas.py`, migration)

A folder remembers the *name* of the library it points at, for display only. `library_id`
stays the source of truth.

- **`db/models.py`** — `Folder` gains:
  ```python
  library_name: str | None = Field(default=None)
  ```
- **`db/schemas.py`** — `FolderCreate` and `FolderRead` gain `library_name: str | None =
  None`. `FolderCreate` does **not** require it; a hand-typed id leaves it `None`.
- **Migration** — new Alembic revision `0002_folder_library_name` (down_revision `0001`):
  ```python
  def upgrade() -> None:
      with op.batch_alter_table("folder", schema=None) as batch_op:
          batch_op.add_column(sa.Column("library_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

  def downgrade() -> None:
      with op.batch_alter_table("folder", schema=None) as batch_op:
          batch_op.drop_column("library_name")
  ```
  (SQLite requires `batch_alter_table`; matches the existing migration's idiom.)

`repo` folder-write paths (`create_folder`, `_set_server_folders`) carry `library_name`
through from `FolderCreate` to the model. No new repo method.

---

## Component 3 — Web endpoints (`web/serverlibraries.py`, `web/pages.py`)

Two endpoints, mirroring the **two** Test endpoints so saved and unsaved servers both work
and never drift:

- `POST /ui/servers/libraries` — **unsaved** path (new-server page). Reads the current
  server-form fields (same `Form(...)` params as `ui_test_server_config`), builds a
  throwaway runtime via `runtime_from_create`, runs the listing, returns the dialog body.
- `POST /ui/servers/{id}/libraries` — **stored** path (detail page). Loads the server +
  decrypted secret via `runtime_from_server`. If the form carries a freshly-typed token
  (replace-in-progress), it overrides the stored secret; otherwise the stored secret is
  used (so the user need not re-enter it).

Both delegate to one shared helper in a new `web/serverlibraries.py` (twin of
`servertest.py`'s `run_connectivity_test`):

```python
async def run_library_listing(runtime: ServerRuntime) -> LibraryListResult:
    client = build_client(verify_tls=runtime.verify_tls, timeout_seconds=runtime.timeout_seconds)
    try:
        adapter = create_adapter(runtime, client)
        if not adapter.supports_library_discovery:
            return LibraryListResult(ok=False, detail="This server type has no libraries to list.")
        return await adapter.list_libraries()
    finally:
        await client.aclose()
```

The endpoints render the result into a picker-dialog partial (Component 4). A type that
does not support discovery returns a clean "not supported" message — never a 500.

---

## Component 4 — Folder editor UI (`templates/_folder_editor.html`, `_library_picker.html`, `_library_options.html`, `_folder_rows_script.html`, `static/app.css`)

Mirrors the existing folder **Browse** picker precedent (`_folder_picker.html` + the
per-row `data-browse` button + one shared dialog per page).

- The folder editor receives a context flag `library_discovery` (true when the server's
  type advertises `supports_library_discovery`). When true, each row renders a **Fetch**
  button next to the Library field and a hidden `folder-<i>-library_name` input
  (pre-filled from `f.library_name`). When false, neither is rendered — the field is the
  plain text input exactly as today.
- One shared `<dialog data-library-picker>` per page (`_library_picker.html`), with an
  `#library-listing` htmx swap target and Cancel/Select footer (`type=button`).
- `_folder_rows_script.html` gains library-picker wiring: the per-row Fetch button records
  the target row's `library_id` + hidden `library_name` inputs, serializes the server-form
  fields, htmx-POSTs to the right endpoint (unsaved vs `{id}`), and swaps the returned
  options list into `#library-listing`. **Select** writes the chosen `id` → the library
  input and `name` → the hidden input, then closes. **Cancel** closes untouched. The
  row-template clone path adds the same Fetch button + hidden field.
- `_library_options.html` renders the result: on `ok`, a radio row per library (name bold,
  id muted); on `not ok`, an inline error styled like `_test_result.html`; on an empty
  list, "No libraries found."
- **Detail render:** `_folder_editor.html` shows `library_name` as the field's companion
  label (a muted sub-line with the id) when present; when absent, the id alone, unchanged.
- **No-JS / failure fallback:** the Library text input is never removed. The Fetch button
  is `type=button`+JS only, so without JS — or if a fetch fails — the user hand-types the
  id as today. The hidden `library_name` simply stays empty.

`_parse_folder_rows` (`web/pages.py`) reads the new `folder-<i>-library_name` field and
sets it on each `FolderCreate` (empty string → `None`).

---

## Error handling

| Situation | Behavior |
|-----------|----------|
| Bad token / 401 | `LibraryListResult(ok=False, detail="HTTP 401")`, inline error in dialog |
| Host unreachable | `ok=False, detail="ConnectError: …"`, inline error |
| Malformed JSON | `ok=False, detail="unexpected response from Audiobookshelf"` |
| Empty library list | `ok=True, libraries=()` → "No libraries found" message |
| Server type without discovery | endpoint returns "not supported" message, no 500 |
| No JS | Fetch button inert; manual id entry unchanged |

Manual entry remains usable in every failure path.

---

## Testing (rule 6 pyramid)

**Unit**
- ABS `list_libraries()`: parses a sample `{"libraries":[{"id","name"},…]}` payload →
  `LibraryOption`s; maps 401 → `ok=False, "HTTP 401"`; maps a connection error; maps
  garbage JSON → `ok=False` "unexpected response".
- Base default `list_libraries()` returns `ok=False, "not supported"`; webhook inherits it.
- `_parse_folder_rows` round-trips `library_name` (present → set; empty → `None`).
- `create_folder` / `_set_server_folders` persist and reload `library_name`.
- Migration `0002` adds the column; an `app.db` at `0001` upgrades cleanly.

**Integration (FastAPI `TestClient`)**
- `POST /ui/servers/libraries` (unsaved) with a mocked ABS backend → options rendered.
- `POST /ui/servers/{id}/libraries` (stored) uses the stored secret → options rendered.
- A webhook server → "not supported" body, HTTP 200 (not 500).
- Bad-token path → inline error body.

**UI assertions (`tests/web/test_pages.py`)**
- Fetch button + hidden `library_name` present on ABS new/detail pages.
- Absent for a webhook server.
- A saved folder with a `library_name` renders the friendly label.

---

## Scope & follow-ups

**In this spec:** the general capability (`base.py`), the migration + persistence, both web
endpoints + shared helper, the picker UI, and the **ABS** `list_libraries()` implementation.

**Deferred to `docs/FOLLOWUPS.md`** (one method + flag each, verified at implement-time):
- **Plex** — `GET /library/sections` → `Directory[].{key, title}`.
- **Emby / Jellyfin** — `GET /Library/VirtualFolders` → `{Name, ItemId}`.

These light up the same Fetch button automatically once their adapter flips the flag.
