# Phase 2 — Detailed Implementation Plans (index)

Phase 2 of `media-scan-monitor` ("All server types", from [`docs/PLAN.md`](../../PLAN.md)) adds the
four remaining notification backends — **Emby, Jellyfin, Audiobookshelf, and a generic Webhook** —
as adapters conforming to the **Phase 1 frozen `ServerAdapter` contract**. Goal of the phase: any
of the five server types can be configured and fired, each declaring its own `supported_scan_modes`
and a `test()` probe.

Each sub-plan is written for an engineer with zero codebase context: bite-sized TDD tasks (failing
test → run it fails → minimal impl → run it passes → commit), exact file paths, complete code in
every step, exact commands with expected output.

## No new interface contract

Phase 2 introduces **no new shared vocabulary** — it consumes the Phase 1 contract verbatim
([`…-phase1-00-interface-contract.md`](2026-06-17-phase1-00-interface-contract.md) §7): the
`ServerAdapter` ABC (`trigger()`/`test()`), the `TriggerResult`/`TestResult` dataclasses, the
`@register` registry, the `servers/http.py` base (`build_client` / `request_with_retry`), and the
frozen `ScanRequest` (§5) / `ServerRuntime` (§6) types. Every adapter below is "one new file under
`servers/` + a one-line self-registration import + its tests" — exactly the extensibility rule
(CLAUDE.md rule 2). The only Phase-2-local addition is the webhook **template-context vocabulary**,
defined inside sub-plan 03 because nothing else consumes it.

## Documents

| # | File | Builds |
|---|------|--------|
| 01 | [`…-02-01-mediabrowser-adapters.md`](2026-06-20-phase2-01-mediabrowser-adapters.md) | `servers/{emby,jellyfin}.py` — the two MediaBrowser-derived siblings (`POST /Items/{id}/Refresh`, library-refresh only), self-registered. |
| 02 | [`…-02-02-audiobookshelf-adapter.md`](2026-06-20-phase2-02-audiobookshelf-adapter.md) | `servers/audiobookshelf.py` — `POST /api/libraries/{id}/scan`, `Bearer` auth, library-refresh only, self-registered. |
| 03 | [`…-02-03-webhook-adapter.md`](2026-06-20-phase2-03-webhook-adapter.md) | `servers/webhook.py` — configurable method/URL/headers + Jinja2 `SandboxedEnvironment` body & header rendering, self-registered. |

## Dependency graph

```
Phase 1 sub-plan 03 (servers/{base,registry,http}) ──┬─> 02·01 emby + jellyfin
                                                      ├─> 02·02 audiobookshelf
                                                      └─> 02·03 webhook
```

All three sub-plans depend **only** on the merged Phase 1 `servers/` foundation, and are
**independent of each other** — any order, or in parallel. The single shared file each one touches
is `mediascanmonitor/servers/__init__.py`, where each appends **one** self-registration import line
(trivial three-way merge; resolve by keeping all lines).

## Canonical execution order

Phase 2 cannot start until **Phase 1 sub-plan 03** (`servers/{base,registry,http,plex}`) is merged —
the adapters subclass `ServerAdapter`, call `request_with_retry`, and reuse the
`tests/servers/conftest.py` builders (`make_plex_runtime`, `make_scan_request`, the `client`
fixture) it created. After that:

1. **02·01 emby + jellyfin** — two siblings built together (near-identical `/Items/{id}/Refresh`).
2. **02·02 audiobookshelf** — independent; its own `/api/libraries/{id}/scan` shape.
3. **02·03 webhook** — independent; Jinja2-templated arbitrary request.

There is no ordering constraint *between* 01/02/03; the numbering is just a recommended sequence.

## Phase 2 conventions (apply to every sub-plan)

1. **Frozen contract, consumed not changed.** Adapters set the two `ClassVar`s
   (`server_type`, `supported_scan_modes`) and implement `async trigger`/`async test`. Nothing in
   `servers/{base,registry,http}.py` or the contract types is edited.
2. **Library-refresh only for the media servers.** Emby/Jellyfin/Audiobookshelf declare
   `supported_scan_modes = frozenset({ScanMode.library})` — only Plex does native path targeting.
   Their `trigger()` ignores `req.scan_path` and refreshes by `req.library_id`. `library_id` is used
   **as configured** — runtime library *discovery* (e.g. Jellyfin `GET /Library/VirtualFolders`) is
   a Phase 3 UI concern and is **not** in the frozen ABC; see [`docs/FOLLOWUPS.md`](../../FOLLOWUPS.md).
3. **Verify API endpoints at implement-time (rule 1, no trusting memory).** The scan/refresh paths
   and the `test()` probe endpoints below are the documented choices, but each task instructs the
   implementer to **confirm the path + auth header + a 2xx response shape against the backend's
   current official API docs** before pinning them. Quirks live in the adapter's module docstring.
4. **Secrets: header only, never URL, never logged** (contract invariant 3). The media-server
   adapters pass the token in the auth header. The webhook adapter renders the **encrypted** `secret`
   into header/body templates at request time (so a token never lands in the plaintext
   `webhook_headers_json` column), and never logs rendered headers or body.
5. **No new dependencies.** `httpx`, `respx`, `tenacity`, and `jinja2==3.1.6` (already pinned for the
   Phase 3 web UI) cover everything. If the implement-time API check reveals a genuine need for a new
   library, stop and justify it per CLAUDE.md rule 1 before adding.

## Verification gate (every sub-plan, before its PR merges)

`ruff check . && ruff format --check . && mypy mediascanmonitor && pytest` — green. CI
(`.github/workflows/ci.yml`) runs the same. All adapter tests use `respx` to mock httpx transport,
so they run on every platform (no network, no real backend).

## After Phase 2

Phase 3 (web UI + auth, including the library-id discovery dropdowns deferred above) and Phase 4
(observability & image) remain high-level in `docs/PLAN.md` and get their own detailed plans once
Phase 2 lands.
