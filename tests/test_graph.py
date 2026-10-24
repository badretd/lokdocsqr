"""Phase 3.5 verification: parser, ports, validator, resolver, builder.

Targets the Workflow v1.0 specification as implemented by the rewritten
``lokdocsqr.core.models`` and ``lokdocsqr.core.graph`` packages.

Every assertion runs through the public surface only::

    parse_graph(data)                   -> WorkflowDocument
    validate_graph(doc)                 -> list[str]
    resolve_states(doc, registry=None)  -> dict[node_id, NodeState]
    build_graph(doc, states, registry)  -> ResolvedGraph

No private helpers (``_parse_port``, ``_topological_order``, ...) are
imported or patched.
"""
# === Phase 3.5 applied: workflow v1.0 spec-compliant tests ===
from __future__ import annotations

import pytest

from lokdocsqr.core.errors import GraphParseError, GraphValidationError
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import NodeState, PortSpec


# ---------------------------------------------------------------------------
# Field-name shim.
#
# The current parser reads the workflow-level metadata block from the root
# ``metadata`` field (see lokdocsqr/core/graph/parser.py). The v1.0 spec
# labels the same logical block ``workflow``. Keeping the field name in a
# single constant means the test file only needs one edit if the parser is
# migrated to the spec name later.
# ---------------------------------------------------------------------------
WF_META_KEY = "metadata"


# ---------------------------------------------------------------------------
# Construction helpers (deliberately tiny so tests stay self-contained)
# ---------------------------------------------------------------------------
def _doc(nodes, edges=None, auto_id="auto", **overrides):
    """Build a minimal valid workflow dict for tests."""
    doc = {
        "spec_version": "1.0",
        WF_META_KEY: {"id": auto_id, "name": "Test Workflow"},
        "nodes": nodes,
        "edges": edges if edges is not None else [],
    }
    doc.update(overrides)
    return doc


def _without(data, *keys):
    """Return a shallow copy of ``data`` with ``keys`` removed."""
    return {key: value for key, value in data.items() if key not in keys}


def _node(nid, ntype="list_files", **extra):
    node = {"id": nid, "type": ntype}
    node.update(extra)
    return node


def _edge(src, tgt, **extra):
    edge = {"source": src, "target": tgt}
    edge.update(extra)
    return edge


class _StubRegistry:
    """Duck-typed NodeRegistry used by ``build_graph``.

    Returns a sentinel handler for known types and ``None`` for unknown
    types, which is exactly what ``build_graph`` consumes.
    """

    def __init__(self, known_types):
        self._known = set(known_types)

    def get(self, node_type):
        if node_type in self._known:
            return f"<handler:{node_type}>"
        return None


# ===========================================================================
# 1. Parser - structural rejection of invalid JSON graphs
# ===========================================================================
@pytest.mark.parametrize(
    "label,data",
    [
        pytest.param(
            "non-dict-root",
            ["not", "a", "workflow"],
            id="non-dict-root",
        ),
        pytest.param(
            "wrong-spec-version",
            {**_doc([_node("a")]), "spec_version": "2.0"},
            id="wrong-spec-version",
        ),
        pytest.param(
            "missing-spec-version",
            _without(_doc([_node("a")]), "spec_version"),
            id="missing-spec-version",
        ),
        pytest.param(
            "missing-workflow-metadata",
            _without(_doc([_node("a")]), WF_META_KEY),
            id="missing-workflow-metadata",
        ),
        pytest.param(
            "missing-workflow-id",
            _doc([_node("a")], **{WF_META_KEY: {"name": "No id"}}),
            id="missing-workflow-id",
        ),
        pytest.param(
            "missing-nodes",
            _without(_doc([_node("a")]), "nodes"),
            id="missing-nodes",
        ),
        pytest.param(
            "nodes-not-a-list",
            _doc({"a": _node("a")}),
            id="nodes-not-a-list",
        ),
        pytest.param(
            "edges-not-a-list",
            _doc([_node("a")], edges="not-a-list"),
            id="edges-not-a-list",
        ),
        pytest.param(
            "node-params-not-an-object",
            _doc([_node("a", params=["not", "an", "object"])]),
            id="node-params-not-an-object",
        ),
        pytest.param(
            "invalid-node-state",
            _doc([_node("a", state="unknown-state")]),
            id="invalid-node-state",
        ),
        pytest.param(
            "empty-node-id",
            _doc([_node("", ntype="list_files")]),
            id="empty-node-id",
        ),
        pytest.param(
            "empty-node-type",
            _doc([_node("a", ntype="")]),
            id="empty-node-type",
        ),
        pytest.param(
            "edge-missing-source",
            _doc([_node("a")], edges=[{"target": "a"}]),
            id="edge-missing-source",
        ),
        pytest.param(
            "edge-missing-target",
            _doc([_node("a")], edges=[{"source": "a"}]),
            id="edge-missing-target",
        ),
    ],
)
def test_parser_rejects_invalid_structures(label, data):
    """Parser must raise GraphParseError for structurally invalid input."""
    with pytest.raises(GraphParseError):
        parse_graph(data)


# ===========================================================================
# 2. Ports - string shorthand and full port objects
# ===========================================================================
def test_parser_converts_string_port_to_portspec():
    """A bare string port becomes PortSpec(name=..., type='any')."""
    doc = _doc([
        _node("a", inputs=["trigger"], outputs=["files"]),
    ])
    node = parse_graph(doc).nodes[0]

    assert node.inputs == [PortSpec(name="trigger", type="any")]
    assert node.outputs == [PortSpec(name="files", type="any")]


def test_parser_accepts_full_port_objects():
    """A port object carries every PortSpec field through the parser."""
    port = {
        "name": "files",
        "type": "file_ref_array",
        "required": True,
        "description": "Files to process",
        "default": None,
    }
    doc = _doc([_node("a", outputs=[port])])
    result = parse_graph(doc).nodes[0].outputs[0]

    assert result.name == "files"
    assert result.type == "file_ref_array"
    assert result.required is True
    assert result.description == "Files to process"
    assert result.default is None


# ===========================================================================
# 3. Validator - semantic graph errors
# ===========================================================================
def test_validator_accepts_valid_graph():
    doc = parse_graph(_doc([_node("a"), _node("b")], edges=[_edge("a", "b")]))
    assert validate_graph(doc) == []


def test_validator_flags_duplicate_node_ids():
    doc = parse_graph(_doc([_node("dup"), _node("dup")]))
    errors = validate_graph(doc)
    assert any("duplicate" in error.lower() for error in errors)


def test_validator_flags_max_concurrency_below_one():
    doc = parse_graph(_doc([_node("a")], settings={"max_concurrency": 0}))
    errors = validate_graph(doc)
    assert any("max_concurrency" in error for error in errors)


def test_validator_flags_empty_graph():
    doc = parse_graph(_doc([]))
    errors = validate_graph(doc)
    assert any("at least one node" in error.lower() for error in errors)


def test_validator_flags_unknown_output_port():
    doc = parse_graph(_doc(
        [
            _node("a", outputs=[{"name": "files"}]),
            _node("b", inputs=[{"name": "in"}]),
        ],
        edges=[_edge("a", "b", source_port="bogus", target_port="in")],
    ))
    errors = validate_graph(doc)
    assert any("unknown output port" in error.lower() for error in errors)


def test_validator_flags_unknown_input_port():
    doc = parse_graph(_doc(
        [
            _node("a", outputs=[{"name": "files"}]),
            _node("b", inputs=[{"name": "in"}]),
        ],
        edges=[_edge("a", "b", source_port="files", target_port="bogus")],
    ))
    errors = validate_graph(doc)
    assert any("unknown input port" in error.lower() for error in errors)


def test_validator_flags_self_loop():
    doc = parse_graph(_doc([_node("x")], edges=[_edge("x", "x")]))
    errors = validate_graph(doc)
    assert any("self-loop" in error.lower() for error in errors)


def test_validator_flags_dangling_edge_target():
    doc = parse_graph(_doc([_node("a")], edges=[_edge("a", "missing")]))
    errors = validate_graph(doc)
    assert any("unknown target" in error.lower() for error in errors)


# ===========================================================================
# 4. Resolver - disabled cascade with EdgeSpec(source/target)
# ===========================================================================
def test_resolver_cascades_skipped_from_disabled_parent():
    """A disabled parent must cascade SKIPPED to every reachable child."""
    doc = parse_graph(_doc(
        [_node("a"), _node("b", state="disabled"), _node("c")],
        edges=[_edge("a", "b"), _edge("b", "c")],
    ))
    assert resolve_states(doc) == {
        "a": NodeState.ENABLED,
        "b": NodeState.DISABLED,
        "c": NodeState.SKIPPED,
    }


def test_resolver_join_skips_on_any_disabled_parent():
    """A join node is SKIPPED if at least one inbound branch is disabled."""
    doc = parse_graph(_doc(
        [
            _node("a", state="disabled"),
            _node("b"),
            _node("c"),
        ],
        edges=[_edge("a", "c"), _edge("b", "c")],
    ))
    assert resolve_states(doc)["c"] == NodeState.SKIPPED


def test_resolver_isolated_nodes_keep_declared_state():
    """Isolated nodes keep their declared state unchanged."""
    doc = parse_graph(_doc([
        _node("a", state="disabled"),
        _node("b", state="enabled"),
    ]))
    assert resolve_states(doc) == {
        "a": NodeState.DISABLED,
        "b": NodeState.ENABLED,
    }


# ===========================================================================
# 5. Builder - topological order and handler selection
# ===========================================================================
def test_builder_rejects_cycle():
    doc = parse_graph(_doc(
        [_node("a"), _node("b"), _node("c")],
        edges=[_edge("a", "b"), _edge("b", "c"), _edge("c", "a")],
    ))
    states = resolve_states(doc)
    with pytest.raises(GraphValidationError):
        build_graph(doc, states, _StubRegistry({"list_files"}))


def test_builder_topological_order_is_dependency_aware():
    """Input order is scrambled; builder must topo-sort."""
    doc = parse_graph(_doc(
        [
            _node("last", ntype="write_file"),
            _node("first", ntype="list_files"),
            _node("mid", ntype="filter"),
        ],
        edges=[_edge("first", "mid"), _edge("mid", "last")],
    ))
    states = resolve_states(doc)
    registry = _StubRegistry({"list_files", "filter", "write_file"})
    resolved = build_graph(doc, states, registry)
    assert list(resolved.ordered_node_ids) == ["first", "mid", "last"]


def test_builder_drops_disabled_and_skipped_nodes():
    """Only ENABLED nodes receive handlers; DISABLED/SKIPPED are dropped."""
    doc = parse_graph(_doc(
        [_node("a"), _node("b", state="disabled"), _node("c")],
        edges=[_edge("a", "b"), _edge("b", "c")],
    ))
    states = resolve_states(doc)
    resolved = build_graph(doc, states, _StubRegistry({"list_files"}))

    assert list(resolved.ordered_node_ids) == ["a"]
    assert set(resolved.handlers.keys()) == {"a"}
    assert resolved.states == {
        "a": NodeState.ENABLED,
        "b": NodeState.DISABLED,
        "c": NodeState.SKIPPED,
    }


def test_builder_rejects_unknown_node_type():
    doc = parse_graph(_doc(
        [_node("a"), _node("b", ntype="unknown_xyz")],
        edges=[_edge("a", "b")],
    ))
    states = resolve_states(doc)
    with pytest.raises(GraphValidationError) as exc_info:
        build_graph(doc, states, _StubRegistry({"list_files"}))
    assert "Unknown node type" in str(exc_info.value)


def test_builder_empty_plan_when_all_disabled():
    doc = parse_graph(_doc(
        [_node("a", state="disabled"), _node("b", state="disabled")],
        edges=[_edge("a", "b")],
    ))
    states = resolve_states(doc)
    resolved = build_graph(doc, states, _StubRegistry({"list_files"}))
    assert resolved.ordered_node_ids == []
    assert resolved.handlers == {}


# ===========================================================================
# 6. Graph package public surface
# ===========================================================================
def test_graph_package_exports_only_public_functions():
    import lokdocsqr.core.graph as graph_pkg

    assert set(graph_pkg.__all__) == {
        "parse_graph",
        "validate_graph",
        "resolve_states",
        "build_graph",
    }
