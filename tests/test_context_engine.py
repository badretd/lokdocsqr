"""Phase 4: ExecutionContext + Executor engine behaviour."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.helpers import edge, make_ctx, make_doc, node_spec, port, resolved  # noqa: E402

from lokdocsqr.core.errors import GraphValidationError, NodeExecutionError  # noqa: E402
from lokdocsqr.core.events import (  # noqa: E402
    EVENT_AUDIT_LOGGED,
    EVENT_NODE_FINISHED,
    EVENT_NODE_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
)
from lokdocsqr.core.executor.engine import execute  # noqa: E402
from lokdocsqr.core.models import ActionLog, NodeResult, NodeState  # noqa: E402
from lokdocsqr.core.node_api import Node  # noqa: E402


_SENTINEL = object()


class _Node:
    def __init__(
        self,
        name: str,
        *,
        raw=_SENTINEL,
        outputs=None,
        logs=None,
        state=None,
        fail_times: int = 0,
        exc: BaseException | None = None,
        always_fail: bool = False,
    ) -> None:
        self.name = name
        self.raw = raw
        self.outputs = dict(outputs or {})
        self.logs = list(logs or [])
        self.state = state
        self.fail_times = fail_times
        self.exc = exc
        self.always_fail = always_fail
        self.calls: list[dict] = []
        self.dry_calls: list[dict] = []

    def run(self, ctx, payload):
        self.calls.append(dict(payload))
        if self.always_fail or self.fail_times > 0:
            if self.fail_times > 0:
                self.fail_times -= 1
            raise self.exc or RuntimeError("boom")
        if self.raw is not _SENTINEL:
            return self.raw
        return NodeResult(
            node_id=self.name,
            state=self.state or NodeState.ENABLED,
            outputs=dict(self.outputs),
            action_logs=list(self.logs),
        )

    def dry_run(self, ctx, payload):
        self.dry_calls.append(dict(payload))
        if self.raw is not _SENTINEL:
            return self.raw
        return NodeResult(
            node_id=self.name,
            state=self.state or NodeState.ENABLED,
            outputs=dict(self.outputs),
            action_logs=list(self.logs),
        )


class _FakeBus:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def emit(self, event_type: str, payload: dict) -> None:
        self.calls.append((event_type, payload))


# ---------------------------------------------------------------------------
# C. ExecutionContext
# ---------------------------------------------------------------------------

def test_context_new_action_log_fills_run_and_automation(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, run_id="run-X", automation_id="auto-Y")
    log = ctx.new_action_log(node_id="n", action_type="move", status="planned")
    assert log.run_id == "run-X"
    assert log.automation_id == "auto-Y"
    assert log.node_id == "n"


def test_context_resolved_path_caches_scope_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = make_ctx(tmp_path)
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")

    real_check = ctx.scope.check
    calls = []

    def spy(path, write=False):
        calls.append((Path(path), write))
        return real_check(path, write=write)

    monkeypatch.setattr(ctx.scope, "check", spy)
    first = ctx.resolved_path(ctx.scope, "f.txt")
    second = ctx.resolved_path(ctx.scope, "f.txt")

    assert first == second
    assert len(calls) == 1


def test_context_emit_delegates_to_event_bus(tmp_path: Path) -> None:
    bus = _FakeBus()
    ctx = make_ctx(tmp_path, events=bus)
    ctx.emit("evt", {"a": 1})
    assert bus.calls == [("evt", {"a": 1})]


# ---------------------------------------------------------------------------
# D1. Toposort / payload
# ---------------------------------------------------------------------------

def test_executor_calls_nodes_in_resolved_topological_order(tmp_path: Path) -> None:
    order: list[str] = []

    class _OrderNode:
        def __init__(self, name: str) -> None:
            self.name = name

        def run(self, ctx, payload):
            order.append(self.name)
            return {}

        def dry_run(self, ctx, payload):
            return {}

    doc = make_doc([node_spec("last"), node_spec("first"), node_spec("mid")], [])
    handlers = {name: _OrderNode(name) for name in ("first", "mid", "last")}
    graph = resolved(doc, ["first", "mid", "last"], handlers)
    execute(graph, make_ctx(tmp_path))
    assert order == ["first", "mid", "last"]


def test_payload_single_edge_copies_source_port_to_target_port(tmp_path: Path) -> None:
    source = _Node("src", outputs={"out": 42})
    target = _Node("dst")
    doc = make_doc(
        [node_spec("src", outputs=[port("out")]), node_spec("dst", inputs=[port("in")])],
        [edge("src", "dst", source_port="out", target_port="in")],
    )
    graph = resolved(doc, ["src", "dst"], {"src": source, "dst": target})
    execute(graph, make_ctx(tmp_path))
    assert target.calls[-1]["in"] == 42


def test_payload_two_edges_to_same_target_port_forms_list(tmp_path: Path) -> None:
    a = _Node("a", outputs={"out": "v1"})
    b = _Node("b", outputs={"out": "v2"})
    target = _Node("t")
    doc = make_doc(
        [
            node_spec("a", outputs=[port("out")]),
            node_spec("b", outputs=[port("out")]),
            node_spec("t", inputs=[port("in")]),
        ],
        [
            edge("a", "t", source_port="out", target_port="in"),
            edge("b", "t", source_port="out", target_port="in"),
        ],
    )
    graph = resolved(doc, ["a", "b", "t"], {"a": a, "b": b, "t": target})
    execute(graph, make_ctx(tmp_path))
    assert target.calls[-1]["in"] == ["v1", "v2"]


def test_payload_applies_default_port_when_other_edge_exists(tmp_path: Path) -> None:
    source = _Node("src", outputs={"out": 1})
    target = _Node("dst")
    doc = make_doc(
        [
            node_spec("src", outputs=[port("out")]),
            node_spec("dst", inputs=[port("in", default="D"), port("other")]),
        ],
        [edge("src", "dst", source_port="out", target_port="other")],
    )
    graph = resolved(doc, ["src", "dst"], {"src": source, "dst": target})
    execute(graph, make_ctx(tmp_path))
    assert target.calls[-1]["in"] == "D"
    assert target.calls[-1]["other"] == 1


def test_payload_default_port_without_any_edge(tmp_path: Path) -> None:
    target = _Node("t")
    doc = make_doc([node_spec("t", inputs=[port("in", default="D")])], [])
    graph = resolved(doc, ["t"], {"t": target})
    execute(graph, make_ctx(tmp_path))
    assert target.calls[-1]["in"] == "D"


def test_required_port_without_edge_raises_before_node_run(tmp_path: Path) -> None:
    target = _Node("t")
    doc = make_doc([node_spec("t", inputs=[port("req", required=True)])], [])
    graph = resolved(doc, ["t"], {"t": target})
    with pytest.raises(GraphValidationError):
        execute(graph, make_ctx(tmp_path))
    assert target.calls == []


def test_optional_port_without_edge_is_absent(tmp_path: Path) -> None:
    target = _Node("t")
    doc = make_doc([node_spec("t", inputs=[port("opt", required=False)])], [])
    graph = resolved(doc, ["t"], {"t": target})
    execute(graph, make_ctx(tmp_path))
    assert target.calls[-1] == {}


def test_trigger_node_receives_empty_payload(tmp_path: Path) -> None:
    trigger = _Node("trigger")
    doc = make_doc([node_spec("trigger")], [])
    graph = resolved(doc, ["trigger"], {"trigger": trigger})
    execute(graph, make_ctx(tmp_path))
    assert trigger.calls[-1] == {}


# ---------------------------------------------------------------------------
# D2. Node.run return normalization
# ---------------------------------------------------------------------------

def test_normalize_dict_return(tmp_path: Path) -> None:
    node = _Node("n", raw={"x": 1})
    doc = make_doc([node_spec("n", outputs=[port("out")])], [])
    graph = resolved(doc, ["n"], {"n": node})
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["n"].outputs == {"x": 1}


def test_normalize_scalar_with_one_output_port(tmp_path: Path) -> None:
    node = _Node("n", raw=7)
    doc = make_doc([node_spec("n", outputs=[port("out")])], [])
    graph = resolved(doc, ["n"], {"n": node})
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["n"].outputs == {"out": 7}


def test_normalize_scalar_with_zero_output_ports(tmp_path: Path) -> None:
    node = _Node("n", raw=7)
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["n"].outputs == {}


def test_normalize_scalar_with_two_output_ports_raises_node_execution_error(
    tmp_path: Path,
) -> None:
    node = _Node("n", raw=7)
    doc = make_doc([node_spec("n", outputs=[port("a"), port("b")])], [])
    graph = resolved(doc, ["n"], {"n": node})
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["n"].state is NodeState.FAILED
    assert result.success is False
    assert any("unsupported value" in error for error in result.errors)


def test_normalize_ready_node_result_is_used_as_is(tmp_path: Path) -> None:
    node = _Node(
        "n",
        raw=NodeResult(node_id="", state=NodeState.ENABLED, outputs={"x": 1}),
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    result = execute(graph, make_ctx(tmp_path))
    assert result.node_results["n"].node_id == "n"
    assert result.node_results["n"].outputs == {"x": 1}


# ---------------------------------------------------------------------------
# D3. Dry-run
# ---------------------------------------------------------------------------

def test_dry_run_calls_dry_run_not_run(tmp_path: Path) -> None:
    node = Mock(spec=Node)
    node.dry_run.return_value = NodeResult(node_id="n", state=NodeState.ENABLED)
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})

    execute(graph, make_ctx(tmp_path, dry_run=True))

    node.dry_run.assert_called_once()
    node.run.assert_not_called()


def test_dry_run_audit_status_is_dry_run(tmp_path: Path) -> None:
    node = _Node(
        "n",
        logs=[ActionLog(node_id="n", action_type="move", status="planned")],
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    ctx = make_ctx(tmp_path, dry_run=True)

    result = execute(graph, ctx)

    logs = ctx.audit.lookup_by_run("run-1")
    assert [entry.status for entry in logs] == ["dry_run"]
    assert result.action_logs[0].status == "dry_run"


def test_dry_run_does_not_change_domain_files(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("x", encoding="utf-8")
    node = _Node(
        "n",
        logs=[
            ActionLog(
                node_id="n",
                action_type="move",
                source=str(work / "a.txt"),
                target=str(work / "b.txt"),
                status="planned",
                rollback_eligible=True,
            )
        ],
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})

    def snapshot() -> list[str]:
        return sorted(str(p.relative_to(work)) for p in work.rglob("*"))

    before = snapshot()
    execute(graph, make_ctx(tmp_path, dry_run=True))
    assert snapshot() == before


# ---------------------------------------------------------------------------
# D4. Retry / fail / continue
# ---------------------------------------------------------------------------

def test_strategy_fail_stops_downstream(tmp_path: Path) -> None:
    first = _Node("a", always_fail=True, exc=RuntimeError("boom"))
    second = _Node("b")
    doc = make_doc(
        [node_spec("a"), node_spec("b")],
        [edge("a", "b")],
        strategy="fail",
    )
    graph = resolved(doc, ["a", "b"], {"a": first, "b": second})

    result = execute(graph, make_ctx(tmp_path))

    assert first.calls != []
    assert second.calls == []
    assert result.success is False
    assert result.errors


def test_strategy_retry_attempts_and_sleep_backoff(tmp_path: Path) -> None:
    node = _Node("n", fail_times=2, raw={})
    doc = make_doc(
        [node_spec("n")],
        [],
        strategy="retry",
        max_retries=2,
        retry_delay_seconds=0.1,
        backoff="exponential",
    )
    graph = resolved(doc, ["n"], {"n": node})
    sleeps: list[float] = []

    result = execute(graph, make_ctx(tmp_path), sleep=sleeps.append)

    assert len(node.calls) == 3
    assert len(sleeps) == 2
    assert sleeps[0] == pytest.approx(0.1)
    assert sleeps[1] == pytest.approx(0.2)
    assert result.node_results["n"].attempts == 3


def test_strategy_continue_marks_failed_and_skips_downstream(tmp_path: Path) -> None:
    first = _Node("a", always_fail=True, exc=RuntimeError("boom"))
    second = _Node("b")
    doc = make_doc(
        [node_spec("a"), node_spec("b")],
        [edge("a", "b")],
        strategy="continue",
    )
    graph = resolved(doc, ["a", "b"], {"a": first, "b": second})

    result = execute(graph, make_ctx(tmp_path))

    assert result.node_results["a"].state is NodeState.FAILED
    assert result.node_results["b"].state is NodeState.SKIPPED
    assert second.calls == []
    assert result.success is False


# ---------------------------------------------------------------------------
# D5. Events
# ---------------------------------------------------------------------------

def test_events_order_and_run_id_payloads(tmp_path: Path) -> None:
    bus = _FakeBus()
    a = _Node("a", logs=[ActionLog(node_id="a", action_type="move", status="planned")])
    b = _Node("b", logs=[ActionLog(node_id="b", action_type="copy", status="planned")])
    doc = make_doc([node_spec("a"), node_spec("b")], [edge("a", "b")])
    graph = resolved(doc, ["a", "b"], {"a": a, "b": b})

    execute(graph, make_ctx(tmp_path, events=bus))

    types = [event_type for event_type, _ in bus.calls]
    assert types[0] == EVENT_RUN_STARTED
    assert types[-1] == EVENT_RUN_FINISHED
    assert types.count(EVENT_NODE_STARTED) == 2
    assert types.count(EVENT_NODE_FINISHED) == 2
    assert types.count(EVENT_AUDIT_LOGGED) == 2
    assert all(payload["run_id"] == "run-1" for _, payload in bus.calls)


def test_executor_uses_ctx_events_emit(tmp_path: Path) -> None:
    bus = _FakeBus()
    node = _Node("n")
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    execute(graph, make_ctx(tmp_path, events=bus))
    assert bus.calls
    assert bus.calls[0][0] == EVENT_RUN_STARTED


# ---------------------------------------------------------------------------
# D6. Audit
# ---------------------------------------------------------------------------

def test_audit_logs_get_run_and_automation_and_applied_status(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    node = _Node(
        "n",
        logs=[
            ActionLog(
                node_id="n",
                action_type="move",
                source=str(src),
                target=str(dst),
                status="planned",
                rollback_eligible=True,
            )
        ],
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    ctx = make_ctx(tmp_path)

    execute(graph, ctx)

    logs = ctx.audit.lookup_by_run("run-1")
    assert len(logs) == 1
    assert logs[0].run_id == "run-1"
    assert logs[0].automation_id == "auto-1"
    assert logs[0].status == "applied"


def test_failed_node_action_log_status_failed(tmp_path: Path) -> None:
    node = _Node(
        "n",
        raw=NodeResult(
            node_id="n",
            state=NodeState.FAILED,
            action_logs=[ActionLog(node_id="n", action_type="move", status="planned")],
        ),
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    ctx = make_ctx(tmp_path)

    execute(graph, ctx)

    logs = ctx.audit.lookup_by_run("run-1")
    assert [entry.status for entry in logs] == ["failed"]


def test_runresult_rollback_plan_equals_audit_plan(tmp_path: Path) -> None:
    node = _Node(
        "n",
        logs=[
            ActionLog(
                node_id="n",
                action_type="move",
                source=str(tmp_path / "src.txt"),
                target=str(tmp_path / "dst.txt"),
                status="planned",
                rollback_eligible=True,
            )
        ],
    )
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})
    ctx = make_ctx(tmp_path)

    result = execute(graph, ctx)

    assert result.rollback_plan == ctx.audit.build_rollback_plan("run-1")


# ---------------------------------------------------------------------------
# F. Negative: arbitrary exception must not be swallowed
# ---------------------------------------------------------------------------

def test_node_run_exception_becomes_node_execution_error_in_runresult(
    tmp_path: Path,
) -> None:
    node = _Node("n", always_fail=True, exc=RuntimeError("boom"))
    doc = make_doc([node_spec("n")], [])
    graph = resolved(doc, ["n"], {"n": node})

    result = execute(graph, make_ctx(tmp_path))

    assert result.success is False
    assert result.node_results["n"].state is NodeState.FAILED
    assert result.node_results["n"].error
    assert any("boom" in error for error in result.errors)
