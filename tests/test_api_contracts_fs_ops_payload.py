"""T0 — fs template nodes honour payload/NodeSpec params.

These tests pin the fix for ``TICKET-8z-T0``:

  * ``fs.copy`` / ``fs.move`` / ``fs.rename`` read ``template`` and
    ``conflict_policy`` from the payload, with instance attributes as
    fallback.
  * The executor seeds ``NodeSpec.params`` into the payload, so a node
    configured only through workflow JSON behaves like a directly
    constructed one.
  * Planner and executor resolve the same params-only payload.
"""
from __future__ import annotations

from pathlib import Path

from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeResult,
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
from lokdocsqr.nodes.fs_ops.copy import CopyNode
from lokdocsqr.nodes.fs_ops.move import MoveNode
from lokdocsqr.nodes.fs_ops.rename import RenameNode


class _AllowPolicy:
    def decide(self, proposal):
        return PolicyDecision(PolicyAction.ALLOW, "allow", proposal)


def test_copy_honors_payload_template(exec_ctx, tmp_path) -> None:
    from lokdocsqr.core.models import ActionLog

    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    result = CopyNode().dry_run(
        exec_ctx,
        {"source": str(src), "template": "dest_{name}{ext}"},
    )
    log = result.get("action_log") if isinstance(result, dict) else result
    assert isinstance(log, ActionLog)
    assert log.target == str(tmp_path / "dest_a.txt")


def test_move_honors_payload_template_and_conflict_policy(
    exec_ctx, tmp_path
) -> None:
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    (tmp_path / "b.txt").write_text("exists", encoding="utf-8")

    # Instance defaults would rename to a.txt/no-op; the payload must win.
    node = MoveNode(template="{name}{ext}", conflict_policy="overwrite")
    log = node.dry_run(
        exec_ctx,
        {
            "source": str(src),
            "template": "b{ext}",
            "conflict_policy": "skip",
        },
    )
    assert log.target == str(tmp_path / "b.txt")
    assert log.reason == "conflict_skip"


def test_rename_honors_payload_template_and_falls_back(
    exec_ctx, tmp_path
) -> None:
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")

    payload_log = RenameNode(template="ignored{ext}").dry_run(
        exec_ctx,
        {"source": str(src), "template": "{name}_payload{ext}"},
    )
    assert payload_log.target == str(tmp_path / "a_payload.txt")

    fallback_log = RenameNode(template="{name}_instance{ext}").dry_run(
        exec_ctx, {"source": str(src)}
    )
    assert fallback_log.target == str(tmp_path / "a_instance.txt")


def test_copy_rejects_non_string_payload_template(exec_ctx, tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")
    try:
        CopyNode().dry_run(exec_ctx, {"source": str(src), "template": 123})
    except ValueError as exc:
        assert "template" in str(exc)
    else:  # pragma: no cover - explicit failure message
        raise AssertionError("non-string template must fail closed")


class _SpyMove(MoveNode):
    """Capture every payload passed to dry_run while keeping real behaviour."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[dict] = []

    def dry_run(self, ctx, payload):
        self.captured.append(dict(payload))
        return super().dry_run(ctx, payload)


def _params_graph(handler, src: Path, template: str) -> ResolvedGraph:
    spec = NodeSpec(
        id="n1",
        type="fs.move",
        params={"source": str(src), "template": template},
    )
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="test"),
        nodes=[spec],
    )
    return ResolvedGraph(
        document=doc,
        ordered_node_ids=["n1"],
        handlers={"n1": handler},
        states={"n1": NodeState.ENABLED},
    )


def test_params_edge_precedence_matches_both_resolvers(exec_ctx) -> None:
    """Order is params < edge values < port defaults in both resolvers."""
    spec = NodeSpec(
        id="n2",
        type="fs.write_file",
        params={"path_template": "from_params.txt"},
        inputs=[PortSpec(name="path_template", default="from_default.txt")],
    )
    edge = EdgeSpec(
        id="e1",
        source="n1",
        target="n2",
        source_port="out",
        target_port="path_template",
    )
    upstream = {"n1": {"out": "from_edge.txt"}}

    planner_payload = _node_payload(
        spec,
        inbound_edges=[edge],
        outputs_by_node=upstream,
        resolve_edges=True,
    )
    executor_payload = Executor(exec_ctx)._build_payload(
        "n2",
        spec,
        [edge],
        {
            "n1": NodeResult(
                node_id="n1",
                state=NodeState.ENABLED,
                outputs={"out": "from_edge.txt"},
            )
        },
    )

    expected = {"path_template": "from_edge.txt"}
    assert planner_payload == expected
    assert executor_payload == expected


def test_planner_executor_params_payload_parity(exec_ctx, tmp_path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("hello", encoding="utf-8")

    planner_spy = _SpyMove()
    plan = build_action_plan(
        _params_graph(planner_spy, src, "planned.txt"),
        exec_ctx,
        _AllowPolicy(),
    )
    assert not plan.blocked, plan.errors
    assert len(planner_spy.captured) == 1

    executor_spy = _SpyMove()
    graph = _params_graph(executor_spy, src, "planned.txt")
    exec_ctx.dry_run = True
    Executor(exec_ctx).execute(graph)

    assert len(executor_spy.captured) == 1
    assert executor_spy.captured[0] == planner_spy.captured[0]
    assert executor_spy.captured[0] == {
        "source": str(src),
        "template": "planned.txt",
    }


def test_engine_executes_params_only_fs_move(exec_ctx, tmp_path) -> None:
    """End-to-end: params-only NodeSpec must perform the configured move."""
    src = tmp_path / "a.txt"
    src.write_text("new-content", encoding="utf-8")
    dst = tmp_path / "moved.txt"
    dst.write_text("old-content", encoding="utf-8")

    spec = NodeSpec(
        id="n1",
        type="fs.move",
        params={
            "source": str(src),
            "template": "moved.txt",
            "conflict_policy": "overwrite",
        },
    )
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="test"),
        nodes=[spec],
    )
    graph = ResolvedGraph(
        document=doc,
        ordered_node_ids=["n1"],
        handlers={"n1": MoveNode},
        states={"n1": NodeState.ENABLED},
    )

    result = Executor(exec_ctx).execute(graph)

    assert result.success, result.errors
    assert not src.exists()
    assert dst.read_text(encoding="utf-8") == "new-content"
