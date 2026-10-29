"""Coverage for the remaining error/branch paths in
``lokdocsqr.core.executor.engine``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.helpers import edge, make_ctx, make_doc, node_spec, port, resolved  # noqa: E402

from lokdocsqr.core.executor.engine import execute  # noqa: E402
from lokdocsqr.core.models import (  # noqa: E402
    ActionLog,
    NodeResult,
    NodeState,
    ResolvedGraph,
)


class _InstanceNode:
    """A handler passed as an instance, recording every call."""

    def __init__(self, name: str = "n", output=None) -> None:
        self.name = name
        self.output = {} if output is None else output
        self.calls = 0

    def run(self, ctx, payload):
        self.calls += 1
        return self.output

    def dry_run(self, ctx, payload):
        self.calls += 1
        return self.output


class _ClassNode:
    """A handler passed as a class — the engine must instantiate it."""

    instances: list["_ClassNode"] = []

    def __init__(self) -> None:
        self.calls = 0
        _ClassNode.instances.append(self)

    def run(self, ctx, payload):
        self.calls += 1
        return {}

    def dry_run(self, ctx, payload):
        self.calls += 1
        return {}


class _BrokenInstantiation:
    def __init__(self) -> None:
        raise RuntimeError("__init__ exploded")


class _FailingNode:
    """Fails ``fail_times`` attempts, then succeeds."""

    def __init__(self, fail_times: int = 1) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def run(self, ctx, payload):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("boom")
        return {}

    def dry_run(self, ctx, payload):
        return self.run(ctx, payload)


class _AlwaysFail:
    def run(self, ctx, payload):
        raise RuntimeError("boom")

    def dry_run(self, ctx, payload):
        raise RuntimeError("boom")


def _resolved_unchecked(
    doc,
    ordered_node_ids: list[str],
    handlers: dict,
    *,
    states: dict | None = None,
) -> ResolvedGraph:
    """Build a :class:`ResolvedGraph` bypassing M5 ``__post_init__``.

    Phase 8d-a contract (M5): ``ResolvedGraph.ordered_node_ids`` must be a
    subset of ``{spec.id for spec in document.nodes}``; ghost ids raise
    ``ValueError`` in ``__post_init__``.

    This helper is used **only** by tests that deliberately construct an
    inconsistent graph to exercise the executor's defensive "missing node
    spec" branch — i.e. the code path that in the real pipeline would have
    been rejected upstream by the parser/validator before a
    :class:`ResolvedGraph` is ever built.
    """
    graph = object.__new__(ResolvedGraph)
    object.__setattr__(graph, "document", doc)
    object.__setattr__(graph, "ordered_node_ids", list(ordered_node_ids))
    object.__setattr__(graph, "handlers", dict(handlers))
    object.__setattr__(graph, "states", dict(states or {}))
    return graph


# ---------------------------------------------------------------------------
# _node_instance: class handler support
# ---------------------------------------------------------------------------

def test_handler_passed_as_class_is_instantiated(tmp_path: Path) -> None:
    _ClassNode.instances.clear()
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": _ClassNode})
    execute(graph, make_ctx(tmp_path))
    assert len(_ClassNode.instances) == 1
    assert _ClassNode.instances[0].calls == 1


# ---------------------------------------------------------------------------
# Missing node_spec / handler / instantiation failure
# ---------------------------------------------------------------------------

def test_missing_node_spec_marks_failed(tmp_path: Path) -> None:
    doc = make_doc([], [])
    handler = _InstanceNode()
    # M5 (phase 8d-a): the public ``ResolvedGraph`` constructor now rejects
    # ghost ids in ``ordered_node_ids`` with ``ValueError``. We build the
    # graph through the unvalidated helper on purpose so this test still
    # exercises the executor's defensive "missing node spec" branch
    # unchanged — that branch runs *before* the invariant would have been
    # checked by the parser/validator in the real pipeline.
    graph = _resolved_unchecked(
        doc,
        ["ghost"],
        {"ghost": handler},
        states={"ghost": NodeState.ENABLED},
    )
    result = execute(graph, make_ctx(tmp_path))
    assert result.success is False
    assert result.node_results["ghost"].state is NodeState.FAILED
    assert any("not found in document" in e for e in result.errors)
    assert handler.calls == 0


def test_missing_handler_marks_failed(tmp_path: Path) -> None:
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {})
    result = execute(graph, make_ctx(tmp_path))
    assert result.success is False
    assert result.node_results["n"].state is NodeState.FAILED
    assert any("no handler" in e for e in result.errors)


def test_handler_instantiation_failure_marks_failed(tmp_path: Path) -> None:
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": _BrokenInstantiation})
    result = execute(graph, make_ctx(tmp_path))
    assert result.success is False
    assert result.node_results["n"].state is NodeState.FAILED
    assert any("instantiation failed" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Skip via resolved states (parent has no result)
# ---------------------------------------------------------------------------

def test_skip_uses_resolved_state_when_parent_has_no_result(
    tmp_path: Path,
) -> None:
    doc = make_doc(
        [node_spec("parent"), node_spec("child", inputs=[port("in")])],
        [edge("parent", "child")],
    )
    child = _InstanceNode("child")
    graph = resolved(
        doc,
        ["child"],
        {"child": child},
        states={
            "parent": NodeState.DISABLED,
            "child": NodeState.ENABLED,
        },
    )
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["child"].state is NodeState.SKIPPED
    assert child.calls == 0


def test_skip_via_failed_parent_result(tmp_path: Path) -> None:
    doc = make_doc(
        [node_spec("parent"), node_spec("child")],
        [edge("parent", "child")],
        strategy="continue",
    )
    child = _InstanceNode("child")
    graph = resolved(
        doc, ["parent", "child"], {"parent": _AlwaysFail(), "child": child}
    )
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["parent"].state is NodeState.FAILED
    assert result.node_results["child"].state is NodeState.SKIPPED
    assert child.calls == 0


# ---------------------------------------------------------------------------
# Retry / backoff variants
# ---------------------------------------------------------------------------

def test_linear_backoff_delays(tmp_path: Path) -> None:
    node = _FailingNode(fail_times=3)
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=3,
        retry_delay_seconds=0.1,
        backoff="linear",
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []
    execute(graph, make_ctx(tmp_path), sleep=sleeps.append)
    assert sleeps == [
        pytest.approx(0.1),
        pytest.approx(0.2),
        pytest.approx(0.3),
    ]


def test_exponential_backoff_delays(tmp_path: Path) -> None:
    node = _FailingNode(fail_times=3)
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=3,
        retry_delay_seconds=0.1,
        backoff="exponential",
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []
    execute(graph, make_ctx(tmp_path), sleep=sleeps.append)
    assert sleeps == [
        pytest.approx(0.1),
        pytest.approx(0.2),
        pytest.approx(0.4),
    ]


def test_unknown_backoff_uses_base_delay(tmp_path: Path) -> None:
    node = _FailingNode(fail_times=1)
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=1,
        retry_delay_seconds=0.05,
        backoff="mystery",
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []
    execute(graph, make_ctx(tmp_path), sleep=sleeps.append)
    assert sleeps == [pytest.approx(0.05)]


def test_zero_retry_delay_does_not_sleep(tmp_path: Path) -> None:
    node = _FailingNode(fail_times=1)
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=1,
        retry_delay_seconds=0.0,
        backoff="exponential",
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []
    execute(graph, make_ctx(tmp_path), sleep=sleeps.append)
    assert sleeps == []


def test_retry_strategy_with_zero_max_retries_fails_immediately(
    tmp_path: Path,
) -> None:
    node = _FailingNode(fail_times=1)
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=0,
        retry_delay_seconds=0.1,
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []
    result = execute(graph, make_ctx(tmp_path), sleep=sleeps.append)
    assert sleeps == []
    assert result.node_results["n"].state is NodeState.FAILED
    assert node.calls == 1


# ---------------------------------------------------------------------------
# Strategy fallback
# ---------------------------------------------------------------------------

def test_unknown_strategy_falls_back_to_fail(tmp_path: Path) -> None:
    downstream = _InstanceNode("b")
    doc = make_doc(
        [node_spec("a"), node_spec("b")],
        [edge("a", "b")],
        strategy="totally-not-a-strategy",
    )
    graph = resolved(
        doc, ["a", "b"], {"a": _AlwaysFail(), "b": downstream}
    )
    result = execute(graph, make_ctx(tmp_path))
    assert result.success is False
    # Fallback ``fail`` aborts execution: downstream must not run.
    assert downstream.calls == 0


# ---------------------------------------------------------------------------
# _normalize_result: max attempts / duration are preserved
# ---------------------------------------------------------------------------

def test_ready_node_result_max_attempts_and_duration_are_kept(
    tmp_path: Path,
) -> None:
    class _Rich:
        def run(self, ctx, payload):
            return NodeResult(
                node_id="",
                state=NodeState.ENABLED,
                outputs={"x": 1},
                attempts=5,
                duration_seconds=99.0,
            )

        def dry_run(self, ctx, payload):
            return self.run(ctx, payload)

    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": _Rich()})
    result = execute(graph, make_ctx(tmp_path), clock=lambda: 0.0)
    node_result = result.node_results["n"]
    assert node_result.attempts == 5
    assert node_result.duration_seconds == 99.0
    assert node_result.node_id == "n"


# ---------------------------------------------------------------------------
# Audit transitions for failed nodes
# ---------------------------------------------------------------------------

def test_failed_node_logs_become_failed_under_continue(
    tmp_path: Path,
) -> None:
    log = ActionLog(node_id="n", action_type="move", status="planned")

    class _Failed:
        def run(self, ctx, payload):
            return NodeResult(
                node_id="n",
                state=NodeState.FAILED,
                action_logs=[log],
                error="boom",
            )

        def dry_run(self, ctx, payload):
            return self.run(ctx, payload)

    doc = make_doc([node_spec("n")], [], strategy="continue")
    graph = resolved(doc, ["n"], {"n": _Failed()})
    ctx = make_ctx(tmp_path)
    execute(graph, ctx)
    stored = ctx.audit.lookup_by_run("run-1")
    assert [entry.status for entry in stored] == ["failed"]


def test_skipped_node_produces_no_audit_entries(tmp_path: Path) -> None:
    class _FailWithLog:
        def run(self, ctx, payload):
            return NodeResult(
                node_id="a",
                state=NodeState.FAILED,
                action_logs=[
                    ActionLog(
                        node_id="a",
                        action_type="move",
                        status="planned",
                    )
                ],
                error="boom",
            )

        def dry_run(self, ctx, payload):
            return self.run(ctx, payload)

    b = _InstanceNode("b")
    doc = make_doc(
        [node_spec("a"), node_spec("b")],
        [edge("a", "b")],
        strategy="continue",
    )
    graph = resolved(doc, ["a", "b"], {"a": _FailWithLog(), "b": b})
    ctx = make_ctx(tmp_path)
    execute(graph, ctx)
    stored = ctx.audit.lookup_by_run("run-1")
    assert {entry.node_id for entry in stored} == {"a"}