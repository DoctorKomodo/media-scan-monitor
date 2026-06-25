# Webhook payload presets — design

**Date:** 2026-06-24
**Status:** approved (brainstorming)
**Topic:** Let a webhook server pick a named, app-managed payload format (starting with a
Sonarr/Radarr-compatible preset) instead of only a hand-written Jinja2 body template.

## Motivation

The webhook adapter already renders a freeform Jinja2 `webhook_body_template`, so a Sonarr/
Radarr-shaped payload is technically possible today by hand. The friction is authoring and
maintaining that Jinja correctly. This feature adds a **named preset** layer: the operator
picks a preset from a dropdown and the app owns the payload shape.

Concrete driver: the operator runs [subtitle-pruner](https://github.com/DoctorKomodo/subtitle-pruner),
which accepts Sonarr/Radarr-style webhooks. Its handler reads `file_path` directly (before the
Radarr `movieFile.path` / Sonarr `episodeFile.path` shapes) and short-circuits
`eventType == "Test"` with a success response without processing the path.

## Decisions (locked during brainstorming)

1. **Extensible preset registry**, not a one-off. Adding a future format is one registry entry
   plus one enum member — mirrors the project's one-file-extensibility ethos.
2. **Managed (live) preset.** The server row stores a preset *key*; the adapter resolves the
   built-in template at send time. Improving a preset later benefits every server using it.
   Enum-validated at the boundary (Rule 3). Needs a DB migration (Rule 7).
3. **Minimal + compatible payload** for Sonarr/Radarr: `eventType` + `instanceName` +
   `file_path`. subtitle-pruner reads `file_path` directly, so this works for both movies and
   TV without MSM guessing media type. No `movieFile`/`episodeFile` block.

## Design

### Data model, runtime carrier, and migration

The preset must travel **all the way to the adapter**, which reads a frozen `ServerRuntime`
snapshot (`config/runtime.py`), *not* the `Server` ORM row. The full carrier chain:

`mediascanmonitor/db/models.py` — new enum beside `ServerType` / `ScanMode` / `DebounceMode`:

```python
class WebhookPreset(StrEnum):
    custom = "custom"            # render webhook_body_template (today's behaviour)
    sonarr_radarr = "sonarr_radarr"
```

1. **`Server` model** — new column beside the other webhook-only fields:
   `webhook_payload_preset: WebhookPreset = WebhookPreset.custom`.
2. **Alembic migration `0003_webhook_payload_preset.py`** — `down_revision = "0002"` (current
   head is `0002`). `upgrade` adds the column via `op.batch_alter_table` (SQLite, matches
   `0002`): `sa.Column("webhook_payload_preset", sqlmodel.sql.sqltypes.AutoString(),`
   `nullable=False, server_default="custom")`. `downgrade` drops it. The `server_default`
   keeps pre-existing rows on `custom`.
3. **`ServerRuntime`** (`config/runtime.py`) — add `webhook_payload_preset: WebhookPreset`.
   **Append it as the last field with NO default** (the dataclass is `frozen, slots=True` and
   every existing field is passed explicitly — `secret` already uses `field(repr=False)` with
   no default mid-struct, so a defaulted field inserted earlier would break "non-default follows
   default"). It is passed explicitly by all builders below.
4. **`build_runtime_config`** (`config/runtime.py`) — pass
   `webhook_payload_preset=server.webhook_payload_preset` into the `ServerRuntime(...)` it builds.
5. **`runtime_from_server`** and **`runtime_from_create`** (`web/serverprobe.py`) — both build a
   `ServerRuntime`; thread the field through (from the `Server` row and the `ServerCreate`
   payload respectively). Without `runtime_from_create`, the unsaved "test before save" path
   always probes `custom`.
6. **`make_plex_runtime`** (`tests/servers/conftest.py`) — add a
   `webhook_payload_preset: WebhookPreset = WebhookPreset.custom` kwarg and pass it through, so
   webhook tests can build a runtime carrying `sonarr_radarr`.

`ServerCreate` / `ServerUpdate` in `db/schemas.py` gain the typed field (default
`WebhookPreset.custom`); `repo.create_server` / `update_server` persist it. `ServerRead` +
`ServerRead.from_model` (`web/api_schemas.py`) gain it too, so the edit form can pre-select the
saved preset. Not sensitive — no redaction change.

### Preset registry (extensible — one entry per format)

New module `mediascanmonitor/servers/webhook_presets.py`:

```python
@dataclass(frozen=True)
class WebhookPresetDef:
    label: str          # UI display name
    body_template: str  # built-in Jinja2 rendered with the webhook render context

WEBHOOK_PRESETS: dict[WebhookPreset, WebhookPresetDef] = {
    WebhookPreset.sonarr_radarr: WebhookPresetDef(
        label="Sonarr / Radarr",
        body_template=_SONARR_RADARR_TEMPLATE,
    ),
}
```

`custom` is deliberately **absent** from the registry (it is the "use the operator's own
template" sentinel). A test asserts every non-`custom` `WebhookPreset` member has an entry,
keeping enum and registry in sync.

A small accessor `get_preset(preset: WebhookPreset) -> WebhookPresetDef` raises a clear
`ValueError` on a missing key (mirroring `registry.get_adapter_class`), rather than letting a
bare `KeyError` escape. A one-line comment notes that `webhook.DEFAULT_BODY_TEMPLATE` is the
`custom`-fallback template and is intentionally kept separate from this registry (it is a
fallback, not a named selectable format) so a future reader doesn't duplicate the Jinja.

### Sonarr/Radarr preset template (minimal + compatible)

```jinja
{
  "eventType": {{ ("Test" if is_test else "Download") | tojson }},
  "instanceName": {{ server_name | tojson }},
  "file_path": {{ file_path | tojson }}
}
```

`| tojson` keeps the JSON valid/escaped for paths and the literal eventType string.

**New render-context var `is_test: bool`.** The webhook adapter's `_context` gains it (the
normal `trigger()` path passes `False`; `test()` passes `True`). It lets the preset emit
`eventType: "Test"` for MSM's own Test button and `"Download"` for real `created`/`moved`
events. Without it, the Test button would send `Download` with the synthetic `/__msm_test__`
path and subtitle-pruner would try to process it; with it, subtitle-pruner short-circuits the
Test and returns success, so the Test button correctly reports "reachable". The var is available
to custom templates too. Delete semantics are out of scope (subtitle-pruner has no delete
concept; the operator controls which events a folder watches).

### Adapter resolution (`servers/webhook.py`)

`_send` selects the template before rendering. `self.server` is the `ServerRuntime` snapshot
(see §carrier), so the preset is read from there:

```python
preset = self.server.webhook_payload_preset
if preset == WebhookPreset.custom:
    template = self.server.webhook_body_template or DEFAULT_BODY_TEMPLATE
else:
    template = get_preset(preset).body_template
```

When a preset is active, `webhook_body_template` is ignored. Headers/method are unchanged —
Sonarr/Radarr is a plain `POST application/json`, already the webhook default.

`_send` and `_context` gain an `is_test: bool = False` parameter. `trigger()` calls
`_send(req)` (defaulting `is_test=False`); `test()` calls `_send(probe, is_test=True)`.
`_context` puts the flag into the render context as `is_test`.

### Web UI (`web/templates/_server_form_fields.html`, `web/pages.py`)

- Add a **Preset** `<select>` to the webhook fieldset in `_server_form_fields.html`, above the
  body template. Options come from the registry: `Custom` first (default), then each
  `WEBHOOK_PRESETS` entry by `label`. On the edit page the saved
  `ServerRead.webhook_payload_preset` marks the current option `selected`.
- The per-type show/hide JS is **inline** in `server_new.html` and `server_detail.html` (driven
  by the `#type-specs` JSON the way `.field-webhook` is toggled today). Add: when a non-`Custom`
  preset is selected, hide/disable the body-template textarea (it is ignored). Showing the
  resolved built-in template as read-only help text is a nice-to-have, not required.
- `pages.py` parses a `webhook_payload_preset: str = Form("custom")` in all three handlers —
  `ui_create_server_with_folders`, `ui_update_server`, and `ui_test_server_config` (the last is
  required, else the unsaved Test always probes `custom`). The raw string is handed to
  `ServerCreate` / `ServerUpdate`, whose enum field validates it (Rule 3 boundary); a bad value
  raises `ValueError` inside the existing `try`, flowing through the current inline-error path
  (no 500). Do not coerce to the enum in the handler.

## Testing (TDD)

- **presets registry:** every non-`custom` member has a `WebhookPresetDef`; the Sonarr/Radarr
  template renders to valid JSON containing `file_path` and `instanceName`.
- **adapter:** preset renders the built-in template and ignores `webhook_body_template`; a real
  event emits `eventType: "Download"` + `file_path` + `instanceName`; `test()` emits
  `eventType: "Test"`; `custom` behaviour is unchanged; a path containing quotes/backslashes
  round-trips via `| tojson`. The runtime under test must carry the preset (built via
  `make_plex_runtime(type=ServerType.webhook, webhook_payload_preset=...)`), proving the carrier
  chain, not just the adapter branch.
- **schema/repo:** `webhook_payload_preset` create + update round-trip; default is `custom`.
- **migration:** extend `tests/db/test_migrations.py` to assert the `0003` column exists. To
  actually verify the "old rows keep `custom`" guarantee, the test must step the chain — upgrade
  to `0002`, insert a webhook row, upgrade to `0003`, assert the new column reads `custom` —
  since `init_db` on a fresh DB has no pre-existing rows. (If the stepwise test is judged too
  heavy, drop the existing-row-default claim and assert only column presence + type.)
- **pages/UI:** the webhook fieldset renders the preset `<select>` with the registry options;
  the edit page pre-selects the saved preset; the form parse maps the field into the schema in
  all three handlers (create / update / test).

## Docs

- Short note beside the webhook adapter (module docstring) and the README webhook section
  describing the preset dropdown and the Sonarr/Radarr payload.
- No FOLLOWUPS change — this is net-new work, not a deferred item being completed.
- `scripts/dev_seed.py` seeds a webhook server; switching it (or adding one) to the
  `sonarr_radarr` preset would surface the feature in the dev UI. Optional polish, not required.

## Out of scope (YAGNI)

- Per-preset header/method overrides (the registry def is body-template-only for now).
- A `movieFile`/`episodeFile`-shaped payload (MSM can't distinguish movies from TV, and
  subtitle-pruner already accepts `file_path`).
- Mapping MSM event types to richer Sonarr eventTypes beyond Test/Download.
