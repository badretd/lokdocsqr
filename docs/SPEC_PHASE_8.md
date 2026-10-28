# SPEC_PHASE_8.md

## 1. `ctx.variables` — introduction and semantics

**Recommendation.** Do not introduce a new field: `ExecutionContext` already has `variables: dict[str, Any] = field(default_factory=dict)`. Phase 8 fixes the contract rather than changing the type. `default_factory=dict` gives an empty dictionary per run; backward compatibility is preserved: old nodes do not read it, and `ctx.policy is None` still means the pre-7c path without the gate.  
**Who writes.** Only `nodes/utils/set_variables.py` and flow containers (`loop_batch`, `subflow`) — into a temporary child scope. `path_template` only reads `ctx.variables` and does not mutate it. All other nodes are considered read-only with respect to `variables`.  
**Read-only write attempt.** Through the `ctx.set_variable(...)` helper — `NodeExecutionError`; direct `ctx.variables[...] = ...` is not intercepted by the runtime in phase 8; this is a contract violation caught by tests/review.  
**Alternative.** Immutable mapping + child context per node — cleaner, but requires changes to executor/context and breaks simple scenarios.  
**Consequences.** `core/executor/context.py` is affected only by documentation/tests; policy engine 7a, planning 7b, and the 7c gate do not change.

## 2. `ctx.clock` — time for `wait`

**Recommendation.** Introduce a `Clock` Protocol: `now() -> float`, `sleep(seconds: float) -> None`. The default is `SystemClock` over `time.time`/`time.sleep`; tests use `FakeClock`. Store it in `ctx.clock: Clock = field(default_factory=SystemClock)`, because `wait`, retry backoff, and debounce must see a single time source.  
**Why ctx, not a node parameter.** A payload parameter would be duplicated in every graph, allow desynchronization, and leak into the GUI editor. `ctx.clock` is per-run infrastructure, like `logger`/`events`.  
**Backward compatibility.** The default is system-based; nodes 0–7 ignore it; `ctx.policy is None` is unaffected.  
**Alternative.** Pass `clock` only in the `wait` payload — rejected: retry/loop also need it.  
**Consequences.** `context.py` gets a field; `wait` does not import `time` directly; policy/planning do not change; FS is not affected.

## 3. `subflow` — subgraph source and `max_depth`

**Recommendation.** The source is `graph_id` from a read-only `AutomationCatalog` maintained by `core.runtime` (not `NodeRegistry`). The catalog is injected into `ctx.automations`; `subflow` does not read FS itself. Inline payload is acceptable for tests/built-in graphs; an FS path is rejected in phase 8.  
**R28.** Reading a graph from the catalog is not an FS mutation; `safe_fs` is not needed. An FS path would be read-only, but it pulls in scope/IO and blurs responsibility.  
**`max_depth`.** A node parameter; default from `AppConfig.flow.max_subflow_depth = 8`; effective depth — `ctx.flow_depth: int = 0`; the child executor receives `+1`.  
**Exceeded.** `NodeExecutionError` on the `subflow` node, then the executor’s normal error policy; not a `raise` of the whole run and not per-item, unless subflow is inside a loop.  
**Alternative.** Only a global `ctx.max_depth` — worse for local exceptions.  
**Consequences.** New `AutomationCatalog` and `ctx.flow_depth`; policy engine 7a does not change; nested nodes are gated by their own 7c gate.

## 4. `loop` / `batch` — how the iteration body is specified

**Recommendation.** One node `flow.loop_batch` with `mode: "loop" | "batch"`. The body is specified by `subgraph_id` (or an inline subgraph) and reuses the subflow machinery. Reject `node_id + payload mapping`: the executor would have to know how to find the node and mix in the payload — weak coupling and a layering violation.  
**Variables.** `item`, `index`, `batch`, `flow.*` are written only into the child scope; they do not leak out after the iteration.  
**One or two nodes.** One: shared iteration engine, difference only in `batch_size`/`mode`; two nodes would duplicate code and UI.  
**Alternative.** `subgraph_id` as a separate `batch` node — acceptable later, but not in phase 8.  
**Consequences.** `loop_batch` depends on the subflow mechanism; planning for nested graphs is fail-closed in phase 8 (see §7); policy classification is container `read`, and inner nodes are gated themselves.

## 5. `retry` — what it wraps and idempotency

**Recommendation.** Retry remains in the executor: before the retry loop, a declarative `retry_policy` is read from node params/metadata (`attempts`, `backoff`, `retry_on`). The node does not call the executor and does not wrap `node.run`. `flow.retry` is an error-handler/routing marker, not a wrapper.  
**7c gate.** The gate remains exactly where `apply_phase_7c.py` inserted it: before `attempts = 0`; the verdict and payload are reused by attempts (R38).  
**Idempotency.** Explicit `retry_safe: bool = False` flag in `Node`/registration; mutating `fs_ops` default to `False`. Retry is allowed only when `retry_safe=True` and policy-allowed. “Caller is obliged” is not sufficient.  
**Alternative.** A node whitelist in the registry — less local and worse for plugins.  
**Consequences.** `node_api/base.py` and the retry part of `executor/engine.py` change, but not the 7c gate; policy engine 7a does not change; audit must record attempts.

## 6. `classify_node` extension — 11 phase-8 nodes

**Recommendation.** Additive extension of `DEFAULT_OPERATION_MAP` in `planning/registry.py`, without changing `PolicyEngine`. Classify all 11 nodes as `read` at the policy-gate level, because the containers themselves do not mutate FS, while inner nodes pass through their own 7c gate:  
`flow.if_branch`, `flow.filter`, `flow.loop_batch`, `flow.merge`, `flow.wait`, `flow.retry`, `flow.subflow`, `utils.set_variables`, `utils.path_template`, `utils.json_validate`, `utils.notify` → `read`.  
**Why not `write`.** `write` for a container with empty `target_paths` risks `ProposalValidationError` and over-blocking; `notify` is an external side effect, not FS policy.  
**Alternatives.** (b) read-only nodes do not pass through the gate — changes the engine; (c) category `"flow"` — changes the engine. Both rejected: policy engine 7a is frozen.  
**Consequences.** `registry.py` — additive exception to 7b; `builder.py` does not change; policy engine 7a is not reworked without a new phase.

## 7. `extract_paths` / `build_action_plan` for nested graphs

**Recommendation.** In phase 8 — fail-closed: `subflow` and `loop_batch` with a graph body raise `NodeExecutionError("nested graph not supported by plan builder in phase 8")` in `dry_run`; `build_action_plan` catches this as `PlanError`. Do not touch `builder.py` 7b.  
**Why.** `extract_paths` cannot handle nested graphs; a silent skip is dangerous. `PlanError` honestly blocks the plan instead of hiding unverified mutations.  
**Alternatives.** (a) recursive traversal — a separate phase 7d; (c) marker `"nested_graph": true` + warning — requires builder changes and gives a false “ok”.  
**Verify UI 12a.** Shows `PlanError` with `node_id` and the text “nested graph unsupported in plan builder; phase 7d required”; the plan is considered blocked.  
**Consequences.** `planning/builder.py` and policy engine do not change; flow nodes get an explicit fail-closed `dry_run`; after 7d, recursion can be enabled.

## Change table

| Change | File | Frozen? | Phase introduced |
|---|---|---|---|
| `ctx.variables` semantics (field already exists) | `core/executor/context.py` + tests | No | 8 |
| `ctx.clock` / `Clock` Protocol | `core/executor/context.py`, new `core/clock.py` | No | 8 |
| `ctx.flow_depth`, `ctx.automations` | `core/executor/context.py` | No | 8 |
| `AutomationCatalog` for `subflow` | `core/runtime.py` / new module | No | 8 |
| `flow.loop_batch` — one node, `subgraph_id` | `nodes/flow/loop_batch.py` | No | 8 |
| `retry_safe` + reading `retry_policy` | `core/node_api/base.py`, `core/executor/engine.py` | 7c gate — yes; retry part — no | 8 |
| Additive classification of 11 nodes | `core/planning/registry.py` | Yes, 7b; additive exception | 8 |
| Nested graph → `PlanError` via `dry_run` | `nodes/flow/subflow.py`, `nodes/flow/loop_batch.py` | `builder.py` 7b — yes, do not touch | 8 |
| Recursive `extract_paths`/builder | `core/planning/builder.py` | Yes, 7b | 7d (not 8) |