
If the agent insists on closing serialization before F-03, reject and reorder.

**`TICKET-8z-T0` is stale but not resolved.** T0 described a symptom
(`fs.copy/move/rename._plan` reads instance state). The root cause is F-01 —
the executor never merges `node_spec.params` into the payload. Closing T0 does
**not** close F-01. If the agent points at "T0 closed" as proof F-01 is done,
that is a false closure: reject, and open `TICKET-8.6-root-params`.

**Gate 8.6-pre:**
- Five tickets closed or explicitly deferred with reason.
- Each closed ticket has a named fail-before-pass-after test.
- No changes to `core/executor/engine.py` or `core/planning/builder.py` in this
  phase (those belong to 8.6).
- `git commit` — separate from 8.6.

---

## 4. Phase 8.6 — P0 engine hardening

**Purpose:** the executor actually executes a `params`-only automation with
real `fs_ops` nodes, audit reaches `RunResult.action_logs` exactly once, and
write scope rejects mutations outside writable roots.

**This phase blocks every subsequent phase. Do not start 9 before 8.6 is green.**

### Scope

#### F-01 — Payload unification

- Introduce one helper: `resolve_payload(node_spec, inbound_edges, port_defaults) -> dict`.
- Precedence: `params` < `edges` < `port_defaults`. Edges override params;
  port defaults fill only missing keys.
- Called from **both** `core/planning/builder._node_payload` and
  `core/executor/engine.Executor._build_payload`. One implementation, two
  call sites.
- `TICKET-8z-T0` is closed **only** as a symptom of F-01.

#### F-02 — Write scope

- `check_scope(ctx, path, *, write: bool)`.
- Classify every call site: source = `write=False`; target / trash / sidecar =
  `write=True`.
- `ScopeFirstTracker` records the `write` argument and asserts that every
  mutating `safe_fs.*` call was preceded by `scope.check(..., write=True)`.
- Invariant 7 stops being a tautology (it currently records "check was called",
  not "check was called correctly").

#### F-03 — Audit contract

- One owner for `ctx.audit.append`: the executor.
- Nodes return `ActionLog` / `list[ActionLog]` / `dict["action_log"]`; remove
  `ctx.audit.append` from nodes.
- `_normalize_result` recognises the three return shapes and populates
  `NodeResult.action_logs`.
- The executor appends, finalises status (`planned → dry_run / applied / failed`),
  emits `EVENT_AUDIT_LOGGED` exactly once per entry, and populates
  `RunResult.action_logs`.

#### Vertical slice acceptance test

New file: `tests/test_acceptance_vertical_slice.py`.

- Real `WorkflowDocument` with `fs.make_dir` + `fs.write_file` + `fs.move`,
  **params-only, no edges**.
- Full pipeline: `parse → validate → resolve → build → Executor.execute(dry_run=False)`.
- Asserts:
  - Files exist / were moved as expected.
  - `RunResult.action_logs` contains one entry per mutating node.
  - `ctx.audit.iter_all()` contains **exactly one** copy of each entry (not two).
  - `EVENT_AUDIT_LOGGED` emitted exactly N times.
  - `apply_rollback(RunResult.rollback_plan)` restores the filesystem.
- Second run with `dry_run=True`: filesystem unchanged; `action_logs` status is
  `dry_run`.

#### Regression rewrites

- `tests/test_api_contracts_edge_parity.py` — parametrise on graphs with
  **params + edges**, not edges only.
- `tests/test_fs_ops_integration.py` — rewrite to go through the engine. If the
  engine is genuinely not ready, mark `xfail` with an explicit reason. **Delete
  the "Engine API not exercised in this drop" bypass** — it is a TODO disguised
  as a test.
- `tests/test_scope_first.py` — extend to check the `write` flag.

### Explicitly NOT in 8.6

F-04 through F-09, F-10, F-13, F-14, F-15. Any runtime, LLM, DSL, GUI, or CLI
work. If the agent brings those, reject the phase.

### Gate 8.6

- Vertical slice green.
- Three regression tests green (edge parity with params; fs_ops integration
  engine-based; scope_first with write).
- All three new P0 tests are **red on the 8.5 baseline** (verify by reverting the
  fix and re-running — mutation sanity).
- Full `pytest -q` green.
- `mypy` / `ruff` clean on touched files.
- `PHASE_STATE.md` updated: F-01, F-02, F-03 = **integrated** (not just
  *tested*). T0 closed as symptom. `engine-double-validate` — closed if D1
  collapsed the call, otherwise left open.

---

## 5. Phase 8.7 — P1 correctness

Each item ships with a fail-before-pass-after test. Vertical slice re-run as
regression.

| Finding | Fix | Acceptance |
|---|---|---|
| F-04 | Preview change kinds keyed by node **type**, not instance id | `test_preview_change_kind_by_instance` — instance id ≠ type id → preview shows `MOVE`/`COPY`/`TRASH` |
| F-05 | All relative paths resolve against `config.base_dir`, not CWD | `test_relative_paths_resolve_from_base_dir` — run with CWD outside workspace |
| F-06 | Dry-run and real trash paths share one helper | `test_dry_run_trash_path_matches_real` — freeze `ctx.clock`, assert identical paths |
| F-07 | `safe_fs.set_xattr` wrapper, or formal exemption in `CONTRACT.md` + AST scan | `test_xattr_goes_through_safe_fs` or `test_xattr_exemption_documented` |
| F-08 | Implement 7z extraction via `py7zr`, or drop 7z from `SUPPORTED_FORMATS` with a clear error | `test_archive_7z_extract` or `test_archive_7z_unsupported` |
| F-09 | Real entry point (`lokdocsqr.cli.main:main`); declare `py7zr` / `xattr` in extras; spec file or remove the build claim | `pip install -e .` → `lokdocsqr --version` works |
| F-10A | Verify gate fail-closed in production path (CLI / runtime default to a `PolicyEngine`, not `None`) | `test_high_risk_without_verify_fails_closed` |
| F-15 | Sync `modules.md` / `PHASE_STATE.md` with actual imports and statuses | doc review |

**Gate 8.7:** all items green; vertical slice still green;

---

## 7. Phase 9 — LLM node

**Only after 8.6 is green.**

- `nodes/llm/local_llm.py`: classification, field extraction, summarization,
  plan generation.
- Structured JSON output per schema + confidence + usage.
- **No filesystem mutations.** `classify_node` → `read`.
- Optional deps via lazy import (R7/R20); no hard dependency added.
- Interaction with `utils.json_validate` for schema validation.
- **Acceptance:** automation with an LLM node + mock LLM → plan runs → executor
  calls the LLM → structured output validated by `json_validate`. Vertical slice
  extended to include the mock LLM path.

**Gate 9:** acceptance green; vertical slice (with mock LLM) green;

---

## 8. Phase 10 — Triggers + runtime

- `core/runtime.py`: watcher, cron, single-instance lock, graceful shutdown,
  per-run error isolation.
- `nodes/triggers/*`: `watch_folder`, `scheduled_scan`, `manual_run`, `webhook`.
- `max_concurrency` (F-13): implement bounded concurrency, or mark as reserved
  and remove from the GUI surface.
- **Acceptance:** cron fires an automation twice without corruption; watcher
  sees a file change → executor runs → audit contains entries.

**Gate 10:** acceptance green; vertical slice green;

---

## 9. Phase 10.5 — Automation DSL (was 9.5)

- Parser: DSL → `WorkflowDocument` / `RawGraph`.
- Schema; round-trip: DSL → document → DSL is stable.
- References to runtime triggers.
- **Acceptance:** a sample DSL parses, executes through the runtime, and produces
  audit entries.

**Gate 10.5:** acceptance green; round-trip stable;

---

## 10. Phase 11 — CLI

- Commands: `run`, `validate`, `list-nodes`, `serve`, `audit show`,
  `audit rollback`.
- Flags: `--dry-run`, `--scope`, `--config`.
- **Acceptance:** the vertical slice is reachable through the CLI end-to-end.

**Gate 11:** acceptance green;

---

## 11. Phases 12a / 12b — GUI

- **12a:** `bridge` (core events → Qt signals) + `viewmodels`, no widgets.
- **12b:** `views` + `widgets` + verify dialog + preview diff viewer.
- **GUI smoke (required at both gates):** open a graph → dry-run → see the diff
  → approve → real-run → see the audit entries.

**Gate 12a / 12b:** smoke green;

---

## 12. How to verify the agent at every gate

Send this checklist after every phase. An acceptance report that omits any point
is incomplete.

1. **Names of the tests** that were red before the fix and green after.
2. **Mutation sanity** (mandatory for P0, recommended for the rest): revert the
   fix, re-run, the test goes red. Attach the output.
3. **What is NOT covered, and why.**
4. **Which frozen zones were touched.** Expected: "none beyond the permitted
   list for this phase."
5. **Changes to `pyproject.toml` / `THIRD-PARTY-NOTICES`.** Expected: "none"
   unless the phase explicitly allows it (F-09 in 8.7, F-18 in 8.8).
6. `pytest -q | tail -3`.
7. `mypy` / `ruff` output on touched files.
8. The phase's acceptance checklist from this roadmap.

**Formal phase closure requires all of:**
- Every acceptance item in the phase is green.
- Vertical slice is green.
- Regression 0..N−1 is green.
- `tests/test_architecture.py` is green.
- `PHASE_STATE.md` records *implemented* / *tested* / *integrated* statuses
  separately.

---

## 13. Known traps (memorise these before reviewing any agent output)

| Trap | Symptom | Required response |
|---|---|---|
| False T0 closure | Agent says "T0 is closed, so F-01 is done" | Reject. T0 was a symptom; F-01 is the root. Demand a red-on-baseline test for F-01. |
| Serialization before F-03 | `ActionLog.to_dict` changes land before the audit contract fix | Reject the order. F-03 first, then serialization. |
| Lenient `ActionLog.from_dict` | Invalid `risk` silently becomes `LOW` | Require fail-closed on invalid `risk`; raw value in `extra`. |
| Fake-node engine tests | Executor tests use `_Node` / `_DryMove` / `_RealMove` fixtures | Reject. Slice test must use real `fs_ops` nodes through real `Executor.execute`. |
| Silent test bypass | Test docstring says "engine not exercised" | Delete the test or convert to `xfail` with an explicit reason. |
| Two payload implementations | `builder._node_payload` and `Executor._build_payload` diverge again | Reject. One helper, two call sites. |
| Scope check without `write` | `check_scope` calls `ctx.scope.check(path)` with default `write=False` | Reject. Every mutating call site must pass `write=True`. |
| Double audit append | Node appends to `ctx.audit`, executor appends again | Reject. One owner; nodes return `ActionLog`. |

---

## 14. Status snapshot

| Status | Content |
|---|---|
| **Closed** | 0 … 8z, 8.5, 8.6-pre, 8.6, 8.7, 9, 10, 10.5, 11, 11-final |
| **Next** | 12a / 12b (GUI bridge/viewmodels, then views/widgets) |
| **Blocked** | none for the headless CLI |
| **Background** | 8.8 (tooling) — no blockers |
| **Audit backlog** | remaining tooling items (F-17…F-19) only |

**Baseline after 11-final:** `pytest -q` green (see current `PHASE_STATE.md`).

---

## 15. Definition of done

> Given an automation JSON with params-only nodes, the CLI can plan it, execute it
> in dry-run and real mode, write one audit entry per action, emit the
> corresponding events, produce a non-empty `RunResult.action_logs`, and roll
> back the run using the audit log.

When that sentence is true end-to-end, core is done. Runtime, triggers, DSL,
GUI, and packaging are product shell — predictable work from that point forward.