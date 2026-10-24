"""Tests for lokdocsqr.core.scope.ScopeManager."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.scope import ScopeManager


def _scope(**kwargs):
    """Build a duck-typed scope object accepted by ScopeManager."""
    return SimpleNamespace(**kwargs)


def test_scope_accepts_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inner = root / "sub" / "file.txt"
    inner.parent.mkdir()
    inner.write_text("x")

    manager = ScopeManager(_scope(roots=[root]))
    assert manager.check(inner) == inner.resolve()


def test_scope_rejects_path_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("x")

    manager = ScopeManager(_scope(roots=[root]))
    with pytest.raises(ScopeViolation):
        manager.check(outside)


def test_scope_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    manager = ScopeManager(_scope(roots=[root]))
    with pytest.raises(ScopeViolation):
        manager.check(Path("..") / "etc" / "passwd")


def test_scope_rejects_symlink_by_default(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("x")
    link = root / "link.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks not supported on this platform")

    manager = ScopeManager(_scope(roots=[root]))
    with pytest.raises(ScopeViolation):
        manager.check(link)


def test_scope_allows_symlink_when_configured(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    real = root / "real.txt"
    real.write_text("x")
    link = root / "link.txt"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks not supported on this platform")

    manager = ScopeManager(_scope(roots=[root], allow_symlinks=True))
    resolved = manager.check(link)
    assert resolved == real.resolve()


def test_scope_write_requires_writable_roots(tmp_path: Path) -> None:
    readable = tmp_path / "read"
    readable.mkdir()
    writable = tmp_path / "write"
    writable.mkdir()
    target = readable / "file.txt"
    target.write_text("x")

    manager = ScopeManager(_scope(roots=[readable], writable=[writable]))
    assert manager.is_safe(target, write=False) is True
    assert manager.is_safe(target, write=True) is False
    with pytest.raises(ScopeViolation):
        manager.check(target, write=True)


def test_scope_writable_defaults_to_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "file.txt"
    target.write_text("x")

    manager = ScopeManager(_scope(roots=[root]))
    assert manager.is_safe(target, write=True) is True


def test_scope_is_safe_returns_bool(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "f.txt"
    inside.write_text("x")
    outside = tmp_path / "outside.txt"
    outside.write_text("x")

    manager = ScopeManager(_scope(roots=[root]))
    assert manager.is_safe(inside) is True
    assert manager.is_safe(outside) is False
