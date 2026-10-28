"""Phase 7c verification tests — policy + verify gate in Executor.

All tests exercise the gate end-to-end via ``Executor.execute``;
``_check_policy_and_verify`` is never called directly.
"""
from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import (
    PolicyBlockedError,
    PolicyError,
    PolicyTimeoutError,
    ProposalValidationError,
    ScopeViolation,
)
from lokdocsqr.core.events import (
    EVENT_NODE_FINISHED,
    EVENT_NODE_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
)
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PolicyAction,
    PortSpec,
    ResolvedGraph,
    VerifyAction,
    VerifyResult,
    WorkflowDocument,
    WorkflowMetadata,
    WorkflowSettings,
)
from lokdocsqr.core.policy import PolicyConfig, PolicyEngine

try:
    from lokdocsqr.core.node_api.base import Node as _NodeBase
except Exception:  # pragma: no cover - defensive
    _NodeBase = object


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _SpyNode(_NodeBase):
    """Minimal node that counts ``run`` invocations and records its payload."""

    title = "Spy Test Node"
    inputs: list = []
    outputs: list = []

    def __init__(self, node_id: str = "spy.test_node") -> None:
        self._spy_id = node_id
        self.run_calls = 0
        self.dry_run_calls = 0
        self.last_payload: dict | None = None

    @property
    def id(self) -> str:
        return self._spy_id

    def run(self, ctx, payload):
        self.run_calls += 1
        self.last_payload = payload
        return {"ok": True}

    def dry_run(self, ctx, payload):
        self.dry_run_calls += 1
        self.last_payload = payload
        return {"ok": True}

    def describe(self):
        return {"id": self._spy_id, "title": self.title}


class _FakeScope:
    def __init__(self, roots) -> None:
        self.roots = [Path(r).resolve() for r in roots]

    def check(self, path, write: bool = False):
        p = Path(path).resolve()
        for r in self.roots:
            if p == r or r in p.parents:
                return p
        raise ScopeViolation(f"outside scope: {p}")


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list = []

    def append(self, log) -> None:
        self.entries.append(log)

    def build_rollback_plan(self, run_id):
        return []


class _FakeEvents:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event_type, payload) -> None:
        self.events.append((event_type, payload))

    def subscribe(self, handler) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(action, node_id: str = "spy.test_node") -> PolicyEngine:
    return PolicyEngine(
        PolicyConfig(node_overrides={node_id: action})
    )


def _make_ctx(
    tmp_path,
    *,
    policy: PolicyEngine | None = None,
    verify_callback=None,
    dry_run: bool = False,
) -> ExecutionContext:
    return ExecutionContext(
        scope=_FakeScope([tmp_path]),
        config=SimpleNamespace(base_dir=tmp_path),
        audit=_FakeAudit(),
        run_id="run-7c",
        automation_id="auto-7c",
        dry_run=dry_run,
        events=_FakeEvents(),
        policy=policy,
        verify_callback=verify_callback,
    )


def _make_graph(
    spy_node,
    *,
    node_id: str = "spy.test_node",
    inputs: list | None = None,
) -> ResolvedGraph:
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="phase7c.test"),
        nodes=[
            NodeSpec(
                id=node_id,
                type=node_id,
                title="Spy",
                inputs=list(inputs or []),
            )
        ],
        edges=[],
        settings=WorkflowSettings(default_dry_run=False),
    )
    return ResolvedGraph(
        document=doc,
        ordered_node_ids=[node_id],
        handlers={node_id: spy_node},
        states={node_id: NodeState.ENABLED},
    )


def _make_two_node_graph():
    parent = _SpyNode("spy.parent")
    child = _SpyNode("spy.child")
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="phase7c.two"),
        nodes=[
            NodeSpec(id="spy.parent", type="spy.parent"),
            NodeSpec(id="spy.child", type="spy.child"),
        ],
        edges=[
            EdgeSpec(id="e1", source="spy.parent", target="spy.child"),
        ],
        settings=WorkflowSettings(default_dry_run=False),
    )
    graph = ResolvedGraph(
        document=doc,
        ordered_node_ids=["spy.parent", "spy.child"],
        handlers={"spy.parent": parent, "spy.child": child},
        states={
            "spy.parent": NodeState.ENABLED,
            "spy.child": NodeState.ENABLED,
        },
    )
    return parent, child, graph


def _run(graph, ctx):
    return Executor(
        ctx, sleep=lambda _s: None, clock=lambda: 0.0
    ).execute(graph)


def _event_types(ctx) -> list[str]:
    return [t for t, _ in ctx.events.events]


# ---------------------------------------------------------------------------
# R37 — backward compatibility (ctx.policy=None)
# ---------------------------------------------------------------------------

def test_r37_policy_none_single_node(tmp_path):
    """R37: no policy → node runs once, RunResult returned, gate skipped."""
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=None)
    graph = _make_graph(spy)

    result = _run(graph, ctx)

    assert spy.run_calls == 1
    assert result.success is True
    assert "spy.test_node" in result.node_results
    assert result.node_results["spy.test_node"].state == NodeState.ENABLED


def test_r37_policy_none_two_node_graph(tmp_path):
    """R37 regression: multi-node graph with policy=None behaves as before."""
    parent, child, graph = _make_two_node_graph()
    ctx = _make_ctx(tmp_path, policy=None)

    result = _run(graph, ctx)

    assert parent.run_calls == 1
    assert child.run_calls == 1
    assert result.success is True
    assert set(result.node_results) == {"spy.parent", "spy.child"}


# ---------------------------------------------------------------------------
# ALLOW
# ---------------------------------------------------------------------------

def test_allow_runs_once_with_original_payload(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.ALLOW)
    ctx = _make_ctx(tmp_path, policy=policy)
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert spy.run_calls == 1
    assert spy.last_payload == {}


def test_allow_does_not_call_verify_callback(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.ALLOW)
    callback_calls = []

    def cb(proposal):
        callback_calls.append(proposal)
        return VerifyResult(action=VerifyAction.APPROVE)

    ctx = _make_ctx(tmp_path, policy=policy, verify_callback=cb)
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert callback_calls == []
    assert spy.run_calls == 1


def test_allow_classify_node_receives_node_id(monkeypatch, tmp_path):
    """classify_node is called with the real node_id, not a fallback."""
    from lokdocsqr.core.executor import engine as engine_mod
    original = engine_mod.classify_node
    seen: list[str] = []

    def spy(nid):
        seen.append(nid)
        return original(nid)

    monkeypatch.setattr(engine_mod, "classify_node", spy)

    spy_node = _SpyNode()
    policy = _make_policy(PolicyAction.ALLOW)
    ctx = _make_ctx(tmp_path, policy=policy)
    graph = _make_graph(spy_node)

    _run(graph, ctx)

    assert seen == ["spy.test_node"]


# ---------------------------------------------------------------------------
# BLOCK
# ---------------------------------------------------------------------------

def test_block_raises_and_skips_run(tmp_path):
    """BLOCK → PolicyBlockedError, node.run NOT called, verify not called."""
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.BLOCK)
    callback_calls = []

    def cb(proposal):
        callback_calls.append(proposal)
        return VerifyResult(action=VerifyAction.APPROVE)

    ctx = _make_ctx(tmp_path, policy=policy, verify_callback=cb)
    graph = _make_graph(spy)

    with pytest.raises(PolicyBlockedError):
        _run(graph, ctx)

    assert spy.run_calls == 0
    assert callback_calls == []


# ---------------------------------------------------------------------------
# R26 — fail-closed
# ---------------------------------------------------------------------------

def test_r26_require_verify_without_callback_blocks(tmp_path):
    """REQUIRE_VERIFY with no verify_callback → PolicyBlockedError."""
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(tmp_path, policy=policy, verify_callback=None)
    graph = _make_graph(spy)

    with pytest.raises(PolicyBlockedError) as exc_info:
        _run(graph, ctx)

    assert "verify required" in str(exc_info.value)
    assert spy.run_calls == 0


def test_r26_unknown_verify_action_raises_policy_error(tmp_path):
    """A verify_callback returning an unrecognized action is fail-closed."""
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)

    bad = VerifyResult(action="bogus")
    ctx = _make_ctx(tmp_path, policy=policy, verify_callback=lambda _p: bad)
    graph = _make_graph(spy)

    with pytest.raises(PolicyError) as exc_info:
        _run(graph, ctx)

    assert type(exc_info.value) is PolicyError
    assert "unknown verify action" in str(exc_info.value)
    assert spy.run_calls == 0


# ---------------------------------------------------------------------------
# APPROVE
# ---------------------------------------------------------------------------

def test_approve_runs_with_original_payload(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(action=VerifyAction.APPROVE),
    )
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert spy.run_calls == 1
    assert spy.last_payload == {}


# ---------------------------------------------------------------------------
# REJECT
# ---------------------------------------------------------------------------

def test_reject_raises_policy_blocked_error(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.REJECT, reason="user said no"
        ),
    )
    graph = _make_graph(spy)

    with pytest.raises(PolicyBlockedError) as exc_info:
        _run(graph, ctx)
    assert "rejected" in str(exc_info.value)
    assert spy.run_calls == 0


# ---------------------------------------------------------------------------
# TIMEOUT
# ---------------------------------------------------------------------------

def test_timeout_raises_policy_timeout_error(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(action=VerifyAction.TIMEOUT),
    )
    graph = _make_graph(spy)

    with pytest.raises(PolicyTimeoutError):
        _run(graph, ctx)
    assert spy.run_calls == 0


# ---------------------------------------------------------------------------
# R35 — EDIT
# ---------------------------------------------------------------------------

def test_r35_edit_with_valid_payload_runs_edited(tmp_path):
    spy = _SpyNode()
    new_payload = {"path": str(tmp_path / "x")}
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT, new_payload=new_payload
        ),
    )
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert spy.run_calls == 1
    assert spy.last_payload == new_payload


def test_r35_edit_validates_original_and_edited(monkeypatch, tmp_path):
    """validate_proposal runs on original AND on edited payload (R35)."""
    from lokdocsqr.core.executor import engine as engine_mod
    original_vp = engine_mod.validate_proposal
    calls: list = []

    def vp_spy(proposal, ctx):
        calls.append(proposal)
        return original_vp(proposal, ctx)

    monkeypatch.setattr(engine_mod, "validate_proposal", vp_spy)

    spy = _SpyNode()
    new_payload = {"path": str(tmp_path / "x")}
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT, new_payload=new_payload
        ),
    )
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert len(calls) == 2
    assert spy.run_calls == 1
    assert spy.last_payload == new_payload


def test_r35_edit_without_new_payload_raises(tmp_path):
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT, new_payload=None
        ),
    )
    graph = _make_graph(spy)

    with pytest.raises(PolicyError) as exc_info:
        _run(graph, ctx)
    assert "EDIT without new_payload" in str(exc_info.value)
    assert type(exc_info.value) is PolicyError
    assert spy.run_calls == 0


def test_r35_edit_empty_target_paths_for_mutation(monkeypatch, tmp_path):
    """R27 via validate_proposal(edited): mutating + empty paths → error."""
    from lokdocsqr.core.executor import engine as engine_mod
    monkeypatch.setattr(engine_mod, "classify_node", lambda _nid: "write")

    spy = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT, new_payload={}
        ),
    )
    graph = _make_graph(
        spy,
        inputs=[PortSpec(name="path", default=str(tmp_path / "x"))],
    )

    with pytest.raises(ProposalValidationError):
        _run(graph, ctx)
    assert spy.run_calls == 0


# ---------------------------------------------------------------------------
# R38 — exactly once
# ---------------------------------------------------------------------------

def test_r38_exactly_once_allow(tmp_path):
    """ALLOW: spy.run is called exactly once, not twice."""
    spy = _SpyNode()
    policy = _make_policy(PolicyAction.ALLOW)
    ctx = _make_ctx(tmp_path, policy=policy)
    graph = _make_graph(spy)

    _run(graph, ctx)

    assert spy.run_calls == 1


# ---------------------------------------------------------------------------
# R39 — no ctx mutation
# ---------------------------------------------------------------------------

def _snapshot_ctx(ctx):
    return {
        "variables": copy.deepcopy(ctx.variables),
        "path_cache": copy.deepcopy(ctx.path_cache),
        "policy": ctx.policy,
        "verify_callback": ctx.verify_callback,
    }


def _assert_ctx_unchanged(ctx, snapshot):
    assert ctx.variables == snapshot["variables"]
    assert ctx.path_cache == snapshot["path_cache"]
    assert ctx.policy is snapshot["policy"]
    assert ctx.verify_callback is snapshot["verify_callback"]


def test_r39_no_ctx_mutation_allow(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=_make_policy(PolicyAction.ALLOW))
    snap = _snapshot_ctx(ctx)

    _run(_make_graph(spy), ctx)

    _assert_ctx_unchanged(ctx, snap)


def test_r39_no_ctx_mutation_approve(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(
        tmp_path,
        policy=_make_policy(PolicyAction.REQUIRE_VERIFY),
        verify_callback=lambda _p: VerifyResult(action=VerifyAction.APPROVE),
    )
    snap = _snapshot_ctx(ctx)

    _run(_make_graph(spy), ctx)

    _assert_ctx_unchanged(ctx, snap)


def test_r39_no_ctx_mutation_edit(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(
        tmp_path,
        policy=_make_policy(PolicyAction.REQUIRE_VERIFY),
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT,
            new_payload={"path": str(tmp_path / "x")},
        ),
    )
    snap = _snapshot_ctx(ctx)

    _run(_make_graph(spy), ctx)

    _assert_ctx_unchanged(ctx, snap)


def test_r39_no_ctx_mutation_block(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=_make_policy(PolicyAction.BLOCK))
    snap = _snapshot_ctx(ctx)

    with pytest.raises(PolicyBlockedError):
        _run(_make_graph(spy), ctx)

    _assert_ctx_unchanged(ctx, snap)


# ---------------------------------------------------------------------------
# D3 — no RunResult / no EVENT_RUN_FINISHED on block
# ---------------------------------------------------------------------------

def test_d3_block_emits_run_started_but_not_finished(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=_make_policy(PolicyAction.BLOCK))
    graph = _make_graph(spy)

    with pytest.raises(PolicyBlockedError):
        _run(graph, ctx)

    types = _event_types(ctx)
    assert EVENT_RUN_STARTED in types
    assert EVENT_NODE_STARTED not in types
    assert EVENT_NODE_FINISHED not in types
    assert EVENT_RUN_FINISHED not in types


def test_d3_allow_emits_started_and_finished(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=_make_policy(PolicyAction.ALLOW))

    result = _run(_make_graph(spy), ctx)

    types = _event_types(ctx)
    assert result.success is True
    assert EVENT_RUN_STARTED in types
    assert EVENT_NODE_STARTED in types
    assert EVENT_NODE_FINISHED in types
    assert EVENT_RUN_FINISHED in types


# ---------------------------------------------------------------------------
# D4 — node_id in exception message
# ---------------------------------------------------------------------------

def test_d4_blocked_message_contains_node_id(tmp_path):
    spy = _SpyNode()
    ctx = _make_ctx(tmp_path, policy=_make_policy(PolicyAction.BLOCK))

    with pytest.raises(PolicyBlockedError) as exc_info:
        _run(_make_graph(spy), ctx)

    assert "spy.test_node" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Defense-in-depth — validate_proposal(original) before policy.decide
# ---------------------------------------------------------------------------

def test_defense_in_depth_validate_before_decide(monkeypatch, tmp_path):
    """ProposalValidationError fires BEFORE policy.decide is reached."""
    from lokdocsqr.core.executor import engine as engine_mod
    monkeypatch.setattr(engine_mod, "classify_node", lambda _nid: "write")

    spy = _SpyNode()
    policy = _make_policy(PolicyAction.ALLOW)
    decide_calls: list = []

    original_decide = policy.decide

    def decide_spy(proposal):
        decide_calls.append(proposal)
        return original_decide(proposal)

    monkeypatch.setattr(policy, "decide", decide_spy)

    ctx = _make_ctx(tmp_path, policy=policy)
    # Empty payload → empty target_paths → validate_proposal(original) raises.
    graph = _make_graph(spy)

    with pytest.raises(ProposalValidationError):
        _run(graph, ctx)

    assert decide_calls == []
    assert spy.run_calls == 0


# ---------------------------------------------------------------------------
# EDIT — classify_node called twice (original + edited)
# ---------------------------------------------------------------------------

def test_edit_classify_node_called_twice(monkeypatch, tmp_path):
    from lokdocsqr.core.executor import engine as engine_mod
    original = engine_mod.classify_node
    seen: list[str] = []

    def spy(nid):
        seen.append(nid)
        return original(nid)

    monkeypatch.setattr(engine_mod, "classify_node", spy)

    spy_node = _SpyNode()
    policy = _make_policy(PolicyAction.REQUIRE_VERIFY)
    ctx = _make_ctx(
        tmp_path,
        policy=policy,
        verify_callback=lambda _p: VerifyResult(
            action=VerifyAction.EDIT,
            new_payload={"path": str(tmp_path / "x")},
        ),
    )
    graph = _make_graph(spy_node)

    _run(graph, ctx)

    # Two calls: original proposal + edited proposal.
    assert seen == ["spy.test_node", "spy.test_node"]
