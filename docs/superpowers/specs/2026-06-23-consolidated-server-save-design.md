# Consolidated server save (one "Save changes" for settings + folders)

**Status:** design approved (2026-06-23), pending spec review → implementation plan.

## Problem

The server **detail** page (`server_detail.html`) saves through two separate forms and
endpoints:

- `POST /ui/servers/{id}/update` — server settings → "Save changes"
- `POST /ui/servers/{id}/folders` — the whole folder list → "Save folders"

plus a **Delete server** button buried inside the Edit card's danger zone. The server
**create** page (`server_new.html`) already does it the way we want: **one** form
(settings + folders) posting to **one** endpoint (`/ui/servers/new`) that writes both in a
single transaction (`apply_server_create_with_folders`).

Two problems follow:

1. **UX:** two save buttons on the detail page is confusing; the primary actions aren't at
   the bottom where the eye finishes the form.
2. **Drift risk:** create saves settings+folders atomically through one path; update splits
   them across two paths. The two flows can (and will) diverge over time.

## Goal

Make the detail (update) flow a **structural mirror** of the create flow:

- One `<form>` on the detail page wrapping the shared settings body **and** the folder editor.
- One **"Save changes"** button (at the bottom) that persists settings + folders together,
  **all-or-nothing** (one transaction, one engine rebuild).
- **Delete server** moved to the bottom of the page (keeps its confirm prompt).
- Remove the now-orphaned "Save folders" button, its `/ui/.../folders` route, and the
  write/repo plumbing only it used.

Non-goal: the JSON `/api/servers/{id}/folders` routes and their per-folder cores
(`apply_folder_create/update/delete`) are **untouched**.

## Decisions (locked)

- **All-or-nothing save.** Any invalid part (bad folder path, failed §D secret gate, server
  gone) → nothing persisted, one inline error. Mirrors `create_server_with_folders`.
- **Remove the old UI endpoint.** Delete `ui_sync_folders` (`POST /ui/servers/{id}/folders`).
  Its only write core (`apply_folders_sync`) and the only repo method that core used
  (`replace_folders`) become orphaned and are removed too; their behavioral coverage moves
  to the new combined paths.
- **Parallel twins, not a merged path.** Create and update stay as explicit parallel
  functions sharing leaf-level helpers — *not* one parameterized save function. Their bodies
  diverge intrinsically (type assignment, secret tri-state, redirect-vs-inline,
  409-vs-`KeyError`); merging would trade clear mirrors for conditional soup. This matches the
  existing `apply_server_create` / `apply_server_update` split.

## Alignment map (create ⇄ update)

`shared` = one source · `mirrored` = parallel twins, edit together · `intrinsic` = differs by
necessity.

| Layer | Create | Update | Status |
|---|---|---|---|
| Form body | `_server_form_fields.html` (`creating=True`) | same (`creating=False`) | shared |
| Folder editor + JS | `_folder_editor.html`, `_folder_rows_script.html` | same | shared |
| Page skeleton | one form: fields + folders + bottom actions | restructure detail page to match | mirrored |
| Folder row parse | `_parse_folder_rows(form)` | same | shared |
| Handler | `ui_create_server_with_folders` | `ui_update_server` | mirrored |
| Write core | `apply_server_create_with_folders` | `apply_server_update_with_folders` | mirrored |
| Secret gate | `_require_secret_or_422` | same | shared |
| Repo method | `create_server_with_folders` | `update_server_with_folders` | mirrored |
| Folder→FileType build | inline loop (today) | would be a 3rd copy | **→ extract shared helper** |
| Success | 204 + `HX-Redirect` to detail | `_saved.html` inline | intrinsic |
| Dup-name conflict | `IntegrityError` → 409 inline | **add the same** `IntegrityError` → 409 inline | mirrored *(fixes existing drift — see below)* |
| Server-gone | n/a | `KeyError` → inline | intrinsic |
| Type / secret | sets type + initial secret | type fixed; secret tri-state keep/replace/clear | intrinsic |

## Design

### Frontend — `server_detail.html`

Restructure to parallel `server_new.html`:

- Keep the **Connection test** card at the top, separate from the save flow. (It probes the
  *stored* server via `POST /ui/servers/{id}/test` — an intrinsic difference from create's
  "test the unsaved config" button, so it stays its own card.)
- Wrap the **Settings** section (`_server_form_fields.html`, `creating=False`) and the
  **Folders** section (`_folder_editor.html`, `folders=server.folders`) in **one** `<form>`
  posting to `POST /ui/servers/{id}/update`, `hx-target="#save-status"`, `hx-swap="innerHTML"`.
- **Bottom actions bar** (inside the form): `Save changes` submit + an inline `#save-status`
  slot, with a `#save-error` slot for retargeted errors.
- **Delete server** stays a *separate* sibling `<form>` (HTML forms can't nest) posting to
  `/delete` with its existing `hx-confirm`, placed in a visually separated danger row at the
  very bottom, below the save bar.
- Drop the Folders intro line's "Save folders" wording.
- The existing token-field `<script>` and `_folder_rows_script.html` are unaffected — they
  operate on elements that are now simply inside the one form.

CSS: **reuse the existing `.form-actions`** (the create page's bottom bar — top border +
`.form-status` empty-hiding, `app.css:602`) and **`.danger-zone`** (`app.css:614`) classes
rather than inventing new ones; that's the more faithful mirror of the create page and less
code. Add a new class only if a concrete layout difference demands it.

### Backend

**Repo (`db/repo.py`)**

- Add private helper `_set_server_folders(server, folders)`: clears `server.folders` and
  appends `Folder` + `FileType` rows from a `list[FolderCreate]` (the current inline loop,
  lifted out once). Operates on the session-attached `server` relationship, so it takes no
  `session` arg. Used by both create and update. (`create_server_with_folders` starts with an
  empty `server.folders`, so the leading `clear()` is a harmless no-op there.)
- Route `create_server_with_folders` through `_set_server_folders` (replaces its inline loop).
- Add `update_server_with_folders(server_id, data: ServerUpdate, folders) -> Server`: in one
  session — `get` (raise `KeyError` if gone), apply `ServerUpdate` fields (secret tri-state,
  same as `update_server`), `_set_server_folders(...)`, commit. Mirror of
  `create_server_with_folders`.
- **Remove** `replace_folders` (logic now lives in `_set_server_folders`).

**Write core (`web/writes.py`)**

- Add `apply_server_update_with_folders(repo, engine, server_id, data, folders) -> Server`:
  fetch existing (`KeyError` if gone), compute `resulting_has_secret` from the tri-state +
  existing (same as `apply_server_update`), call `_require_secret_or_422`, then the single
  `repo.update_server_with_folders(...)` **wrapped in `try/except IntegrityError → _name_conflict`
  (409)**, then **one** `rebuild_engine`. Structural twin of `apply_server_create_with_folders`.
- **Drift fix (from review):** `Server.name` is `unique=True`, so renaming a server to an
  existing name raises `IntegrityError` on commit. Today's `apply_server_update` has **no**
  `IntegrityError` guard and `ui_update_server` catches only `(HTTPException, ValueError,
  KeyError)` — so a rename-collision **500s** (and, post-consolidation, would also visually
  discard the unsaved folder edits). The new core catches `IntegrityError → _name_conflict`
  (409) exactly like create, giving the two flows true parity and honoring the all-or-nothing
  inline-error promise. The transaction already rolls back, so data is safe regardless; this
  only converts the 500 into the promised inline 409.
- **Remove** `apply_folders_sync` (orphaned).
- Extend the module docstring: name the create/update-`with_folders` functions as parallel
  twins to edit together (alongside the existing "/api and /ui never drift" note).

**Page handler (`web/pages.py`)**

- `ui_update_server`: keep its `Form(...)` server-field params, additionally
  `form = await request.form()` and `folders = _parse_folder_rows(form)` (the same dual-read
  `ui_create_server_with_folders` already uses), build `ServerUpdate`, and call
  `apply_server_update_with_folders(...)`. Keeps catching `(HTTPException, ValueError,
  KeyError)` — the new core's 409 surfaces as an `HTTPException`, so it lands inline like the
  others. Error targets unify to `#save-error`; success still renders `_saved.html` ("Saved.").
- **Remove** the `ui_sync_folders` route and its `apply_folders_sync` import.

### Data flow

`Save changes` → FastAPI parses server `Form(...)` fields + repeated `folder-i-*` rows →
`ServerUpdate` + `list[FolderCreate]` (schema validators normalize/dedupe at the boundary) →
`_require_secret_or_422` → **one** transaction (`update_server_with_folders`) → **one**
`rebuild_engine` → `_saved.html` into `#save-status`.

### Error handling

Any failure → nothing written → `_error_partial` retargeted to `#save-error`:

- invalid field/path → `ValueError` (schema) → inline.
- missing required secret → `HTTPException` 422 (§D gate) → inline.
- **duplicate name (rename collision) → `IntegrityError` → `_name_conflict` 409 → inline**
  (new — see Drift fix above; was a 500).
- server deleted concurrently → `KeyError` → inline.

All-or-nothing: the single transaction means a rejected save leaves both settings and folders
exactly as they were.

## Testing

- **Migrate** the 3 `replace_folders` repo tests (`tests/db/test_repo.py`) →
  `update_server_with_folders`: swaps whole set / empty clears all / unknown server raises
  `KeyError`. Add one asserting server fields **and** folders change in the same call.
- **Migrate** the 3 `/ui/.../folders` route tests (`tests/web/test_ui_forms.py`) → the
  consolidated `/update` path: whole-list replace, empty clears, missing-server 200 + inline
  error (retarget `#save-error`, **not** `#folder-error`).
  - **Migration trap (from review):** `/update` declares `name`/`scan_mode`/`debounce_mode` as
    required `Form(...)`. The old folder tests POST only `folder-*` rows; migrated as-is,
    FastAPI returns a hard `422 RequestValidationError` **before** the handler runs — not the
    inline-error path. Each migrated POST must include a full valid server payload so it reaches
    the handler (and, for the missing-server case, the `KeyError`→inline branch).
- **Add** a UI duplicate-name test: renaming a server to an existing name via `/update` returns
  200 + inline 409 (retarget `#save-error`), **not** a 500, and persists nothing (covers the
  drift fix).
- **Update** `tests/web/test_pages.py` detail-page assertion: it currently asserts the page
  contains `/ui/.../folders`; change to assert the consolidated `/update` form + a single
  "Save changes" button (and Delete at the bottom).
- **Add** `apply_server_update_with_folders` write-core tests (`tests/web/test_writes.py`):
  secret gate enforced, one rebuild, `KeyError` on missing server, **`IntegrityError`→409 on
  duplicate name** — mirroring the existing `apply_server_create_with_folders` tests.
- **Add** one UI happy-path: `POST /update` with both server fields and folder rows persists
  both with exactly one rebuild.
- The two existing `/update` tests (no folder rows seeded) keep passing — an empty folder set
  is a no-op there.
- Gate: `ruff` + `mypy --strict` + `pytest` all green.

## Files touched

- `mediascanmonitor/web/templates/server_detail.html` — restructure to one form + bottom actions.
- `mediascanmonitor/web/static/app.css` — `.detail-actions` bottom bar (+ danger variant).
- `mediascanmonitor/db/repo.py` — `_set_server_folders` helper, `update_server_with_folders`,
  route `create_server_with_folders` through the helper, remove `replace_folders`.
- `mediascanmonitor/web/writes.py` — `apply_server_update_with_folders`, remove
  `apply_folders_sync`, docstring twin note.
- `mediascanmonitor/web/pages.py` — combined `ui_update_server`, remove `ui_sync_folders`.
- Tests: `tests/db/test_repo.py`, `tests/web/test_ui_forms.py`, `tests/web/test_pages.py`,
  `tests/web/test_writes.py` (migrate + add per above).

## Out of scope

- `/api/servers/{id}/folders` JSON routes and `apply_folder_create/update/delete` — unchanged.
- The extension chip-editor / folder-row layout work (separate, already in the working tree).
