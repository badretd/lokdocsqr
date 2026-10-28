"""Per-node retry_policy + gate + D3 (SPEC §5, R38, D3)."""
import pytest
from types import SimpleNamespace

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.executor.engine import Executor, _read_node_retry_policy
from lokdocsqr.core.models import NodeState


# --- simple fake nodes ---

class _FailingNode:
    retry_safe = True
    def __init__(self):
        self.run_calls = 0
        self.dry_calls = 0
    def run(self, ctx, payload):
        self.run_calls += 1
        raise NodeExecutionError("boom")
    def dry_run(self, ctx, payload):
        self.dry_calls += 1
        raise NodeExecutionError("boom")


class _SafeFailNode:
    """retry_safe=False, always fails."""
    retry_safe = False
    def __init__(self):
        self.run_calls = 0
        self.dry_calls = 0
    def run(self, ctx, payload):
        self.run_calls += 1
        raise NodeExecutionError("boom")
    def dry_run(self, ctx, payload):
        self.dry_calls += 1
        return {}


# --- graph builder (duck-typed SimpleNamespace; engine uses attribute access) ---

def _make_graph(node_id, node, params=None, *,
                strategy="fail", max_retries=0,
                retry_delay=0.0, backoff="none"):
    handling = SimpleNamespace(
        strategy=strategy, max_retries=max_retries,
        retry_delay_seconds=retry_delay, backoff=backoff,
    )
    settings = SimpleNamespace(error_handling=handling)
    node_spec = SimpleNamespace(
        id=node_id, params=dict(params or {}),
        inputs=[], outputs=[],
    )
    document = SimpleNamespace(nodes=[node_spec], edges=[], settings=settings)
    return SimpleNamespace(
        document=document,
        ordered_node_ids=[node_id],
        handlers={node_id: node},
        states={node_id: NodeState.ENABLED},
    )


# ---------------------------------------------------------- unit tests ---

def test_retry_policy_string_retry_on_becomes_tuple():
    spec = SimpleNamespace(params={"retry_policy": {"attempts": 3, "backoff": 0, "retry_on": "NodeExecutionError"}})
    result = _read_node_retry_policy(spec, "n1")
    assert result == {"attempts": 3, "backoff": 0.0, "retry_on": ("NodeExecutionError",)}


def test_retry_policy_list_retry_on_becomes_tuple():
    spec = SimpleNamespace(params={"retry_policy": {"attempts": 3, "retry_on": ["A", "B"]}})
    result = _read_node_retry_policy(spec, "n1")
    assert result["retry_on"] == ("A", "B")


def test_retry_policy_malformed_raises():
    spec = SimpleNamespace(params={"retry_policy": "not-a-dict"})
    with pytest.raises(NodeExecutionError):
        _read_node_retry_policy(spec, "n1")


def test_retry_policy_attempts_zero_rejected():
    spec = SimpleNamespace(params={"retry_policy": {"attempts": 0}})
    with pytest.raises(NodeExecutionError):
        _read_node_retry_policy(spec, "n1")


def test_retry_policy_negative_backoff_rejected():
    spec = SimpleNamespace(params={"retry_policy": {"attempts": 1, "backoff": -1.0}})
    with pytest.raises(NodeExecutionError):
        _read_node_retry_policy(spec, "n1")


def test_retry_policy_absent_returns_none():
    spec = SimpleNamespace(params={})
    assert _read_node_retry_policy(spec, "n1") is None


# ------------------------------------------------------ executor tests ---

def test_retry_safe_false_blocks_retry_and_run_not_called(exec_ctx):
    node = _SafeFailNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 3,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 0}})
    result = Executor(exec_ctx).execute(graph)
    assert node.run_calls == 0, "node.run must not be called when retry_safe=False"
    assert result.node_results["n1"].state == NodeState.FAILED


def test_attempts_3_exact_count(exec_ctx):
    node = _FailingNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 3,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 0}})
    Executor(exec_ctx).execute(graph)
    assert node.run_calls == 3, f"expected 3 attempts, got {node.run_calls}"


def test_backoff_via_ctx_clock_not_executor_sleep(exec_ctx):
    from lokdocsqr.core.clock import FakeClock
    exec_ctx.clock = FakeClock()
    node = _FailingNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 3,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 1.0}})
    sleep_spy = []
    Executor(exec_ctx, sleep=sleep_spy.append).execute(graph)
    # backoff = 1.0 * 2^0 (attempt1) + 1.0 * 2^1 (attempt2) = 3.0
    assert exec_ctx.clock.now() == 3.0, \
        f"ctx.clock advanced to {exec_ctx.clock.now()}, expected 3.0"
    assert sleep_spy == [], \
        f"executor._sleep called for per-node retry: {sleep_spy}"


def test_gate_runs_once_per_node_not_per_attempt(exec_ctx, monkeypatch):
    """R38: gate is outside retry loop; verdict reused across attempts."""
    from lokdocsqr.core.executor import engine as eng

    gate_calls = []
    def _wrapped(node, ctx, payload, *, node_id=None):
        gate_calls.append(node_id)
        return payload
    monkeypatch.setattr(eng, "_check_policy_and_verify", _wrapped)

    exec_ctx.policy = object()  # non-None, but monkeypatched gate ignores it
    exec_ctx.dry_run = False
    node = _FailingNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 3,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 0}})
    Executor(exec_ctx).execute(graph)
    assert len(gate_calls) == 1, \
        f"gate ran {len(gate_calls)} times, expected 1 (R38)"
    assert node.run_calls == 3


def test_dry_run_skips_retry_d3(exec_ctx):
    """D3: dry_run does not enter retry loop even with retry_policy."""
    exec_ctx.dry_run = True
    node = _FailingNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 3,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 0}})
    Executor(exec_ctx).execute(graph)
    assert node.run_calls == 0
    assert node.dry_calls == 1, \
        f"dry_run must call node once (no retry); got {node.dry_calls}"


def test_graph_level_retry_uses_executor_sleep_not_ctx_clock(exec_ctx):
    """Regression 0-7 + D3: without retry_policy, old graph-level path runs."""
    from lokdocsqr.core.clock import FakeClock
    exec_ctx.clock = FakeClock()
    node = _FailingNode()
    graph = _make_graph("n1", node, params={},  # NO retry_policy
                        strategy="retry", max_retries=2,
                        retry_delay=1.0, backoff="none")
    sleep_spy = []
    Executor(exec_ctx, sleep=sleep_spy.append).execute(graph)
    assert len(sleep_spy) == 2, \
        f"graph-level retry used self._sleep {len(sleep_spy)} times, expected 2"
    assert exec_ctx.clock.now() == 0.0, \
        "graph-level retry must not advance ctx.clock (D3 boundary)"
    assert node.run_calls == 3


def test_per_node_retry_does_not_call_time_sleep(exec_ctx, monkeypatch):
    """Per-node retry must go through ctx.clock, never time.sleep."""
    import time as _t
    real_sleep_calls = []
    monkeypatch.setattr(_t, "sleep", lambda s: real_sleep_calls.append(s))

    from lokdocsqr.core.clock import FakeClock
    exec_ctx.clock = FakeClock()
    node = _FailingNode()
    graph = _make_graph("n1", node,
                        params={"retry_policy": {"attempts": 2,
                                                 "retry_on": ["NodeExecutionError"],
                                                 "backoff": 5.0}})
    Executor(exec_ctx).execute(graph)
    assert real_sleep_calls == [], \
        f"per-node retry path called time.sleep: {real_sleep_calls}"
    assert exec_ctx.clock.now() >= 5.0
