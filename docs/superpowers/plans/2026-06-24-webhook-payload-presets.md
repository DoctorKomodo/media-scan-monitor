# Webhook Payload Presets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a webhook server pick a named, app-managed payload preset (first: a Sonarr/Radarr-compatible format for [subtitle-pruner](https://github.com/DoctorKomodo/subtitle-pruner)) instead of only a hand-written Jinja2 body template.

**Architecture:** A `WebhookPreset` StrEnum is stored on the `Server` row and carried through the `ServerRuntime` snapshot to the webhook adapter, which resolves the preset key to a built-in Jinja2 template at send time (`custom` = render the operator's own template, as today). A small registry module (`servers/webhook_presets.py`) holds one entry per format. A new `is_test` render-context var lets the Sonarr/Radarr preset announce MSM's own Test button as an `eventType: "Test"` ping.

**Tech Stack:** Python 3.14, SQLModel/SQLite, Alembic, Pydantic v2, FastAPI + htmx + Jinja2, `jinja2.sandbox.SandboxedEnvironment`, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-06-24-webhook-payload-presets-design.md`

## Global Constraints

Copied verbatim from the spec and project rules; every task implicitly includes these:

- **PEP 649 annotations** — never add `from __future__ import annotations`; leave forward refs unquoted.
- **Enums subclass `StrEnum`**, never `(str, Enum)` (ruff `UP042`).
- **Full type hints; `mypy --strict` clean.** Validate external boundaries with Pydantic.
- **Ruff select is exactly `E, F, I, UP, B, C4, SIM, RUF`** (`B` ignored under `tests/**`). No `# noqa` for unselected rules. `mediascanmonitor` is first-party for isort (blank line between third-party and first-party).
- **DB changes go through an explicit Alembic migration** (Rule 7) — never `create_all`.
- **Security:** never log secrets; tokens stay in headers, never the URL. (Unchanged here — Sonarr/Radarr is a plain `POST application/json`.)
- **Extensibility:** adding a future preset must be one enum member + one registry entry; the adapter must not special-case a named preset.
- **Gate before "done":** `ruff format . && ruff check . && mypy mediascanmonitor && pytest` all green.

---

### Task 1: `WebhookPreset` enum, `Server` column, and migration `0003`

**Files:**
- Modify: `mediascanmonitor/db/models.py` (add enum near the other StrEnums; add column to `Server`)
- Create: `mediascanmonitor/migrations/versions/0003_webhook_payload_preset.py`
- Test: `tests/db/test_migrations.py` (column presence + server-default), `tests/db/test_session.py::test_migrations_match_models` (must stay green)

**Interfaces:**
- Produces: `WebhookPreset` StrEnum with members `custom = "custom"`, `sonarr_radarr = "sonarr_radarr"`; `Server.webhook_payload_preset: WebhookPreset` defaulting to `WebhookPreset.custom`.

**Context for the implementer:** `tests/db/test_session.py::test_migrations_match_models` runs `compare_metadata(..., compare_type=True)` and asserts an **empty** diff. SQLModel renders a `StrEnum` column as `sa.Enum(<values>, name="<classname-lowercased>")` (see `0001_initial.py` for `servertype`/`scanmode`). Therefore the migration MUST use `sa.Enum("custom", "sonarr_radarr", name="webhookpreset")` — **not** `AutoString` — or the sync test fails. `server_default` is not compared (only `compare_type`), so it is safe to set.

- [ ] **Step 1: Write the failing migration tests**

Add to `tests/db/test_migrations.py` (it currently imports `Path`, `inspect`, `init_db`; add `import sqlalchemy as sa` at the top with the other imports):

```python
def test_server_table_has_webhook_payload_preset_column(tmp_path: Path) -> None:
    engine = init_db(tmp_path / "app.db")  # runs Alembic upgrade to head
    columns = {c["name"] for c in inspect(engine).get_columns("server")}
    assert "webhook_payload_preset" in columns


def test_webhook_payload_preset_server_default_is_custom(tmp_path: Path) -> None:
    # A row inserted WITHOUT the column (as a pre-0003 row reads after migration) takes the
    # server_default 'custom' — this is the "existing rows keep current behaviour" guarantee.
    engine = init_db(tmp_path / "app.db")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO server (name, type, base_url, verify_tls, timeout_seconds, "
                "scan_mode, debounce_mode, debounce_window_seconds, retry_attempts, enabled) "
                "VALUES ('h', 'webhook', '', 1, 10.0, 'targeted', 'trailing', 30, 3, 1)"
            )
        )
        value = conn.execute(
            sa.text("SELECT webhook_payload_preset FROM server WHERE name = 'h'")
        ).scalar_one()
    assert value == "custom"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/db/test_migrations.py -v`
Expected: both new tests FAIL — `webhook_payload_preset` not in columns / `OperationalError: no such column`.

- [ ] **Step 3: Add the enum and column to the model**

In `mediascanmonitor/db/models.py`, add the enum next to `DebounceMode` (after it):

```python
class WebhookPreset(StrEnum):
    custom = "custom"  # render webhook_body_template (today's behaviour)
    sonarr_radarr = "sonarr_radarr"  # subtitle-pruner-compatible payload
```

In the `Server` class, add the column with the other webhook-only fields (after `webhook_body_template`):

```python
    webhook_payload_preset: WebhookPreset = WebhookPreset.custom
```

- [ ] **Step 4: Create the migration**

Create `mediascanmonitor/migrations/versions/0003_webhook_payload_preset.py`:

```python
"""server.webhook_payload_preset

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "webhook_payload_preset",
                sa.Enum("custom", "sonarr_radarr", name="webhookpreset"),
                nullable=False,
                server_default="custom",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("server", schema=None) as batch_op:
        batch_op.drop_column("webhook_payload_preset")
```

- [ ] **Step 5: Run the migration tests + the model-sync guard to verify they pass**

Run: `pytest tests/db/test_migrations.py tests/db/test_session.py::test_migrations_match_models -v`
Expected: PASS — column present, default `custom`, and the autogenerate diff is empty (the `sa.Enum` type matches the model). If `test_migrations_match_models` reports a diff, the `sa.Enum` name/values do not match the model — fix the migration to mirror exactly.

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/db/models.py mediascanmonitor/migrations/versions/0003_webhook_payload_preset.py tests/db/test_migrations.py
git commit -m "feat(db): WebhookPreset enum + server.webhook_payload_preset column"
```

---

### Task 2: Preset registry module

**Files:**
- Create: `mediascanmonitor/servers/webhook_presets.py`
- Test: `tests/servers/test_webhook_presets.py`

**Interfaces:**
- Consumes: `WebhookPreset` (Task 1).
- Produces:
  - `WebhookPresetDef` — `@dataclass(frozen=True)` with `label: str`, `body_template: str`.
  - `WEBHOOK_PRESETS: dict[WebhookPreset, WebhookPresetDef]` — keyed by preset; `custom` is ABSENT.
  - `get_preset(preset: WebhookPreset) -> WebhookPresetDef` — raises `ValueError` for an unregistered key (incl. `custom`).

- [ ] **Step 1: Write the failing tests**

Create `tests/servers/test_webhook_presets.py`:

```python
"""Webhook payload-preset registry: enum/registry sync + accessor behaviour."""

import pytest

from mediascanmonitor.db.models import WebhookPreset
from mediascanmonitor.servers.webhook_presets import (
    WEBHOOK_PRESETS,
    get_preset,
)


def test_every_non_custom_preset_has_a_definition() -> None:
    # Keeps the enum and the registry in sync: every selectable format is renderable.
    for preset in WebhookPreset:
        if preset is WebhookPreset.custom:
            continue
        definition = WEBHOOK_PRESETS[preset]
        assert definition.label
        assert definition.body_template


def test_custom_is_not_in_the_registry() -> None:
    # `custom` is the "use the operator's own template" sentinel, not a built-in format.
    assert WebhookPreset.custom not in WEBHOOK_PRESETS


def test_get_preset_returns_the_definition() -> None:
    assert get_preset(WebhookPreset.sonarr_radarr).label == "Sonarr / Radarr"


def test_get_preset_rejects_an_unregistered_key() -> None:
    with pytest.raises(ValueError, match="custom"):
        get_preset(WebhookPreset.custom)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/servers/test_webhook_presets.py -v`
Expected: FAIL with `ModuleNotFoundError: mediascanmonitor.servers.webhook_presets`.

- [ ] **Step 3: Write the registry module**

Create `mediascanmonitor/servers/webhook_presets.py`:

```python
"""Named webhook payload presets — one entry per emitted format.

A preset is an app-owned Jinja2 body template selected by key on the webhook server row
(``Server.webhook_payload_preset``). The webhook adapter renders it with the SAME render
context as a custom template, so a preset may use every context var (``file_path``,
``server_name``, ``is_test``, ...).

Adding a format = one ``WebhookPreset`` enum member (``db/models.py``) + one entry here.
``WebhookPreset.custom`` is intentionally ABSENT: it is the "render the operator's own
``webhook_body_template``" sentinel, whose fallback is ``webhook.DEFAULT_BODY_TEMPLATE``
(kept beside the adapter, NOT duplicated here).
"""

from dataclasses import dataclass

from mediascanmonitor.db.models import WebhookPreset

# Sonarr/Radarr-compatible. subtitle-pruner reads ``file_path`` directly and short-circuits
# ``eventType == "Test"`` with a success response; ``is_test`` lets MSM's Test button send a
# recognised Test ping while real file events send "Download". ``| tojson`` keeps the JSON
# valid/escaped for paths and the literal eventType string.
_SONARR_RADARR_TEMPLATE = (
    "{\n"
    '  "eventType": {{ ("Test" if is_test else "Download") | tojson }},\n'
    '  "instanceName": {{ server_name | tojson }},\n'
    '  "file_path": {{ file_path | tojson }}\n'
    "}"
)


@dataclass(frozen=True)
class WebhookPresetDef:
    label: str  # UI display name
    body_template: str  # built-in Jinja2, rendered with the webhook render context


WEBHOOK_PRESETS: dict[WebhookPreset, WebhookPresetDef] = {
    WebhookPreset.sonarr_radarr: WebhookPresetDef(
        label="Sonarr / Radarr",
        body_template=_SONARR_RADARR_TEMPLATE,
    ),
}


def get_preset(preset: WebhookPreset) -> WebhookPresetDef:
    """Return the built-in template for a preset; raise ValueError if it has no entry."""
    try:
        return WEBHOOK_PRESETS[preset]
    except KeyError:
        raise ValueError(f"no webhook preset registered for {preset!r}") from None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/servers/test_webhook_presets.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/servers/webhook_presets.py tests/servers/test_webhook_presets.py
git commit -m "feat(servers): webhook payload-preset registry (Sonarr/Radarr)"
```

---

### Task 3: Carry the preset end-to-end (schemas → repo → ServerRead → runtime)

**Files:**
- Modify: `mediascanmonitor/db/schemas.py` (`ServerCreate`, `ServerUpdate`)
- Modify: `mediascanmonitor/db/repo.py` (`create_server`, `create_server_with_folders`)
- Modify: `mediascanmonitor/web/api_schemas.py` (`ServerRead`, `ServerRead.from_model`)
- Modify: `mediascanmonitor/config/runtime.py` (`ServerRuntime`, `build_runtime_config`)
- Modify: `mediascanmonitor/web/serverprobe.py` (`runtime_from_server`, `runtime_from_create`)
- Modify test ServerRuntime construction sites: `tests/servers/conftest.py`, `tests/_helpers.py`, `tests/pipeline/factories.py`, `tests/config/test_runtime.py`
- Test: `tests/db/test_repo.py`, `tests/config/test_runtime.py`, and a ServerRead assertion in `tests/web/test_pages.py`

**Interfaces:**
- Consumes: `WebhookPreset` (Task 1).
- Produces:
  - `ServerCreate.webhook_payload_preset: WebhookPreset = WebhookPreset.custom`
  - `ServerUpdate.webhook_payload_preset: WebhookPreset | None = None`
  - `ServerRead.webhook_payload_preset: WebhookPreset`
  - `ServerRuntime.webhook_payload_preset: WebhookPreset` (NO default — every construction site passes it, matching the existing `webhook_method` etc. convention)
  - `make_plex_runtime(..., webhook_payload_preset: WebhookPreset = WebhookPreset.custom)`

**Context for the implementer:** `ServerRuntime` is a `@dataclass(frozen=True, slots=True)` whose fields have **no defaults** by convention — every literal passes all fields. Adding a no-default field breaks every `ServerRuntime(...)` site until updated; this task updates them all atomically. `update_server` in `repo.py` already applies `model_dump(exclude_unset=True)` via `setattr`, so it needs **no** change — adding the field to `ServerUpdate` is sufficient.

#### Cycle A — schemas + repo (write path)

- [ ] **Step 1: Write the failing repo round-trip tests**

Add to `tests/db/test_repo.py` (top-of-file imports already include `ServerCreate`; add `ServerUpdate` and `WebhookPreset` as needed):

```python
def test_create_server_persists_webhook_payload_preset(repo: Repo) -> None:
    from mediascanmonitor.db.models import ServerType, WebhookPreset
    from mediascanmonitor.db.schemas import ServerCreate

    created = repo.create_server(
        ServerCreate(
            name="hook-sr",
            type=ServerType.webhook,
            webhook_payload_preset=WebhookPreset.sonarr_radarr,
        )
    )
    assert created.id is not None
    assert repo.get_server(created.id).webhook_payload_preset == WebhookPreset.sonarr_radarr


def test_create_server_defaults_preset_to_custom(repo: Repo) -> None:
    from mediascanmonitor.db.models import ServerType, WebhookPreset
    from mediascanmonitor.db.schemas import ServerCreate

    created = repo.create_server(ServerCreate(name="hook-default", type=ServerType.webhook))
    assert created.id is not None
    assert repo.get_server(created.id).webhook_payload_preset == WebhookPreset.custom


def test_update_server_changes_preset(repo: Repo) -> None:
    from mediascanmonitor.db.models import ServerType, WebhookPreset
    from mediascanmonitor.db.schemas import ServerCreate, ServerUpdate

    created = repo.create_server(ServerCreate(name="hook-upd", type=ServerType.webhook))
    assert created.id is not None
    repo.update_server(
        created.id, ServerUpdate(webhook_payload_preset=WebhookPreset.sonarr_radarr)
    )
    assert repo.get_server(created.id).webhook_payload_preset == WebhookPreset.sonarr_radarr
```

(If `test_repo.py` already imports these symbols at module level, use those instead of the local imports — the local imports just guarantee the snippet is self-contained.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/db/test_repo.py -k webhook_payload_preset -v`
Expected: FAIL — `ServerCreate` has no field `webhook_payload_preset` (Pydantic `ValidationError`/`TypeError`).

- [ ] **Step 3: Add the field to the schemas and persist it in the repo**

In `mediascanmonitor/db/schemas.py`, import the enum (add `WebhookPreset` to the existing `from mediascanmonitor.db.models import ...` line) and add a field to each model.

`ServerCreate` (after `webhook_body_template`):

```python
    webhook_payload_preset: WebhookPreset = WebhookPreset.custom
```

`ServerUpdate` (after `webhook_body_template`):

```python
    webhook_payload_preset: WebhookPreset | None = None
```

In `mediascanmonitor/db/repo.py`, add to the `Server(...)` construction in **both** `create_server` and `create_server_with_folders` (after `webhook_body_template=...`):

```python
                webhook_payload_preset=data.webhook_payload_preset,
```

(In `create_server_with_folders` the variable is `server_data`, so use `webhook_payload_preset=server_data.webhook_payload_preset`.)

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/db/test_repo.py -k webhook_payload_preset -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/db/schemas.py mediascanmonitor/db/repo.py tests/db/test_repo.py
git commit -m "feat(db): carry webhook_payload_preset through ServerCreate/Update + repo"
```

#### Cycle B — `ServerRead` (read path for the edit form)

- [ ] **Step 6: Write the failing ServerRead test**

Add to `tests/web/test_pages.py`:

```python
def test_server_read_carries_webhook_payload_preset(repo) -> None:  # type: ignore[no-untyped-def]
    from mediascanmonitor.db.models import WebhookPreset
    from mediascanmonitor.web.api_schemas import ServerRead

    server = repo.create_server(
        ServerCreate(
            name="hook-read",
            type=ServerType.webhook,
            webhook_payload_preset=WebhookPreset.sonarr_radarr,
        )
    )
    read = ServerRead.from_model(server, [])
    assert read.webhook_payload_preset == WebhookPreset.sonarr_radarr
```

- [ ] **Step 7: Run to verify failure**

Run: `pytest tests/web/test_pages.py::test_server_read_carries_webhook_payload_preset -v`
Expected: FAIL — `ServerRead` has no field `webhook_payload_preset`.

- [ ] **Step 8: Add the field to `ServerRead`**

In `mediascanmonitor/web/api_schemas.py`: import the enum (add `WebhookPreset` to the existing model import from `mediascanmonitor.db.models`), add the field after `webhook_body_template` in the class body:

```python
    webhook_payload_preset: WebhookPreset
```

and in `from_model(...)` after `webhook_body_template=server.webhook_body_template,`:

```python
            webhook_payload_preset=server.webhook_payload_preset,
```

- [ ] **Step 9: Run to verify pass**

Run: `pytest tests/web/test_pages.py::test_server_read_carries_webhook_payload_preset -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add mediascanmonitor/web/api_schemas.py tests/web/test_pages.py
git commit -m "feat(web): expose webhook_payload_preset on ServerRead for edit pre-select"
```

#### Cycle C — `ServerRuntime` carrier (engine + probe paths)

- [ ] **Step 11: Write the failing runtime-propagation test**

In `tests/config/test_runtime.py`, add a `webhook_payload_preset` parameter to the local `make_server` helper (after `enabled`) and pass it into the `Server(...)` it builds:

```python
def make_server(
    server_id: int,
    *,
    name: str,
    type: ServerType = ServerType.plex,
    base_url: str = "https://plex.local:32400",
    scan_mode: ScanMode = ScanMode.targeted,
    debounce_mode: DebounceMode = DebounceMode.trailing,
    enabled: bool = True,
    webhook_payload_preset: WebhookPreset = WebhookPreset.custom,
) -> Server:
    return Server(
        id=server_id,
        name=name,
        type=type,
        base_url=base_url,
        verify_tls=True,
        timeout_seconds=10.0,
        secret_encrypted="ciphertext-ignored-by-stub",
        scan_mode=scan_mode,
        debounce_mode=debounce_mode,
        debounce_window_seconds=30,
        retry_attempts=3,
        enabled=enabled,
        webhook_payload_preset=webhook_payload_preset,
    )
```

Add `WebhookPreset` to the `from mediascanmonitor.db.models import (...)` block at the top of the file, then add the test:

```python
def test_build_runtime_config_carries_webhook_payload_preset() -> None:
    server = make_server(
        1,
        name="hook",
        type=ServerType.webhook,
        webhook_payload_preset=WebhookPreset.sonarr_radarr,
    )
    repo = FakeRepo(servers=[server], folders_by_server={}, secrets={1: None})

    cfg = build_runtime_config(cast("Repo", repo))

    assert cfg.servers[1].webhook_payload_preset == WebhookPreset.sonarr_radarr
```

- [ ] **Step 12: Run to verify failure**

Run: `pytest tests/config/test_runtime.py::test_build_runtime_config_carries_webhook_payload_preset -v`
Expected: FAIL — `AttributeError: 'ServerRuntime' object has no attribute 'webhook_payload_preset'`.

- [ ] **Step 13: Add the field to `ServerRuntime` and thread it through every construction site**

In `mediascanmonitor/config/runtime.py`:
- Add `WebhookPreset` to the `from mediascanmonitor.db.models import ...` line.
- Append the field to `ServerRuntime` (last field, no default):

```python
    webhook_payload_preset: WebhookPreset
```

- In `build_runtime_config`, add to the `ServerRuntime(...)` literal (after `webhook_body_template=...`):

```python
            webhook_payload_preset=server.webhook_payload_preset,
```

In `mediascanmonitor/web/serverprobe.py`, add to **both** runtime builders (after `webhook_body_template=...`):
- `runtime_from_server`: `webhook_payload_preset=server.webhook_payload_preset,`
- `runtime_from_create`: `webhook_payload_preset=data.webhook_payload_preset,`

Now update the remaining `ServerRuntime(...)` literals so the suite compiles (all need the new field):

- `tests/servers/conftest.py` — `make_plex_runtime`: add a keyword param `webhook_payload_preset: WebhookPreset = WebhookPreset.custom` to the signature (next to `webhook_body_template`), add `from mediascanmonitor.db.models import WebhookPreset` to the imports, and pass `webhook_payload_preset=webhook_payload_preset,` in the returned `ServerRuntime(...)`.
- `tests/_helpers.py` — `make_server_runtime`: add `webhook_payload_preset=WebhookPreset.custom,` to the literal and import `WebhookPreset`.
- `tests/pipeline/factories.py` — the `ServerRuntime(...)` literal: add `webhook_payload_preset=WebhookPreset.custom,` and import `WebhookPreset`.
- `tests/config/test_runtime.py` — the three inline `ServerRuntime(...)` literals (in `test_server_runtime_fields_frozen_slotted`, `test_server_runtime_secret_excluded_from_repr`, `test_runtime_config_fields_frozen_slotted`): add `webhook_payload_preset=WebhookPreset.custom,` to each.

- [ ] **Step 14: Run the runtime test, then the whole suite, to verify pass**

Run: `pytest tests/config/test_runtime.py -v && pytest -q`
Expected: the new test PASSES and the **entire** suite is green (every `ServerRuntime(...)` site now supplies the field).

- [ ] **Step 15: Commit**

```bash
git add mediascanmonitor/config/runtime.py mediascanmonitor/web/serverprobe.py tests/servers/conftest.py tests/_helpers.py tests/pipeline/factories.py tests/config/test_runtime.py
git commit -m "feat(config): carry webhook_payload_preset onto ServerRuntime + all builders"
```

---

### Task 4: Adapter resolves the preset + `is_test` context var

**Files:**
- Modify: `mediascanmonitor/servers/webhook.py` (`_context`, `_send`, `trigger`, `test`)
- Test: `tests/servers/test_webhook.py`

**Interfaces:**
- Consumes: `WEBHOOK_PRESETS`/`get_preset` (Task 2), `ServerRuntime.webhook_payload_preset` (Task 3), `make_plex_runtime(..., webhook_payload_preset=...)` (Task 3).
- Produces: webhook adapter that renders the built-in preset template when `webhook_payload_preset != custom`; `is_test` available in the render context (`False` from `trigger`, `True` from `test`).

- [ ] **Step 1: Write the failing adapter tests**

In `tests/servers/test_webhook.py`, extend the local `webhook_runtime` helper to accept the preset, and import `WebhookPreset`. Update the helper signature + body:

```python
def webhook_runtime(
    *,
    base_url: str = URL,
    secret: str | None = None,
    retry_attempts: int = 1,
    webhook_method: str | None = None,
    webhook_headers_json: str | None = None,
    webhook_body_template: str | None = None,
    webhook_payload_preset: WebhookPreset = WebhookPreset.custom,
) -> ServerRuntime:
    return make_runtime(
        type=ServerType.webhook,
        base_url=base_url,
        scan_mode=ScanMode.library,
        secret=secret,
        retry_attempts=retry_attempts,
        webhook_method=webhook_method,
        webhook_headers_json=webhook_headers_json,
        webhook_body_template=webhook_body_template,
        webhook_payload_preset=webhook_payload_preset,
    )
```

Add `from mediascanmonitor.db.models import ScanMode, ServerType, WebhookPreset` (extend the existing import). Then add the tests:

```python
@respx.mock
async def test_sonarr_radarr_preset_emits_download_and_path(client: httpx.AsyncClient) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(webhook_payload_preset=WebhookPreset.sonarr_radarr), client
    )
    res = await adapter.trigger(library_request(file_path="/data/tv/Show/ep.mkv"))
    assert res.ok is True
    body = json.loads(route.calls.last.request.content.decode())
    assert body == {
        "eventType": "Download",
        "instanceName": "My Plex",  # make_plex_runtime default name
        "file_path": "/data/tv/Show/ep.mkv",
    }


@respx.mock
async def test_sonarr_radarr_preset_ignores_custom_body_template(
    client: httpx.AsyncClient,
) -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(
            webhook_payload_preset=WebhookPreset.sonarr_radarr,
            webhook_body_template='{"ignored": true}',
        ),
        client,
    )
    await adapter.trigger(library_request())
    body = json.loads(route.calls.last.request.content.decode())
    assert "ignored" not in body
    assert body["eventType"] == "Download"


@respx.mock
async def test_sonarr_radarr_preset_test_button_announces_test_event(
    client: httpx.AsyncClient,
) -> None:
    # subtitle-pruner short-circuits eventType == "Test", so the Test button must send it.
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(
        webhook_runtime(webhook_payload_preset=WebhookPreset.sonarr_radarr), client
    )
    res = await adapter.test()
    assert res.ok is True
    body = json.loads(route.calls.last.request.content.decode())
    assert body["eventType"] == "Test"


@respx.mock
async def test_custom_preset_still_renders_default_template(client: httpx.AsyncClient) -> None:
    # The default (custom) preset is unchanged: DEFAULT_BODY_TEMPLATE shape with an "event" key.
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    adapter = WebhookAdapter(webhook_runtime(), client)  # preset defaults to custom
    await adapter.trigger(library_request())
    body = json.loads(route.calls.last.request.content.decode())
    assert body["event"] == "created"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/servers/test_webhook.py -k "preset or test_button" -v`
Expected: FAIL — the preset templates are never selected; the body still renders `DEFAULT_BODY_TEMPLATE` (`KeyError`/assertion mismatch on `eventType`).

- [ ] **Step 3: Implement preset resolution + `is_test` in the adapter**

In `mediascanmonitor/servers/webhook.py`:

Add imports (first-party block):

```python
from mediascanmonitor.db.models import ScanMode, ServerType, WebhookPreset
from mediascanmonitor.servers.webhook_presets import get_preset
```

(extend the existing `from mediascanmonitor.db.models import ScanMode, ServerType` line; add the `get_preset` import beside the other `mediascanmonitor.servers...` imports.)

Change `_context` to accept `is_test` and put it in the context:

```python
    def _context(self, req: ScanRequest, *, is_test: bool = False) -> dict[str, Any]:
        return {
            "event_type": req.event_type.value,
            "file_path": req.file_path,
            "host_path": req.file_path,
            "scan_path": req.scan_path,
            "top_folder": req.top_folder,
            "library_id": req.library_id,
            "server_name": self.server.name,
            "secret": self.server.secret or "",
            "is_test": is_test,
        }
```

Change `_send` to take `is_test` and select the template by preset:

```python
    async def _send(self, req: ScanRequest, *, is_test: bool = False) -> TriggerResult:
        url = self.server.base_url.strip()
        if not url:
            return TriggerResult(
                ok=False, status_code=None, detail="webhook url (base_url) is empty"
            )
        method = (self.server.webhook_method or "POST").upper()
        context = self._context(req, is_test=is_test)
        preset = self.server.webhook_payload_preset
        if preset == WebhookPreset.custom:
            template = self.server.webhook_body_template or DEFAULT_BODY_TEMPLATE
        else:
            template = get_preset(preset).body_template
        try:
            body = self._render(template, context)
            headers = self._headers(context)
        except (TemplateError, ValueError, json.JSONDecodeError) as exc:
            return TriggerResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")
        try:
            resp = await request_with_retry(
                self.client,
                method,
                url,
                attempts=self.server.retry_attempts,
                headers=headers,
                content=body.encode("utf-8"),
            )
        except httpx.HTTPError as exc:
            return TriggerResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")
        if resp.is_success:
            return TriggerResult(ok=True, status_code=resp.status_code, detail="webhook delivered")
        return TriggerResult(
            ok=False, status_code=resp.status_code, detail=f"HTTP {resp.status_code}"
        )
```

In `test()`, change the call to mark it a test:

```python
        result = await self._send(probe, is_test=True)
```

(`trigger` still calls `await self._send(req)` — `is_test` defaults to `False`.)

- [ ] **Step 4: Run the new tests, then the whole webhook suite, to verify pass**

Run: `pytest tests/servers/test_webhook.py -v`
Expected: PASS — new preset tests pass AND all pre-existing webhook tests stay green (custom path unchanged).

- [ ] **Step 5: Commit**

```bash
git add mediascanmonitor/servers/webhook.py tests/servers/test_webhook.py
git commit -m "feat(servers): webhook adapter resolves payload preset + is_test context var"
```

---

### Task 5: Web form — preset `<select>`, handler parsing, body-template toggle

**Files:**
- Modify: `mediascanmonitor/web/pages.py` (add `_webhook_preset_options`; pass into `server_new_page` + `server_detail` contexts; add the `webhook_payload_preset` Form param to the three handlers)
- Modify: `mediascanmonitor/web/templates/_server_form_fields.html` (preset `<select>`; `field-webhook-body` class on the body label)
- Create: `mediascanmonitor/web/templates/_webhook_preset_script.html` (hide body template when a preset is active)
- Modify: `mediascanmonitor/web/templates/server_new.html`, `mediascanmonitor/web/templates/server_detail.html` (include the toggle script)
- Test: `tests/web/test_pages.py`, `tests/web/test_ui_forms.py`

**Interfaces:**
- Consumes: `WEBHOOK_PRESETS` (Task 2), `WebhookPreset` (Task 1), `ServerRead.webhook_payload_preset` (Task 3), `ServerCreate`/`ServerUpdate.webhook_payload_preset` (Task 3).
- Produces: `_webhook_preset_options() -> list[tuple[str, str]]` (Custom first, then registry); a `webhook_payload_preset` form field round-tripped by all three webhook-form handlers.

#### Cycle A — render the preset select

- [ ] **Step 1: Write the failing render tests**

Add to `tests/web/test_pages.py`:

```python
def test_webhook_form_renders_payload_preset_select(auth_client: httpx.Client) -> None:
    body = auth_client.get("/servers/new").text
    assert 'name="webhook_payload_preset"' in body
    assert "Sonarr / Radarr" in body  # the registry label is offered


def test_webhook_detail_preselects_saved_preset(auth_client: httpx.Client, repo) -> None:  # type: ignore[no-untyped-def]
    import re

    from mediascanmonitor.db.models import WebhookPreset

    hook = repo.create_server(
        ServerCreate(
            name="hook-pre",
            type=ServerType.webhook,
            webhook_payload_preset=WebhookPreset.sonarr_radarr,
        )
    )
    body = auth_client.get(f"/servers/{hook.id}").text
    # the saved preset's <option> is the selected one
    assert re.search(r'value="sonarr_radarr"[^>]*\bselected\b', body)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/web/test_pages.py -k "payload_preset or preselects" -v`
Expected: FAIL — no `webhook_payload_preset` select in the rendered form.

- [ ] **Step 3: Add the options helper + pass it into both page contexts**

In `mediascanmonitor/web/pages.py`, add imports (first-party block):

```python
from mediascanmonitor.db.models import WebhookPreset
from mediascanmonitor.servers.webhook_presets import WEBHOOK_PRESETS
```

(extend existing imports — `WebhookPreset` joins the `db.models` import; add the registry import beside other `servers` imports.)

Add the helper near `_type_specs`:

```python
def _webhook_preset_options() -> list[tuple[str, str]]:
    """(value, label) for the webhook payload-preset <select>: Custom first, then the registry."""
    options = [(WebhookPreset.custom.value, "Custom")]
    options += [(preset.value, definition.label) for preset, definition in WEBHOOK_PRESETS.items()]
    return options
```

In `server_new_page`'s context dict, add:

```python
            "webhook_presets": _webhook_preset_options(),
```

In `server_detail`'s context dict, add the same line:

```python
            "webhook_presets": _webhook_preset_options(),
```

- [ ] **Step 4: Add the `<select>` to the webhook fieldset**

In `mediascanmonitor/web/templates/_server_form_fields.html`, inside the webhook `<fieldset>`, **above** the body-template label, add:

```html
    <label class="field field-webhook-preset">
      <span class="field-label">Payload preset</span>
      <select name="webhook_payload_preset" id="webhook-preset-select">
        {% for value, label in webhook_presets %}
        <option value="{{ value }}"
          {% if (creating and value == "custom") or (not creating and server.webhook_payload_preset == value) %}selected{% endif %}>{{ label }}</option>
        {% endfor %}
      </select>
    </label>
```

and add the `field-webhook-body` class to the existing body-template label so the toggle script can find it — change:

```html
    <label class="field">
      <span class="field-label">Body template</span>
```

to:

```html
    <label class="field field-webhook-body">
      <span class="field-label">Body template</span>
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/web/test_pages.py -k "payload_preset or preselects" -v`
Expected: PASS. (`server.webhook_payload_preset == value` works because `WebhookPreset` is a `StrEnum`, equal to its value string.)

- [ ] **Step 6: Commit**

```bash
git add mediascanmonitor/web/pages.py mediascanmonitor/web/templates/_server_form_fields.html tests/web/test_pages.py
git commit -m "feat(web): render webhook payload-preset select (pre-selected on edit)"
```

#### Cycle B — parse the field in the three handlers

- [ ] **Step 7: Write the failing form round-trip test**

Add to `tests/web/test_ui_forms.py`:

```python
def test_ui_create_webhook_persists_payload_preset(
    auth_client: httpx.Client,
    repo,  # type: ignore[no-untyped-def]
    engine,  # type: ignore[no-untyped-def]
) -> None:
    from mediascanmonitor.db.models import WebhookPreset

    resp = auth_client.post(
        "/ui/servers/new",
        data={
            "name": "Hook Preset",
            "type": "webhook",
            "scan_mode": "library",
            "debounce_mode": "off",
            "debounce_window_seconds": "30",
            "retry_attempts": "3",
            "timeout_seconds": "10",
            "webhook_payload_preset": "sonarr_radarr",
            "folder-0-path": "/data/tv",
            "folder-0-extensions": "mkv",
            "folder-0-enabled": "on",
        },
    )
    assert resp.status_code == 204
    created = next(s for s in repo.list_servers() if s.name == "Hook Preset")
    assert created.webhook_payload_preset == WebhookPreset.sonarr_radarr
```

- [ ] **Step 8: Run to verify failure**

Run: `pytest tests/web/test_ui_forms.py::test_ui_create_webhook_persists_payload_preset -v`
Expected: FAIL — the handler ignores the field, so the stored preset is `custom`.

- [ ] **Step 9: Parse `webhook_payload_preset` in all three handlers**

In `mediascanmonitor/web/pages.py`:

`ui_create_server_with_folders` — add the Form param (next to `webhook_body_template`):

```python
    webhook_payload_preset: str = Form("custom"),
```

and pass it into the `ServerCreate(...)` (after `webhook_body_template=...`):

```python
            webhook_payload_preset=webhook_payload_preset,
```

`ui_test_server_config` — add the same Form param and pass it into its `ServerCreate(...)`:

```python
    webhook_payload_preset: str = Form("custom"),
```
```python
            webhook_payload_preset=webhook_payload_preset,
```

`ui_update_server` — add the same Form param and add it to the `fields` dict (after `"webhook_body_template": ...`):

```python
    webhook_payload_preset: str = Form("custom"),
```
```python
            "webhook_payload_preset": webhook_payload_preset,
```

The raw string is validated by the schema's `WebhookPreset` enum field (Rule 3 boundary). A bad value raises a Pydantic `ValueError` inside the existing `try`, flowing to the current inline-error path — no handler-side coercion.

- [ ] **Step 10: Run to verify pass**

Run: `pytest tests/web/test_ui_forms.py::test_ui_create_webhook_persists_payload_preset -v`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add mediascanmonitor/web/pages.py tests/web/test_ui_forms.py
git commit -m "feat(web): parse webhook_payload_preset in create/update/test handlers"
```

#### Cycle C — hide the body template when a preset is active (progressive enhancement)

- [ ] **Step 12: Create the toggle script partial**

Create `mediascanmonitor/web/templates/_webhook_preset_script.html`:

```html
<script>
  // When a non-Custom payload preset is selected, the body template is ignored — hide it.
  // No-JS users still see the textarea; the server ignores it whenever a preset is active.
  (function () {
    const sel = document.getElementById("webhook-preset-select");
    const body = document.querySelector(".field-webhook-body");
    if (!sel || !body) return;
    function apply() {
      body.style.display = sel.value === "custom" ? "" : "none";
    }
    sel.addEventListener("change", apply);
    apply();
  })();
</script>
```

- [ ] **Step 13: Include the script on both pages**

In `mediascanmonitor/web/templates/server_new.html`, before `{% include "_folder_rows_script.html" %}`, add:

```html
{% include "_webhook_preset_script.html" %}
```

In `mediascanmonitor/web/templates/server_detail.html`, before `{% include "_folder_rows_script.html" %}`, add the same line.

- [ ] **Step 14: Write + run a smoke test that the include renders**

Add to `tests/web/test_pages.py`:

```python
def test_webhook_preset_toggle_script_present(auth_client: httpx.Client) -> None:
    body = auth_client.get("/servers/new").text
    assert "webhook-preset-select" in body  # the select id the toggle script binds to
    assert "field-webhook-body" in body  # the element it shows/hides
```

Run: `pytest tests/web/test_pages.py::test_webhook_preset_toggle_script_present -v`
Expected: PASS.

- [ ] **Step 15: Commit**

```bash
git add mediascanmonitor/web/templates/_webhook_preset_script.html mediascanmonitor/web/templates/server_new.html mediascanmonitor/web/templates/server_detail.html tests/web/test_pages.py
git commit -m "feat(web): hide webhook body template when a payload preset is active"
```

---

### Task 6: Documentation

**Files:**
- Modify: `mediascanmonitor/servers/webhook.py` (module docstring — note the preset path)
- Modify: `README.md` (webhook section — document the preset dropdown + Sonarr/Radarr payload)

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the webhook adapter module docstring**

In `mediascanmonitor/servers/webhook.py`, extend the configuration list in the module docstring (after the `webhook_body_template` bullet) with:

```
  * webhook_payload_preset -> named app-managed payload (WebhookPreset). "custom"
                              renders webhook_body_template (above); any other value
                              renders a built-in template from servers/webhook_presets.py
                              (e.g. "sonarr_radarr" — a subtitle-pruner-compatible payload).
                              When a preset is active, webhook_body_template is ignored.
```

- [ ] **Step 2: Update the README webhook section**

In `README.md`, in the server-configuration area (near line 125, the "fill in the base URL and API token" instructions), add a short paragraph:

```markdown
For a **Webhook** server you can also choose a **Payload preset**. `Custom` (the default) sends
your own Jinja2 body template; `Sonarr / Radarr` sends a minimal payload
(`eventType` + `instanceName` + `file_path`) compatible with apps that ingest Sonarr/Radarr
webhooks (e.g. subtitle-pruner). The Test button sends that preset's payload with
`eventType: "Test"` so the receiver can recognise it as a probe.
```

- [ ] **Step 3: Final full gate**

Run: `ruff format . && ruff check . && mypy mediascanmonitor && pytest -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add mediascanmonitor/servers/webhook.py README.md
git commit -m "docs: document webhook payload presets (adapter + README)"
```

---

## Self-Review

**Spec coverage:**
- Enum + `Server` column + migration → Task 1. ✔
- `ServerRuntime` carrier + `build_runtime_config` + both `serverprobe` builders + `make_plex_runtime` → Task 3 Cycle C. ✔
- `ServerCreate`/`ServerUpdate` + repo persistence → Task 3 Cycle A. ✔
- `ServerRead` for edit pre-select → Task 3 Cycle B. ✔
- Preset registry (`webhook_presets.py`, `get_preset`, enum/registry sync test) → Task 2. ✔
- Sonarr/Radarr template (minimal `eventType`/`instanceName`/`file_path`, `| tojson`) → Task 2. ✔
- `is_test` context var (`False` on trigger, `True` on test) → Task 4. ✔
- Adapter resolution (`custom` ⇒ body template/default, else built-in) → Task 4. ✔
- Web preset `<select>` + edit pre-select → Task 5 Cycle A. ✔
- Form parse in all three handlers (create/update/test) → Task 5 Cycle B. ✔
- Body-template hide JS (inline per established pattern) → Task 5 Cycle C. ✔
- Migration test (presence + server-default) → Task 1. ✔
- Docs (adapter docstring + README) → Task 6. ✔
- `dev_seed.py` — spec marks it optional polish; intentionally omitted (no task). Noted, not a gap.

**Placeholder scan:** none — every code/step block is concrete.

**Type consistency:** `WebhookPreset` (members `custom`, `sonarr_radarr`); `WebhookPresetDef(label, body_template)`; `WEBHOOK_PRESETS: dict[WebhookPreset, WebhookPresetDef]`; `get_preset(WebhookPreset) -> WebhookPresetDef`; `_webhook_preset_options() -> list[tuple[str, str]]`; `_context(req, *, is_test=False)`, `_send(req, *, is_test=False)`. Names used consistently across Tasks 2/4/5. `ServerRuntime.webhook_payload_preset` has no default (matches the no-default convention); `ServerCreate`/`ServerRead` default/require it; `ServerUpdate` is `| None`. Migration enum type (`sa.Enum("custom","sonarr_radarr", name="webhookpreset")`) matches the model so `test_migrations_match_models` stays green.
