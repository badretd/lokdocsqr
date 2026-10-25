"""Zip-slip protection for fs.archive extract.

The mandatory negative test asserts that no file is created *outside*
target_dir — not merely that an exception was raised.
"""
import zipfile
from pathlib import Path

import pytest

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.nodes.fs_ops.archive import (
    ArchiveNode,
    _entry_is_safe,
    _resolve_entry_target,
)


def _make_zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize("name", [
    "",
    "../escape.txt",
    "../../escape.txt",
    "a/../../escape.txt",
    "/etc/passwd",
    "\\windows\\system32",
    "C:\\Windows\\system32",
])
def test_entry_is_safe_rejects_unsafe(name):
    assert _entry_is_safe(name) is False


@pytest.mark.parametrize("name", [
    "file.txt",
    "a/b/c.txt",
    "a\\b\\c.txt",
])
def test_entry_is_safe_accepts_normal(name):
    assert _entry_is_safe(name) is True


def test_resolve_entry_target_rejects_parent_traversal(tmp_path):
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    with pytest.raises(ScopeViolation):
        _resolve_entry_target(target_dir, "../../escape.txt")


def test_resolve_entry_target_inside_is_under_target(tmp_path):
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    final = _resolve_entry_target(target_dir, "sub/file.txt")
    assert final == (target_dir / "sub" / "file.txt").resolve()


def test_extract_zip_slip_does_not_create_escape_file(ctx, tmp_path):
    """Mandatory: archive contains '../../escape.txt'; nothing must land outside.

    target_dir is nested 3 levels deep, so a naive join would place the
    payload at tmp_path/a/escape.txt — well outside target_dir.
    """
    target_dir = tmp_path / "a" / "b" / "target"
    naive_escape = tmp_path / "a" / "escape.txt"
    archive = _make_zip(tmp_path / "evil.zip", {
        "../../escape.txt": b"PWNED",
        "safe.txt": b"ok",
    })

    node = ArchiveNode()
    with pytest.raises(ScopeViolation):
        node.run(ctx, {
            "operation": "extract",
            "source": str(archive),
            "target_dir": "a/b/target",
        })

    assert not naive_escape.exists(), (
        f"zip-slip leak: {naive_escape} was created on disk"
    )
    assert not (tmp_path / "escape.txt").exists()
    assert not target_dir.exists()
    if target_dir.exists():
        assert list(target_dir.rglob("*")) == []


def test_extract_zip_slip_absolute_entry_does_not_create_target_dir(ctx, tmp_path):
    target_dir = tmp_path / "a" / "target"
    archive = _make_zip(tmp_path / "abs.zip", {
        "/tmp/abs_escape.txt": b"PWNED",
    })
    node = ArchiveNode()
    with pytest.raises(ScopeViolation):
        node.run(ctx, {
            "operation": "extract",
            "source": str(archive),
            "target_dir": "a/target",
        })
    assert not target_dir.exists()
