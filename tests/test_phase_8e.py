"""Verification tests for LokDocSQR Phase 8e (gate 8e).

Covers groups A-L from the 8e verification brief:
  A payload validation, B catalog lookup, C cycle detection,
  D depth guard, E dry-run fail-closed, F child context / R37-R39,
  G port contract, H graph_id vs inline chains (D8e.4),
  I config persistence (D8e.3), J ExecutionContext compat (D8e.2),
  K discovery / smoke, L real child Executor smoke.

No production-code scanning. Executor is mocked for subflow-contract
tests; group L uses a real child Executor with a single non-mutating
flow.filter node.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import lokdocsqr.nodes.flow.subflow as subflow_mod
from lokdocsqr.core.catalog import AutomationCatalog
from lokdocsqr.core.clock import FakeClock
from lokdocsqr.core.config import AppConfig, load_config, save_config
from lokdocsqr.core.errors import (
    GraphParseError,
    GraphValidationError,
    NodeExecutionError,
)
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.plugins import discover_plugins
from lokdocsqr.nodes.flow.subflow import SubflowNode


# ---------------------------------------------------------------------------
# Helpers / doubles
# ---------------------------------------------------------------------------

class FakeCatalog:
    """Minimal AutomationCatalog double recording lookups."""

    def __init__(self, graphs: dict[str, Any] | None = None) -> None:
        self.graphs = dict(graphs or {})
        self.calls: list[str] = []

    def get(self, graph_id: str) -> Any | None:
        self.calls.append(graph_id)
        return self.graphs.get(graph_id)


class FakePolicy:
    """Policy double; decide() raises to prove subflow does not call it."""

    def decide(self, proposal: Any) -> Any:
        raise AssertionError("policy.decide called in subflow")


class _FakeAudit:
    def __init__(self) -> None:
        self.logs: list[Any] = []

    def append(self, log: Any) -> None:
        self.logs.append(log)

    def build_rollback_plan(self, run_id: str) -> list[Any]:
        return []


def _ctx(
    tmp_path: Path,
    *,
    flow_depth: int = 0,
    subflow_stack: tuple[str, ...] = (),
    policy: Any = None,
    automations: Any = None,
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
        run_id="run-1",
        automation_id="auto-1",
        dry_run=False,
        policy=policy,
        clock=FakeClock(),
        events=EventBus(),
        flow_depth=flow_depth,
        subflow_stack=subflow_stack,
        automations=automations,
    )


def _real_ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        scope=object(),
        config=SimpleNamespace(
            base_dir=tmp_path,
            trash_dir=tmp_path / "trash",
            logs_dir=tmp_path / "logs",
            flow=SimpleNamespace(max_subflow_depth=4),
        ),
        audit=_FakeAudit(),
        run_id="run-real",
        automation_id="auto-real",
        dry_run=False,
        policy=None,
        clock=FakeClock(),
        events=EventBus(),
        flow_depth=0,
        subflow_stack=(),
        automations=None,
    )


def _filter_raw_graph() -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": "inline-filter"},
        "nodes": [{"id": "f1", "type": "flow.filter"}],
        "edges": [],
    }


@pytest.fixture
def executor_spy(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Patch subflow's Executor and record child ctx / resolved graph."""
    ctxs: list[Any] = []
    resolved: list[Any] = []

    class _FakeExecutor:
        def __init__(self, ctx: Any) -> None:
            ctxs.append(ctx)

        def execute(self, built: Any) -> dict[str, Any]:
            resolved.append(built)
            return {"ok": True}

    monkeypatch.setattr(subflow_mod, "Executor", _FakeExecutor)
    return SimpleNamespace(ctxs=ctxs, resolved=resolved)


# ---------------------------------------------------------------------------
# A. payload validation
# ---------------------------------------------------------------------------

def test_a1_empty_payload_exactly_one(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(_ctx(tmp_path), {})
    assert "exactly one" in str(exc.value).lower()


def test_a2_both_graph_id_and_graph_exactly_one(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(
            _ctx(tmp_path),
            {"graph_id": "x", "graph": {"nodes": []}},
        )
    assert "exactly one" in str(exc.value).lower()


def test_a3_empty_graph_id_treated_as_absent(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(_ctx(tmp_path), {"graph_id": ""})
    assert "exactly one" in str(exc.value).lower()


def test_a4_empty_graph_dict_treated_as_absent(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(_ctx(tmp_path), {"graph": {}})
    assert "exactly one" in str(exc.value).lower()


def test_a5_graph_id_with_slash_rejected(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(_ctx(tmp_path), {"graph_id": "sub/foo.json"})
    text = str(exc.value).lower()
    assert "filesystem paths" in text or "not supported" in text


def test_a6_graph_id_with_backslash_rejected(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError):
        SubflowNode().run(_ctx(tmp_path), {"graph_id": r"sub\foo"})


# ---------------------------------------------------------------------------
# B. graph_id not found / catalog missing
# ---------------------------------------------------------------------------

def test_b7_catalog_missing_graph_id(tmp_path: Path) -> None:
    cat = FakeCatalog({})
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(
            _ctx(tmp_path, automations=cat),
            {"graph_id": "missing"},
        )
    text = str(exc.value).lower()
    assert "not found" in text or "catalog" in text


def test_b8_catalog_not_configured(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError):
        SubflowNode().run(_ctx(tmp_path, automations=None), {"graph_id": "x"})


def test_b9_catalog_hit_reaches_child(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    fake = object()
    cat = FakeCatalog({"A": fake})
    SubflowNode().run(_ctx(tmp_path, automations=cat), {"graph_id": "A"})
    assert executor_spy.resolved == [fake]


# ---------------------------------------------------------------------------
# C. cycle detection (D8e.1)
# ---------------------------------------------------------------------------

def test_c10_cycle_on_single_stack_entry(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, subflow_stack=("A",), automations=cat)
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(ctx, {"graph_id": "A"})
    assert "cycle" in str(exc.value).lower()


def test_c11_cycle_on_first_stack_entry(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, subflow_stack=("A", "B"), automations=cat)
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(ctx, {"graph_id": "A"})
    assert "cycle" in str(exc.value).lower()


def test_c12_no_cycle_for_different_graph_id(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    fake = object()
    cat = FakeCatalog({"C": fake})
    ctx = _ctx(tmp_path, subflow_stack=("A", "B"), automations=cat)
    SubflowNode().run(ctx, {"graph_id": "C"})
    assert executor_spy.resolved == [fake]


def test_c13_inline_not_tracked_by_cycle_stack(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subflow_mod, "_build_inline", lambda graph: object())
    ctx = _ctx(tmp_path, subflow_stack=("A",))
    SubflowNode().run(ctx, {"graph": {"any": "truthy"}})
    assert len(executor_spy.resolved) == 1


# ---------------------------------------------------------------------------
# D. depth guard (D8e.1 / R26)
# ---------------------------------------------------------------------------

def test_d14_depth_guard_raises_at_limit(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, flow_depth=4, automations=cat)
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(ctx, {"graph_id": "A"})
    assert "depth" in str(exc.value).lower()


def test_d15_depth_below_limit_passes(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, flow_depth=3, automations=cat)
    SubflowNode().run(ctx, {"graph_id": "A"})
    assert len(executor_spy.resolved) == 1


def test_d16_equal_limit_raises_actual_code(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    # Actual code uses ``current_depth >= max_depth``; pin that behaviour.
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, flow_depth=10, automations=cat)
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().run(ctx, {"graph_id": "A", "max_depth": 10})
    assert "depth" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# E. dry-run (D8e.1 / fail-closed)
# ---------------------------------------------------------------------------

def test_e18_dry_run_empty_payload_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode().dry_run(_ctx(tmp_path), {})
    text = str(exc.value).lower()
    assert "phase" not in text
    assert "8" not in str(exc.value)
    assert "7d" not in text


def test_e19_dry_run_always_raises_for_any_payload(tmp_path: Path) -> None:
    for payload in (
        {"graph_id": "A"},
        {"graph": {"spec_version": "1.0"}},
        {},
    ):
        with pytest.raises(NodeExecutionError):
            SubflowNode().dry_run(_ctx(tmp_path), payload)


def test_e20_run_does_not_call_preview(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom_preview(self: Any, ctx: Any, payload: Any) -> Any:
        raise AssertionError("_preview called from run")

    monkeypatch.setattr(SubflowNode, "_preview", _boom_preview)
    monkeypatch.setattr(subflow_mod, "_build_inline", lambda graph: object())
    SubflowNode().run(_ctx(tmp_path), {"graph": {"x": 1}})
    assert len(executor_spy.resolved) == 1


# ---------------------------------------------------------------------------
# F. child context / R37 / R38 / R39
# ---------------------------------------------------------------------------

def test_f21_child_flow_depth_incremented(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, flow_depth=2, automations=cat)
    SubflowNode().run(ctx, {"graph_id": "A"})
    assert executor_spy.ctxs[-1].flow_depth == ctx.flow_depth + 1


def test_f22_graph_id_pushes_stack(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, subflow_stack=("root",), automations=cat)
    SubflowNode().run(ctx, {"graph_id": "A"})
    assert executor_spy.ctxs[-1].subflow_stack == ("root", "A")


def test_f23_inline_does_not_push_stack(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subflow_mod, "_build_inline", lambda graph: object())
    ctx = _ctx(tmp_path, subflow_stack=("root",))
    SubflowNode().run(ctx, {"graph": {"x": 1}})
    assert executor_spy.ctxs[-1].subflow_stack == ("root",)


def test_f24_r39_child_ctx_is_new_and_parent_unchanged(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, automations=cat)
    before = (
        ctx.flow_depth,
        ctx.subflow_stack,
        ctx.run_id,
        ctx.automation_id,
        ctx.dry_run,
        ctx.policy,
        dict(ctx.variables),
        dict(ctx.path_cache),
    )
    SubflowNode().run(ctx, {"graph_id": "A"})
    after = (
        ctx.flow_depth,
        ctx.subflow_stack,
        ctx.run_id,
        ctx.automation_id,
        ctx.dry_run,
        ctx.policy,
        dict(ctx.variables),
        dict(ctx.path_cache),
    )
    child = executor_spy.ctxs[-1]
    assert child is not ctx
    assert child.run_id == ctx.run_id
    assert before == after


def test_f25_r37_policy_is_copied_by_identity(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})

    ctx_none = _ctx(tmp_path, policy=None, automations=cat)
    SubflowNode().run(ctx_none, {"graph_id": "A"})
    assert executor_spy.ctxs[-1].policy is None

    policy = FakePolicy()
    ctx_policy = _ctx(tmp_path, policy=policy, automations=cat)
    SubflowNode().run(ctx_policy, {"graph_id": "A"})
    assert executor_spy.ctxs[-1].policy is policy


def test_f26_r38_subflow_does_not_call_policy(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    ctx = _ctx(tmp_path, policy=FakePolicy(), automations=cat)
    SubflowNode().run(ctx, {"graph_id": "A"})
    assert len(executor_spy.resolved) == 1


# ---------------------------------------------------------------------------
# G. port contract
# ---------------------------------------------------------------------------

def test_g27_graph_id_port_contract(
    tmp_path: Path, executor_spy: SimpleNamespace
) -> None:
    cat = FakeCatalog({"A": object()})
    res = SubflowNode().run(_ctx(tmp_path, automations=cat), {"graph_id": "A"})
    assert set(res) >= {"result", "graph_id", "depth", "errors", "error_count"}
    assert res["graph_id"] == "A"
    assert res["depth"] == 1
    assert res["errors"] == []
    assert res["error_count"] == 0


def test_g28_inline_port_contract(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subflow_mod, "_build_inline", lambda graph: object())
    res = SubflowNode().run(_ctx(tmp_path), {"graph": {"x": 1}})
    assert set(res) >= {"result", "graph_id", "depth", "errors", "error_count"}
    assert res["graph_id"] is None
    assert res["depth"] == 1


# ---------------------------------------------------------------------------
# H. graph_id vs inline — different chains (D8e.4)
# ---------------------------------------------------------------------------

def test_h29_graph_id_skips_parse_validate_resolve_build(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _forbidden(name: str):
        def _inner(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            raise AssertionError(f"{name} called for graph_id branch")

        return _inner

    for name in (
        "parse_graph",
        "validate_graph",
        "resolve_states",
        "build_graph",
    ):
        monkeypatch.setattr(subflow_mod, name, _forbidden(name))

    fake = object()
    cat = FakeCatalog({"A": fake})
    SubflowNode().run(_ctx(tmp_path, automations=cat), {"graph_id": "A"})
    assert calls == []
    assert executor_spy.resolved == [fake]


def test_h30_inline_chain_order(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    document = object()
    built = object()

    def fake_parse(graph: Any) -> Any:
        order.append("parse_graph")
        return document

    def fake_validate(doc: Any) -> list[str]:
        order.append("validate_graph")
        assert doc is document
        return []

    def fake_resolve(doc: Any) -> dict[str, Any]:
        order.append("resolve_states")
        assert doc is document
        return {}

    def fake_build(doc: Any, states: Any, reg: Any) -> Any:
        order.append("build_graph")
        assert doc is document
        assert states == {}
        assert reg is registry
        return built

    monkeypatch.setattr(subflow_mod, "parse_graph", fake_parse)
    monkeypatch.setattr(subflow_mod, "validate_graph", fake_validate)
    monkeypatch.setattr(subflow_mod, "resolve_states", fake_resolve)
    monkeypatch.setattr(subflow_mod, "build_graph", fake_build)

    SubflowNode().run(_ctx(tmp_path), {"graph": {"x": 1}})
    assert order == [
        "parse_graph",
        "validate_graph",
        "resolve_states",
        "build_graph",
    ]
    assert executor_spy.resolved == [built]


def test_h31_validate_graph_errors_raise_graphvalidationerror(
    tmp_path: Path,
    executor_spy: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, list[str]] = {"errors": ["bad port"]}
    document = object()
    built = object()

    monkeypatch.setattr(subflow_mod, "parse_graph", lambda graph: document)
    monkeypatch.setattr(
        subflow_mod, "validate_graph", lambda doc: state["errors"]
    )
    monkeypatch.setattr(subflow_mod, "resolve_states", lambda doc: {})
    monkeypatch.setattr(
        subflow_mod, "build_graph", lambda doc, states, reg: built
    )

    with pytest.raises(GraphValidationError):
        SubflowNode().run(_ctx(tmp_path), {"graph": {"x": 1}})

    state["errors"] = []
    res = SubflowNode().run(_ctx(tmp_path), {"graph": {"x": 1}})
    assert res["result"] == {"ok": True}
    assert executor_spy.resolved == [built]


def test_h32_real_parse_graph_error_propagates_unwrapped(tmp_path: Path) -> None:
    with pytest.raises(GraphParseError):
        SubflowNode().run(_ctx(tmp_path), {"graph": {"nodes": "broken"}})


# ---------------------------------------------------------------------------
# I. config persistence (D8e.3)
# ---------------------------------------------------------------------------

def test_i33_flow_max_subflow_depth_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cfg = AppConfig(base_dir=tmp_path)
    cfg.flow.max_subflow_depth = 7
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.flow.max_subflow_depth == 7


def test_i34_default_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    save_config(AppConfig(base_dir=tmp_path), path)
    loaded = load_config(path)
    assert loaded.flow.max_subflow_depth == 4


def test_i35_missing_flow_key_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"base_dir": str(tmp_path)}), encoding="utf-8")
    loaded = load_config(path)
    assert loaded.flow.max_subflow_depth == 4


def test_i36_broken_flow_type_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "base_dir": str(tmp_path),
                "flow": {"max_subflow_depth": "abc"},
            }
        ),
        encoding="utf-8",
    )
    loaded = load_config(path)
    assert loaded.flow.max_subflow_depth == 4


def test_i37_save_preserves_other_keys(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    cfg = AppConfig(
        base_dir=tmp_path,
        trash_dir=tmp_path / "trash",
        logs_dir=tmp_path / "logs",
        scope_roots=[tmp_path / "a", tmp_path / "b"],
        default_dry_run=False,
    )
    cfg.flow.max_subflow_depth = 9
    save_config(cfg, path)

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "flow" in raw
    assert raw["flow"]["max_subflow_depth"] == 9

    loaded = load_config(path)
    assert loaded.base_dir == tmp_path
    assert loaded.trash_dir == tmp_path / "trash"
    assert loaded.logs_dir == tmp_path / "logs"
    assert loaded.scope_roots == [tmp_path / "a", tmp_path / "b"]
    assert loaded.default_dry_run is False


# ---------------------------------------------------------------------------
# J. ExecutionContext backward compat (D8e.2)
# ---------------------------------------------------------------------------

def test_j38_default_subflow_stack_is_empty_tuple(tmp_path: Path) -> None:
    ctx = ExecutionContext(
        scope=object(),
        config=SimpleNamespace(
            base_dir=tmp_path,
            flow=SimpleNamespace(max_subflow_depth=4),
        ),
        audit=object(),
        run_id="r",
        automation_id="a",
    )
    assert ctx.subflow_stack == ()
    assert isinstance(ctx.subflow_stack, tuple)


def test_j39_replace_subflow_stack(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    new = replace(ctx, subflow_stack=("A",))
    assert new.subflow_stack == ("A",)
    assert new is not ctx
    assert new.run_id == ctx.run_id


# ---------------------------------------------------------------------------
# K. discovery / smoke
# ---------------------------------------------------------------------------

def test_k40_subflow_discovered_and_not_retry_safe() -> None:
    ids = discover_plugins()
    assert "flow.subflow" in ids
    cls = registry.get("flow.subflow")
    assert cls is not None
    assert cls.retry_safe is False


def test_k41_phase_8a_8d_nodes_registered() -> None:
    ids = set(discover_plugins())
    expected = {
        "flow.filter",
        "flow.if_branch",
        "flow.merge",
        "flow.wait",
        "utils.set_variables",
        "utils.json_validate",
        "utils.path_template",
    }
    missing = expected - ids
    assert not missing, f"missing registered nodes: {sorted(missing)}"


# ---------------------------------------------------------------------------
# L. real child Executor smoke (inline flow.filter)
# ---------------------------------------------------------------------------

def test_l42_real_child_executor_inline_filter(tmp_path: Path) -> None:
    discover_plugins()
    ctx = _real_ctx(tmp_path)
    res = SubflowNode().run(ctx, {"graph": _filter_raw_graph()})
    run_result = res["result"]
    assert run_result.success
    outputs = run_result.node_results["f1"].outputs
    assert {"items", "total", "kept"} <= set(outputs)


def test_l43_child_flow_depth_is_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discover_plugins()
    from lokdocsqr.nodes.flow.filter import FilterNode

    seen: dict[str, int] = {}
    original = FilterNode.run

    def spy_run(self: Any, ctx: Any, payload: Any) -> Any:
        seen["depth"] = ctx.flow_depth
        return original(self, ctx, payload)

    monkeypatch.setattr(FilterNode, "run", spy_run)
    ctx = _real_ctx(tmp_path)
    SubflowNode().run(ctx, {"graph": _filter_raw_graph()})
    assert seen["depth"] == 1


# ---------------------------------------------------------------------------
# D17 — known BUG pin, placed last so --maxfail=1 still exercises A-L.
# ---------------------------------------------------------------------------

def test_d17_negative_max_depth_raises(tmp_path: Path) -> None:
    """R26: invalid max_depth must fail closed, not silently fall back."""
    cat = FakeCatalog({"A": object()})
    with pytest.raises(NodeExecutionError):
        SubflowNode().run(
            _ctx(tmp_path, automations=cat),
            {"graph_id": "A", "max_depth": -1},
        )
