# PHASE_8F_TESTS_V1
"""Verification tests for LokDocSQR Phase 8f (gate 8f).

Covers groups 1-12 from the 8f verification brief:
  structure, items validation, mode/batch_size validation, empty items,
  loop mode, batch mode, per-item fail-soft, depth/cycle guards,
  _resolve_child_graph misc, _preview, subflow regression, port contract.

No production-code scanning, no git calls, no test-from-test calls.
Expected red: batch chunk ordinal index bug in loop_batch.py:152.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import lokdocsqr.nodes.flow._common as common_mod
import lokdocsqr.nodes.flow.loop_batch as loop_batch_mod
import lokdocsqr.nodes.flow.subflow as subflow_mod
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.models import RunResult
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.nodes.flow._common import _resolve_child_graph
from lokdocsqr.nodes.flow.loop_batch import LoopBatchNode
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


class BoomError(LokError):
    """LokError subclass used to exercise per-item fail-soft."""


def _make_ctx(
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
        run_id="run-8f",
        automation_id="auto-8f",
        dry_run=False,
        policy=None,
        variables=dict(variables or {}),
        flow_depth=flow_depth,
        subflow_stack=subflow_stack,
        automations=automations,
    )


def _run_result(tag: str = "child") -> RunResult:
    return RunResult(
        run_id=tag,
        automation_id="auto-child",
        dry_run=False,
        success=True,
    )


def _install_fake_executor(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    behavior: Any = None,
) -> SimpleNamespace:
    """Patch module.Executor and record child ctxs / graphs.

    ``behavior(child_ctx, graph, call_no)`` may return a RunResult or raise.
    """
    ctxs: list[Any] = []
    graphs: list[Any] = []

    class _FakeExecutor:
        def __init__(self, ctx: Any) -> None:
            ctxs.append(ctx)

        def execute(self, graph: Any) -> Any:
            graphs.append(graph)
            call_no = len(graphs) - 1
            if behavior is not None:
                return behavior(ctxs[-1], graph, call_no)
            return _run_result(f"child-{call_no}")

    monkeypatch.setattr(module, "Executor", _FakeExecutor)
    return SimpleNamespace(ctxs=ctxs, graphs=graphs)


def _items_generator():
    yield 1


# ---------------------------------------------------------------------------
# Group 1 — structure
# ---------------------------------------------------------------------------

def test_loop_batch_inputs_outputs_retry_safe() -> None:
    node = LoopBatchNode()
    assert node.inputs == {
        "items": "any[]",
        "mode": "string",
        "batch_size": "integer",
    }
    assert node.outputs == {
        "results": "any[]",
        "errors": "any[]",
        "error_count": "integer",
        "total": "integer",
        "mode": "string",
        "batch_size": "integer",
        "graph_id": "string",
        "depth": "integer",
    }
    assert node.retry_safe is False


def test_loop_batch_registered_and_describe() -> None:
    cls = registry.get("flow.loop_batch")
    assert cls is LoopBatchNode
    desc = LoopBatchNode().describe()
    assert set(desc) == {"id", "title", "inputs", "outputs"}
    assert desc["id"] == "flow.loop_batch"


# ---------------------------------------------------------------------------
# Group 2 — items validation
# ---------------------------------------------------------------------------

def test_items_none_noop(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(ctx, {"items": None, "graph_id": "g"})
    assert out["results"] == []
    assert out["errors"] == []
    assert out["error_count"] == 0
    assert out["total"] == 0
    assert len(fake.graphs) == 0


def test_items_empty_noop_resolved_mode_batch_size(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {"items": [], "mode": "batch", "batch_size": 10, "graph_id": "g"},
    )
    assert out["results"] == []
    assert out["errors"] == []
    assert out["error_count"] == 0
    assert out["total"] == 0
    assert out["mode"] == "batch"
    assert out["batch_size"] == 10
    assert len(fake.graphs) == 0


def test_items_list_accepted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": [1, 2], "graph_id": "g"}
    )
    assert out["total"] == 2
    assert len(fake.graphs) == 2


def test_items_tuple_converted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": (1, 2), "graph_id": "g"}
    )
    assert out["total"] == 2
    assert fake.ctxs[0].variables["batch"] == [1]


def test_items_str_rejected(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError) as exc:
        LoopBatchNode()._execute(ctx, {"items": "abc"})
    assert str(exc.value).startswith("loop_batch:")


@pytest.mark.parametrize("bad", [123, {}, _items_generator()])
def test_items_invalid_types_rejected(tmp_path: Any, bad: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError) as exc:
        LoopBatchNode()._execute(ctx, {"items": bad})
    assert str(exc.value).startswith("loop_batch:")


# ---------------------------------------------------------------------------
# Group 3 — mode / batch_size validation
# ---------------------------------------------------------------------------

def test_mode_default_loop(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(ctx, {"items": [1], "graph_id": "g"})
    assert out["mode"] == "loop"
    assert out["batch_size"] == 1


def test_mode_case_sensitive(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError):
        LoopBatchNode()._execute(
            ctx, {"items": [], "mode": "Loop", "graph_id": "g"}
        )


def test_mode_batch_accepted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": [1], "mode": "batch", "batch_size": 2, "graph_id": "g"}
    )
    assert out["mode"] == "batch"
    assert out["batch_size"] == 2


@pytest.mark.parametrize("bad", [0, -1, True, "3"])
def test_batch_size_invalid_in_batch(tmp_path: Any, bad: Any) -> None:
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    with pytest.raises(NodeExecutionError):
        LoopBatchNode()._execute(
            ctx,
            {
                "items": [1],
                "mode": "batch",
                "batch_size": bad,
                "graph_id": "g",
            },
        )


def test_batch_size_ignored_in_loop(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2],
            "mode": "loop",
            "batch_size": 5,
            "graph_id": "g",
        },
    )
    assert out["batch_size"] == 1
    assert out["total"] == 2


# ---------------------------------------------------------------------------
# Group 4 — empty items
# ---------------------------------------------------------------------------

def test_empty_items_zero_outputs(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": [], "graph_id": "g"}
    )
    assert out["results"] == []
    assert out["errors"] == []
    assert out["error_count"] == 0
    assert out["total"] == 0


def test_empty_items_still_resolves_child(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({}))
    with pytest.raises(NodeExecutionError):
        LoopBatchNode()._execute(
            ctx, {"items": [], "graph_id": "missing"}
        )


# ---------------------------------------------------------------------------
# Group 5 — loop mode
# ---------------------------------------------------------------------------

def test_loop_n_calls(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    LoopBatchNode()._execute(
        ctx, {"items": [10, 20, 30], "graph_id": "g"}
    )
    assert len(fake.graphs) == 3


def test_loop_child_variables(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
        variables={"parent": 1},
    )
    LoopBatchNode()._execute(
        ctx, {"items": ["x", "y"], "graph_id": "g"}
    )
    assert fake.ctxs[0].variables == {
        "parent": 1,
        "item": "x",
        "index": 0,
        "batch": ["x"],
    }
    assert fake.ctxs[1].variables == {
        "parent": 1,
        "item": "y",
        "index": 1,
        "batch": ["y"],
    }


def test_loop_results_are_run_result(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": [1, 2], "graph_id": "g"}
    )
    assert all(isinstance(item, RunResult) for item in out["results"])


def test_loop_does_not_mutate_parent_context(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(
        tmp_path,
        automations=FakeCatalog({"g": object()}),
        variables={"parent": 1},
        subflow_stack=("root",),
    )
    variables_before = dict(ctx.variables)
    stack_before = ctx.subflow_stack
    LoopBatchNode()._execute(
        ctx, {"items": [1, 2], "graph_id": "g"}
    )
    assert ctx.variables == variables_before
    assert ctx.subflow_stack == stack_before


# ---------------------------------------------------------------------------
# Group 6 — batch mode
# ---------------------------------------------------------------------------

def test_batch_chunking_calls_and_total(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2, 3, 4, 5],
            "mode": "batch",
            "batch_size": 2,
            "graph_id": "g",
        },
    )
    assert len(fake.graphs) == 3
    assert out["total"] == 3


def test_batch_item_none_and_batch_chunk(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2, 3, 4, 5],
            "mode": "batch",
            "batch_size": 2,
            "graph_id": "g",
        },
    )
    assert fake.ctxs[0].variables["item"] is None
    assert fake.ctxs[0].variables["batch"] == [1, 2]
    assert fake.ctxs[1].variables["item"] is None
    assert fake.ctxs[1].variables["batch"] == [3, 4]
    assert fake.ctxs[2].variables["batch"] == [5]


def test_batch_size_larger_than_items(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2],
            "mode": "batch",
            "batch_size": 10,
            "graph_id": "g",
        },
    )
    assert len(fake.graphs) == 1
    assert fake.ctxs[0].variables["batch"] == [1, 2]
    assert out["total"] == 1


# ---------------------------------------------------------------------------
# Group 7 — per-item fail-soft
# ---------------------------------------------------------------------------

def test_lokerror_fail_soft(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def behavior(child_ctx: Any, graph: Any, call_no: int) -> Any:
        if child_ctx.variables.get("index") == 1:
            raise BoomError("boom")
        return _run_result(f"child-{call_no}")

    _install_fake_executor(monkeypatch, loop_batch_mod, behavior)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": ["a", "b", "c"], "graph_id": "g"}
    )
    assert out["results"][1] is None
    assert isinstance(out["results"][0], RunResult)
    assert isinstance(out["results"][2], RunResult)
    assert out["errors"] == [
        {"index": 1, "error": "boom", "error_type": "BoomError"}
    ]


def test_non_lokerror_reraises(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def behavior(child_ctx: Any, graph: Any, call_no: int) -> Any:
        if call_no == 1:
            raise ValueError("bad")
        return _run_result(f"child-{call_no}")

    _install_fake_executor(monkeypatch, loop_batch_mod, behavior)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    with pytest.raises(ValueError):
        LoopBatchNode()._execute(
            ctx, {"items": ["a", "b", "c"], "graph_id": "g"}
        )


def test_error_count_matches_errors(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def behavior(child_ctx: Any, graph: Any, call_no: int) -> Any:
        if child_ctx.variables.get("index") in (0, 2):
            raise BoomError("boom")
        return _run_result(f"child-{call_no}")

    _install_fake_executor(monkeypatch, loop_batch_mod, behavior)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": ["a", "b", "c"], "graph_id": "g"}
    )
    assert out["error_count"] == len(out["errors"]) == 2


# ---------------------------------------------------------------------------
# Group 8 — depth / cycle guards via _resolve_child_graph
# ---------------------------------------------------------------------------

def test_depth_guard(tmp_path: Any) -> None:
    ctx = _make_ctx(
        tmp_path,
        flow_depth=4,
        max_subflow_depth=4,
        automations=FakeCatalog({"g": object()}),
    )
    with pytest.raises(NodeExecutionError) as exc:
        _resolve_child_graph(ctx, {"graph_id": "g"})
    assert "depth" in str(exc.value).lower()


def test_cycle_guard(tmp_path: Any) -> None:
    ctx = _make_ctx(
        tmp_path,
        subflow_stack=("g",),
        automations=FakeCatalog({"g": object()}),
    )
    with pytest.raises(NodeExecutionError) as exc:
        _resolve_child_graph(ctx, {"graph_id": "g"})
    assert "cycle" in str(exc.value).lower()


@pytest.mark.parametrize("bad", [True, "10", 0, -1])
def test_max_depth_invalid(tmp_path: Any, bad: Any) -> None:
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    with pytest.raises(NodeExecutionError):
        _resolve_child_graph(
            ctx, {"graph_id": "g", "max_depth": bad}
        )


# ---------------------------------------------------------------------------
# Group 9 — _resolve_child_graph misc
# ---------------------------------------------------------------------------

def test_exactly_one_both(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError):
        _resolve_child_graph(
            ctx, {"graph_id": "g", "graph": {"nodes": []}}
        )


def test_exactly_one_neither(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError):
        _resolve_child_graph(ctx, {})


@pytest.mark.parametrize("gid", ["a/b", r"a\b", "x.json"])
def test_graph_id_fs_path_rejected(tmp_path: Any, gid: str) -> None:
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({gid: object()}))
    with pytest.raises(NodeExecutionError) as exc:
        _resolve_child_graph(ctx, {"graph_id": gid})
    text = str(exc.value).lower()
    assert "filesystem" in text or "not supported" in text


def test_resolve_child_graph_in_all() -> None:
    assert "_resolve_child_graph" in common_mod.__all__


# ---------------------------------------------------------------------------
# Group 10 — _preview
# ---------------------------------------------------------------------------

def test_loop_batch_preview_exact(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError) as exc:
        LoopBatchNode()._preview(ctx, {})
    assert str(exc.value) == (
        "loop_batch: nested graph is not supported by the action plan builder"
    )


def test_subflow_preview_exact(tmp_path: Any) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError) as exc:
        SubflowNode()._preview(ctx, {})
    assert str(exc.value) == (
        "subflow: nested graph is not supported by the action plan builder"
    )


# ---------------------------------------------------------------------------
# Group 11 — subflow regression
# ---------------------------------------------------------------------------

def test_subflow_execute_returns_dict_envelope(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_ctx(
        tmp_path, automations=FakeCatalog({"A": object()})
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
    assert out["graph_id"] == "A"
    assert out["depth"] == 1
    assert out["errors"] == []
    assert out["error_count"] == 0


def test_subflow_does_not_merge_variables(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, subflow_mod)
    ctx = _make_ctx(
        tmp_path,
        automations=FakeCatalog({"A": object()}),
        variables={"parent": 1},
    )
    SubflowNode()._execute(ctx, {"graph_id": "A"})
    child = fake.ctxs[-1]
    assert child is not ctx
    assert child.variables == {"parent": 1}


def test_subflow_propagates_child_lokerror(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def behavior(child_ctx: Any, graph: Any, call_no: int) -> Any:
        raise BoomError("child failed")

    _install_fake_executor(monkeypatch, subflow_mod, behavior)
    ctx = _make_ctx(
        tmp_path, automations=FakeCatalog({"A": object()})
    )
    with pytest.raises(BoomError):
        SubflowNode()._execute(ctx, {"graph_id": "A"})


# ---------------------------------------------------------------------------
# Group 12 — port contract superset
# ---------------------------------------------------------------------------

EXPECTED_LOOP_BATCH_KEYS = {
    "results",
    "errors",
    "error_count",
    "total",
    "mode",
    "batch_size",
    "graph_id",
    "depth",
}


def test_port_contract_loop_success(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx, {"items": [1], "graph_id": "g"}
    )
    assert EXPECTED_LOOP_BATCH_KEYS <= set(out)


def test_port_contract_empty_items(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {"items": [], "mode": "batch", "batch_size": 10, "graph_id": "g"},
    )
    assert EXPECTED_LOOP_BATCH_KEYS <= set(out)


# ---------------------------------------------------------------------------
# Expected red — kept last so --maxfail=1 still exercises groups 1-12 above.
#
# BUG: src/lokdocsqr/nodes/flow/loop_batch.py:152 — B2 (batch index)
# — ожидалось index = порядковый номер чанка (0..total-1),
#   получено index = смещение среза (0, batch_size, 2*batch_size, ...),
#   что нарушает index ∈ [0, total).
# ---------------------------------------------------------------------------

def test_batch_index_is_chunk_ordinal(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install_fake_executor(monkeypatch, loop_batch_mod)
    ctx = _make_ctx(tmp_path, automations=FakeCatalog({"g": object()}))
    out = LoopBatchNode()._execute(
        ctx,
        {
            "items": [1, 2, 3, 4, 5],
            "mode": "batch",
            "batch_size": 2,
            "graph_id": "g",
        },
    )
    indexes = [child_ctx.variables["index"] for child_ctx in fake.ctxs]
    assert out["total"] == 3
    assert indexes == [0, 1, 2]
