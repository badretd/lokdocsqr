# ==== PHASE_8G_TESTS ====
"""Verification tests for LokDocSQR Phase 8g.

Covers groups 1-12 from the 8g verification brief:
  flow.retry structure/behaviour, utils.notify structure/channels/preview/
  email/webhook delivery, flow.* injection in subflow/loop_batch,
  Executor on_error injection, port contracts, imports.

No production-code scanning, no git calls, no test-from-test calls.
"""
from __future__ import annotations

import importlib
import smtplib
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any

import pytest

import lokdocsqr.nodes.flow.loop_batch as loop_batch_mod
import lokdocsqr.nodes.flow.subflow as subflow_mod
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeResult,
    NodeSpec,
    NodeState,
    ResolvedGraph,
    RunResult,
    WorkflowDocument,
    WorkflowErrorHandling,
    WorkflowMetadata,
    WorkflowSettings,
)
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.nodes.flow.loop_batch import LoopBatchNode
from lokdocsqr.nodes.flow.retry import FlowRetry
from lokdocsqr.nodes.flow.subflow import SubflowNode
from lokdocsqr.nodes.utils.notify import Notify

# ---------------------------------------------------------------------------
# Helpers / doubles
# ---------------------------------------------------------------------------

_RUN_ID = "run-8g"
_FLOW_KEYS = frozenset({
    "flow.run_id",
    "flow.automation_id",
    "flow.depth",
    "flow.parent_run_id",
})


class FakeCatalog:
    def __init__(self, graphs: dict[str, Any] | None = None) -> None:
        self.graphs = dict(graphs or {})
        self.calls: list[str] = []

    def get(self, graph_id: str) -> Any | None:
        self.calls.append(graph_id)
        return self.graphs.get(graph_id)


def _make_exec_ctx(
    tmp_path: Any,
    *,
    flow_depth: int = 0,
    subflow_stack: tuple[str, ...] = (),
    automations: Any = None,
    variables: dict[str, Any] | None = None,
    max_subflow_depth: int = 4,
) -> ExecutionContext:
    return ExecutionContext(
        scope=object(),
        config=SimpleNamespace(
            base_dir=tmp_path,
            trash_dir=tmp_path / "trash",
            logs_dir=tmp_path / "logs",
            flow=SimpleNamespace(max_subflow_depth=max_subflow_depth),
        ),
        audit=object(),
        run_id=_RUN_ID,
        automation_id="auto-8g",
        dry_run=False,
        policy=None,
        variables=dict(variables or {}),
        flow_depth=flow_depth,
        subflow_stack=subflow_stack,
        automations=automations,
    )


def _install_fake_executor(monkeypatch: pytest.MonkeyPatch, module: Any) -> SimpleNamespace:
    ctxs: list[Any] = []
    graphs: list[Any] = []

    class _FakeExecutor:
        def __init__(self, ctx: Any) -> None:
            ctxs.append(ctx)

        def execute(self, graph: Any) -> Any:
            graphs.append(graph)
            return RunResult(
                run_id=f"child-{len(graphs) - 1}",
                automation_id="auto-child",
                dry_run=False,
                success=True,
            )

    monkeypatch.setattr(module, "Executor", _FakeExecutor)
    return SimpleNamespace(ctxs=ctxs, graphs=graphs)


def _assert_flow_vars(
    child_vars: dict[str, Any],
    *,
    expected_automation_id: Any,
    expected_depth: int,
) -> None:
    assert child_vars["flow.run_id"] == _RUN_ID
    assert child_vars["flow.automation_id"] == expected_automation_id
    assert child_vars["flow.depth"] == expected_depth
    assert child_vars["flow.parent_run_id"] == _RUN_ID


def _simple_ctx() -> SimpleNamespace:
    return SimpleNamespace(
        run_id="run-exec",
        automation_id="auto-exec",
        dry_run=False,
        policy=None,
        verify_callback=None,
        emit=lambda *args, **kwargs: None,
        audit=SimpleNamespace(build_rollback_plan=lambda run_id: []),
    )


class _RecorderNode:
    def __init__(
        self,
        node_id: str,
        *,
        fail: bool = False,
        inject: bool = False,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        self.id = node_id
        self._inject_on_error = inject
        self.retry_safe = False
        self.fail = fail
        self.outputs = dict(outputs or {"out": "ok"})
        self.calls: list[dict[str, Any]] = []

    def run(self, ctx: Any, payload: Any) -> Any:
        self.calls.append(dict(payload))
        if self.fail:
            raise NodeExecutionError("boom")
        return dict(self.outputs)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self.run(ctx, payload)


def _make_resolved(
    nodes: list[str],
    edges: list[tuple[str, str]],
    handlers: dict[str, Any],
    *,
    ordered: list[str] | None = None,
    states: dict[str, NodeState] | None = None,
    strategy: str = "continue",
) -> ResolvedGraph:
    document = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="g-8g"),
        settings=WorkflowSettings(
            error_handling=WorkflowErrorHandling(strategy=strategy)
        ),
        nodes=[NodeSpec(id=nid, type="test") for nid in nodes],
        edges=[
            EdgeSpec(
                id=f"e{i}",
                source=src,
                target=dst,
                source_port="out",
                target_port="in",
            )
            for i, (src, dst) in enumerate(edges)
        ],
    )
    return ResolvedGraph(
        document=document,
        ordered_node_ids=list(ordered or nodes),
        handlers=dict(handlers),
        states=states or dict.fromkeys(nodes, NodeState.ENABLED),
    )


# ---------------------------------------------------------------------------
# Group 1 — flow.retry structure
# ---------------------------------------------------------------------------

def test_retry_registered_and_identity() -> None:
    assert registry.get("flow.retry") is FlowRetry
    assert FlowRetry.id == "flow.retry"


def test_retry_structure_flags_and_ports() -> None:
    node = FlowRetry()
    assert node.retry_safe is False
    assert getattr(FlowRetry, "_inject_on_error", False) is True
    assert node.inputs == {"retry_policy": "object"}
    assert node.outputs == {
        "retry": "boolean",
        "pass": "boolean",
        "retry_policy": "object",
        "on_error": "boolean",
    }


def test_retry_describe() -> None:
    desc = FlowRetry().describe()
    assert desc["id"] == "flow.retry"
    assert set(desc) == {"id", "title", "inputs", "outputs"}


# ---------------------------------------------------------------------------
# Group 2 — flow.retry behaviour
# ---------------------------------------------------------------------------

def test_retry_on_error_true() -> None:
    out = FlowRetry()._execute(None, {"on_error": True})
    assert out["retry"] is True
    assert out["pass"] is False
    assert isinstance(out["retry"], bool)
    assert isinstance(out["pass"], bool)
    assert out["on_error"] is True


def test_retry_on_error_false() -> None:
    out = FlowRetry()._execute(None, {"on_error": False})
    assert out["retry"] is False
    assert out["pass"] is True
    assert isinstance(out["retry"], bool)
    assert isinstance(out["pass"], bool)
    assert out["on_error"] is False


def test_retry_on_error_missing() -> None:
    out = FlowRetry()._execute(None, {})
    assert out["retry"] is False
    assert out["pass"] is True
    assert out["on_error"] is False


def test_retry_policy_passthrough() -> None:
    policy = {"attempts": 3, "backoff": 1.0}
    out = FlowRetry()._execute(None, {"retry_policy": policy, "on_error": True})
    assert out["retry_policy"] is policy


@pytest.mark.parametrize("on_error", [True, False])
def test_retry_preview_matches_execute(on_error: bool) -> None:
    node = FlowRetry()
    payload = {"on_error": on_error, "retry_policy": {"attempts": 2}}
    assert node._preview(None, payload) == node._execute(None, payload)


# ---------------------------------------------------------------------------
# Group 3 — utils.notify structure
# ---------------------------------------------------------------------------

def test_notify_retry_safe_and_ports() -> None:
    node = Notify()
    assert node.retry_safe is False
    assert node.inputs == {
        "channel": "string",
        "title": "string",
        "body": "string",
        "target": "string",
        "config": "object",
    }
    assert node.outputs == {
        "sent": "boolean",
        "channel": "string",
        "error": "string",
        "error_count": "integer",
        "dry_run": "boolean",
    }


def test_notify_preview_override_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        called.append(True)

    monkeypatch.setattr(Notify, "_send_email", spy)
    out = Notify()._preview(None, {"channel": "email"})
    assert out == {
        "sent": False,
        "channel": "email",
        "error": None,
        "error_count": 0,
        "dry_run": True,
    }
    assert called == []


# ---------------------------------------------------------------------------
# Group 4 — utils.notify channel validation
# ---------------------------------------------------------------------------

def test_notify_missing_channel_raises() -> None:
    with pytest.raises(NodeExecutionError):
        Notify()._execute(None, {})


def test_notify_desktop_channel_raises() -> None:
    with pytest.raises(NodeExecutionError) as exc:
        Notify()._execute(None, {"channel": "desktop"})
    text = str(exc.value).lower()
    assert "not supported" in text or "use 'email' or 'webhook'" in text


def test_notify_invalid_channel_raises() -> None:
    with pytest.raises(NodeExecutionError) as exc:
        Notify()._execute(None, {"channel": "invalid"})
    assert "expected 'email' or 'webhook'" in str(exc.value)


def test_notify_email_missing_config_fail_soft() -> None:
    out = Notify()._execute(
        None,
        {"channel": "email", "target": "to@example.com", "config": {}},
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "smtp_host" in out["error"] or "from_addr" in out["error"]


def test_notify_webhook_bad_target_fail_soft() -> None:
    out = Notify()._execute(
        None,
        {"channel": "webhook", "target": "ftp://example.com", "config": {}},
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "http" in out["error"].lower()


# ---------------------------------------------------------------------------
# Group 5 — utils.notify _preview
# ---------------------------------------------------------------------------

def test_notify_preview_email_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        called.append(True)

    monkeypatch.setattr(Notify, "_send_email", spy)
    out = Notify()._preview(None, {"channel": "email"})
    assert out == {
        "sent": False,
        "channel": "email",
        "error": None,
        "error_count": 0,
        "dry_run": True,
    }
    assert called == []


def test_notify_preview_desktop_raises() -> None:
    with pytest.raises(NodeExecutionError):
        Notify()._preview(None, {"channel": "desktop"})


def test_notify_preview_invalid_raises() -> None:
    with pytest.raises(NodeExecutionError):
        Notify()._preview(None, {"channel": "invalid"})


# ---------------------------------------------------------------------------
# Group 6 — utils.notify email _execute
# ---------------------------------------------------------------------------

def _install_fake_smtp(
    monkeypatch: pytest.MonkeyPatch, *, fail_send: bool = False
) -> list[Any]:
    instances: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.starttls_called = False
            self.login_called: tuple[str, str] | None = None
            self.sent: list[Any] = []
            self.quit_called = False
            instances.append(self)

        def starttls(self) -> None:
            self.starttls_called = True

        def login(self, user: str, password: str) -> None:
            self.login_called = (user, password)

        def send_message(self, msg: Any) -> None:
            if fail_send:
                raise smtplib.SMTPException("boom")
            self.sent.append(msg)

        def quit(self) -> None:
            self.quit_called = True

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    return instances


def test_notify_email_execute_success(monkeypatch: pytest.MonkeyPatch) -> None:
    instances = _install_fake_smtp(monkeypatch)
    out = Notify()._execute(
        None,
        {
            "channel": "email",
            "target": "to@example.com",
            "title": "t",
            "body": "b",
            "config": {
                "smtp_host": "smtp.example.com",
                "from_addr": "from@example.com",
                "smtp_tls": False,
            },
        },
    )
    assert out["sent"] is True
    assert out["error"] is None
    assert out["error_count"] == 0
    assert len(instances) == 1
    assert instances[0].sent


def test_notify_email_smtp_exception_fail_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_smtp(monkeypatch, fail_send=True)
    out = Notify()._execute(
        None,
        {
            "channel": "email",
            "target": "to@example.com",
            "config": {
                "smtp_host": "smtp.example.com",
                "from_addr": "from@example.com",
                "smtp_tls": False,
            },
        },
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "boom" in out["error"]


def test_notify_email_invalid_target_fail_soft() -> None:
    out = Notify()._execute(
        None,
        {
            "channel": "email",
            "target": "not-an-email",
            "config": {
                "smtp_host": "smtp.example.com",
                "from_addr": "from@example.com",
            },
        },
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "valid address" in out["error"]


def test_notify_email_tls_false_does_not_starttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances = _install_fake_smtp(monkeypatch)
    out = Notify()._execute(
        None,
        {
            "channel": "email",
            "target": "to@example.com",
            "config": {
                "smtp_host": "smtp.example.com",
                "from_addr": "from@example.com",
                "smtp_tls": False,
            },
        },
    )
    assert out["sent"] is True
    assert instances[0].starttls_called is False


# ---------------------------------------------------------------------------
# Group 7 — utils.notify webhook _execute
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _install_fake_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int | None = None,
    exc: BaseException | None = None,
) -> list[Any]:
    calls: list[Any] = []

    def fake_urlopen(req: Any, timeout: float | None = None) -> Any:
        calls.append((req, timeout))
        if exc is not None:
            raise exc
        return _FakeResponse(int(status))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_notify_webhook_200_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(monkeypatch, status=200)
    out = Notify()._execute(
        None,
        {
            "channel": "webhook",
            "target": "https://example.com/hook",
            "title": "t",
            "body": "b",
            "config": {},
        },
    )
    assert out["sent"] is True
    assert out["error"] is None
    assert out["error_count"] == 0


def test_notify_webhook_500_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(monkeypatch, status=500)
    out = Notify()._execute(
        None,
        {
            "channel": "webhook",
            "target": "https://example.com/hook",
            "config": {},
        },
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "HTTP" in out["error"]


def test_notify_webhook_urlerror_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_urlopen(monkeypatch, exc=urllib.error.URLError("nope"))
    out = Notify()._execute(
        None,
        {
            "channel": "webhook",
            "target": "https://example.com/hook",
            "config": {},
        },
    )
    assert out["sent"] is False
    assert out["error_count"] == 1
    assert "nope" in out["error"]


# ---------------------------------------------------------------------------
# Group 8 — flow.* injection in subflow
# ---------------------------------------------------------------------------

def test_subflow_flow_vars_injected(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"A": object()}),
        variables={"parent": 1},
    )
    out = SubflowNode()._execute(ctx, {"graph_id": "A"})
    child = fake.ctxs[-1]
    assert child.variables["parent"] == 1
    _assert_flow_vars(
        child.variables,
        expected_automation_id="A",
        expected_depth=1,
    )
    assert out["graph_id"] == "A"
    assert out["depth"] == 1


def test_subflow_merges_parent_and_flow_vars(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"A": object()}),
        variables={"parent": 1},
    )
    SubflowNode()._execute(ctx, {"graph_id": "A"})
    child = fake.ctxs[-1]
    assert child is not ctx
    assert child.variables["parent"] == 1
    _assert_flow_vars(
        child.variables,
        expected_automation_id="A",
        expected_depth=1,
    )
    assert set(child.variables) == {"parent", *_FLOW_KEYS}


def test_subflow_inline_automation_id_none(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, subflow_mod)
    monkeypatch.setattr(
        subflow_mod, "_build_inline", lambda graph: SimpleNamespace()
    )
    ctx = _make_exec_ctx(tmp_path, variables={"parent": 1})
    SubflowNode()._execute(ctx, {"graph": {"nodes": []}})
    child = fake.ctxs[-1]
    assert child.variables["parent"] == 1
    _assert_flow_vars(
        child.variables,
        expected_automation_id=None,
        expected_depth=1,
    )


def test_subflow_does_not_mutate_parent_ctx(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"A": object()}),
        variables={"parent": 1},
        subflow_stack=("root",),
        flow_depth=1,
    )
    variables_before = dict(ctx.variables)
    stack_before = ctx.subflow_stack
    depth_before = ctx.flow_depth
    flow_var_depth_before = ctx.variables.get("flow.depth")
    SubflowNode()._execute(ctx, {"graph_id": "A"})
    assert ctx.variables == variables_before
    assert ctx.flow_depth == depth_before
    assert ctx.variables.get("flow.depth") == flow_var_depth_before
    assert ctx.subflow_stack == stack_before


def test_subflow_returns_envelope(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"A": object()}),
    )
    out = SubflowNode()._execute(ctx, {"graph_id": "A"})
    assert isinstance(out, dict)
    assert set(out) >= {
        "result",
        "graph_id",
        "depth",
        "errors",
        "error_count",
    }
    assert isinstance(out["result"], RunResult)
    assert out["errors"] == []
    assert out["error_count"] == 0


# ---------------------------------------------------------------------------
# Group 9 — flow.* injection in loop_batch
# ---------------------------------------------------------------------------

def test_loop_batch_flow_vars_injected_loop_mode(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
        variables={"parent": 1},
    )
    LoopBatchNode()._execute(ctx, {"items": ["x", "y"], "graph_id": "g"})
    assert len(fake.ctxs) == 2
    for child in fake.ctxs:
        assert child.variables["parent"] == 1
        assert isinstance(child.variables["index"], int)
        assert isinstance(child.variables["batch"], list)
        _assert_flow_vars(
            child.variables,
            expected_automation_id="g",
            expected_depth=1,
        )


def test_loop_batch_batch_mode_item_none_and_flow_vars(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
    )
    LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2, 3],
            "mode": "batch",
            "batch_size": 2,
            "graph_id": "g",
        },
    )
    assert len(fake.ctxs) == 2
    for child in fake.ctxs:
        assert child.variables["item"] is None
        assert isinstance(child.variables["batch"], list)
        _assert_flow_vars(
            child.variables,
            expected_automation_id="g",
            expected_depth=1,
        )


def test_loop_batch_inline_automation_id_none(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    monkeypatch.setattr(
        loop_batch_mod,
        "_resolve_child_graph",
        lambda ctx, payload: (None, SimpleNamespace()),
    )
    ctx = _make_exec_ctx(tmp_path)
    LoopBatchNode()._execute(
        ctx,
        {"items": [1], "graph": {"nodes": []}},
    )
    assert fake.ctxs[0].variables["flow.automation_id"] is None


def test_loop_batch_returns_8_keys(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
    )
    out = LoopBatchNode()._execute(
        ctx, {"items": [1], "graph_id": "g"}
    )
    assert {
        "results",
        "errors",
        "error_count",
        "total",
        "mode",
        "batch_size",
        "graph_id",
        "depth",
    } <= set(out)


def test_loop_batch_does_not_mutate_parent_ctx(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
        variables={"parent": 1},
        subflow_stack=("root",),
    )
    variables_before = dict(ctx.variables)
    stack_before = ctx.subflow_stack
    flow_var_depth_before = ctx.variables.get("flow.depth")
    LoopBatchNode()._execute(ctx, {"items": [1, 2], "graph_id": "g"})
    assert ctx.variables == variables_before
    assert ctx.variables.get("flow.depth") == flow_var_depth_before
    assert ctx.subflow_stack == stack_before


def test_loop_batch_empty_items_no_child(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_exec_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
    )
    out = LoopBatchNode()._execute(
        ctx, {"items": [], "graph_id": "g"}
    )
    assert out["total"] == 0
    assert fake.graphs == []


# ---------------------------------------------------------------------------
# Group 10 — executor inject on_error
# ---------------------------------------------------------------------------

def test_executor_node_results_starts_empty() -> None:
    ex = Executor(_simple_ctx())
    assert ex._node_results == {}


def test_executor_node_results_populated_after_node() -> None:
    rec = _RecorderNode("a")
    graph = _make_resolved(["a"], [], {"a": rec})
    ex = Executor(_simple_ctx())
    ex.execute(graph)
    assert "a" in ex._node_results
    assert isinstance(ex._node_results["a"], NodeResult)


def test_executor_on_error_false_no_incoming_edges() -> None:
    rec = _RecorderNode("a", inject=True)
    graph = _make_resolved(["a"], [], {"a": rec})
    Executor(_simple_ctx()).execute(graph)
    assert rec.calls
    assert rec.calls[0]["on_error"] is False


def test_executor_on_error_false_upstream_succeeded() -> None:
    upstream = _RecorderNode("up")
    target = _RecorderNode("t", inject=True)
    graph = _make_resolved(
        ["up", "t"], [("up", "t")], {"up": upstream, "t": target}
    )
    Executor(_simple_ctx()).execute(graph)
    assert target.calls
    assert target.calls[0]["on_error"] is False


def test_executor_on_error_true_upstream_failed() -> None:
    upstream = _RecorderNode("up", fail=True)
    target = _RecorderNode("t", inject=True)
    graph = _make_resolved(
        ["up", "t"], [("up", "t")], {"up": upstream, "t": target}
    )
    Executor(_simple_ctx()).execute(graph)
    assert target.calls
    assert target.calls[0]["on_error"] is True


def test_executor_on_error_false_upstream_absent() -> None:
    target = _RecorderNode("t", inject=True)
    graph = _make_resolved(
        ["t"],
        [("ghost", "t")],
        {"t": target},
        ordered=["t"],
        states={"t": NodeState.ENABLED},
    )
    Executor(_simple_ctx()).execute(graph)
    assert target.calls
    assert target.calls[0]["on_error"] is False


def test_executor_no_inject_no_on_error_key() -> None:
    upstream = _RecorderNode("up")
    target = _RecorderNode("t", inject=False)
    graph = _make_resolved(
        ["up", "t"], [("up", "t")], {"up": upstream, "t": target}
    )
    Executor(_simple_ctx()).execute(graph)
    assert target.calls
    assert "on_error" not in target.calls[0]


# ---------------------------------------------------------------------------
# Group 11 — port-contract superset
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("on_error", [True, False])
def test_retry_port_contract_superset(on_error: bool) -> None:
    out = FlowRetry()._execute(None, {"on_error": on_error})
    assert {"retry", "pass", "retry_policy", "on_error"} <= set(out)


def test_notify_port_contract_superset() -> None:
    preview = Notify()._preview(None, {"channel": "email"})
    assert {"sent", "channel", "error", "error_count", "dry_run"} <= set(preview)

    execute = Notify()._execute(
        None,
        {"channel": "email", "target": "to@example.com", "config": {}},
    )
    assert {"sent", "channel", "error", "error_count", "dry_run"} <= set(execute)


# ---------------------------------------------------------------------------
# Group 12 — import / lazy
# ---------------------------------------------------------------------------

def test_import_retry_module() -> None:
    importlib.import_module("lokdocsqr.nodes.flow.retry")


def test_import_notify_module() -> None:
    importlib.import_module("lokdocsqr.nodes.utils.notify")
