"""Tests for lokdocsqr.core.paths (non-hypothesis).

All tests use ``tmp_path`` so absolute-path cases are portable across POSIX
and Windows. The hardcoded POSIX strings ("/etc/passwd") that used to live
here were removed because ``Path("/etc/passwd").is_absolute()`` is ``False``
on Windows (``nt``): those paths are "root-relative", not absolute, and the
production code correctly let them through.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.paths import render_template, resolve_path


# ---------------------------------------------------------------------------
# resolve_path
# ---------------------------------------------------------------------------

def test_resolve_path_relative_inside(tmp_path: Path) -> None:
    result = resolve_path(tmp_path, "sub/file.txt")
    expected = os.path.abspath(str(tmp_path / "sub" / "file.txt"))
    assert str(result) == expected


def test_resolve_path_relative_inside_with_dot(tmp_path: Path) -> None:
    result = resolve_path(tmp_path, "./sub/./file.txt")
    expected = os.path.abspath(str(tmp_path / "sub" / "file.txt"))
    assert str(result) == expected


def test_resolve_path_absolute_outside_raises(tmp_path: Path) -> None:
    # ``tmp_path.parent`` is a real directory the caller does not own; using
    # it keeps the test portable (on Windows ``str(tmp_path)`` begins with a
    # drive letter, so the result is genuinely absolute).
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(ScopeViolation):
        resolve_path(tmp_path, outside)


def test_resolve_path_absolute_inside_is_ok(tmp_path: Path) -> None:
    # An absolute path that happens to live under base_dir is allowed.
    inside = tmp_path / "sub" / "file.txt"
    result = resolve_path(tmp_path, inside)
    assert result == Path(os.path.abspath(str(inside)))


def test_resolve_path_parent_escape_raises(tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        resolve_path(tmp_path, "../outside.txt")


def test_resolve_path_deep_parent_escape_raises(tmp_path: Path) -> None:
    with pytest.raises(ScopeViolation):
        resolve_path(tmp_path, "sub/../../outside.txt")


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------

def test_render_template_simple() -> None:
    assert render_template("foo/{name}.txt", {"name": "bar"}) == "foo/bar.txt"


def test_render_template_multiple_placeholders() -> None:
    result = render_template("{a}/{b}/{c}.txt", {"a": "x", "b": "y", "c": "z"})
    assert result == "x/y/z.txt"


def test_render_template_missing_key_preserved() -> None:
    assert render_template("foo/{missing}.txt", {}) == "foo/{missing}.txt"


def test_render_template_partial_missing_keys_preserved() -> None:
    result = render_template("{present}/{absent}.txt", {"present": "ok"})
    assert result == "ok/{absent}.txt"


def test_render_template_rejects_absolute(tmp_path: Path) -> None:
    # Portable absolute path: on POSIX /tmp/..., on Windows C:\...\Temp\...
    absolute = str(tmp_path / "file.txt")
    with pytest.raises(ScopeViolation):
        render_template(absolute, {})


def test_render_template_rejects_absolute_via_context(tmp_path: Path) -> None:
    absolute = str(tmp_path / "file.txt")
    with pytest.raises(ScopeViolation):
        render_template("{p}", {"p": absolute})


def test_render_template_rejects_leading_parent() -> None:
    with pytest.raises(ScopeViolation):
        render_template("../escape.txt", {})


def test_render_template_rejects_embedded_parent() -> None:
    with pytest.raises(ScopeViolation):
        render_template("foo/../../escape.txt", {})


def test_render_template_context_escape_raises() -> None:
    with pytest.raises(ScopeViolation):
        render_template("{p}", {"p": "../etc/passwd"})


def test_render_template_context_escape_deep_raises() -> None:
    with pytest.raises(ScopeViolation):
        render_template("safe/{p}", {"p": "a/../../../etc/passwd"})