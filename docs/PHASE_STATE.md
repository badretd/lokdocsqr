# Phase State

Done: 0-8, 8.6-pre, 8.6, 8.7, 9
Current: 8.8 (background tooling) / 10 unblocked

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
