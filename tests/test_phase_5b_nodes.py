"""Subphase 5b: behavioural tests for fs.rename / fs.copy / fs.move.

Post-refactor: `render_template` / `render_and_check` now live in
`lokdocsqr.nodes.fs_ops._base`; patch targets for path resolution
point there. `safe_fs.*` / `shutil.*` still live in the concrete
node modules.
"""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

try:
    from lokdocsqr.nodes.fs_ops import copy as copy_mod
    from lokdocsqr.nodes.fs_ops import move as move_mod
    from lokdocsqr.nodes.fs_ops import rename as rename_mod
    from lokdocsqr.nodes.fs_ops import _base as base_mod
    from lokdocsqr.core.errors import SafeFsError, ScopeViolation
except BaseException as exc:  # pragma: no cover
    pytest.skip(
        f"5b node modules not importable standalone: {exc!r}",
        allow_module_level=True,
    )


def _ctx():
    ctx = MagicMock()
    ctx.scope.check = MagicMock()
    ctx.run_id = "run-test"
    ctx.automation_id = "auto-test"
    ctx.audit = MagicMock()
    return ctx


# ── describe() schemas ────────────────────────────────────────────
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


# ── rename: template edge cases ───────────────────────────────────
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


# ── copy: free-space precheck ────────────────────────────────────
def test_copy_insufficient_space_raises_before_copy(tmp_path):
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
            node.run(_ctx(), {"source": str(src)})
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


# ── move: delegation + dry-run purity ────────────────────────────
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


def test_move_cross_volume_flag_roundtrips():
    node = move_mod.MoveNode(cross_volume=False)
    assert node.cross_volume is False
    assert node.describe()["params"]["cross_volume"]["default"] is False