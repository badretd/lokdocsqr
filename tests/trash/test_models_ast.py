"""Fast AST/inspect checks — no filesystem, no node execution."""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

from tests.trash.conftest import ROOT, SRC

ACTIONLOG_ROLLBACK_FIELDS = (
    "rollback_eligible", "original_path", "trash_path",
    "sidecar_path", "hash_before",
)
COMMON_5A5B_PARAMS = (
    "run_id", "automation_id", "node_id", "action_type",
    "source", "target", "reason", "confidence", "risk", "dry_run",
)


def _actionlog_declared():
    path = SRC / "lokdocsqr" / "core" / "models.py"
    if not path.exists():
        pytest.fail("BUG: src/lokdocsqr/core/models.py — file missing")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "ActionLog"), None)
    if cls is None:
        pytest.fail("BUG: models.py — ActionLog class not found")
    declared = set()
    for stmt in cls.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            declared.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            declared |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    return declared


def test_actionlog_declares_all_rollback_fields():
    declared = _actionlog_declared()
    missing = [f for f in ACTIONLOG_ROLLBACK_FIELDS if f not in declared]
    if missing:
        pytest.fail(
            f"BUG: src/lokdocsqr/core/models.py — ActionLog missing "
            f"{missing}; present: {sorted(declared)}"
        )


def test_actionlog_constructible_with_rollback_fields():
    import lokdocsqr.core.models as models
    ActionLog = getattr(models, "ActionLog", None)
    Risk = getattr(models, "Risk", None)
    if ActionLog is None or Risk is None:
        pytest.fail("BUG: models.py — ActionLog/Risk not exported")
    try:
        log = ActionLog(
            run_id="r", automation_id="a", node_id="n",
            action_type="trash", source="/s", target="/t",
            reason="test", confidence=1.0, risk=Risk.MEDIUM, dry_run=False,
            rollback_eligible=True, original_path="/orig",
            trash_path="/trash", sidecar_path="/sc", hash_before="ab" * 32,
        )
    except TypeError as exc:
        pytest.fail(f"BUG: models.py — ActionLog rejects rollback kwargs: {exc}")
    assert log.rollback_eligible is True


def test_common_build_action_log_keeps_5a5b_params():
    import lokdocsqr.nodes.fs_ops._common  # noqa: F401
    mod = sys.modules["lokdocsqr.nodes.fs_ops._common"]
    fn = getattr(mod, "build_action_log", None)
    if fn is None:
        pytest.fail(
            "BUG: src/lokdocsqr/nodes/fs_ops/_common.py — "
            "build_action_log removed/renamed since 5a/5b"
        )
    params = set(inspect.signature(fn).parameters)
    missing = set(COMMON_5A5B_PARAMS) - params
    if missing:
        pytest.fail(
            f"BUG: _common.py — build_action_log lost 5a/5b params {missing}"
        )


def test_common_build_action_log_forwards_rollback_fields():
    import lokdocsqr.nodes.fs_ops._common  # noqa: F401
    mod = sys.modules["lokdocsqr.nodes.fs_ops._common"]
    fn = getattr(mod, "build_action_log", None)
    if fn is None:
        pytest.fail("BUG: _common.py — build_action_log removed")
    params = set(inspect.signature(fn).parameters)
    missing = [f for f in ACTIONLOG_ROLLBACK_FIELDS if f not in params]
    if missing:
        pytest.fail(
            f"BUG: _common.py — build_action_log missing kwargs {missing}"
        )


def test_trash_has_no_post_construction_setattr():
    path = SRC / "lokdocsqr" / "nodes" / "fs_ops" / "trash.py"
    if not path.exists():
        pytest.fail("BUG: src/lokdocsqr/nodes/fs_ops/trash.py — file missing")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Attribute)
                        and tgt.attr in ACTIONLOG_ROLLBACK_FIELDS):
                    violations.append((node.lineno, tgt.attr))
    if violations:
        detail = ", ".join(f"line {ln}: .{a} =" for ln, a in violations)
        pytest.fail(
            f"BUG: trash.py — post-construction setattr on rollback fields "
            f"({detail}); must go via build_action_log(**kwargs)"
        )