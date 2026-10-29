# LokDocSQR API contracts (behavioral, test-time)
Version: v1.1 (phase 8d-a, test fix iter_1)
Правило появления: только через new test + запись здесь.

API contracts отличаются от CONTRACT.md: CONTRACT.md — invariants, enforced AST-scan'ом на каждом гейте; здесь — behavioral contracts, пинованные обычными тестами. При добавлении новой behavioral-гарантии — сначала тест, потом строка здесь.

8a known limitation closed: `NodeRegistry.list()` returns `list[dict]` (`describe()` metadata); `discover_plugins()` does `[item["id"] for item in registry.list()]`.

| ID | Rule | Pinning test | Phase |
|----|------|--------------|-------|
| H3 | utils.path_template output port name == "path_template" == PAYLOAD_PATH_KEYS["fs.write_file"][0] | test_api_contracts_ports.py::test_path_template_port_contract | 8d-a |
| H4 | utils.set_variables.dry_run возвращает dict с ключом "variables" (dict); единственный продюсер planning variables. | _variables.py::test_set_variables_emits_variables_dict | 8d-a |
| H5 | access.list_files output port ∈ {items, files, file_refs, input, data} | _ports.py::test_list_files_output_port_in_extract_items_set | 8d-a |
| H6 | Observational: все registered nodes возвращают все declared output ports из dry_run. Пока наблюдательный, enforcement в 8z. | _ports.py::test_all_registered_nodes_return_declared_ports_in_dry_run | 8d-a (obs) / 8z (enforce) |
| H7 | classify_node(id) != "unknown" ⇒ id ∈ discover_plugins() ∪ KNOWN_FUTURE_NODES. | _design.py::test_no_premature_classification | 8d-a |
| M2 | Reserved keys (RESERVED_VARIABLES из core.executor.context) отфильтрованы из planning_vars в _merge_planning_vars. | _variables.py::test_reserved_keys_filtered_in_planning | 8d-a |
| M3 | Множество fs.* в DEFAULT_OPERATION_MAP ⊆ ключи NODE_CHANGE_KIND. | _design.py::test_node_change_kind_covers_all_fs_ops | 8d-a |
| M5 | ResolvedGraph.ordered_node_ids ⊆ {n.id for n in document.nodes}, иначе ValueError. | _design.py::test_resolved_graph_rejects_ghost_ids | 8d-a |
| M6 | validate_proposal и preview_diff НЕ читают ctx.variables. | (deferred to 8z) | doc-only |
| M7 | parser ставит source_port="out", target_port="in" по умолчанию. | (deferred to 8z) | doc-only |
| M8 | validate_graph — структурная валидность; required-input-defaults — ответственность _build_payload. | (deferred to 8z) | doc-only |
| M9 | Multi-port return: нода с 2+ output-портами обязана вернуть dict/NodeResult; _normalize_result иначе NodeExecutionError. | (deferred to 8z) | doc-only |


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
