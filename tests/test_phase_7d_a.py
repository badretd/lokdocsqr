"""Verification tests for LokDocSQR Phase 7d-a (gate 7d-a).

Pins two under-documented contracts:

  (A) resolve_edges
      True iff the document contains any NodeSpec with type in
      {"utils.path_template", "utils.set_variables"}. When enabled,
      edge-resolution applies to *every* node in the graph, including
      fs.make_dir (not only fs.write_file).

  (B) payload-build error
      `_node_payload` may raise ValueError for a required input port
      with neither edge nor default. `build_action_plan` catches it and
      records a PlanError with exc_type="ValueError"; no exception
      escapes to the caller and plan.blocked is True.

Run:
    pytest tests/test_phase_7d_a.py -v
"""
from __future__ import annotations

import inspect
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import os

import pytest

import lokdocsqr.core.planning.builder as builder_mod
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PolicyAction,
    PolicyDecision,
    PortSpec,
    ResolvedGraph,
    WorkflowDocument,
    WorkflowMetadata,
)
from lokdocsqr.core.planning.builder import _node_payload, build_action_plan
from lokdocsqr.core.planning.registry import extract_paths
from lokdocsqr.core.planning.validator import validate_proposal
from lokdocsqr.nodes.fs_ops.make_dir import MakeDirNode
from lokdocsqr.nodes.fs_ops.write_file import WriteFileNode
from lokdocsqr.nodes.utils.path_template import PathTemplateNode
from lokdocsqr.nodes.utils.set_variables import SetVariablesNode


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class AllowScope:
    """Permissive scope double: every path is in scope."""

    def check(self, path: Any, write: bool = False) -> Path:
        return Path(path)


@dataclass
class FakeDataclassCtx:
    """Dataclass ctx: _isolated_ctx performs variables isolation."""

    variables: dict[str, Any] = field(default_factory=dict)
    scope: Any = field(default_factory=AllowScope)
    run_id: str = "test-run"
    automation_id: str = "test-auto"
    audit: list[Any] = field(default_factory=list)

    def set_variable(self, name: str, value: Any) -> None:
        self.variables[name] = value


class NonDataclassCtx:
    """Duck-typed ctx: _isolated_ctx must be a no-op (D.3)."""

    def __init__(self) -> None:
        self.variables: dict[str, Any] = {}
        self.scope = AllowScope()
        self.run_id = "test-run"
        self.automation_id = "test-auto"
        self.audit: list[Any] = []

    def set_variable(self, name: str, value: Any) -> None:
        self.variables[name] = value


class AllowPolicy:
    """Policy double that always ALLOWs."""

    def decide(self, proposal: Any) -> PolicyDecision:
        return PolicyDecision(PolicyAction.ALLOW, "allow", proposal)


class _NestedFail:
    """Fail-closed nested-graph double (SPEC §7)."""

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        raise NodeExecutionError("nested graph not supported by plan builder")


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _node(
    node_id: str,
    node_type: str,
    params: dict[str, Any] | None = None,
    inputs: list[PortSpec] | None = None,
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type=node_type,
        params=dict(params or {}),
        inputs=list(inputs or []),
    )


def _edge(
    source: str,
    target: str,
    source_port: str = "out",
    target_port: str = "in",
) -> EdgeSpec:
    return EdgeSpec(
        id=f"{source}->{target}:{source_port}->{target_port}",
        source=source,
        target=target,
        source_port=source_port,
        target_port=target_port,
    )


def _graph(
    nodes: list[NodeSpec],
    edges: list[EdgeSpec],
    handlers: dict[str, Any],
) -> ResolvedGraph:
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="test-automation"),
        nodes=nodes,
        edges=edges,
    )
    return ResolvedGraph(
        document=doc,
        ordered_node_ids=[node.id for node in nodes],
        handlers=handlers,
        states={node.id: NodeState.ENABLED for node in nodes},
    )


def _plan_json(plan: Any) -> str:
    return json.dumps(
        [asdict(proposal) for proposal in plan.proposals],
        default=str,
        sort_keys=True,
    )


def _three_node_graph(
    handlers: dict[str, Any],
    *,
    set_vars_params: dict[str, Any] | None = None,
    template_params: dict[str, Any] | None = None,
    target_params: dict[str, Any] | None = None,
    target_type: str = "fs.write_file",
) -> ResolvedGraph:
    """n0 set_variables -> n1 path_template -> n2 target."""
    n0 = _node(
        "n0", "utils.set_variables",
        set_vars_params or {"variables": {"name": "x"}},
    )
    n1 = _node(
        "n1", "utils.path_template",
        template_params or {"template": "{name}.txt"},
    )
    n2 = _node("n2", target_type, target_params or {"content": "hi"})
    return _graph(
        [n0, n1, n2],
        [
            _edge("n0", "n1", "variables", "variables"),
            _edge("n1", "n2", "path_template", "path_template"),
        ],
        handlers,
    )


# ---------------------------------------------------------------------------
# A. P1 functional
# ---------------------------------------------------------------------------

def test_a1_static_variables_payload() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node(
        "n1", "utils.path_template",
        {"template": "{name}.txt", "variables": {"name": "x"}},
    )
    n2 = _node("n2", "fs.write_file", {"content": "hi"})
    graph = _graph(
        [n1, n2],
        [_edge("n1", "n2", "path_template", "path_template")],
        {"n1": PathTemplateNode(), "n2": WriteFileNode()},
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors


def test_a2_set_variables_stateful() -> None:
    ctx = FakeDataclassCtx()
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": WriteFileNode(),
        },
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors


def test_a3_make_dir_p1() -> None:
    ctx = FakeDataclassCtx()
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": MakeDirNode(),
        },
        set_vars_params={"variables": {"year": 2026}},
        template_params={"template": "reports/{year}"},
        target_params={},
        target_type="fs.make_dir",
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors


def test_a4_missing_variable() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node("n1", "utils.path_template", {"template": "{missing}.txt"})
    n2 = _node("n2", "fs.write_file", {"content": "hi"})
    graph = _graph(
        [n1, n2],
        [_edge("n1", "n2", "path_template", "path_template")],
        {"n1": PathTemplateNode(), "n2": WriteFileNode()},
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert plan.blocked
    assert plan.errors


def test_a5_reserved_key(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = FakeDataclassCtx()
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": WriteFileNode(),
        },
        set_vars_params={"variables": {"run_id": 1, "ok": "x"}},
        template_params={"template": "{ok}.txt"},
    )

    seen: list[dict[str, Any]] = []
    original = builder_mod._merge_planning_vars

    def wrapped(planning_vars: dict[str, Any], result: Any) -> None:
        original(planning_vars, result)
        seen.append(dict(planning_vars))

    monkeypatch.setattr(builder_mod, "_merge_planning_vars", wrapped)

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors
    assert seen
    assert "run_id" not in seen[-1]


def test_a6_nested_fail_closed() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node("n1", "flow.subflow", {"graph_id": "x"})
    graph = _graph([n1], [], {"n1": _NestedFail()})

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert plan.blocked
    assert any(error.node_id == "n1" for error in plan.errors)


# ---------------------------------------------------------------------------
# B. Pin resolve_edges (Gap A)
# ---------------------------------------------------------------------------

def test_b1_static_no_edge() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node(
        "n1", "fs.write_file",
        {"path_template": "static/x.txt", "content": "hi"},
    )
    graph = _graph([n1], [], {"n1": WriteFileNode()})

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors


def test_b2_edge_wins() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node(
        "n1", "utils.path_template",
        {"template": "{name}.txt", "variables": {"name": "x"}},
    )
    n2 = _node(
        "n2", "fs.write_file",
        {"path_template": "static/ignored.txt", "content": "hi"},
    )
    graph = _graph(
        [n1, n2],
        [_edge("n1", "n2", "path_template", "path_template")],
        {"n1": PathTemplateNode(), "n2": WriteFileNode()},
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert not plan.blocked, plan.errors


def test_b3_missing_path_template() -> None:
    ctx = FakeDataclassCtx()
    n1 = _node("n1", "fs.write_file", {})
    graph = _graph([n1], [], {"n1": WriteFileNode()})

    try:
        plan = build_action_plan(graph, ctx, AllowPolicy())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"build_action_plan raised {type(exc).__name__}: {exc}")

    assert plan.blocked
    assert any(error.node_id == "n1" for error in plan.errors)


# ---------------------------------------------------------------------------
# C. Pin payload-build error propagation (Gap B)
# ---------------------------------------------------------------------------

def test_c1_no_exception_outward() -> None:
    """Graph from B3: dry_run failure inside a node must not escape."""
    ctx = FakeDataclassCtx()
    n1 = _node("n1", "fs.write_file", {})
    graph = _graph([n1], [], {"n1": WriteFileNode()})

    try:
        plan = build_action_plan(graph, ctx, AllowPolicy())
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"payload-build error escaped: {type(exc).__name__}: {exc}")

    assert plan.blocked
    assert plan.errors


def test_c1_payload_build_valueerror_to_planerror() -> None:
    """_node_payload ValueError must become a per-node PlanError."""
    spec = _node(
        "n1", "fs.write_file", {},
        inputs=[PortSpec(name="path_template", required=True)],
    )
    with pytest.raises(ValueError):
        _node_payload(spec, resolve_edges=True)

    ctx = FakeDataclassCtx()
    n0 = _node("n0", "utils.set_variables", {"variables": {"a": 1}})
    n1 = _node(
        "n1", "fs.write_file", {},
        inputs=[PortSpec(name="path_template", required=True)],
    )
    graph = _graph(
        [n0, n1],
        [],
        {"n0": SetVariablesNode(), "n1": WriteFileNode()},
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert plan.blocked
    n1_errors = [error for error in plan.errors if error.node_id == "n1"]
    assert n1_errors
    assert n1_errors[0].exc_type == "ValueError"


# ---------------------------------------------------------------------------
# D. Pin _isolated_ctx (D.3)
# ---------------------------------------------------------------------------

def test_d1_real_ctx_isolated(exec_ctx) -> None:
    before = dict(exec_ctx.variables)
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": WriteFileNode(),
        },
    )

    build_action_plan(graph, exec_ctx, AllowPolicy())

    assert exec_ctx.variables == before
    assert "name" not in exec_ctx.variables


def test_d2_non_dataclass_noop() -> None:
    ctx = NonDataclassCtx()
    calls = {"count": 0}

    class _SpyPathTemplate(PathTemplateNode):
        def dry_run(self, ctx, payload):
            calls["count"] += 1
            return super().dry_run(ctx, payload)

    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": _SpyPathTemplate(),
            "n2": WriteFileNode(),
        },
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert plan is not None
    assert calls["count"] == 1
    # D.3: no isolation for non-dataclass — but set_variables._preview
    # also never mutates ctx.variables, so no leakage is observed.
    assert ctx.variables == {}


# ---------------------------------------------------------------------------
# E. Port contract for path_template
# ---------------------------------------------------------------------------

def test_e1_path_template_dry_run_keys() -> None:
    ctx = FakeDataclassCtx(variables={"name": "x"})
    result = PathTemplateNode().dry_run(ctx, {"template": "{name}.txt"})

    assert "path_template" in result
    assert result["path_template"] == "x.txt"


def test_e2_path_alias_errors() -> None:
    ctx = FakeDataclassCtx(variables={"name": "x"})
    result = PathTemplateNode().dry_run(ctx, {"template": "{name}.txt"})

    assert "path" in result
    assert result["path"] == "x.txt"
    assert isinstance(result["errors"], list)


# ---------------------------------------------------------------------------
# G. Determinism / no mutation
# ---------------------------------------------------------------------------

def test_g1_determinism() -> None:
    ctx = FakeDataclassCtx()
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": WriteFileNode(),
        },
    )

    first = build_action_plan(graph, ctx, AllowPolicy())
    second = build_action_plan(graph, ctx, AllowPolicy())

    assert _plan_json(first) == _plan_json(second)


def test_g2_ctx_not_mutated() -> None:
    ctx = FakeDataclassCtx()
    before = dict(ctx.variables)
    graph = _three_node_graph(
        {
            "n0": SetVariablesNode(),
            "n1": PathTemplateNode(),
            "n2": WriteFileNode(),
        },
    )

    build_action_plan(graph, ctx, AllowPolicy())

    assert ctx.variables == before


# ---------------------------------------------------------------------------
# H. Signature stability
# ---------------------------------------------------------------------------

def test_h1_build_signature() -> None:
    sig = inspect.signature(build_action_plan)
    assert list(sig.parameters) == ["graph", "ctx", "policy"]


def test_h2_validate_extract_signatures() -> None:
    assert list(inspect.signature(validate_proposal).parameters) == [
        "proposal", "ctx",
    ]
    assert list(inspect.signature(extract_paths).parameters) == [
        "payload", "node_id",
    ]


# ---------------------------------------------------------------------------
# J. Known-issue pinning (not BUGs)
# ---------------------------------------------------------------------------

def test_j1_source_port_out_fail_closed() -> None:
    """UX contract for 12a: GUI must set source_port='path_template'."""
    ctx = FakeDataclassCtx()
    n1 = _node(
        "n1", "utils.path_template",
        {"template": "{name}.txt", "variables": {"name": "x"}},
    )
    n2 = _node("n2", "fs.write_file", {"content": "hi"})
    graph = _graph(
        [n1, n2],
        [_edge("n1", "n2", "out", "path_template")],
        {"n1": PathTemplateNode(), "n2": WriteFileNode()},
    )

    plan = build_action_plan(graph, ctx, AllowPolicy())

    assert plan.blocked
    assert any(error.node_id == "n2" for error in plan.errors)