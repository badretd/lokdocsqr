"""Phase 8b gate tests — utils.set_variables / utils.json_validate.

Expected red (declared): none from static review of apply_phase_8b.py.
If a test fails, report as:
    BUG: <file>:<line> — <invariant/AC/R#> — <expected/got>
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from lokdocsqr.core.executor.context import RESERVED_VARIABLES, ExecutionContext
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.nodes.flow._base import _FlowBase
from lokdocsqr.nodes.utils.json_validate import JsonValidateNode
from lokdocsqr.nodes.utils.set_variables import SetVariablesNode

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_RESERVED = frozenset(
    {
        "run_id",
        "automation_id",
        "flow_depth",
        "automations",
        "scope",
        "config",
        "audit",
        "logger",
        "events",
        "policy",
        "verify_callback",
        "clock",
    }
)


def _make_ctx() -> ExecutionContext:
    """Real ExecutionContext with only the fields set_variable needs."""
    ctx = object.__new__(ExecutionContext)
    ctx.variables = {}
    return ctx


def _run_python(code: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# set_variables — mandatory negatives 1–4, 15
# ---------------------------------------------------------------------------


def test_set_variables_reserved_key_is_per_item_error_not_raise() -> None:
    node = SetVariablesNode()
    ctx = _make_ctx()
    result = node.run(ctx, {"variables": {"run_id": 1, "ok": 2}})

    assert result["count"] == 1
    assert result["variables"] == {"ok": 2}
    assert len(result["errors"]) == 1
    assert result["errors"][0]["name"] == "run_id"
    assert "reserved" in result["errors"][0]["error"]
    assert ctx.variables == {"ok": 2}


def test_set_variables_all_reserved_keys_leave_ctx_untouched() -> None:
    node = SetVariablesNode()
    ctx = _make_ctx()
    payload = {
        "variables": {
            name: index
            for index, name in enumerate(sorted(RESERVED_VARIABLES))
        }
    }

    result = node.run(ctx, payload)

    assert result["count"] == 0
    assert result["variables"] == {}
    assert len(result["errors"]) == len(RESERVED_VARIABLES)
    assert ctx.variables == {}


def test_set_variables_preview_is_pure_and_reports_reserved() -> None:
    node = SetVariablesNode()
    ctx = _make_ctx()
    before = dict(ctx.variables)

    result = node.dry_run(ctx, {"variables": {"run_id": 1, "ok": 2}})

    assert ctx.variables == before
    assert result["dry_run"] is True
    assert result["count"] == 1
    assert result["variables"] == {"ok": 2}
    assert any(e["name"] == "run_id" for e in result["errors"])
    assert isinstance(result["preview"], str) and result["preview"]


def test_set_variables_missing_variables_payload_is_structural_error() -> None:
    node = SetVariablesNode()
    ctx = _make_ctx()

    result = node.run(ctx, {"not_variables": {}})

    assert result["count"] == 0
    assert result["variables"] == {}
    assert result["errors"]
    assert "variables" in result["errors"][0]["error"]
    assert ctx.variables == {}

    preview = node.dry_run(ctx, {"not_variables": {}})
    assert preview["dry_run"] is True
    assert preview["errors"]


def test_set_variables_failed_set_does_not_write_ctx_variables() -> None:
    node = SetVariablesNode()
    ctx = _make_ctx()

    result = node.run(ctx, {"variables": {1: "bad", "ok": 2}})

    assert ctx.variables == {"ok": 2}
    assert result["count"] == 1
    assert any(e["name"] == "1" for e in result["errors"])


# ---------------------------------------------------------------------------
# json_validate — mandatory negatives 5–9
# ---------------------------------------------------------------------------


def test_json_validate_parse_error_is_structured_not_raised() -> None:
    node = JsonValidateNode()

    result = node.run(
        None, {"data": "not json", "schema": {"type": "object"}}
    )

    assert result["valid"] is False
    assert result["error_count"] == 1
    assert result["errors"][0]["path"] == "$"
    assert "parse error" in result["errors"][0]["message"].lower()


def test_json_validate_unknown_keyword_rejected() -> None:
    node = JsonValidateNode()

    result = node.run(
        None, {"data": {}, "schema": {"required_typo": ["x"]}}
    )

    assert result["valid"] is False
    assert result["error_count"] >= 1
    assert any(
        "unsupported keyword" in error["message"]
        for error in result["errors"]
    )


def test_json_validate_required_missing_path() -> None:
    node = JsonValidateNode()

    result = node.run(
        None,
        {
            "data": {"a": 1},
            "schema": {"type": "object", "required": ["b"]},
        },
    )

    assert result["valid"] is False
    assert result["errors"]
    assert any(error["path"] == "$.b" for error in result["errors"])


def test_json_validate_required_present_echoes_data() -> None:
    node = JsonValidateNode()
    data = {"a": 1}

    result = node.run(
        None,
        {
            "data": data,
            "schema": {"type": "object", "required": ["a"]},
        },
    )

    assert result["valid"] is True
    assert result["error_count"] == 0
    assert result["errors"] == []
    assert result["data"] == data


def test_json_validate_preview_does_not_read_fs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("sentinel", encoding="utf-8")

    called: list[str] = []

    def guarded_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        called.append(str(self))
        raise AssertionError(f"Path.open called for {self}")

    monkeypatch.setattr(Path, "open", guarded_open)

    node = JsonValidateNode()
    result = node.dry_run(
        None, {"data": {"a": 1}, "schema": {"type": "object"}}
    )

    assert result["dry_run"] is True
    assert called == []


def test_json_validate_no_jsonschema_and_no_fs_imports_in_subprocess() -> None:
    code = textwrap.dedent(
        """
        import sys
        import lokdocsqr.nodes.utils.json_validate as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "safe_fs" not in source, "safe_fs referenced in json_validate"
        assert "pathlib" not in source, "pathlib referenced in json_validate"
        assert "jsonschema" not in sys.modules, "jsonschema imported"
        """
    )

    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# Port contract, preview override, well-formedness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node_cls", "payload"),
    [
        (SetVariablesNode, {"variables": {"a": 1}}),
        (
            JsonValidateNode,
            {"data": {"a": 1}, "schema": {"type": "object"}},
        ),
    ],
)
def test_port_contract_run_and_preview_superset_of_outputs(
    node_cls: type[Any], payload: dict[str, Any]
) -> None:
    node = node_cls()
    ctx = _make_ctx() if node_cls is SetVariablesNode else None

    for method_name in ("run", "dry_run"):
        result = getattr(node, method_name)(ctx, payload)

        assert isinstance(result, dict)
        missing = set(node.outputs) - set(result)
        assert not missing, f"{method_name} missing outputs {missing}"

        if method_name == "dry_run":
            assert result["dry_run"] is True
            assert isinstance(result["preview"], str)
            assert result["preview"]


def test_both_utils_nodes_override_preview() -> None:
    assert SetVariablesNode._preview is not _FlowBase._preview
    assert JsonValidateNode._preview is not _FlowBase._preview


def test_set_variables_outputs_well_formed() -> None:
    result = SetVariablesNode().run(_make_ctx(), {"variables": {"a": 1}})

    assert isinstance(result["variables"], dict)
    assert isinstance(result["count"], int)
    assert isinstance(result["errors"], list)


def test_json_validate_outputs_well_formed() -> None:
    result = JsonValidateNode().run(
        None, {"data": {"a": 1}, "schema": {"type": "object"}}
    )

    assert isinstance(result["valid"], bool)
    assert isinstance(result["errors"], list)
    assert isinstance(result["error_count"], int)
    assert "data" in result


# ---------------------------------------------------------------------------
# Invariant 5 (AST) — only CONTRACT.md invariants use AST
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relpath",
    [
        "src/lokdocsqr/nodes/utils/set_variables.py",
        "src/lokdocsqr/nodes/utils/json_validate.py",
    ],
)
def test_invariant_5_concrete_nodes_declare_run_and_dry_run(
    relpath: str,
) -> None:
    path = ROOT / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Node")
    ]

    assert node_classes, f"no *Node class in {relpath}"

    for cls in node_classes:
        methods = {
            node.name
            for node in cls.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert {"run", "dry_run"} <= methods, (
            f"{cls.name} must declare run and dry_run in class body"
        )


# ---------------------------------------------------------------------------
# Discovery, inheritance, RESERVED_VARIABLES
# ---------------------------------------------------------------------------


def test_discovery_registers_utils_nodes() -> None:
    import lokdocsqr.nodes  # noqa: F401  (triggers registration)

    ids = {item["id"] for item in registry.list()}
    assert "utils.set_variables" in ids
    assert "utils.json_validate" in ids


def test_discovery_in_fresh_interpreter() -> None:
    code = textwrap.dedent(
        """
        from lokdocsqr.core.node_api.registry import registry
        import lokdocsqr.nodes  # noqa: F401

        ids = {item["id"] for item in registry.list()}
        assert "utils.set_variables" in ids, sorted(ids)
        assert "utils.json_validate" in ids, sorted(ids)
        """
    )

    proc = _run_python(code)
    assert proc.returncode == 0, proc.stderr


def test_utils_nodes_inherit_flow_base() -> None:
    assert isinstance(SetVariablesNode(), _FlowBase)
    assert isinstance(JsonValidateNode(), _FlowBase)


def test_reserved_variables_match_contract() -> None:
    assert RESERVED_VARIABLES == EXPECTED_RESERVED


def test_set_variables_imports_reserved_variables_from_context() -> None:
    source = (
        ROOT / "src/lokdocsqr/nodes/utils/set_variables.py"
    ).read_text(encoding="utf-8")

    assert (
        "from lokdocsqr.core.executor.context import RESERVED_VARIABLES"
        in source
    )


# ---------------------------------------------------------------------------
# pyproject.toml immutability (R17)
# ---------------------------------------------------------------------------


def test_pyproject_toml_not_modified() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", "pyproject.toml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0:
        pytest.skip("git is not available")

    assert status.stdout.strip() == "", (
        f"pyproject.toml modified: {status.stdout!r}"
    )

    diff = subprocess.run(
        ["git", "diff", "--stat", "--", "pyproject.toml"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert diff.stdout.strip() == ""


# ---------------------------------------------------------------------------
# 8a regression smoke checks (flow.if_branch / flow.filter)
# ---------------------------------------------------------------------------


def test_8a_flow_nodes_still_satisfy_port_contract() -> None:
    from lokdocsqr.nodes.flow.filter import FilterNode
    from lokdocsqr.nodes.flow.if_branch import IfBranchNode

    cases = [
        (IfBranchNode(), {"condition": "True"}),
        (FilterNode(), {"items": [], "condition": "True"}),
    ]

    for node, payload in cases:
        for method_name in ("run", "dry_run"):
            result = getattr(node, method_name)(None, payload)
            assert isinstance(result, dict)
            assert set(result) >= set(node.outputs)


def test_8a_filter_preview_contains_items_and_results() -> None:
    from lokdocsqr.nodes.flow.filter import FilterNode

    result = FilterNode().dry_run(
        None, {"items": [{"name": "a"}], "condition": "name == 'a'"}
    )

    assert "items" in result
    assert "results" in result
