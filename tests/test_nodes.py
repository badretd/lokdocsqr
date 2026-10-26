"""Phase 5 — behavioural tests for fs_ops nodes.

Merged from:
  - Subphase 5b: fs.rename / fs.copy / fs.move behavioural tests
  - Phase 5a: per-node behavior (make_dir, write_file), render_and_check,
    invariants.

Runtime tests use MagicMock for ctx and monkeypatch for safe_fs spies. The
FS-purity and ordering tests mock `build_action_log` because they don't test
log construction (see test_build_action_log_constructs_action_log for that).

Post-refactor note (FsTemplateNode / _base.py):
    `render_template` and `render_and_check` are now imported/used by
    `lokdocsqr.nodes.fs_ops._base`, not by the concrete node modules.
    Patch targets for path resolution must therefore point at `_base`,
    not at `rename` / `copy` / `move`. `safe_fs.*` and `shutil.*` calls
    still live in the concrete modules (inside `_execute` /
    `_assert_free_space`), so their patch targets are unchanged.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

try:
    from lokdocsqr.nodes.fs_ops import copy as copy_mod
    from lokdocsqr.nodes.fs_ops import move as move_mod
    from lokdocsqr.nodes.fs_ops import rename as rename_mod
    from lokdocsqr.nodes.fs_ops import _base as base_mod
    from lokdocsqr.core.errors import SafeFsError, ScopeViolation
    from lokdocsqr.core.models import Risk
    from lokdocsqr.nodes.fs_ops._common import (
        assert_dry_run_no_side_effects,
        build_action_log,
        render_and_check,
    )
    from lokdocsqr.nodes.fs_ops.make_dir import MakeDirNode
    from lokdocsqr.nodes.fs_ops.write_file import WriteFileNode
except ImportError as exc:  # pragma: no cover
    pytest.skip(
        f"fs_ops nodes not installed: {exc!r}",
        allow_module_level=True,
    )


# ══════════════════════════════════════════════════════════════════
# Shared test infrastructure
# ══════════════════════════════════════════════════════════════════

def _ctx():
    """Minimal ctx for rename / copy / move behavioural tests."""
    ctx = MagicMock()
    ctx.scope.check = MagicMock()
    ctx.run_id = "run-test"
    ctx.automation_id = "auto-test"
    ctx.audit = MagicMock()
    return ctx


def _make_ctx(*, allow_scope: bool = True, roots: list[Path] | None = None) -> MagicMock:
    """Richer ctx for make_dir / write_file / render_and_check tests."""
    ctx = MagicMock()
    ctx.run_id = "run-test"
    ctx.automation_id = "auto-test"
    ctx.scope.check.return_value = allow_scope
    ctx.scope.roots = list(roots or [])
    ctx.audit = []
    return ctx


def _payload(node_cls: type, path: str, **extra) -> dict:
    payload = {"path_template": path}
    if node_cls is WriteFileNode:
        payload["content"] = "hello"
    payload.update(extra)
    return payload


_NODES = [
    pytest.param(MakeDirNode, id="make_dir"),
    pytest.param(WriteFileNode, id="write_file"),
]


@pytest.fixture
def render_template_identity(monkeypatch):
    # `FsNode._resolve` → `_common.render_and_check` → `_common.render_template`.
    monkeypatch.setattr(
        "lokdocsqr.nodes.fs_ops._common.render_template",
        lambda template, variables: template,
    )


@pytest.fixture
def safe_fs_spy(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def _spy(name):
        def _impl(*args, **kwargs):
            calls.append((name, args))
        return _impl

    for name in ("make_dir", "atomic_write", "atomic_append"):
        monkeypatch.setattr(f"lokdocsqr.core.safe_fs.{name}", _spy(name))
    return calls


@pytest.fixture
def mute_build_action_log(monkeypatch):
    """Mute build_action_log for tests that verify FS behavior, not logging.

    Also mutes the module-level import in make_dir / write_file so the
    ActionLog(model) bug doesn't cascade into these assertions.
    """
    for mod in ("make_dir", "write_file"):
        monkeypatch.setattr(
            f"lokdocsqr.nodes.fs_ops.{mod}.build_action_log",
            lambda **kwargs: None,
        )


# ══════════════════════════════════════════════════════════════════
# Smoke: ActionLog / build_action_log contract
# ══════════════════════════════════════════════════════════════════

def test_build_action_log_constructs_action_log() -> None:
    """build_action_log must be able to construct the model it targets.

    If this fails with `unexpected keyword argument 'details'`, then
    core.models.ActionLog and _common.build_action_log are out of sync —
    a code fix, not a test fix. Check `inspect.signature(ActionLog)`.
    """
    log = build_action_log(
        run_id="r", automation_id="a", node_id="fs.make_dir",
        action_type="make_dir", source=None, target="x",
        reason="test", confidence=1.0, risk=Risk.LOW, dry_run=True,
    )
    assert log is not None


# ══════════════════════════════════════════════════════════════════
# describe() schemas
# ══════════════════════════════════════════════════════════════════

def test_rename_describe():
    desc = rename_mod.RenameNode().describe()
    assert desc["id"] == "fs.rename"
    assert desc["title"] == "Rename File(s)"
    assert {"template", "conflict_policy"} <= set(desc["params"])


def test_copy_describe():
    desc = copy_mod.CopyNode().describe()
    assert desc["id"] == "fs.copy"
    assert {"template", "conflict_policy", "check_free_space"} <= set(desc["params"])


def test_move_describe():
    desc = move_mod.MoveNode().describe()
    assert desc["id"] == "fs.move"
    assert {"template", "conflict_policy", "cross_volume"} <= set(desc["params"])


# ══════════════════════════════════════════════════════════════════
# Invariant 5 — run / dry_run declared per class
# (see note at the bottom of this file for the FsTemplateNode case)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("node_cls", _NODES)
def test_run_and_dry_run_declared_per_class(node_cls: type) -> None:
    assert "run" in node_cls.__dict__
    assert "dry_run" in node_cls.__dict__
    assert callable(node_cls.__dict__["run"])
    assert callable(node_cls.__dict__["dry_run"])


# ══════════════════════════════════════════════════════════════════
# render_and_check (items 3 & 4)
# ══════════════════════════════════════════════════════════════════

def test_render_and_check_rejects_dotdot_after_render(render_template_identity) -> None:
    ctx = _make_ctx()
    with pytest.raises(ScopeViolation):
        render_and_check(ctx, "../escape", {})
    ctx.scope.check.assert_not_called()


def test_render_and_check_rejects_out_of_scope() -> None:
    ctx = _make_ctx(allow_scope=False)
    with pytest.raises(ScopeViolation):
        render_and_check(ctx, "safe/file", {})
    ctx.scope.check.assert_called_once()


def test_render_and_check_returns_path_on_valid() -> None:
    ctx = _make_ctx(allow_scope=True)
    result = render_and_check(ctx, "safe/file", {})
    assert isinstance(result, Path)
    # Path == Path normalizes separators on Windows; str == str would not.
    assert result == Path("safe/file")


# ══════════════════════════════════════════════════════════════════
# make_dir / write_file: run ordering & scope guard (items 3, 4, 7)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("node_cls", _NODES)
def test_run_rejects_dotdot_without_mutation(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True)
    with pytest.raises(ScopeViolation):
        node_cls().run(ctx, _payload(node_cls, "../escape"))
    assert safe_fs_spy == []
    assert ctx.audit == []


@pytest.mark.parametrize("node_cls", _NODES)
def test_run_rejects_out_of_scope_without_mutation(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=False)
    with pytest.raises(ScopeViolation):
        node_cls().run(ctx, _payload(node_cls, "outside/file"))
    assert safe_fs_spy == []
    assert ctx.audit == []


@pytest.mark.parametrize("node_cls", _NODES)
def test_run_scope_check_precedes_mutation(
    node_cls, monkeypatch, render_template_identity, mute_build_action_log,
) -> None:
    calls: list[tuple[str, str]] = []
    ctx = _make_ctx(allow_scope=True)
    ctx.scope.check.side_effect = lambda p: (calls.append(("scope.check", str(p))), True)[1]

    def _spy(name):
        def _impl(*args, **kwargs):
            calls.append((f"safe_fs.{name}", str(args[0])))
        return _impl
    for name in ("make_dir", "atomic_write", "atomic_append"):
        monkeypatch.setattr(f"lokdocsqr.core.safe_fs.{name}", _spy(name))

    if node_cls is WriteFileNode:
        payload = _payload(node_cls, "safe/file", create_parents=False)
    else:
        payload = _payload(node_cls, "safe/dir")
    node_cls().run(ctx, payload)

    assert calls and calls[0][0] == "scope.check", calls
    first_safe_fs = next(i for i, (n, _) in enumerate(calls) if n.startswith("safe_fs."))
    assert first_safe_fs > 0
    assert all(calls[i][0] == "scope.check" for i in range(first_safe_fs)), calls


# ══════════════════════════════════════════════════════════════════
# make_dir / write_file: dry_run purity (item 1)
# ══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("node_cls", _NODES)
def test_dry_run_no_side_effects_snapshot(
    node_cls, tmp_path, render_template_identity, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True, roots=[tmp_path])
    payload = _payload(node_cls, str(tmp_path / "would_create.txt"))
    assert_dry_run_no_side_effects(ctx, node_cls().dry_run, ctx, payload)
    assert not (tmp_path / "would_create.txt").exists()


@pytest.mark.parametrize("node_cls", _NODES)
def test_dry_run_does_not_call_safe_fs(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True)
    node_cls().dry_run(ctx, _payload(node_cls, "any/path"))
    assert safe_fs_spy == []


# ══════════════════════════════════════════════════════════════════
# fs.rename: template edge cases
#
# `FsTemplateNode._render_target` (in _base.py) calls `render_template`,
# so patch targets move from `rename_mod` to `base_mod`.
# `rename_mod.safe_fs.safe_rename` / `.safe_move` still live in
# `rename.py::_execute` — unchanged targets.
# ══════════════════════════════════════════════════════════════════

def test_rename_empty_render_raises(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="X")
    with patch.object(base_mod, "render_template", return_value="   "):
        with pytest.raises(ValueError, match="rendered to empty"):
            node.dry_run(_ctx(), {"source": str(src)})


def test_rename_dotdot_in_render_raises(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="X")
    with patch.object(
        base_mod, "render_template", return_value="../evil.txt"
    ):
        with pytest.raises(ScopeViolation):
            node.dry_run(_ctx(), {"source": str(src)})


def test_rename_no_change_returns_skip(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="{name}{ext}")
    with patch.object(base_mod, "render_template", return_value="foo.txt"):
        log = node.dry_run(_ctx(), {"source": str(src)})
    assert log.reason == "no_change"
    assert log.action_type == "rename"
    assert log.dry_run is True


def test_rename_appends_source_suffix_when_missing(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value="bar"
        ))
        mock_ren = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_rename"
        ))
        node.run(_ctx(), {"source": str(src)})
    mock_ren.assert_called_once()
    args = mock_ren.call_args[0]
    assert args[0] == src
    assert args[1] == "bar.txt"


def test_rename_same_parent_uses_safe_rename(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value="bar.txt"
        ))
        mock_ren = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_rename"
        ))
        mock_mov = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_move"
        ))
        node.run(_ctx(), {"source": str(src)})
    mock_ren.assert_called_once()
    mock_mov.assert_not_called()


def test_rename_different_parent_uses_safe_move(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    target = tmp_path / "sub" / "bar.txt"
    node = rename_mod.RenameNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        mock_ren = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_rename"
        ))
        mock_mov = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_move"
        ))
        node.run(_ctx(), {"source": str(src)})
    mock_mov.assert_called_once()
    mock_ren.assert_not_called()


def test_rename_dry_run_does_not_mutate(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    node = rename_mod.RenameNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value="bar.txt"
        ))
        mock_ren = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_rename"
        ))
        mock_mov = st.enter_context(patch.object(
            rename_mod.safe_fs, "safe_move"
        ))
        log = node.dry_run(_ctx(), {"source": str(src)})
    mock_ren.assert_not_called()
    mock_mov.assert_not_called()
    assert log.dry_run is True


# ══════════════════════════════════════════════════════════════════
# fs.copy: free-space precheck
#
# Path resolution goes through `_base.render_template`; the free-space
# probe (`shutil.disk_usage`) and the mutation (`safe_fs.copy_file`)
# stay in `copy.py`.
# ══════════════════════════════════════════════════════════════════

def test_copy_insufficient_space_raises_before_copy(tmp_path):
    src = tmp_path / "source.bin"
    src.write_bytes(b"x" * 100)
    target = tmp_path / "target.bin"
    node = copy_mod.CopyNode(template="X", check_free_space=True)
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        mock_du = st.enter_context(patch.object(
            copy_mod.shutil, "disk_usage",
            return_value=SimpleNamespace(total=1000, used=950, free=50),
        ))
        mock_copy = st.enter_context(patch.object(
            copy_mod.safe_fs, "copy_file"
        ))
        with pytest.raises(SafeFsError, match="insufficient_space"):
            node.run(_ctx(), {"source": str(src)})
    mock_du.assert_called_once()
    mock_copy.assert_not_called()


def test_copy_dry_run_still_probes_free_space(tmp_path):
    src = tmp_path / "source.bin"
    src.write_bytes(b"x" * 100)
    target = tmp_path / "target.bin"
    node = copy_mod.CopyNode(template="X", check_free_space=True)
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        st.enter_context(patch.object(
            copy_mod.shutil, "disk_usage",
            return_value=SimpleNamespace(total=1000, used=950, free=50),
        ))
        mock_copy = st.enter_context(patch.object(
            copy_mod.safe_fs, "copy_file"
        ))
        with pytest.raises(SafeFsError, match="insufficient_space"):
            node.dry_run(_ctx(), {"source": str(src)})
    mock_copy.assert_not_called()


def test_copy_skips_free_space_when_disabled(tmp_path):
    src = tmp_path / "source.bin"
    src.write_bytes(b"x" * 100)
    target = tmp_path / "target.bin"
    node = copy_mod.CopyNode(template="X", check_free_space=False)
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        mock_du = st.enter_context(patch.object(
            copy_mod.shutil, "disk_usage"
        ))
        mock_copy = st.enter_context(patch.object(
            copy_mod.safe_fs, "copy_file"
        ))
        node.run(_ctx(), {"source": str(src)})
    mock_du.assert_not_called()
    mock_copy.assert_called_once()


# ══════════════════════════════════════════════════════════════════
# fs.move: delegation + dry-run purity
# ══════════════════════════════════════════════════════════════════

def test_move_uses_safe_move(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    target = tmp_path / "bar.txt"
    node = move_mod.MoveNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        mock_mov = st.enter_context(patch.object(
            move_mod.safe_fs, "safe_move"
        ))
        node.run(_ctx(), {"source": str(src)})
    mock_mov.assert_called_once()


def test_move_dry_run_does_not_mutate(tmp_path):
    src = tmp_path / "foo.txt"
    src.write_text("x")
    target = tmp_path / "bar.txt"
    node = move_mod.MoveNode(template="X")
    with ExitStack() as st:
        st.enter_context(patch.object(
            base_mod, "render_template", return_value=str(target)
        ))
        mock_mov = st.enter_context(patch.object(
            move_mod.safe_fs, "safe_move"
        ))
        log = node.dry_run(_ctx(), {"source": str(src)})
    mock_mov.assert_not_called()
    assert log.dry_run is True


# ══════════════════════════════════════════════════════════════════
# fs.move: reserved cross_volume flag is stored
# ══════════════════════════════════════════════════════════════════

def test_move_cross_volume_flag_roundtrips():
    node = move_mod.MoveNode(cross_volume=False)
    assert node.cross_volume is False
    assert node.describe()["params"]["cross_volume"]["default"] is False