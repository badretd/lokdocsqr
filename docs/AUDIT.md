# LokDocSQR — Interim Architectural Audit

**Date:** 2026-11-02  
**Commit:** `41c9ffe` — *ActionProposal to ActionSummary refactor*  
**Branch:** `main`  
**Scope:** Whole repository, read-only, with emphasis on the core execution contract.  
**Report:** New file only. No source, test, configuration, or documentation file was modified.

---

## 0. Executive summary

LokDocSQR contains a substantial and often carefully written set of **core primitives**: a single `safe_fs` mutation layer, a scope manager, graph parsing/validation/resolution, a planner, an executor with retry and policy hooks, an append-only audit log with rollback planning, and four node families (`fs_ops`, `access`, `flow`, `utils`). The code is organised, typed in intent, and covered by a large test suite. On a module-by-module reading, the “core is ~80% complete” claim is **plausible for the primitive layer**.

It is **not** accurate for the product or for the execution contract as a whole. Three defects sit on the main execution path and invalidate the most important architectural promises:

1. **The executor never merges `NodeSpec.params` into the payload.** The planner does. As a result, a normal automation JSON whose nodes configure `params` either fails required-port validation or silently executes with constructor defaults. The flagship `fs_ops` integration test avoids the engine entirely, so this is not caught.
2. **Write scope is not enforced by `fs_ops` nodes.** `check_scope()` calls `ctx.scope.check(path)` without `write=True`. `ScopeManager` then validates against read roots, not writable roots. The invariant-7 test only records that `check()` was called, not how.
3. **The audit/event pipeline is disconnected from `RunResult`.** Real fs nodes append directly to `ctx.audit` and return an `ActionLog`; the executor only collects `NodeResult.action_logs`. Consequently `RunResult.action_logs` is empty, `EVENT_AUDIT_LOGGED` is not emitted for normal nodes, and the executor’s status rewriting is bypassed.

Each of these is a **contract-level** problem, not a cosmetic bug. Together they mean that the system is not yet a reliable end-to-end automation engine, even though its components are individually credible.

**Overall assessment: Amber–Red.** Strong foundations; the integration spine needs to be fixed before any further feature work or “80% done” claim.

Rough estimate, not a precise metric:

| Layer | Readiness | Basis |
|---|---:|---|
| Core primitives (`safe_fs`, scope, models, graph, audit, policy) | ~80–85% | Implemented, unit-tested, mostly coherent |
| Execution integration | ~50–60% | Params, write scope, audit/event contract broken |
| Node coverage (`fs_ops`, `access`, `flow`, `utils`) | ~70–80% | Broad, but several parity/typing gaps |
| Product shell (runtime, triggers, CLI, GUI, packaging) | ~5–15% | Stubs or absent |
| **Shippable product** | **~35–45%** | Depends on the above, not just file count |

---

## 1. Scope and method

### 1.1 What was reviewed

- Repository structure, `pyproject.toml`, `uv.lock`, packaging assets, scripts.
- All documentation in `docs/`: `modules.md`, `CONTRACT.md`, `PHASE_STATE.md`, `SPEC_PHASE_8.md`, `TICKETS_8z.md`, `API_CONTRACTS.md`, `devstart.md`, `ocr-setup.md`.
- All core modules: `safe_fs`, `models`, `scope`, `paths`, `audit`, `config`, `errors`, `events`, `logging`, `plugins`, `catalog`, `clock`, `preview`, `policy/*`, `graph/*`, `planning/*`, `executor/*`, `node_api/*`.
- All implemented node families: `access`, `fs_ops`, `flow`, `utils`; stubs in `cli`, `gui`, `llm`, `planning`, `triggers`.
- Selected tests, especially architecture, invariants, integration, engine, audit, and fs-ops tests.
- Static checks: `pytest`, `ruff`, `mypy`.
- One ad-hoc read-only probe through the real `Executor` (described in Appendix B; it writes only to a temporary directory outside the repository).

### 1.2 What was not reviewed

- Windows-specific behaviour on a real Windows host (the code has Windows retry logic, but this audit ran on Linux).
- The GUI and CLI, because they are stubs.
- LLM, triggers, and runtime, because the modules are absent.
- Dependency CVE scanning and licence compliance beyond the surface `pyproject.toml` / `THIRD-PARTY-NOTICES` check.
- Performance under load, large graphs, or large files.
- A real PyInstaller build (there is no `.spec` file in the tree).

### 1.3 Evidence convention

Each finding below states **Evidence** (what was observed), **Impact** (why it matters architecturally), and **Remediation** (what to do). Findings marked *verified by probe* were reproduced through the real executor, not only inferred from source. Findings marked *by inspection* are source-level conclusions.

---

## 2. What exists and is credible

This is not a greenfield or discarded codebase. The following are real, working assets.

| Area | Assessed state | Evidence |
|---|---|---|
| `core/safe_fs.py` | Strong | Atomic write with temp file + fsync + `os.replace`; cross-device move; transient-lock retry; no public deletion API; `quarantine`/`trash` with timestamp and sidecar |
| `core/scope.py` | Good design, incomplete use | Resolves roots, rejects `..`, handles leaf/ancestor symlinks, separate read/write roots — but write mode is rarely requested |
| `core/models.py` | Broad, with drift | Workflow, node, edge, audit, policy, planning, and result models exist; some serialization loss |
| `core/graph/*` | Solid | Parser, validator, resolver (disabled cascade), builder (topological order, registry lookup) are separated |
| `core/planning/*` | Substantial | Action plans, nested subflow/loop previews, variable merging, proposal validation, policy integration |
| `core/executor/*` | Substantial | Topological execution, retry policy, error handling, dry-run switch, policy/verify gate, event emission |
| `core/audit.py` | Good core | Append-only JSONL, rollback plan, rollback application; writes via `safe_fs.atomic_append` |
| `nodes/fs_ops/*` | Broad | 9 node types: make_dir, write_file, copy, move, rename, trash, archive, convert, tag |
| `nodes/access/*` | Broad | list, read content/metadata, hash/dedupe, extract text, OCR, index/search |
| `nodes/flow/*` | Broad | if/merge/wait/retry/filter/subflow/loop_batch |
| `nodes/utils/*` | Useful | path_template, set_variables, json_validate, notify |
| Tests | Large but unbalanced | 1355 collected; 1341 passed, 11 skipped, 3 xfailed in ~6.6 s |
| Documentation | Extensive | Contract, module map, phase state, API contracts, setup guides |

The architecture is also **genuinely layered in one direction**: `core` does not import `nodes` or `gui`, and the executor resolves concrete nodes only through `NodeRegistry`. The AST tests enforcing this are real and valuable.

---

## 3. Architecture assessment

### 3.1 Layering

**Intended:** `nodes → core.node_api → core`; `gui → core`; `cli → core + nodes`; `core` never imports `gui`, `nodes`, or PySide.

**Actual:**

- The `core → nodes/gui` direction is respected and test-enforced.
- The `nodes → core.node_api` boundary is **not** respected. Nodes import deep core internals:
  - `nodes/flow/subflow.py` and `nodes/flow/loop_batch.py` import `core.executor.engine.Executor`.
  - `nodes/flow/_common.py` imports `core.graph.builder/parser/resolver/validator`.
  - `nodes/utils/set_variables.py` imports `core.executor.context.RESERVED_VARIABLES`.
  - All fs nodes import `core.safe_fs`, `core.models`, `core.errors`, which `modules.md` permits in prose but contradicts its own one-way diagram.
- `nodes/utils/*` imports `nodes.flow._base`, violating `modules.md` §4.0 rule 3 (“not imported outside their node family”).

This is not a circular-import emergency, but it means the “node plugin boundary” is not stable. A third-party node cannot rely on `core.node_api` alone; it would need `core.graph`, `core.executor`, and `core.safe_fs`.

**Assessment:** acceptable for a monolith in pre-alpha, but the documented boundary should either be enforced or corrected. Do not claim a plugin API until this is resolved.

### 3.2 The single mutation layer

**Intended:** only `core/safe_fs.py` mutates the filesystem; `core/logging.py` is the single documented exemption.

**Actual:** mostly true. `fs_ops` nodes route writes through `safe_fs`; `convert`, `archive`, `write_file`, `make_dir`, `move`, `copy`, `rename`, and `trash` do so.

The exception is `nodes/fs_ops/tag.py`, which calls `xattr.setxattr` directly (see F-07). Its module docstring calls this “the single documented exception”, but `docs/CONTRACT.md` documents only `core/logging.py`. The architecture test does not scan `xattr`, so the violation passes.

`tests/test_architecture.py` also has a self-documented blind spot: `open(..., "w"/"a"/"x")` is not detected. In this tree there are no offending direct opens outside `safe_fs`, but the guarantee is review-only, not test-enforced.

**Assessment:** the invariant is close to true, but the exemption process and the AST scan need tightening.

### 3.3 Scope model

`ScopeManager` has the right shape: separate `roots` and `writable` lists, `..` rejection, symlink policy, and `write=True` selecting writable roots.

The problem is **call-site discipline**. `nodes/fs_ops/_common.py::check_scope` always calls `ctx.scope.check(path)` with the default `write=False`. Since most fs nodes use this helper for both source (read) and target (write) paths, target mutations are checked against read roots. When `writable` is narrower than `roots`, writes outside the writable set are allowed. When `ctx.policy is None` — the default `ExecutionContext` — there is no second gate.

The planning validator, by contrast, correctly calls `ctx.scope.check(Path(raw), write=True)` (`core/planning/validator.py`). The intended pattern exists; the node helper did not adopt it.

**Assessment:** the model is sound; enforcement is not. This is a security/contract defect (F-02), not a design flaw.

### 3.4 Dry-run

Dry-run is implemented per node and is treated as first-class in the executor. `Executor` calls `node.dry_run` when `ctx.dry_run` is true, and tests snapshot the workspace for purity. That is a genuine strength.

There are two parity gaps:

- `fs.trash` dry-run plans `trash_dir / origin.name`; the real `safe_fs.trash` adds a `YYYYmmddTHHMMSS_` prefix and collision suffix. The preview is therefore not the path that will be used.
- The executor’s status rewrite (`planned → dry_run/applied/failed`) only runs for `NodeResult.action_logs`. Real nodes return bare `ActionLog`s and append directly, so the rewrite is bypassed.

**Assessment:** dry-run is real, but “preview equals execution” is not yet a contract.

### 3.5 Audit and rollback

The disk-side audit is real: `AuditLog.append` writes JSONL through `safe_fs.atomic_append`; `build_rollback_plan` reads applied, rollback-eligible entries; `apply_rollback` reverses moves/renames/trash. Nodes create meaningful `ActionLog`s with source/target, risk, reason, and rollback flags.

The **in-memory / event-side** audit is broken:

- Real fs nodes call `ctx.audit.append(log)` themselves and return the log (or a list of logs).
- `_normalize_result` treats that return as node output data, not as `NodeResult.action_logs`.
- The executor’s collection loop (`for log in result.action_logs`) therefore sees nothing.
- `RunResult.action_logs` is empty, `EVENT_AUDIT_LOGGED` is not emitted, and the executor cannot rewrite statuses.

On-disk rollback still works because the log was appended directly, which makes the defect easy to miss. But any consumer of `RunResult`, the event bus, or a GUI run monitor sees an incomplete picture.

**Assessment:** durability is good; observability and the result contract are broken (F-03).

### 3.6 Planning / execution parity

The planner merges `node_spec.params` into the payload (`core/planning/builder.py::_node_payload`). The executor does not (`core/executor/engine.py::_build_payload`). This is a direct violation of the *spirit* of invariant 10 (“payload-resolution parity”), even though the invariant’s text and test cover only edge resolution.

The practical consequences are severe:

- A node configured only through `params` gets no static inputs at execution time.
- Required input ports with no edge and no port-level default raise `GraphValidationError`.
- If the port has a default, the default is used instead of the configured value.
- If the operation can proceed with defaults (e.g. `fs.move` with `template` defaulting to `{name}{ext}`), it may report success while doing nothing.
- Policy/verify decisions are built from the same incomplete payload, so `extract_paths` can return empty `target_paths`, causing `validate_proposal` to fail closed for mutating operations when a policy is enabled.

The existing `TICKET-8z-T0` describes the fs copy/move/rename symptom. The root cause is broader: **params are not part of the execution payload at all**. Fixing only `FsTemplateNode._plan` would leave every other node’s params ignored.

**Assessment:** this is the highest-priority defect in the repository (F-01).

### 3.7 Node families

`fs_ops` is the most mature family. `access` is broad and includes lazy OCR imports, which is good for packaging. `flow` implements nested execution; the real child-executor smoke test is a positive sign. `utils` is small and useful.

Weak points:

- Node metadata shape is inconsistent: some classes declare `inputs`/`outputs` as tuples of names (`fs.move`, `fs.trash`, `fs.archive`), others as dicts of port definitions (`fs.make_dir`, `fs.write_file`, `fs.tag`). `describe()` and registry output therefore vary by family.
- Several nodes use `self.*` constructor defaults in planning paths where payload overrides are expected (`FsTemplateNode` is the clearest case).
- `fs.archive` advertises zip and 7z, but extraction is zip-only.
- `fs.tag` mutates extended attributes outside `safe_fs`.
- `fs.convert` supports a deliberately small converter set; unimplemented pairs fail loudly, which is the right behaviour.

### 3.8 Product shell

The following are absent or stubs:

| Component | State |
|---|---|
| `core/runtime.py` | Absent |
| `nodes/triggers/*` | Package exists, no implementations |
| `nodes/llm/*` | Package exists, no implementations |
| `nodes/planning/*` | Package exists, no implementations (planning lives in `core.planning`) |
| `cli/main.py` | Raises `NotImplementedError` |
| `gui/app.py` | Raises `NotImplementedError` |
| PyInstaller spec | Referenced as `lokkdoc.spec`; absent |
| CI | No `.github/` or equivalent |
| Concurrency | `max_concurrency` is parsed and validated but never used |

This is the missing “20%” — and it is the part that turns a library into a product.

### 3.9 Principle scorecard

Against the six principles in `README.md`:

| Principle | Verdict | Notes |
|---|---|---|
| 1. One-way dependencies | **Partial** | `core` isolation holds; `nodes → core internals` does not |
| 2. Only `safe_fs` touches the filesystem | **Partial** | Exception: `fs.tag` xattr; scan blind spots |
| 3. All filesystem work through `WorkspaceScope` | **No** | Write scope not requested by fs nodes; default policy is `None` |
| 4. Dry-run is first-class | **Partial** | Implemented, but preview/real parity gaps |
| 5. Audit is append-only | **Partial** | Disk log is append-only; `RunResult`/events are disconnected |
| 6. Verify is mandatory before risky operations | **No by default** | Only enforced when a `PolicyEngine` is supplied; default context has `policy=None` |

---

## 4. Findings

### 4.1 Critical

#### F-01 — Executor drops all node `params`; planner/executor payloads diverge

**Severity:** Critical  
**Evidence:** `verified by inspection and probe`

- Planner: `core/planning/builder.py::_node_payload` starts with `dict(node_spec.params)`.
- Executor: `core/executor/engine.py::_build_payload` builds only from inbound edges and port defaults; it never reads `node_spec.params`.
- The ad-hoc probe ran a real `fs.move` graph with `params={"source": ..., "template": "b.txt", "conflict_policy": "overwrite"}` through `Executor.execute`. Result: `success=True`, but the source file still existed and `b.txt` did not. The node had used its constructor default `{name}{ext}`, rendered the source’s own name, and skipped as a no-op.
- The probe’s `fs.make_dir` with `params={"path_template": "work"}` did not create `work`; the executor used a port default instead, and the relative path was resolved against the process CWD.
- `tests/test_fs_ops_integration.py` explicitly says “Engine API not exercised in this drop” and falls back to direct node calls, manually applying `template`/`conflict_policy` to the node instance. That is why the golden test passes while the real engine path is broken.

**Impact:**

- Automation JSON `params` are effectively ignored at runtime. This breaks the central user story: configure a node, run the graph.
- The planner and executor can show/validate one target and execute against another, violating H2 parity.
- With a policy engine enabled, `extract_paths(payload, ...)` can see no target paths and fail closed, so the policy layer cannot compensate.
- `TICKET-8z-T0` is a symptom, not the root cause. Fixing only `_plan` will not fix the other nodes.

**Remediation:**

1. In `_build_payload`, seed the payload with `dict(node_spec.params)` before applying edge values and port defaults. Decide and document precedence: static params first, edge values override (recommended), port defaults fill only missing keys.
2. Use the same precedence in `builder._node_payload` so planning and execution are byte-for-byte aligned. Add a shared helper rather than duplicating the logic.
3. Add a contract test that executes a real `WorkflowDocument` through `parse → validate → resolve → build → Executor.execute` with params only (no edges) and asserts the filesystem effect.
4. Add a parity test comparing `_node_payload(..., resolve_edges=True)` with `Executor._build_payload` for a graph containing params and edges.
5. Make `tests/test_fs_ops_integration.py` use the engine, not the direct-node fallback. If the engine is not ready, mark the test `xfail` rather than silently bypassing it.

---

#### F-02 — Write scope is not enforced by fs nodes

**Severity:** Critical  
**Evidence:** `verified by inspection`

- `nodes/fs_ops/_common.py::check_scope` calls `ctx.scope.check(path)` with no `write=True`.
- `core/scope.py::ScopeManager.check` selects `self._writable` only when `write=True`; otherwise it uses `self._roots`.
- `ScopeManager` defaults `_writable` to `_roots` when the scope has no explicit writable list, which hides the bug in simple configurations.
- `tests/test_scope_first.py` wraps `scope.check` but records only the path and return value; it does not inspect the `write` keyword. Invariant 7 therefore passes even though every fs mutation was authorized against read roots.
- `ExecutionContext.policy` defaults to `None`, so the policy gate does not run by default.

**Impact:**

- A workspace configured with `roots=[/data]` and `writable=[/data/out]` would allow a node to mutate `/data/in` if the node author used `check_scope`.
- The contract “all filesystem work is confined to allowed roots declared by WorkspaceScope” is true for read roots but false for write roots.
- This is a security boundary, not just a correctness issue: a malformed template or edited policy payload can target a read-only area.

**Remediation:**

1. Extend `check_scope(ctx, path, *, write: bool)` and call it with `write=True` for every target/trash/sidecar path; use `write=False` only for sources that are read.
2. Audit every `check_scope`/`ctx.scope.check` call site and classify source vs target.
3. Update invariant 7’s tracker to record the `write` argument and assert that every mutating `safe_fs.*` call was preceded by `scope.check(..., write=True)`.
4. Add a test with disjoint read and writable roots: a write inside read-only roots must raise `ScopeViolation`, and a write inside writable roots must succeed.
5. Consider making `validate_proposal` (which already uses `write=True`) the mandatory pre-execution gate even when `policy is None`, or at least run the scope check unconditionally before `node.run`.

---

#### F-03 — Audit records and events do not reach `RunResult`

**Severity:** High (critical for observability)  
**Evidence:** `verified by inspection and probe`

- Real fs nodes call `ctx.audit.append(log)` and return an `ActionLog`, a `dict` containing `action_log`, or a list of logs.
- `_normalize_result` converts the return value into `NodeResult.outputs`; it does not populate `NodeResult.action_logs`.
- The executor only iterates `result.action_logs` to append, rewrite status, and emit `EVENT_AUDIT_LOGGED`.
- The probe’s real `fs.move` run produced `RunResult.action_logs == []`, while the node had written to the audit file.
- Engine tests use fake nodes that return `NodeResult(action_logs=[...])`, so the real-node path is not covered. `tests/test_integration_arch.py` and `tests/test_context_engine.py` are examples.

**Impact:**

- `RunResult.action_logs` is not a reliable record of what happened.
- `EVENT_AUDIT_LOGGED` is never emitted for ordinary fs nodes, so a GUI/run monitor cannot observe audit entries.
- The executor’s status rewriting (`planned → dry_run/applied/failed`) and its rollback-plan construction become inconsistent with node-written logs.
- There is a future double-logging hazard if a node is changed to return `NodeResult(action_logs=...)` while still appending directly.

**Remediation:**

1. Choose one owner for audit append and status finalization. Recommended: nodes return `ActionLog`/`NodeResult`; the executor appends, finalizes status, emits `EVENT_AUDIT_LOGGED`, and populates `RunResult.action_logs`. Remove `ctx.audit.append` from nodes.
2. Alternatively, keep node-side append but have `_normalize_result` explicitly recognize `ActionLog`, `list[ActionLog]`, and `dict["action_log"]` and expose them as `action_logs` without re-appending. This is less clean but smaller.
3. Add a real-node test that runs `fs.write_file` and `fs.move` through `Executor.execute` and asserts:
   - `RunResult.action_logs` contains the entries,
   - `ctx.audit.iter_all()` contains exactly one copy,
   - `EVENT_AUDIT_LOGGED` was emitted once per entry,
   - dry-run entries have status `dry_run`.
4. Remove or rewrite the fake-node tests that currently mask this gap.

---

### 4.2 High

#### F-04 — Preview change kinds use the graph instance id as a node-type key

**Severity:** High  
**Evidence:** `verified by inspection`

- `core/preview.py::NODE_CHANGE_KIND` is keyed by node type (`"fs.move"`, `"fs.copy"`, `"fs.rename"`, `"fs.trash"`).
- `_resolve_change_type` looks up `NODE_CHANGE_KIND.get(proposal.node_id)`.
- `ActionSummary.node_id` is the graph instance id (`"n_move"`), not the node type.
- Unless an author names every node after its type, move/copy/rename/trash previews fall back to `_change_type_from_operation`, where `write` becomes `CREATE`/`MODIFY`.

**Impact:** The preview API shows the wrong change kind for the most important filesystem operations. A move appears as a create/modify, which misleads the GUI and any approval flow.

**Remediation:** Carry the node type on `ActionSummary` (or map instance id → type in the preview layer) and look up `NODE_CHANGE_KIND` with the type. Add a preview test using instance ids that differ from type ids.

---

#### F-05 — Relative paths resolve against process CWD, not `config.base_dir`

**Severity:** High  
**Evidence:** `verified by inspection and probe`

- `nodes/access/_base.py` resolves relative paths against `ctx.config.base_dir`.
- `nodes/fs_ops/_base.py::_plan` uses `Path(payload["source"])`; `Path`/`safe_fs` resolve relative paths against `os.getcwd()`.
- `nodes/fs_ops/trash.py` uses `Path(payload["path"])` and `trash_dir` from config; mixed bases are possible.
- `core/preview.py::_target_exists` uses `Path(primary)`, again CWD.
- `ExecutionContext.resolved_path` correctly uses `config.base_dir`, but fs_ops does not use it.
- `tests/test_fs_ops_integration.py` documents the problem and compensates with `monkeypatch.chdir(tmp_path)`.

**Impact:** The same automation produces different results depending on where the process was launched. The test suite hides this by changing CWD. The CLI/runtime must either guarantee `chdir(base_dir)` or, preferably, resolve all relative paths against `config.base_dir`.

**Remediation:** Centralize path resolution in a helper that takes `ctx` and resolves relative paths against `config.base_dir` (or `ctx.workspace`), then scope-checks with the correct write flag. Use it in fs_ops, access, preview, and trash. Remove the CWD workaround from tests and add a test that runs with CWD outside the workspace.

---

#### F-06 — Dry-run and real trash paths diverge

**Severity:** High (for approval/dry-run trust)  
**Evidence:** `verified by inspection`

- `fs.trash.dry_run` plans `trash_dir / origin.name` and a sidecar path beside it.
- `safe_fs.quarantine` (used by `safe_fs.trash`) creates `trash_dir / f"{YYYYmmddTHHMMSS}_{origin.name}"`, with a numeric suffix on collision.
- The dry-run log therefore shows a different target and sidecar path from the real operation.

**Impact:** A user approving a dry-run sees a path that will not be written. This undermines the “dry-run is first-class” principle and makes audit comparisons unreliable.

**Remediation:** Make the timestamp injectable (`ctx.clock`/freezegun) and share the path-planning logic between dry-run and real execution. At minimum, return a planned path with a deterministic timestamp and test dry-run/real parity.

---

#### F-07 — `fs.tag` mutates extended attributes outside `safe_fs`

**Severity:** High (contract violation)  
**Evidence:** `verified by inspection`

- `nodes/fs_ops/tag.py::_write_xattr` calls `xattr.setxattr` directly.
- The module docstring claims this is “the single documented exception”, but `docs/CONTRACT.md` documents only `core/logging.py`.
- `tests/test_architecture.py` does not scan `xattr`, so invariant 9 does not catch it.
- `xattr` is imported lazily and is not declared in `pyproject.toml`.

**Impact:** The “only `safe_fs` mutates” invariant is false. On platforms without `xattr`, the node silently degrades to sidecar-only; on platforms with it, the mutation bypasses the audit/safe-fs path.

**Remediation:** Either (a) add an explicit `safe_fs.set_xattr` wrapper with scope, audit, and failure semantics, or (b) declare `xattr` a formal, documented exemption in both `CONTRACT.md` and `test_architecture.py`, and add it to dependencies. Prefer (a).

---

#### F-08 — `fs.archive` advertises 7z extraction but only implements zip extraction

**Severity:** High (feature correctness)  
**Evidence:** `verified by inspection`

- `SUPPORTED_FORMATS = ("zip", "7z")`.
- Creation of 7z uses `py7zr` (optional, lazily imported).
- Extraction uses `zipfile.ZipFile` unconditionally in `_run_extract` and `_dry_extract`.
- `py7zr` is not declared in `pyproject.toml`. There is no `[ocr]`-style extra either, despite docs mentioning one.

**Impact:** A user can create a 7z archive and then cannot extract it; the failure will be a `zipfile.BadZipFile`, not a clear “unsupported” error. Packaging may omit `py7zr`, so 7z creation also fails at runtime.

**Remediation:** Implement 7z extraction with `py7zr` behind the same zip-slip gate, or remove 7z from `SUPPORTED_FORMATS` and fail with a clear message. Declare `py7zr` and `xattr` in an explicit optional extra (e.g. `[archive]`, `[xattr]`), and document installation.

---

#### F-09 — Packaging, entry points, and dependencies are inconsistent

**Severity:** High (cannot ship)  
**Evidence:** `verified by inspection`

- `[project.scripts] lokdocsqr = "lokdocsqr:main"`, but `src/lokdocsqr/__init__.py` defines only `__version__` and `__all__`; there is no `main`.
- `cli/main.py` and `gui/app.py` both raise `NotImplementedError`.
- `pandas`, `openpyxl`, `deskew`, `typer`, and `PySide6` are declared but not imported anywhere in `src/` (some are clearly future GUI/OCR work; currently they inflate install size).
- `py7zr` and `xattr` are imported but undeclared.
- Several OCR error hints tell users to install `lokdocsqr[ocr]`, but `pyproject.toml` has no `[ocr]` extra.
- `docs/devstart.md` reports a successful PyInstaller one-dir build (`PASS`, 260 MB), but there is no `lokkdoc.spec` in the repository and no CI workflow. The claim is not reproducible.

**Impact:** `pip install lokdocsqr` yields a broken console script. A clean environment will fail on `fs.archive` 7z and `fs.tag` xattr. The build documentation cannot be trusted as evidence.

**Remediation:** Add a real `main` or change the script target to `lokdocsqr.cli.main:main`; implement at least `--help`/`--version`. Move future dependencies behind extras. Declare `py7zr`/`xattr`. Add a `.spec` file or remove the build claim. Add a minimal CI job that installs, runs tests, and builds.

---

#### F-10 — The product shell is absent, so “80% complete” overstates readiness

**Severity:** High (roadmap)  
**Evidence:** `verified by inspection`

- No `core/runtime.py`; no watcher/cron host.
- `nodes/triggers/*` empty.
- `nodes/llm/*` empty.
- `nodes/planning/*` empty (planning is in core, not nodes).
- CLI/GUI stubs.
- `max_concurrency` parsed and validated but never used; the executor is single-threaded.
- No packaging spec or CI.

**Impact:** The repository is a capable core library plus node implementations, not a usable automation product. The phase state (“Done 0–8, Current 8.5”) measures core module delivery, not end-to-end capability. This mismatch is the main source of the “80%” impression.

**Remediation:** Re-baseline the roadmap around a vertical slice: config → parse → plan → execute → audit → rollback via CLI. Treat runtime/triggers/GUI as later milestones, not as the definition of done for the core.

---

#### F-10A — The verify gate is opt-in, not mandatory

**Severity:** High (contract violation)  
**Evidence:** `verified by inspection`

- `ExecutionContext.policy` defaults to `None`.
- `_check_policy_and_verify` returns the payload unchanged when `ctx.policy is None` (R37 backward-compatibility rule).
- There is no runtime/CLI layer that constructs a non-`None` policy by default, because the runtime and CLI are absent.
- `README.md` principle 6 says verify is mandatory before irreversible or high-risk operations, with an explicit automation-JSON bypass.

**Impact:** In the default context, no policy decision and no verify callback run. High-risk mutations are not gated. This is acceptable as a library default for unit tests, but it is not the documented product behaviour.

**Remediation:** Make the production path (CLI/runtime) construct a default `PolicyEngine` and require an explicit opt-out for risky operations. Alternatively, change the default to the built-in policy engine and require tests to pass `policy=None` explicitly. Add an end-to-end test asserting that a high-risk mutation without a verify callback fails closed.

---

### 4.3 Medium

#### F-11 — Audit serialization loses fields

**Severity:** Medium  
**Evidence:** `verified by inspection`

- `ActionLog.to_dict` omits `original_path`, `trash_path`, `sidecar_path`, and `hash_before`.
- `ActionLog.from_dict` does not restore them.
- `RunResult.to_dict` omits `NodeResult.payload`.
- These are already recorded as `TICKET-8z-ActionLog-to_dict` and `TICKET-8z-RunResult-to_dict`.

**Impact:** Audit round-trip is lossy. Rollback still works from `source`/`target`, but sidecar and hash evidence is lost, weakening forensic value.

**Remediation:** Serialize and restore all fields; add a round-trip property test for `ActionLog` and `RunResult`.

#### F-12 — `ActionLog.from_dict` is asymmetric on invalid data

**Severity:** Medium  
**Evidence:** `verified by inspection`

- Missing `action_type` raises `KeyError`.
- Invalid `risk` silently becomes `LOW`.

**Impact:** Corrupted audit lines with an invalid risk are accepted and understate risk, while a missing action type is rejected. The asymmetry is surprising for an audit-integrity path.

**Remediation:** Decide fail-closed vs lenient for all fields and apply consistently. For audit integrity, fail closed on invalid risk or record the raw value in `extra`.

#### F-13 — Declared concurrency is not implemented

**Severity:** Medium  
**Evidence:** `verified by inspection`

- `WorkflowSettings.max_concurrency` is parsed and validated (`>= 1`).
- The executor processes nodes sequentially; no worker pool or async path exists.

**Impact:** Users can configure a setting that has no effect. If the GUI exposes it, it will appear broken.

**Remediation:** Implement bounded concurrency for independent branches, or remove/validate the setting as “reserved” and document it. Do not expose it in the GUI until it works.

#### F-14 — Node metadata shape is inconsistent

**Severity:** Medium  
**Evidence:** `verified by inspection`

- `fs.move` and `fs.archive` declare `inputs = ("file",)` / `outputs = ("file",)`; `fs.trash` uses `("files",)`.
- `fs.make_dir`, `fs.write_file`, `fs.tag` declare dicts of port definitions.
- `describe()` returns `self.inputs`/`self.outputs` directly in the default path.
- `NodeSpec` parsing expects graph-level ports, not class-level metadata.

**Impact:** Registry metadata, GUI editor generation, and type checking become family-dependent. This will cause friction when the GUI is implemented.

**Remediation:** Standardize on a single port-spec type and have every node declare it. Generate class-level metadata from the same source as `describe()`.

#### F-15 — Docs and code have drifted

**Severity:** Medium  
**Evidence:** `verified by inspection`

Examples:

- `docs/modules.md` says nodes depend only on `core.node_api`; actual nodes import `core.safe_fs`, `core.errors`, `core.models`, `core.executor.engine`, and `core.graph.*`.
- `docs/modules.md` §4.0 says helper modules are not imported outside their family; `nodes/utils/*` imports `nodes.flow._base`.
- `docs/modules.md` names `FsOpsBase(Node)`; the code has `FsNode` and `FsTemplateNode`.
- `docs/modules.md` lists `core/runtime.py`; it is absent.
- `docs/modules.md` lists `core/policy.py`; the code has a `core/policy/` package.
- `README.md` says “nothing works end-to-end”; `PHASE_STATE.md` says phases 0–8 are done. Both can be true only if “done” means “module exists”, which should be stated.
- `docs/devstart.md` reports a build result without a reproducible spec/CI.

**Impact:** New contributors and auditors cannot use the docs as a reliable map. The phase state overstates readiness.

**Remediation:** Add a short “implementation status vs. module map” section to `modules.md`; correct the dependency diagram or the imports; mark missing modules explicitly; make `PHASE_STATE` distinguish “implemented”, “tested”, and “integrated”.

#### F-16 — The test suite is large but does not cover the real execution path

**Severity:** Medium (high for confidence)  
**Evidence:** `verified by inspection`

- 1355 tests collected, 1341 pass in ~6.6 s. That speed is consistent with unit/AST tests, not integration tests.
- `tests/test_fs_ops_integration.py` is the flagship fs_ops test, but it explicitly bypasses the engine and applies params manually.
- Engine tests use fake nodes (`_Node`, `_DryMove`, `_RealMove`) that return `NodeResult(action_logs=...)`; real fs nodes append directly and return bare logs.
- 11 test files read or reference documentation files; more than 100 assertions/reads inspect source text or source structure.
- `tests/test_architecture.py` documents blind spots: `open(..., "w")` is not scanned; `tests/test_invariants.py` documents that `Path.replace` is omitted.
- `xattr` is not scanned.
- There is no test that parses a realistic automation JSON and executes it through the full stack.

**Impact:** The suite gives a false sense of safety for exactly the paths that matter. Many tests verify that code contains certain strings or that helpers behave in isolation; few verify the composed system.

**Remediation:** Add a small set of high-value end-to-end tests (see §5). Replace doc-presence tests with behavioural tests where possible. Expand AST scans to `open(..., "w"/"a"/"x")` and `xattr.*`. Consider mutation testing for the no-delete/no-mutation invariants.

#### F-17 — Type and lint debt is significant and hides real defects

**Severity:** Medium  
**Evidence:** `verified by commands`

- `ruff check src tests`: 440 errors (117 `I001`, 63 `RUF100`, 48 `W292`, 34 `F401`, 23 `RUF012`, plus others); 309 are auto-fixable.
- `mypy src`: 153 errors in 26 files (83 checked) despite `strict = true`.
- The mypy errors include `"FsTemplateNode" has no attribute "template"` and `"conflict_policy"`, which are direct symptoms of F-01.
- `[tool.ruff] src = ["core", "nodes", "gui", "cli"]` does not match the `src/lokdocsqr/...` layout, so import resolution/isort behaviour is misconfigured.

**Impact:** The static checks are not a usable gate. Real defects (the missing base-class attributes) are buried in noise.

**Remediation:** Fix the `src` list, run `ruff --fix` in a dedicated commit, then burn down `mypy` starting with `_base.py` and the executor. Make both clean before adding CI enforcement.

#### F-18 — Dependency and optional-feature declarations are inconsistent

**Severity:** Medium  
**Evidence:** `verified by inspection`

- Declared but unused in `src/`: `pandas`, `openpyxl`, `deskew`, `typer`, `PySide6`.
- Used but undeclared: `py7zr`, `xattr`.
- `python-docx` and Pillow are used via lazy imports in `extract_text`/`ocr`; they are declared, which is correct.
- OCR error hints reference an `[ocr]` extra that does not exist in `pyproject.toml`.
- `pytesseract`/`pypdfium2` are declared as hard dependencies even though OCR is optional.

**Impact:** Install size and failure modes do not match the feature set. A user who installs the base package may get OCR dependencies they do not need; a user who enables 7z/xattr may get an ImportError.

**Remediation:** Define extras (`[ocr]`, `[archive]`, `[gui]`, `[dev]`), keep the base runtime minimal, and declare every lazy import in the appropriate extra. Document the extras in `README.md`.

#### F-19 — No CI, no reproducible build, no release gate

**Severity:** Medium  
**Evidence:** `verified by inspection`

- No `.github/`, no other CI configuration.
- No `.spec` file despite `devstart.md` claiming a build.
- No coverage configuration or threshold.
- No pre-commit configuration.

**Impact:** The 440 lint errors, 153 mypy errors, and the real integration gaps can accumulate unnoticed. The build claim cannot be verified.

**Remediation:** Add CI for install, `pytest`, `ruff`, `mypy`, and a smoke import. Add a packaging job once a spec exists. Keep the gate honest: if mypy is not clean, either fix it or set a documented baseline.

---

### 4.4 Low

#### F-20 — Version/status messaging is inconsistent

`pyproject.toml` says `0.1.0`; `README.md` says pre-alpha; `PHASE_STATE.md` says phases 0–8 done. These are not contradictory, but the combination invites over-reading. Add a short “what works today” section with a concrete vertical slice.

#### F-21 — `path_template` absolute-path restriction is surprising

`core/paths.py::render_template` rejects absolute rendered paths, while access-node paths may be absolute. This is documented in a test docstring but not in the public node docs. It also forces relative outputs through CWD resolution (F-05). Either allow absolute templates after scope-check or document the restriction prominently.

#### F-22 — `RunResult.rollback_plan` is built but never applied automatically

The executor builds a rollback plan at the end of a run; it never calls `apply_rollback`. That is a reasonable safety choice, but the README’s “can be rolled back” should state “manually, via the audit API/CLI”. Add a CLI path for rollback once the CLI exists.

#### F-23 — `flow.retry` port-type contract

`nodes/flow/retry.py::_execute` returns `retry=None` / `pass=None` for the irrelevant branch while the declared port type is boolean. The existing ticket records this as low priority; it matters for GUI/type-aware consumers.

#### F-24 — Audit/read error handling is quiet

`AuditLog.iter_all` logs a warning and returns an empty iterator when the file is missing or unreadable. That is pragmatic, but a run monitor cannot distinguish “no entries” from “audit file unreadable”. Consider surfacing a structured error or a health flag.

---

## 5. Test-suite assessment

### 5.1 What the suite does well

- `safe_fs` has substantial unit coverage: atomic writes, cross-device moves, conflict policies, trash/quarantine, transient retries.
- Graph parsing/validation/resolution is covered, including cycles, dangling edges, disabled cascade, and port defaults.
- Executor behaviour is covered with fake nodes: retry, error handling, dry-run switch, state transitions, policy gate.
- Planning is covered, including nested subflow/loop preview and variable merging.
- Node families have unit tests, including archive zip-slip, convert round-trip, OCR lazy imports, and fs-ops conflict policies.
- Dry-run purity and scope-first are enforced by live wrappers, which is the right instinct.
- The architecture tests catch core→nodes/gui imports and public delete APIs.

### 5.2 What the suite misses

1. **No full-stack execution test.** Nothing parses a realistic automation document, validates it, resolves it, builds it, and executes it through `Executor.execute` with real nodes and `params`.
2. **The flagship integration test bypasses the engine.** `tests/test_fs_ops_integration.py` directly instantiates nodes and manually moves `template`/`conflict_policy` into instance state. It proves node behaviour, not engine behaviour.
3. **Engine tests use fake nodes.** They validate the executor’s retry/audit plumbing with `NodeResult(action_logs=...)`, but not the real fs-node return shape.
4. **Scope-first test does not check `write=True`.** The tracker records paths, not the write flag.
5. **AST scans have known blind spots.** `open(..., "w")` is review-only; `Path.replace` is omitted from `test_invariants.py`; `xattr` is not scanned.
6. **Documentation/source-inspection tests inflate the count.** 11 files read or reference documentation; more than 100 assertions/reads inspect source text or structure. These are cheap and do not increase confidence in runtime behaviour.
7. **No CLI/GUI/runtime tests**, because those layers are stubs.
8. **No Windows tests** for the Windows-specific retry logic.
9. **No performance/large-file tests.**
10. **No coverage threshold or CI gate.**

### 5.3 Recommended test additions (highest value first)

1. `test_engine_executes_params_only_graph`: real `WorkflowDocument` with `fs.make_dir`/`fs.write_file` params, no edges; assert files exist.
2. `test_engine_fs_move_uses_params`: real `fs.move` with `template`/`conflict_policy`; assert source/target.
3. `test_engine_audit_contract`: real fs node; assert `RunResult.action_logs`, `ctx.audit` count, `EVENT_AUDIT_LOGGED` count, statuses.
4. `test_scope_write_enforced`: disjoint read/writable roots; assert mutation in read-only root raises.
5. `test_planner_executor_payload_parity`: property-style comparison for graphs with params + edges.
6. `test_preview_change_kind_by_instance`: instance id ≠ type id; assert MOVE/COPY/TRASH.
7. `test_dry_run_trash_path_matches_real`: freeze the clock; assert planned and actual paths match.
8. `test_relative_paths_resolve_from_base_dir`: run with CWD outside workspace.
9. `test_audit_roundtrip`: `ActionLog` and `RunResult` serialize/deserialize without loss.
10. `test_archive_7z_extract` or an explicit “unsupported” test if the feature is removed.

---

## 6. Build, packaging, and tooling assessment

| Check | Result | Assessment |
|---|---|---|
| `pytest -q` | 1341 passed, 11 skipped, 3 xfailed in 6.56 s | Green, but fast and unit-heavy |
| `ruff check src tests` | 440 errors (309 fixable) | Not a gate; fixable noise |
| `mypy src` | 153 errors in 26 files | Strict config is aspirational, not enforced |
| Entry point | `lokdocsqr:main` missing | Broken install |
| PyInstaller | `lokkdoc.spec` absent | Build claim unverifiable |
| CI | None | No automated gate |
| `[tool.ruff] src` | `core`, `nodes`, `gui`, `cli` | Wrong for `src/lokdocsqr` layout |

The tooling situation is recoverable quickly: fix the ruff `src` list, auto-fix formatting/imports, then address mypy starting with the executor and `fs_ops/_base.py`. Do not enable a hard CI gate until the baseline is clean, or the team will learn to ignore it.

---

## 7. Roadmap risk

### 7.1 The “80%” trap

The repository has many files, many tests, and many implemented nodes. That creates a natural impression of near-completion. But the **critical path** is:

```
automation JSON
  → parse
  → validate
  → resolve
  → build
  → payload (params + edges)
  → policy/scope gate
  → execute
  → audit
  → run result / events
  → rollback
  → CLI / runtime / GUI
```

The first three and the last three are largely present. The middle — payload, scope, audit — is where the defects are. That is why the product is not 80% done even though the module count is high.

### 7.2 What happens if the current path continues

- More node types and more unit tests will increase the apparent completion percentage.
- The real engine path will remain untested and will diverge further from the planner.
- The GUI, when started, will be built against a preview/audit contract that is already wrong.
- Packaging and CLI work will expose the params/scope/audit defects late, forcing a broad refactor.
- The “500+ useless tests” problem in `README.md` will get worse, not better.

### 7.3 Recommended re-baseline

Define “core complete” as a **vertical slice**, not a module checklist:

> Given an automation JSON with params-only nodes, the CLI can plan it, execute it in dry-run and real mode, write one audit entry per action, emit the corresponding events, produce a non-empty `RunResult.action_logs`, and roll back the run using the audit log.

Once that slice is green, the remaining 20% is mostly product shell work (runtime, triggers, CLI, GUI, packaging), which is more predictable.

---

## 8. Prioritized remediation sequence

### P0 — before any new feature or “80%” claim

1. **Fix F-01 (params → payload).** This is the single highest-leverage change. Implement shared payload resolution, align planner/executor, add real-engine tests.
2. **Fix F-02 (write scope).** Add `write` to `check_scope`, classify call sites, extend invariant 7, add disjoint-root test.
3. **Fix F-03 (audit/event contract).** Pick one append owner, normalize real node returns, populate `RunResult.action_logs`, emit events.
4. **Add the vertical-slice acceptance test.** Parse a real automation JSON, execute through the engine, assert files, audit, events, and rollback.

### P1 — correctness and safety

5. **F-04** preview change kinds.
6. **F-05** path base consistency (`config.base_dir`, not CWD).
7. **F-06** dry-run/real trash parity.
8. **F-07** xattr either wrapped or formally exempted.
9. **F-08** 7z extraction or explicit unsupported.
10. **F-09** entry point, extras, dependencies, build spec.
11. **F-10A** make the verify gate fail closed in the production path.
12. **F-15** correct the module map and phase-state language.

### P2 — product and hygiene

13. **F-10** runtime/triggers/CLI/GUI; implement or remove `max_concurrency`.
14. **F-11/F-12** audit serialization and fail-closed parsing.
15. **F-14** uniform node port metadata.
16. **F-16** replace doc-presence/self-referential tests with behavioural tests; expand AST scans.
17. **F-17/F-18/F-19** lint/type baseline, extras, CI.

---

## Appendix A — commands and results

Run from the repository root with the project virtualenv:

```text
.venv/bin/python -m pytest -q
1341 passed, 11 skipped, 3 xfailed in 6.56 s

.venv/bin/python -m ruff check src tests
Found 440 errors.
[*] 309 fixable with `--fix` (37 unsafe fixes available with `--fix --unsafe-fixes`).

.venv/bin/python -m mypy src
Found 153 errors in 26 files (checked 83 source files)
```

Ruff breakdown (top codes): `I001` 117, `RUF100` 63, `W292` 48, `F401` 34, `RUF012` 23, `RUF022` 16, `PTH109` 14, `SIM105` 11, `UP037` 10, `E402` 10.

Mypy breakdown: 50 “Function is missing a type”, 20 “Call to untyped function”, 14 “Returning Any from function”, plus the `FsTemplateNode` missing-attribute errors that point at F-01.

Working tree at audit start and end: clean except for this new `AUDIT.md`.

---

## Appendix B — ad-hoc probe through the real executor

A temporary script outside the repository (`/tmp/opencode/probe.py`) built `WorkflowDocument`/`ResolvedGraph` objects manually and called `core.executor.engine.Executor.execute`. It used a temporary directory, not the repository.

Observed results:

1. **`fs.make_dir` with `params={"path_template": "work"}`**
   - The executor did not see the param.
   - It used a port default instead.
   - In one variant, the default was the relative string `DEFAULT_DIR`; it resolved against the process CWD (`/home/logka/Projects/lokdocsqr/DEFAULT_DIR`) and raised a `ScopeViolation` because that path was outside the temporary workspace.
   - In another variant, the default was an absolute path, which `render_template` rejects with `ScopeViolation: Rendered template is absolute`.
   - In both variants, the configured `work` directory was not created.

2. **`fs.move` with `params={"source": ..., "template": "b.txt", "conflict_policy": "overwrite"}`**
   - Result: `success=True`.
   - Source file still existed; `b.txt` did not exist.
   - The node used `template="{name}{ext}"`, rendered the source’s own path, and skipped as a no-op.
   - `RunResult.action_logs` was `[]` even though the node had appended to `ctx.audit`.

These results are consistent with F-01, F-02, F-03, and F-05. They are not a substitute for the recommended acceptance tests, but they confirm that the defects are reachable through the public executor.

---

## Appendix C — key file index

| Concern | File |
|---|---|
| Mutation layer | `src/lokdocsqr/core/safe_fs.py` |
| Scope gate | `src/lokdocsqr/core/scope.py` |
| Payload construction (executor) | `src/lokdocsqr/core/executor/engine.py::_build_payload` |
| Payload construction (planner) | `src/lokdocsqr/core/planning/builder.py::_node_payload` |
| Node scope helper | `src/lokdocsqr/nodes/fs_ops/_common.py::check_scope` |
| Template nodes | `src/lokdocsqr/nodes/fs_ops/_base.py` |
| Preview change kind | `src/lokdocsqr/core/preview.py::_resolve_change_type` |
| Trash dry-run | `src/lokdocsqr/nodes/fs_ops/trash.py` |
| Trash real path | `src/lokdocsqr/core/safe_fs.py::quarantine` |
| Xattr mutation | `src/lokdocsqr/nodes/fs_ops/tag.py::_write_xattr` |
| Archive extraction | `src/lokdocsqr/nodes/fs_ops/archive.py::_run_extract` |
| Audit model | `src/lokdocsqr/core/models.py::ActionLog` |
| Audit store | `src/lokdocsqr/core/audit.py` |
| Architecture tests | `tests/test_architecture.py` |
| Invariant tests | `tests/test_invariants.py` |
| Scope-first test | `tests/test_scope_first.py` |
| fs_ops integration test | `tests/test_fs_ops_integration.py` |
| Packaging | `pyproject.toml` |

---

## Closing assessment

The project has a credible core and an unusually thoughtful set of safety intentions. The problem is not a lack of code or tests; it is that the **integration contract has not been exercised as a whole**. Fix F-01, F-02, and F-03, add one real vertical-slice test, and the “80%” claim becomes much more defensible. Until then, the honest description is: *strong primitives, unproven engine, incomplete product.*
