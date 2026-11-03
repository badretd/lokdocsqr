# LokDocSQR API contracts (behavioral, test-time)
Version: v1.3 (phase 8.5, nested planning + ActionSummary)
Правило появления: только через new test + запись здесь.

API contracts отличаются от CONTRACT.md: CONTRACT.md — invariants, enforced AST-scan'ом на каждом гейте; здесь — behavioral contracts, пинованные обычными тестами. При добавлении новой behavioral-гарантии — сначала тест, потом строка здесь.

8a known limitation closed: `NodeRegistry.list()` returns `list[dict]` (`describe()` metadata); `discover_plugins()` does `[item["id"] for item in registry.list()]`.

| ID | Rule | Pinning test | Phase |
|----|------|--------------|-------|
| H3 | utils.path_template output port name == "path_template" == PAYLOAD_PATH_KEYS["fs.write_file"][0] | test_api_contracts_ports.py::test_path_template_port_contract | 8d-a |
| H4 | utils.set_variables.dry_run возвращает dict с ключом "variables" (dict); единственный продюсер planning variables. | _variables.py::test_set_variables_emits_variables_dict | 8d-a |
| H5 | access.list_files output port ∈ {items, files, file_refs, input, data} | _ports.py::test_list_files_output_port_in_extract_items_set | 8d-a |
| H6 | ENFORCED: все registered nodes возвращают все declared output ports из dry_run, КРОМЕ нод с `Node._preview_fail_closed = True` (тогда dry_run обязан поднять NodeExecutionError). В текущем дереве fail-closed — только flow.subflow и flow.loop_batch. | _ports.py::test_all_registered_nodes_return_declared_ports_in_dry_run (+ verifier 8z: _preview_fail_closed check) | 8d-a (obs) → 8z (enforced) |
| H7 | classify_node(id) != "unknown" ⇒ id ∈ discover_plugins() ∪ KNOWN_FUTURE_NODES. | _design.py::test_no_premature_classification | 8d-a |
| M2 | Reserved keys (RESERVED_VARIABLES из core.executor.context) отфильтрованы из planning_vars в _merge_planning_vars. | _variables.py::test_reserved_keys_filtered_in_planning | 8d-a |
| M3 | Множество fs.* в DEFAULT_OPERATION_MAP ⊆ ключи NODE_CHANGE_KIND. | _design.py::test_node_change_kind_covers_all_fs_ops | 8d-a |
| M5 | ResolvedGraph.ordered_node_ids ⊆ {n.id for n in document.nodes}, иначе ValueError. | _design.py::test_resolved_graph_rejects_ghost_ids | 8d-a |
| M6 | ENFORCED: validate_proposal и preview_diff НЕ читают ctx.variables. | verifier 8z (test_api_contracts_variables.py) | 8z |
| M7 | ENFORCED: parser ставит source_port="out", target_port="in" по умолчанию. | verifier 8z (test_api_contracts_parser_defaults.py) | 8z |
| M8 | KNOWN LIMITATION: _node_payload валидирует required inputs условно (только при resolve_edges=True); authoritative — Executor._build_payload. Alignment — 7d-b. | (deferred 7d-b) | 8z-doc |
| M9 | ENFORCED: Multi-port return — нода с 2+ output-портами обязана вернуть dict/NodeResult; _normalize_result иначе NodeExecutionError. | engine._normalize_result (implementation) + verifier 8z | 8z |
| H2-ext | CLOSED: source_port=None или target_port=None → ValueError с одинаковым текстом в builder._node_payload и Executor._build_payload. | test_api_contracts_edge_parity.py::test_missing_source_port_fails_closed_in_builder | 8z |


## v1.1 (phase 8d-a, test fix iter_1)

### H2-extended (partial): missing source_port
When an edge carries source_port=None, the target mutating node
(fs.write_file / fs.make_dir / etc.) has no resolved path_template.
Both planner and executor fail-closed via validate_proposal (R27):
  - planner: build_action_plan records PlanError(ProposalValidationError
    "mutating proposal with empty target_paths"), plan.blocked=True.
  - executor: _check_policy_and_verify raises ProposalValidationError
    at the same validate_proposal call.
Pinned by:
  tests/test_api_contracts_edge_parity.py::
  test_missing_source_port_fails_closed_in_builder

### H2-extended (deferred to 8z): source_port=None asymmetry
The two payload resolvers diverge on source_port=None:
  - builder._node_payload: continue (skip the edge)
  - executor._build_payload: outputs.get(None) → payload[port]=None
Both end up fail-closed, but via different code paths. Aligning the
two resolvers (either both skip, or both resolve to None, plus an
early ValueError on source_port=None) is deferred to 8z.


## v1.2 (phase 8z)

### H6 → enforced (opt-out via Node._preview_fail_closed)
`Node._preview_fail_closed: ClassVar[bool] = False` added in 8z.
A node with `_preview_fail_closed = True` MUST raise
NodeExecutionError from `dry_run`; the plan builder deliberately does
not support nested-graph execution and fails closed. Only
`flow.subflow` and `flow.loop_batch` set the flag in the current tree;
`flow.filter` / `flow.merge` / `flow.wait` / `flow.retry` /
`flow.if_branch` keep the default `False` and MUST return every
declared output port from `dry_run`.

### M6 → enforced
`validate_proposal` and `preview_diff` remain pure w.r.t.
`ctx.variables`. Behavioral test pending verifier 8z.

### M7 → enforced
The parser default-port contract (`source_port="out"`,
`target_port="in"` when omitted) is now pinned. Behavioral test
pending verifier 8z.

### M8 → known limitation (deferred to 7d-b)
`core.planning.builder._node_payload` validates required input ports
only when `resolve_edges=True` (i.e. when the graph contains
`utils.path_template` or `utils.set_variables`). The authoritative
required-input validation is `Executor._build_payload`. This
asymmetry is accepted for 8z and will be aligned in 7d-b.

### M9 → enforced
`_normalize_result` already raises NodeExecutionError for
multi-port returns that are not dict / NodeResult; the contract is
now marked enforced.

### H2-extended (asymmetry closed in 8z)
`source_port=None` or `target_port=None` now raises `ValueError`
with identical text in both `builder._node_payload` and
`Executor._build_payload`:
  `f"edge with source_port=None or target_port=None: source={source!r}"`
A string-typed-but-unknown port (e.g. `"not_a_real_port"`) continues
to be silently skipped by the builder (pinned by
`test_missing_source_port_fails_closed_in_builder`), and continues to
resolve to `None` on the executor side; the resulting empty
`target_paths` still fails closed via `validate_proposal` (R27).

## v1.3 (phase 8.5)

### Q2 — policy vs retry_policy
`ctx.policy` / `PolicyEngine` remain the filesystem-policy decision layer:
they classify operations, produce `PolicyDecision`, and drive the verify
gate. They do not read `retry_policy`.

`retry_policy` remains an executor concern:
`Executor._read_node_retry_policy` reads it from `NodeSpec.params`, validates
it, and applies it before the retry loop. The policy engine and
`_check_policy_and_verify` are not changed for retry policy; only the
executor consumes it.

This split is intentional: policy answers "may this action happen?", retry
answers "how many times may a failed attempt be repeated?". Neither layer
overrides the other.

## v1.4 (phase 9)

### L1 — local LLM output contract
`llm.local_llm` is classified as `read` and declares output ports `data`,
`text`, `confidence`, `usage`. Every port is present in both `run` and
`dry_run`. `data` is the parsed JSON document; `text` is the raw model output;
`confidence` is read from the document's `confidence` (or `score`) field when
present; `usage` always contains `model`, `prompt_tokens` and
`completion_tokens` (plus `total_tokens` when both counts are available).

Pinned by:
- `tests/test_phase_9.py::test_local_llm_declares_structured_output_ports`
- `tests/test_phase_9.py::test_task_modes_return_parsed_structured_json`
- `tests/test_api_contracts_h6_preview.py` (dynamic non-fail-closed node sweep)

### L2 — dry-run never constructs a client
`dry_run` must not import `ollama`, construct `ollama.Client`, or call the
model. It returns the declared output shape with `data=None` and
`usage["dry_run"] is True`.

Pinned by:
- `tests/test_phase_9.py::test_dry_run_returns_all_ports_and_never_calls_the_client`
- `tests/test_acceptance_vertical_slice.py::test_params_only_vertical_slice_dry_run_is_pure`

### L3 — schema validation is delegated to `utils.json_validate`
The LLM node sends the schema in the prompt and returns `data`; the graph
connects `llm.data` to `utils.json_validate.data`. The validation node is the
single owner of `valid` / `errors` / `error_count`.

Pinned by:
- `tests/test_phase_9.py::test_llm_output_validates_through_json_validate_node`
- `tests/test_phase_9.py::test_llm_graph_plans_executes_and_validates`
- `tests/test_acceptance_vertical_slice.py::test_params_only_vertical_slice_applies_audits_and_rolls_back`

### L4 — optional dependency
`ollama` is imported only inside `_get_client` (R7/R20). Importing
`lokdocsqr.nodes.llm.local_llm` must succeed when `ollama` is unavailable.

Pinned by:
- `tests/test_phase_9.py::test_module_imports_without_ollama`
