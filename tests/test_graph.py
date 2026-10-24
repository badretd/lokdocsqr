"""Phase 3 verification: graph parser, validator, resolver, builder.

Scope
-----
Black-box tests only. Every assertion runs through the public surface:

    parse_graph(data)                 -> RawGraph
    validate_graph(graph)             -> list[str]
    resolve_states(graph, [registry]) -> dict[node_id, NodeState]
    build_graph(graph, states, reg)   -> ResolvedGraph

Internal helpers (``_coerce_state``, ``_topological_order``, etc.) are
deliberately *not* imported or patched.
"""

from __future__ import annotations

import pytest

from lokdocsqr.core.errors import GraphParseError, GraphValidationError
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import NodeState


# ---------------------------------------------------------------------------
# Fake NodeRegistry — only ``.get(type)`` is required by the builder.
# ---------------------------------------------------------------------------
class _StubRegistry:
    """Minimal duck-typed stand-in for :class:`NodeRegistry`.

    Returns a fake handler identifier for known types and ``None`` for
    unknown ones, which is exactly what ``build_graph`` needs.
    """

    def __init__(self, known_types):
        self._known = set(known_types)

    def get(self, node_type):
        if node_type in self._known:
            return f"<handler:{node_type}>"
        return None


# ---------------------------------------------------------------------------
# Tiny construction helpers (kept local so tests are self-contained)
# ---------------------------------------------------------------------------
def _make_graph(nodes, edges, auto_id="test-auto"):
    return {
        "automation": {"id": auto_id},
        "nodes": nodes,
        "edges": edges,
    }


def _node(nid, ntype="list_files", state=None, params=None):
    n = {"id": nid, "type": ntype}
    if state is not None:
        n["state"] = state
    if params is not None:
        n["params"] = params
    return n


def _edge(src, tgt):
    return {"source": src, "target": tgt}


# ===========================================================================
# 1. PARSER
# ===========================================================================
def test_parse_valid_graph_returns_raw_graph():
    data = _make_graph(
        [_node("a"), _node("b")],
        [_edge("a", "b")],
    )
    graph = parse_graph(data)

    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    assert graph.nodes[0].id == "a"
    assert graph.nodes[0].type == "list_files"
    assert graph.nodes[0].state == NodeState.ENABLED
    assert graph.edges[0].source == "a"
    assert graph.edges[0].target == "b"
    assert graph.automation.id == "test-auto"


def test_parse_accepts_disabled_state_string():
    data = _make_graph([_node("a", state="disabled")], [])
    graph = parse_graph(data)
    assert graph.nodes[0].state == NodeState.DISABLED


def test_parse_missing_automation_id_raises():
    with pytest.raises(GraphParseError):
        parse_graph({"automation": {}, "nodes": [], "edges": []})


def test_parse_node_missing_id_raises():
    with pytest.raises(GraphParseError):
        parse_graph(_make_graph([{"type": "list_files"}], []))


def test_parse_node_missing_type_raises():
    with pytest.raises(GraphParseError):
        parse_graph(_make_graph([{"id": "a"}], []))


def test_parse_non_dict_root_raises():
    with pytest.raises(GraphParseError):
        parse_graph(["not", "a", "dict"])


# ===========================================================================
# 2. VALIDATOR
# ===========================================================================
def test_validate_valid_graph_returns_empty():
    graph = parse_graph(
        _make_graph([_node("a"), _node("b")], [_edge("a", "b")])
    )
    assert validate_graph(graph) == []


def test_validate_empty_nodes_detected():
    graph = parse_graph(_make_graph([], []))
    errors = validate_graph(graph)
    assert any("at least one node" in e.lower() for e in errors)


def test_validate_duplicate_ids_detected():
    graph = parse_graph(_make_graph([_node("dup"), _node("dup")], []))
    errors = validate_graph(graph)
    assert any("duplicate" in e.lower() for e in errors)


def test_validate_dangling_edge_detected():
    graph = parse_graph(_make_graph([_node("a")], [_edge("a", "missing")]))
    errors = validate_graph(graph)
    assert any("unknown target" in e.lower() for e in errors)


def test_validate_self_loop_detected():
    graph = parse_graph(_make_graph([_node("x")], [_edge("x", "x")]))
    errors = validate_graph(graph)
    assert any("self-loop" in e.lower() for e in errors)


def test_validate_rejects_dotdot_in_path_param():
    node = _node("a", ntype="move_files", params={"dest": "../escape"})
    graph = parse_graph(_make_graph([node], []))
    errors = validate_graph(graph)
    assert any(".." in e for e in errors)


def test_validate_rejects_absolute_path_param():
    node = _node("a", ntype="move_files", params={"dest": "/etc/passwd"})
    graph = parse_graph(_make_graph([node], []))
    errors = validate_graph(graph)
    assert any("absolute" in e.lower() for e in errors)


# ===========================================================================
# 3. RESOLVER — parametrized scenarios
# ===========================================================================
@pytest.mark.parametrize(
    "case_name,nodes,edges,expected",
    [
        # -- simple chain ---------------------------------------------------
        pytest.param(
            "chain_all_enabled",
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("b", "c")],
            {
                "a": NodeState.ENABLED,
                "b": NodeState.ENABLED,
                "c": NodeState.ENABLED,
            },
            id="chain-all-enabled",
        ),
        pytest.param(
            "chain_root_disabled_cascades",
            [_node("a", state="disabled"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("b", "c")],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.SKIPPED,
                "c": NodeState.SKIPPED,
            },
            id="chain-root-disabled",
        ),
        pytest.param(
            "chain_middle_disabled",
            [_node("a"), _node("b", state="disabled"), _node("c")],
            [_edge("a", "b"), _edge("b", "c")],
            {
                "a": NodeState.ENABLED,
                "b": NodeState.DISABLED,
                "c": NodeState.SKIPPED,
            },
            id="chain-middle-disabled",
        ),
        # -- join (A->C, B->C) ----------------------------------------------
        pytest.param(
            "join_one_branch_disabled",
            [_node("a", state="disabled"), _node("b"), _node("c")],
            [_edge("a", "c"), _edge("b", "c")],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.ENABLED,
                "c": NodeState.SKIPPED,
            },
            id="join-one-branch-disabled",
        ),
        pytest.param(
            "join_both_branches_enabled",
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "c"), _edge("b", "c")],
            {
                "a": NodeState.ENABLED,
                "b": NodeState.ENABLED,
                "c": NodeState.ENABLED,
            },
            id="join-both-enabled",
        ),
        pytest.param(
            "join_both_branches_disabled",
            [
                _node("a", state="disabled"),
                _node("b", state="disabled"),
                _node("c"),
            ],
            [_edge("a", "c"), _edge("b", "c")],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.DISABLED,
                "c": NodeState.SKIPPED,
            },
            id="join-both-disabled",
        ),
        # -- branch (A->B, A->C) -------------------------------------------
        pytest.param(
            "branch_root_enabled",
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("a", "c")],
            {
                "a": NodeState.ENABLED,
                "b": NodeState.ENABLED,
                "c": NodeState.ENABLED,
            },
            id="branch-root-enabled",
        ),
        pytest.param(
            "branch_root_disabled",
            [_node("a", state="disabled"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("a", "c")],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.SKIPPED,
                "c": NodeState.SKIPPED,
            },
            id="branch-root-disabled",
        ),
        # -- diamond (A->B, A->C, B->D, C->D) -----------------------------
        pytest.param(
            "diamond_source_disabled",
            [
                _node("a", state="disabled"),
                _node("b"),
                _node("c"),
                _node("d"),
            ],
            [
                _edge("a", "b"),
                _edge("a", "c"),
                _edge("b", "d"),
                _edge("c", "d"),
            ],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.SKIPPED,
                "c": NodeState.SKIPPED,
                "d": NodeState.SKIPPED,
            },
            id="diamond-source-disabled",
        ),
        # -- isolated nodes ------------------------------------------------
        pytest.param(
            "isolated_nodes_keep_their_state",
            [_node("a", state="disabled"), _node("b", state="enabled")],
            [],
            {
                "a": NodeState.DISABLED,
                "b": NodeState.ENABLED,
            },
            id="isolated-nodes",
        ),
    ],
)
def test_resolve_states_scenarios(case_name, nodes, edges, expected):
    graph = parse_graph(_make_graph(nodes, edges))
    states = resolve_states(graph)
    assert states == expected, f"scenario {case_name} failed"


def test_resolve_states_tolerates_optional_registry():
    """Resolver must not need a registry; passing one must not change output."""
    graph = parse_graph(_make_graph([_node("a")], []))
    states_none = resolve_states(graph)
    states_with_reg = resolve_states(graph, _StubRegistry({"list_files"}))
    assert states_none == states_with_reg == {"a": NodeState.ENABLED}


# ===========================================================================
# 4. BUILDER
# ===========================================================================
def test_build_rejects_cycle():
    data = _make_graph(
        [_node("a"), _node("b"), _node("c")],
        [_edge("a", "b"), _edge("b", "c"), _edge("c", "a")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    with pytest.raises(GraphValidationError):
        build_graph(graph, states, _StubRegistry({"list_files"}))


def test_build_rejects_unknown_node_type():
    data = _make_graph(
        [_node("a"), _node("b", ntype="unknown_xyz")],
        [_edge("a", "b")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    registry = _StubRegistry({"list_files"})  # does NOT know 'unknown_xyz'
    with pytest.raises(GraphValidationError) as exc_info:
        build_graph(graph, states, registry)
    assert "Unknown node type" in str(exc_info.value)


def test_build_topological_order():
    # Input order is deliberately scrambled; builder must topo-sort.
    data = _make_graph(
        [
            _node("last", ntype="write_file"),
            _node("first", ntype="list_files"),
            _node("mid", ntype="filter"),
        ],
        [_edge("first", "mid"), _edge("mid", "last")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    registry = _StubRegistry({"list_files", "filter", "write_file"})
    resolved = build_graph(graph, states, registry)

    assert list(resolved.ordered_node_ids) == ["first", "mid", "last"]


def test_build_includes_only_enabled_nodes_in_handlers():
    data = _make_graph(
        [_node("a"), _node("b", state="disabled"), _node("c")],
        [_edge("a", "b"), _edge("b", "c")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    resolved = build_graph(graph, states, _StubRegistry({"list_files"}))

    # Only 'a' is ENABLED. 'b' is DISABLED; 'c' cascades to SKIPPED.
    assert list(resolved.ordered_node_ids) == ["a"]
    assert "a" in resolved.handlers
    assert "b" not in resolved.handlers
    assert "c" not in resolved.handlers


def test_build_state_map_covers_all_nodes():
    data = _make_graph(
        [_node("a"), _node("b", state="disabled"), _node("c")],
        [_edge("a", "b"), _edge("b", "c")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    resolved = build_graph(graph, states, _StubRegistry({"list_files"}))

    assert resolved.states["a"] == NodeState.ENABLED
    assert resolved.states["b"] == NodeState.DISABLED
    assert resolved.states["c"] == NodeState.SKIPPED


def test_build_handlers_keys_match_ordered_ids():
    data = _make_graph(
        [_node("a"), _node("b")],
        [_edge("a", "b")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    resolved = build_graph(graph, states, _StubRegistry({"list_files"}))

    assert set(resolved.handlers.keys()) == set(resolved.ordered_node_ids)


def test_build_all_disabled_yields_empty_plan():
    data = _make_graph(
        [_node("a", state="disabled"), _node("b", state="disabled")],
        [_edge("a", "b")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    resolved = build_graph(graph, states, _StubRegistry({"list_files"}))

    assert list(resolved.ordered_node_ids) == []
    assert resolved.handlers == {}


def test_build_branch_keeps_only_enabled_handlers():
    data = _make_graph(
        [
            _node("root"),
            _node("left"),
            _node("right", state="disabled"),
        ],
        [_edge("root", "left"), _edge("root", "right")],
    )
    graph = parse_graph(data)
    states = resolve_states(graph)
    resolved = build_graph(graph, states, _StubRegistry({"list_files"}))

    assert list(resolved.ordered_node_ids) == ["root", "left"]
    assert set(resolved.handlers.keys()) == {"root", "left"}
