# Phase State

Done: 0-8, 8.6-pre, 8.6, 8.7, 9, 10, 10.5, 11
Current: 8.8 (background tooling)

## 8.6-pre — ticket closeout

- `TICKET-8z-ActionLog-from_dict` — closed (fail-closed audit parsing).
- `TICKET-8z-FlowRetry-port-types` — closed (real booleans).
- `TICKET-8z-T0` — closed in 8.6 as a symptom of F-01.
- `TICKET-8z-RunResult-to_dict` / `TICKET-8z-ActionLog-to_dict` —
  closed after F-03, as required by the ordering rule.
- The pre-phase commit contains no changes to `engine.py` / `builder.py`.

## 8.6 — P0 engine hardening

| Finding | Implemented | Tested | Integrated |
|---|---|---|---|
| F-01 payload unification | yes | yes | yes — `core.payload.build_node_payload`, two call sites |
| F-02 write scope | yes | yes | yes — every mutating fs_ops call uses `write=True` |
| F-03 audit contract | yes | yes | yes — executor is the only `ctx.audit.append` owner |

- Vertical slice: `tests/test_acceptance_vertical_slice.py` (params-only
  `fs.make_dir` + `fs.write_file` + `fs.move`, real pipeline, dry-run,
  one audit copy, one event per entry, rollback).
- Regression rewrites: `test_api_contracts_edge_parity.py` (params + edges),
  `test_fs_ops_integration.py` (engine-based), `test_scope_first.py`
  (write flag enforced).

## 8.7 — P1 correctness

| Finding | Fix | Acceptance |
|---|---|---|
| F-04 | Preview keys off node type | `test_preview_change_kind_by_instance` |
| F-05 | Relative paths use `config.base_dir` | `test_relative_paths_resolve_from_base_dir` |
| F-06 | Dry-run/real trash share one target helper | `test_dry_run_trash_path_matches_real` |
| F-07 | `safe_fs.set_xattr` wrapper | `test_xattr_goes_through_safe_fs` |
| F-08 | 7z removed from supported formats | `test_archive_7z_unsupported` |
| F-09 | Entry point + `xattr` extra; build claim removed | `lokdocsqr --version` |
| F-10A | Default `ExecutionContext` policy is `PolicyEngine.default()` | `test_high_risk_without_verify_fails_closed` |
| F-15 | `modules.md` / `PHASE_STATE.md` synced | doc review |

All 8.7 acceptance tests are green. Per operator instruction, no Git tags
were created for 8.6 or 8.7.

## 9 — LLM node

| Item | Implemented | Tested | Integrated |
|---|---|---|---|
| `llm.local_llm` (classify / extract / summarize / plan) | yes | yes | yes — registered and discovered |
| Structured JSON + confidence + usage | yes | yes | yes — `data`, `text`, `confidence`, `usage` ports |
| No filesystem mutations | yes | yes | yes — `classify_node("llm.local_llm") == "read"` |
| Lazy optional dependency (R7/R20) | yes | yes | yes — `ollama` imported only inside `_get_client` |
| `utils.json_validate` interaction | yes | yes | yes — graph edge `llm.data → validate.data` |

- Acceptance: `tests/test_phase_9.py` (mock client only; the real
  `ollama.Client` is never constructed) covers planning, execution, schema
  validation through the real `utils.json_validate` node, failure modes,
  dry-run purity, and import without the optional dependency.
- Vertical slice extended: `tests/test_acceptance_vertical_slice.py` now runs
  `llm.local_llm → utils.json_validate` alongside the params-only `fs_ops`
  chain. Dry-run is still pure and mutating audit entries remain exactly one
  per fs node.
- Planner: `llm.local_llm` participates in dynamic edge payload resolution
  (`core.planning.builder._DYNAMIC_PAYLOAD_NODE_TYPES`).
- Per operator instruction, no Git tag was created for Phase 9; `pyproject.toml`
  was not changed (the optional client is still lazily imported).

## 10 — Triggers + runtime

| Item | Implemented | Tested | Integrated |
|---|---|---|---|
| `core/runtime.py` watcher + cron host | yes | yes | yes — dispatches real `Executor.execute` |
| Single-instance lock (stale-lock recovery) | yes | yes | yes — acquired by `Runtime.start()` |
| Graceful shutdown | yes | yes | yes — `stop()` joins watcher/scheduler/pool |
| Per-run error isolation | yes | yes | yes — failed run does not stop the host |
| `max_concurrency` (F-13) | yes | yes | yes — bounded `ThreadPoolExecutor`; per-automation serialisation |
| `triggers.watch_folder` | yes | yes | yes — registered, read-only |
| `triggers.scheduled_scan` | yes | yes | yes — registered, read-only |
| `triggers.manual_run` | yes | yes | yes — registered, read-only |
| `triggers.webhook` | yes | yes | yes — registered, read-only |

- Acceptance: `tests/test_phase_10.py` — cron fires an automation twice with
  distinct run ids, intact output and exactly one audit entry per run; the
  folder watcher sees a created file, runs the executor, and leaves an applied
  audit entry.
- Runtime tests also cover deterministic `tick()` scheduling, stale/held
  locks, idempotent shutdown, per-run error isolation, bounded concurrency and
  registration-time trigger binding.
- Vertical slice (`tests/test_acceptance_vertical_slice.py`) remains green.
- No `pyproject.toml` / `THIRD-PARTY-NOTICES` changes: the watcher and cron
  parser are stdlib-only.
- Per operator instruction, no Git tag was created for Phase 10.

## 10.5 — Automation DSL

| Item | Implemented | Tested | Integrated |
|---|---|---|---|
| Parser: DSL → schema dict / `WorkflowDocument` | yes | yes | yes — `core/dsl/parser.py` |
| Serializer + `document_to_dict` (inverse of `parse_graph`) | yes | yes | yes — `core/dsl/serializer.py` |
| Round-trip: DSL → document → DSL stable | yes | yes | yes — text-idempotent |
| Runtime trigger references | yes | yes | yes — `triggers.watch_folder` etc. bind on register |
| Schema documented | yes | — | — — `docs/SCHEMA.md` |

- Public surface: `compile_dsl`, `parse_dsl`, `emit_dsl`, `document_to_dict`
  (`lokdocsqr.core.dsl`).
- Acceptance: `tests/test_phase_10_5.py` — a sample DSL compiles, parses,
  executes through the real `Runtime`, and produces `fs.make_dir` /
  `fs.write_file` audit entries; a declared watch trigger binds at
  registration time.
- Round-trip pinned by `test_round_trip_is_text_idempotent` and
  `test_document_to_dict_inverts_parse_graph`.
- No `pyproject.toml` / `THIRD-PARTY-NOTICES` changes: the DSL parser and
  serializer are stdlib-only.
- Per operator instruction, no Git tag was created for Phase 10.5.

## 11 — CLI

| Item | Implemented | Tested | Integrated |
|---|---|---|---|
| `run` (JSON + DSL, real/dry-run) | yes | yes | yes — real `Runtime`/`Executor` pipeline |
| `compile` (`.ldsq`/JSON → canonical JSON) | yes | yes | yes — `core.dsl.compile_dsl` / `document_to_dict` |
| `validate` | yes | yes | yes — parse + `validate_graph` + build |
| `list-nodes` (human + JSON) | yes | yes | yes — uses the live node registry |
| `serve` (watch/cron host) | yes | parser/help only | yes — delegates to `Runtime.serve` |
| `audit show <run-id>` | yes | yes | yes — reads `AuditLog.lookup_by_run` |
| `audit rollback <run-id>` | yes | yes | yes — `build_rollback_plan` + `apply_rollback` |
| `--dry-run` / `--no-dry-run` | yes | yes | yes — explicit opt-in for real writes |
| `--scope` | yes | yes | yes — overrides config/workflow scope |
| `--config` | yes | yes | yes — `AppConfig` via `load_config` |
| Production policy default (F-10A) | yes | yes | yes — never `policy=None`; fail-closed without opt-in |
| PyInstaller CLI spec | yes | not built here | prepared — `packaging/lokdocsqr-cli.spec` |

- Acceptance: `tests/test_phase_11.py` drives the CLI end-to-end through
  `fs.make_dir` + `fs.write_file` + `fs.move`, checks applied audit entries,
  reads them back with `audit show`, and restores the filesystem with
  `audit rollback`. Dry-run is exercised separately and leaves the workspace
  untouched.
- `--no-dry-run` and `--yes` are the explicit CLI opt-ins that provide the
  verify callback for the built-in fail-closed `PolicyEngine`. A config or
  workflow that asks for a real run without either flag fails closed.
- DSL file extension: `.ldsq` (`core.dsl.DSL_EXTENSION`). Only `.json` and
  `.ldsq` are accepted automation extensions; obsolete DSL suffixes are
  rejected up front. The CLI `run`/`validate`/`compile` paths share the same
  check.
- Packaging is prepared but not executed in this phase:
  `packaging/lokdocsqr-cli.spec`, `packaging/pyinstaller_cli.py` and
  `docs/PACKAGING.md`. The GUI spec belongs to Phase 12.
- No `pyproject.toml` / `THIRD-PARTY-NOTICES` changes: the CLI uses stdlib
  `argparse` and already-declared dependencies; PyInstaller was already in the
  `dev` group.
- Per operator instruction, no Git tag was created for Phase 11; the built
  binary is generated by the operator from the shipped spec.
