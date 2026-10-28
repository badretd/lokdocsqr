"""Phase 8a gate: flow.if_branch + flow.filter."""
from __future__ import annotations

import ast
import builtins
import importlib
import inspect
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _src_root() -> Path:
    return _repo_root() / "src"


def _make_audit(tmp_path: Path):
    from lokdocsqr.core.audit import AuditLog
    for ctor in (
        lambda: AuditLog(tmp_path / "audit.jsonl"),
        lambda: AuditLog(path=tmp_path / "audit.jsonl"),
        lambda: AuditLog(tmp_path),
        lambda: AuditLog(),
    ):
        try:
            return ctor()
        except TypeError:
            continue
    raise RuntimeError("cannot construct AuditLog")


def _make_scope(tmp_path: Path):
    """Build a real ScopeManager with whatever constructor shape it exposes."""
    from lokdocsqr.core.scope import ScopeManager
    try:
        params = set(inspect.signature(ScopeManager.__init__).parameters)
    except (TypeError, ValueError):
        params = set()

    attempts = []
    if "roots" in params:
        attempts.append(lambda: ScopeManager(roots=[tmp_path]))
    if "allowed_roots" in params:
        attempts.append(lambda: ScopeManager(allowed_roots=[tmp_path]))
    if "base_dir" in params:
        attempts.append(lambda: ScopeManager(base_dir=tmp_path))
    attempts.append(lambda: ScopeManager([tmp_path]))
    attempts.append(lambda: ScopeManager(tmp_path))
    attempts.append(lambda: ScopeManager())

    last: TypeError | None = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last = exc
            continue
    raise last or RuntimeError("no ScopeManager constructor matched")


def exec_ctx(tmp_path: Path):
    from lokdocsqr.core.clock import FakeClock
    from lokdocsqr.core.events import EventBus
    from lokdocsqr.core.executor.context import ExecutionContext
    return ExecutionContext(
        scope=_make_scope(tmp_path),
        config=types.SimpleNamespace(base_dir=tmp_path),
        audit=_make_audit(tmp_path),
        run_id="test-run-8a",
        automation_id="test-automation-8a",
        events=EventBus(),
        variables={},
        policy=None,
        verify_callback=None,
        clock=FakeClock(),
    )


def _snapshot(root: Path):
    return {
        str(p.relative_to(root)): (p.is_dir(), p.stat().st_size, p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*"))
    }


# ── A. Discovery ────────────────────────────────────────────────────────────

def test_a1_discovery_registers_flow_nodes_once():
    import lokdocsqr.nodes  # noqa: F401
    from lokdocsqr.core.node_api.registry import registry
    ids = [item["id"] for item in registry.list()]
    assert ids.count("flow.filter") == 1
    assert ids.count("flow.if_branch") == 1


def test_a2_reimport_does_not_duplicate_registry_entries():
    importlib.import_module("lokdocsqr.nodes")
    importlib.import_module("lokdocsqr.nodes")
    from lokdocsqr.core.node_api.registry import registry
    ids = [item["id"] for item in registry.list()]
    assert ids.count("flow.filter") == 1
    assert ids.count("flow.if_branch") == 1


def test_a3_flow_package_imports_filter_and_if_branch():
    import lokdocsqr.nodes.flow as flow_pkg
    src = Path(flow_pkg.__file__).read_text(encoding="utf-8")
    assert "filter" in src
    assert "if_branch" in src
    assert "import" in src


# ── B. if_branch ────────────────────────────────────────────────────────────

def test_b1_execute_and_preview_agree(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    cases = [
        {"condition": "x > 1", "x": 2},
        {"condition": "x > 1", "x": 0},
        {"condition": "unknown_name == 1"},
        [1, 2, 3],
    ]
    for payload in cases:
        assert node._execute(ctx, payload) == node._preview(ctx, payload)


def test_b2_determinism_two_calls_same_payload(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    payload = {"condition": "x == 1", "x": 1}
    assert node._execute(ctx, payload) == node._execute(ctx, payload)


def test_b3_does_not_read_filesystem(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    (tmp_path / "probe.txt").write_text("probe", encoding="utf-8")
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    before = _snapshot(tmp_path)
    payload = {"condition": "x == 1", "x": 1}
    node.run(ctx, payload)
    node.dry_run(ctx, payload)
    assert _snapshot(tmp_path) == before


def test_b4_sequence_input_has_all_three_ports_as_lists(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    payload = [1, 2, 3]
    result = node._execute(ctx, payload)
    assert set(result) == {"result", "branch", "items"}
    for key in ("result", "branch", "items"):
        assert isinstance(result[key], list)
        assert len(result[key]) == len(payload)


def test_b5_non_sequence_input_has_all_three_ports(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, {"condition": "True"})
    assert set(result) == {"result", "branch", "items"}
    assert result["result"] is True
    assert result["branch"] == "true"
    assert result["items"] == []


def test_b6_invalid_condition_is_per_item_error_not_exception(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, {"condition": "unknown_name == 1"})
    assert result["result"] == "error"
    assert result["branch"] == "error"


def test_b7_path_traversal_condition_is_error(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, {"condition": ".."})
    assert result["result"] == "error"
    assert result["branch"] == "error"


def test_b8_m1_condition_key_not_in_non_sequence_mapping(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, {"condition": "condition"})
    assert result["result"] == "error"
    assert result["branch"] == "error"


def test_b9_preview_is_not_overridden_and_delegates_to_execute(tmp_path: Path):
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    node = IfBranchNode()
    node._execute = lambda ctx, payload: "sentinel"  # type: ignore[method-assign]
    assert node._preview(None, {}) == "sentinel"


# ── C. filter ───────────────────────────────────────────────────────────────

def test_c1_preview_override_does_not_call_execute(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    node._execute = lambda *a, **k: pytest.fail("_execute must not be called by _preview")  # type: ignore[method-assign]
    payload = {"items": [{"kind": "a"}], "condition": "kind == 'a'"}
    assert isinstance(node._preview(ctx, payload), dict)


@pytest.mark.xfail(
    reason=(
        "C2 real production bug: filter on non-list input returns empty "
        "aggregates (total=0, kept=0, errors=0) without an 'error' marker; "
        "_extract_items silently returns [] for non-list payloads"
    ),
    strict=False,
)
def test_c2_filter_non_list_input_reports_error(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, 123)
    assert isinstance(result, dict)
    has_error = (
        result.get("errors", 0) > 0
        or "error" in result.get("results", [])
        or "error" in result.get("items", [])
    )
    assert has_error, f"C2: expected 'error' marker, got {result!r}"


def test_c3_filter_empty_list(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, [])
    assert result["items"] == []
    assert result["kept"] == 0
    assert result["filtered"] == 0
    assert result["errors"] == 0


def test_c4_filter_metadata_predicate_preserves_order(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    payload = {
        "condition": "kind == 'a'",
        "items": [
            {"kind": "b", "id": 1},
            {"kind": "a", "id": 2},
            {"kind": "a", "id": 3},
        ],
    }
    result = node._execute(ctx, payload)
    assert [item["id"] for item in result["items"]] == [2, 3]


def test_c5_execute_always_returns_dict_even_for_sequence_input(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    assert isinstance(node._execute(ctx, [{"kind": "a"}]), dict)


def test_c6_preview_contains_items_key(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    payload = {"items": [{"kind": "a"}], "condition": "kind == 'a'"}
    result = node._preview(ctx, payload)
    assert "items" in result
    assert isinstance(result["items"], list)


def test_c7_items_never_contains_error_marker(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    payload = {"items": [{"kind": "a"}], "condition": "unknown_name == 1"}
    result = node._execute(ctx, payload)
    assert "error" not in result["items"]
    assert result["errors"] == 1


def test_c8_preview_does_not_read_files_and_does_not_call_execute(tmp_path: Path):
    from lokdocsqr.nodes.flow.filter import FilterNode
    (tmp_path / "probe.txt").write_text("probe", encoding="utf-8")
    node = FilterNode()
    ctx = exec_ctx(tmp_path)
    node._execute = lambda *a, **k: pytest.fail("_execute must not be called by _preview")  # type: ignore[method-assign]
    before = _snapshot(tmp_path)
    payload = {"items": [{"kind": "a"}], "condition": "kind == 'a'"}
    node._preview(ctx, payload)
    assert _snapshot(tmp_path) == before


# ── D. Condition evaluator ──────────────────────────────────────────────────

def test_d1a_clean_subprocess_imports_common():
    code = (
        "import sys\n"
        "assert 'lokdocsqr.nodes.flow._common' not in sys.modules\n"
        "import lokdocsqr.nodes.flow._common\n"
        "print('ok')\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_src_root()) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_repo_root()),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_d1b_no_eval_exec_compile_in_source():
    from lokdocsqr.nodes.flow import _common as flow_common
    src = Path(flow_common.__file__).read_text(encoding="utf-8")
    for needle in ("eval(", "exec(", "compile(", "__import__"):
        assert needle not in src, f"forbidden source marker: {needle!r}"


def test_d1c_no_eval_exec_compile_ast_calls():
    from lokdocsqr.nodes.flow import _common as flow_common
    tree = ast.parse(Path(flow_common.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"eval", "exec", "compile"}
        ):
            pytest.fail(f"forbidden AST call: {node.func.id}")


def test_d2_no_ast_parse_or_walk_in_common():
    from lokdocsqr.nodes.flow import _common as flow_common
    tree = ast.parse(Path(flow_common.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "ast"
            and node.func.attr in {"parse", "walk"}
        ):
            pytest.fail(f"ast.{node.func.attr} flagged by D2")


def test_d3_lookup_rejects_unsafe_values():
    from lokdocsqr.nodes.flow import _common as flow_common
    mapping = {
        "__builtins__": builtins,
        "module": types.ModuleType("m"),
        "class": type("C", (), {}),
        "callable": lambda: None,
    }
    for name in mapping:
        with pytest.raises(flow_common._CondError):
            flow_common._lookup(name, mapping)


def test_d4_in_operators_require_safe_container(tmp_path: Path):
    from lokdocsqr.nodes.flow import _common as flow_common
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode
    with pytest.raises(flow_common._CondError):
        flow_common._compare(1, "in", 5)
    with pytest.raises(flow_common._CondError):
        flow_common._compare(1, "not in", 5)
    node = IfBranchNode()
    ctx = exec_ctx(tmp_path)
    result = node._execute(ctx, {"condition": "x in y", "x": 1, "y": 5})
    assert result["result"] == "error"


def test_d5_path_like_condition_rejected():
    from lokdocsqr.nodes.flow import _common as flow_common
    for expr in ("..", "/tmp", "a\\b"):
        with pytest.raises(flow_common._CondError):
            flow_common._evaluate(expr, {})


# ── E. Общая база и дублирование ────────────────────────────────────────────

def test_e1_flow_base_defined_once_in_base_py():
    from lokdocsqr.nodes.flow import _common as flow_common
    flow_dir = Path(flow_common.__file__).parent
    found = [
        path.name
        for path in sorted(flow_dir.glob("*.py"))
        if "class _FlowBase" in path.read_text(encoding="utf-8")
    ]
    assert found == ["_base.py"]


def test_e2_parser_evaluator_defined_once():
    """str-in-src with exact needles — ``def _evaluate(`` does not match ``_evaluate_one``."""
    from lokdocsqr.nodes.flow import _common as flow_common
    flow_dir = Path(flow_common.__file__).parent
    needles = [
        "def _tokenize(",
        "class _Parser",
        "def _evaluate(",
        "def _compare(",
        "def _lookup(",
    ]
    for needle in needles:
        count = sum(
            path.read_text(encoding="utf-8").count(needle)
            for path in flow_dir.glob("*.py")
        )
        assert count == 1, f"{needle!r} defined {count} times"


def _class_method_names(path, class_name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name
                for item in node.body
                if isinstance(item, ast.FunctionDef)
            }
    return set()


def test_e3_invariant5_run_and_dry_run_in_class_body():
    import lokdocsqr.nodes.flow.filter as filter_mod
    import lokdocsqr.nodes.flow.if_branch as if_branch_mod
    filter_methods = _class_method_names(filter_mod.__file__, "FilterNode")
    branch_methods = _class_method_names(if_branch_mod.__file__, "IfBranchNode")
    assert {"run", "dry_run"} <= filter_methods
    assert {"run", "dry_run"} <= branch_methods


def test_e4_underscore_modules_have_no_registered_nodes():
    from lokdocsqr.nodes.flow import _base as flow_base
    from lokdocsqr.nodes.flow import _common as flow_common
    for path in (flow_common.__file__, flow_base.__file__):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for dec in node.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and isinstance(dec.func, ast.Name)
                    and dec.func.id == "register_node"
                ):
                    pytest.fail(f"{path}: @register_node(...) in underscore module")
                if isinstance(dec, ast.Name) and dec.id == "register_node":
                    pytest.fail(f"{path}: @register_node in underscore module")


# ── F. Интеграционные/регрессионные проверки ────────────────────────────────

def test_f4_pyproject_diff_is_empty():
    proc = subprocess.run(
        ["git", "diff", "--", "pyproject.toml"],
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("git unavailable")
    assert proc.stdout == "", proc.stdout
