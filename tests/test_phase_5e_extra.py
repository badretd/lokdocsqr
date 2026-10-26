"""Extra Phase 5e verification tests.

This file intentionally does not rewrite the existing Phase 5e tests.
It covers gaps, delta checks, documentation checks, and mutation tests
that prove the new green mechanisms are not vacuous.

Requires: pytest, freezegun.
"""
from __future__ import annotations

import os
import ast
import inspect
import json
from datetime import datetime
from pathlib import Path

import pytest
from freezegun import freeze_time

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.registry import registry

import lokdocsqr.nodes.fs_ops.tag as tag_mod
from lokdocsqr.nodes.fs_ops._common import build_action_log

from . import test_architecture as arch
from . import test_fs_ops_integration as integ
from .test_dry_run import (
    FS_OPS_NODE_IDS,
    _TestCtx,
    _prepare,
    _snapshot,
    check_invariant_6,
)
from .test_scope_first import ScopeFirstTracker


ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# build_action_log deltas (Warning 1 from 5d)
# ---------------------------------------------------------------------------

def _make_log(**overrides):
    base = dict(
        run_id="r",
        automation_id="a",
        node_id="n",
        action_type="x",
        source=None,
        target=None,
        reason="r",
        confidence=1.0,
        risk=Risk.LOW,
        dry_run=False,
    )
    base.update(overrides)
    return build_action_log(**base)


def test_build_action_log_extra_is_not_dropped() -> None:
    log = _make_log(extra={"x": 1})
    assert log.extra == {"x": 1}


def test_build_action_log_details_is_backward_compatible_alias() -> None:
    # Fact from _common.py: details is accepted and used when extra is None.
    log = _make_log(details={"x": 1})
    assert log.extra == {"x": 1}


def test_build_action_log_extra_wins_over_details() -> None:
    log = _make_log(details={"old": 1}, extra={"new": 2})
    assert log.extra == {"new": 2}


def test_build_action_log_status_defaults() -> None:
    assert _make_log(dry_run=True).status == "planned"
    assert _make_log(dry_run=False).status == "applied"


# ---------------------------------------------------------------------------
# archive log delta: sources must be a list in extra, comma-joined in source
# ---------------------------------------------------------------------------

def test_archive_create_log_extra_sources_is_list(tmp_path: Path, monkeypatch) -> None:
    # All artifacts must be written while CWD == tmp_path, but CWD must be
    # restored before this function returns — otherwise the leak detector
    # (which runs at end-of-body, while monkeypatch.chdir is still in
    # effect) flags every file under tmp_path as a CWD leak.
    original_cwd = os.getcwd()
    monkeypatch.chdir(tmp_path)
    try:
        node, payload = _prepare("fs.archive", tmp_path)
        ctx = _TestCtx([tmp_path])
        log = node.run(ctx, payload)
    finally:
        os.chdir(original_cwd)

    expected = [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]
    assert log.extra["sources"] == expected
    assert log.source == ",".join(expected)
    assert log.extra["format"] == "zip"


# ---------------------------------------------------------------------------
# Tag node (fs.tag)
# ---------------------------------------------------------------------------

def _tag_sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".tags.json")


def _read_tag_sidecar(path: Path) -> dict:
    return json.loads(_tag_sidecar(path).read_text(encoding="utf-8"))


def test_tag_set_add_remove_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    r1 = node.run(ctx, {"path": str(target), "tags": ["x", "y"], "mode": "set"})
    assert r1["tags"] == ["x", "y"]
    assert _read_tag_sidecar(target)["tags"] == ["x", "y"]

    r2 = node.run(ctx, {"path": str(target), "tags": ["z"], "mode": "add"})
    assert r2["tags"] == ["x", "y", "z"]

    r3 = node.run(ctx, {"path": str(target), "tags": ["x"], "mode": "remove"})
    assert r3["tags"] == ["y", "z"]
    assert target.exists(), "removing a tag must not delete the file"


def test_tag_sidecar_json_shape(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    node.run(ctx, {"path": str(target), "tags": ["x"], "mode": "set"})
    data = _read_tag_sidecar(target)

    assert data["tags"] == ["x"]
    datetime.fromisoformat(data["updated_at"])


def test_tag_updated_at_changes_between_calls(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    with freeze_time("2024-01-01 00:00:00"):
        node.run(ctx, {"path": str(target), "tags": ["a"], "mode": "set"})
        first = _read_tag_sidecar(target)["updated_at"]

    with freeze_time("2024-01-01 00:00:01"):
        node.run(ctx, {"path": str(target), "tags": ["b"], "mode": "add"})
        second = _read_tag_sidecar(target)["updated_at"]

    assert first != second


def test_tag_dry_run_does_not_write_sidecar(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    before = _snapshot(tmp_path)
    node.dry_run(ctx, {"path": str(target), "tags": ["x"], "mode": "set"})
    after = _snapshot(tmp_path)

    assert before == after
    assert not _tag_sidecar(target).exists()


def test_tag_run_audits_and_dry_run_does_not(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()

    ctx_run = _TestCtx([tmp_path])
    node.run(ctx_run, {"path": str(target), "tags": ["x"], "mode": "set"})
    assert len(ctx_run.audit.entries) == 1

    ctx_dry = _TestCtx([tmp_path])
    node.dry_run(ctx_dry, {"path": str(target), "tags": ["y"], "mode": "add"})
    assert ctx_dry.audit.entries == []


def test_tag_rejects_invalid_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    with pytest.raises(NodeExecutionError, match="mode"):
        node.run(ctx, {"path": str(target), "tags": ["x"], "mode": "bad"})


def test_tag_xattr_branch_calls_setxattr(tmp_path: Path, monkeypatch) -> None:
    class FakeXattr:
        def __init__(self) -> None:
            self.calls = []

        def setxattr(self, path: str, key: str, value: bytes) -> None:
            self.calls.append((path, key, value))

    fake = FakeXattr()
    monkeypatch.setattr(tag_mod, "_try_xattr", lambda: fake)

    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    node.run(ctx, {"path": str(target), "tags": ["x"], "mode": "set"})

    assert fake.calls
    assert fake.calls[0][1] == tag_mod._XATTR_KEY
    assert json.loads(fake.calls[0][2].decode("utf-8")) == ["x"]


def test_tag_xattr_absence_degrades_to_sidecar(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tag_mod, "_try_xattr", lambda: None)

    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    node = registry.get("fs.tag")()
    ctx = _TestCtx([tmp_path])

    node.run(ctx, {"path": str(target), "tags": ["x"], "mode": "set"})
    assert _tag_sidecar(target).exists()


# ---------------------------------------------------------------------------
# Invariant 5: real tree and mutation guard
# ---------------------------------------------------------------------------

def test_invariant_5_guard_real_tree_passes() -> None:
    arch.test_invariant_5_no_registered_nodes_in_underscore_modules()


def test_mutation_invariant_5_guard_catches_registered_node(tmp_path: Path, monkeypatch) -> None:
    nodes = tmp_path / "nodes"
    nodes.mkdir()
    (nodes / "_probe.py").write_text(
        "from lokdocsqr.core.node_api.decorators import register_node\n"
        "@register_node('probe.fake')\n"
        "class ProbeNode:\n"
        "    pass\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(arch, "NODES", nodes)
    monkeypatch.setattr(arch, "ROOT", tmp_path)

    with pytest.raises(AssertionError, match="Invariant-5 exemption breach"):
        arch.test_invariant_5_no_registered_nodes_in_underscore_modules()


def test_invariant_5_node_contract_real_tree_passes() -> None:
    arch.test_invariant_5_node_contract()


def test_invariant_5_contract_skips_all_underscore_modules_by_prefix() -> None:
    source = inspect.getsource(arch.test_invariant_5_node_contract)
    assert 'py_file.name.startswith("_")' in source
    assert 'py_file.name == "_base.py"' not in source


def test_concrete_fs_ops_nodes_declare_run_and_dry_run_explicitly() -> None:
    for node_id in FS_OPS_NODE_IDS:
        cls = registry.get(node_id)
        source_path = Path(inspect.getsourcefile(cls))
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        class_def = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == cls.__name__
        )
        methods = {
            item.name
            for item in class_def.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert {"run", "dry_run"} <= methods, (
            f"{node_id} ({cls.__name__}) must declare run and dry_run "
            "in the class body, not inherit them"
        )


# ---------------------------------------------------------------------------
# Invariant 6/7: skip removal and call-time enforcement
# ---------------------------------------------------------------------------

def test_invariant_6_and_7_are_not_skipped_and_call_checkers(
    tmp_path: Path, monkeypatch
) -> None:
    source = (ROOT / "tests" / "test_architecture.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {
        "test_invariant_6_dry_run_purity": "check_invariant_6",
        "test_invariant_7_scope_first": "check_invariant_7",
    }

    for func_name, checker in wanted.items():
        fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == func_name
        )
        for deco in fn.decorator_list:
            deco_src = ast.get_source_segment(source, deco) or ""
            assert "skip" not in deco_src.lower()
        body = ast.get_source_segment(source, fn) or ""
        assert checker in body

    # Both arch tests now take fixtures — forward them.  Skip-guard is
    # enforced globally by tests/conftest.py, so this test's own AST
    # "no skip decorator" check is a redundancy we keep for clarity.
    arch.test_invariant_6_dry_run_purity(tmp_path, monkeypatch)
    arch.test_invariant_7_scope_first(tmp_path, monkeypatch)


def test_mutation_scope_first_tracker_catches_unchecked_make_dir(tmp_path: Path) -> None:
    tracker = ScopeFirstTracker()
    original = safe_fs.make_dir
    safe_fs.make_dir = tracker.wrap_op("make_dir", original, ["path"])
    try:
        with pytest.raises(
            RuntimeError,
            match="scope.check not called before safe_fs.make_dir",
        ):
            safe_fs.make_dir(tmp_path / "unchecked")
    finally:
        safe_fs.make_dir = original


def test_mutation_check_invariant_6_catches_dry_run_write() -> None:
    cls = registry.get("fs.tag")
    original = cls.dry_run

    def bad_dry_run(self, ctx, payload):
        safe_fs.atomic_write(ctx.workspace / "mutated.txt", b"x")
        return None

    cls.dry_run = bad_dry_run
    try:
        with pytest.raises(AssertionError, match="dry-run purity"):
            check_invariant_6()
    finally:
        cls.dry_run = original


# ---------------------------------------------------------------------------
# ZipFile AST invariant: real file and mutation probe
# ---------------------------------------------------------------------------

def _zipfile_write_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    bytesio_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            value = node.value
            func = value.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name and name.endswith("BytesIO"):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bytesio_names.add(target.id)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr

        if name == "ZipFile":
            mode = None
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if mode not in ("w", "a", "x"):
                continue

            first = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg == "file":
                    first = kw.value

            ok = False
            if isinstance(first, ast.Call):
                first_func = first.func
                first_name = None
                if isinstance(first_func, ast.Name):
                    first_name = first_func.id
                elif isinstance(first_func, ast.Attribute):
                    first_name = first_func.attr
                if first_name and first_name.endswith("BytesIO"):
                    ok = True
            elif isinstance(first, ast.Name) and first.id in bytesio_names:
                ok = True

            if not ok:
                violations.append(
                    f"{path.name}:{node.lineno} ZipFile write mode on non-BytesIO"
                )

        if isinstance(func, ast.Attribute) and func.attr in ("extract", "extractall"):
            violations.append(f"{path.name}:{node.lineno} .{func.attr}() call")

    return violations


def test_mutation_zipfile_ast_guard_catches_path_write(tmp_path: Path) -> None:
    probe = tmp_path / "tmp_probe.py"
    probe.write_text(
        "import zipfile\n"
        "zipfile.ZipFile('fake_path.txt', 'w')\n",
        encoding="utf-8",
    )

    violations = _zipfile_write_violations(probe)
    assert violations
    assert any("ZipFile write mode" in item for item in violations)


def test_zipfile_ast_guard_real_archive_is_clean() -> None:
    archive_py = ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops" / "archive.py"
    assert _zipfile_write_violations(archive_py) == []
    arch.test_zipfile_never_opens_path_for_write()


# ---------------------------------------------------------------------------
# Golden integration really executes (not skip/xfail)
# ---------------------------------------------------------------------------

def test_golden_fs_ops_integration_executes(tmp_path: Path, monkeypatch) -> None:
    source = inspect.getsource(integ.test_golden_fs_ops_integration)
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_golden_fs_ops_integration"
    )
    for deco in fn.decorator_list:
        deco_src = ast.get_source_segment(source, deco) or ""
        assert "skip" not in deco_src.lower()
        assert "xfail" not in deco_src.lower()

    # integ.test_golden_fs_ops_integration re-uses our monkeypatch and
    # chdirs into tmp_path internally. Restore the real CWD before the
    # test body ends so the leak detector doesn't scan tmp_path.
    original_cwd = os.getcwd()
    try:
        integ.test_golden_fs_ops_integration(tmp_path, monkeypatch)
    finally:
        os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# Documentation checks: modules.md §4.0 / CONTRACT.md §Notes
# ---------------------------------------------------------------------------

def test_modules_md_documents_internal_helpers() -> None:
    text = (ROOT / "docs" / "modules.md").read_text(encoding="utf-8")
    assert "### 4.0 Internal helper modules" in text
    assert "#### 4.0.1 Invariant 5 interaction" in text
    assert "_common.py" in text
    assert "_base.py" in text
    assert "Concrete node classes must still declare" in text
    assert "run" in text and "dry_run" in text


def test_contract_md_notes_phase_5e() -> None:
    text = (ROOT / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
    assert "Invariants 6 and 7 — call-time enforcement" in text
    assert "ScopeFirstTracker" in text
    assert "Invariant 5 and underscore helper modules" in text
    assert "test_invariant_5_no_registered_nodes_in_underscore_modules" in text
    assert "ZipFile write-pattern AST invariant" in text
    assert "test_zipfile_never_opens_path_for_write" in text
    assert "@pytest.mark.skip stubs" not in text
