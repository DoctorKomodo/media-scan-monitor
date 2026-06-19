# Phase 1 — Detailed Implementation Plans (index)

Phase 1 of `media-scan-monitor` ("Engine core + DB + Plex", from [`docs/PLAN.md`](../../PLAN.md))
is split into **one frozen interface contract** plus **six independently-testable sub-plans**.
Goal of the phase: configure DB-seeded Plex servers/folders and get targeted partial scans with
per-server debounce, headless (`run --no-web`).

Each sub-plan is written for an engineer with zero codebase context: bite-sized TDD tasks
(failing test → run it fails → minimal impl → run it passes → commit), exact file paths, complete
code in every step, exact commands with expected output.

## Documents

| # | File | Builds |
|---|------|--------|
| — | [`…-00-interface-contract.md`](2026-06-17-phase1-00-interface-contract.md) | **FROZEN** shared vocabulary: enums, DB models, `SecretBox`, `Repo`, `FsEvent`/`ScanRequest`, `RuntimeConfig`, `ServerAdapter`, watcher/pipeline/engine signatures, cross-plan invariants. Not code — the spine the six plans agree on. |
| 01 | [`…-01-db-and-crypto.md`](2026-06-17-phase1-01-db-and-crypto.md) | `normalize.py` + `db/{models,schemas,crypto,session,repo}.py` — pure normalizers (leaf), SQLModel schema, Fernet secrets, sync `Repo` (CRUD + cascade + `resolve_secret`). |
| 02 | [`…-02-config-runtime.md`](2026-06-17-phase1-02-config-runtime.md) | `config/{defaults,runtime}.py` + `pipeline/events.py` — presets/defaults, `FsEvent`/`ScanRequest`, `build_runtime_config` (reuses `normalize.py`). |
| 03 | [`…-03-server-adapters.md`](2026-06-17-phase1-03-server-adapters.md) | `servers/{base,registry,http,plex}.py` — `ServerAdapter` ABC, registry, httpx+tenacity, Plex adapter. |
| 04 | [`…-04-watcher.md`](2026-06-17-phase1-04-watcher.md) | `watcher/{base,watch_limit,inotify_backend}.py` — `WatcherBackend` + canonical `FakeWatcher`, watch-limit gate, recursive async inotify. |
| 05 | [`…-05-pipeline.md`](2026-06-17-phase1-05-pipeline.md) | `pipeline/{filters,router,debounce,dispatcher}.py` — routing, per-server debounce, isolated fan-out. |
| 06 | [`…-06-engine-cli.md`](2026-06-17-phase1-06-engine-cli.md) | `engine.py`, `observ/logging.py`, `cli.py` (`run --no-web`) — wires everything; live `rebuild()`. |

## Dependency graph

```
01 db&crypto ──┬─> 02 types&runtime ──┬─> 03 servers/plex ──┐
               │                       └─> 04 watcher ──────┤
               │                                            ├─> 05 pipeline ─> 06 engine/cli
               └────────────────────────────────────────────┘
```

## Canonical execution order

Moving the pure normalizers into the leaf module `mediascanmonitor/normalize.py` (owned by 01,
imports nothing from the package) removes the former 01↔02 cycle: 01 no longer reaches up into
`config/` for `normalize_extension`, and 02 depends forward onto 01 only. The plans are now
acyclic at the **module** level, so the order below is just the dependency-graph topological sort
— no file-level interleave to special-case:

1. **01 · db&crypto** — `normalize.py` (leaf) → `db/{models,crypto,session,schemas,repo}.py`.
   Schemas normalize via `normalize.py`; `repo` reuses the same functions. Self-contained.
2. **02 · types&runtime** — `config/{defaults,runtime}.py` + `pipeline/events.py`.
   `defaults.py` needs 01's enums; `runtime.py` needs 01's `Repo`; both reuse `normalize.py`.
3. **03 · servers**, **04 · watcher** — independent of each other; either order (or in parallel).
4. **05 · pipeline** — needs 02 (events/runtime) + 03 (adapter ABC/`TriggerResult`).
5. **06 · engine/cli** — needs everything.

Each numbered step is a sequence of green-CI commits and maps 1:1 to a sub-plan; no sub-plan has
to be split across another's tasks anymore.

## Reconciliation rulings (cross-plan, applied to the docs)

These were resolved during the post-draft self-review and are already reflected in the plan files:

1. **Pure normalizers live in the leaf module `mediascanmonitor/normalize.py` (sub-plan 01),
   not in `config/defaults.py`.** This is the post-review change that broke the old 01↔02 cycle:
   `db` and `config` both depend *down* onto `normalize.py`; neither depends on the other for
   normalization. `config/defaults.py` (presets + debounce defaults) stays owned by 02. Sub-plan
   01's former "Task 0" defaults stub and its SKIP banner are removed.
2. **`FakeWatcher` has one home: `mediascanmonitor/watcher/base.py` (sub-plan 04).** It is widened
   to a superset API (`set_roots`/`roots`/`roots_history`/`current_roots`/`feed`/`emit`/
   `close_stream`/`events`/`aclose`/`closed`) covering every test in sub-plans 05 and 06. Sub-plan
   06's `tests/_helpers.py` **imports and re-exports** it rather than defining its own.

## Verification gate (every sub-plan, before its PR merges)

`ruff check . && ruff format --check . && mypy mediascanmonitor && pytest` — green. CI
(`.github/workflows/ci.yml`) runs the same. Watcher integration tests are Linux-only (gated by a
`skipif`); all other tests run on every platform.

## After Phase 1

Phases 2–4 (all server types, web UI + auth, observability) remain high-level in `docs/PLAN.md`
and get their own detailed plans once Phase 1 lands and has informed the design.
