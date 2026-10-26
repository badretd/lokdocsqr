from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import LokError
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access import list_files, read_metadata, read_content
from lokdocsqr.nodes.access._base import AccessBinaryError, AccessLimitError
from lokdocsqr.nodes.access.list_files import ListFilesNode
from lokdocsqr.nodes.access.read_content import ReadContentNode
from lokdocsqr.nodes.access.read_metadata import ReadMetadataNode

ACCESS_CLASSES = (ListFilesNode, ReadMetadataNode, ReadContentNode)


def make_ctx(tmp_path, allow_symlinks=False):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path],
        writable=[tmp_path],
        allow_symlinks=allow_symlinks,
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
        audit=SimpleNamespace(append=lambda log: None),
    )


def _snapshot(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            st = path.stat()
        except OSError:
            continue
        result[path.relative_to(root).as_posix()] = (
            path.is_dir(),
            st.st_size,
            st.st_mtime_ns,
        )
    return result


def test_import_package_exports_modules():
    import lokdocsqr.nodes.access as access_pkg

    assert access_pkg.list_files is list_files
    assert access_pkg.read_metadata is read_metadata
    assert access_pkg.read_content is read_content


def test_registry_names_follow_package_node_pattern():
    for cls in ACCESS_CLASSES:
        module_leaf = cls.__module__.rsplit(".", 1)[-1]
        expected = f"access.{module_leaf}"
        assert cls.id == expected
        assert registry.get(expected) is cls
        assert re.fullmatch(r"[a-z_]+\.[a-z_]+", cls.id)


def test_run_and_dry_run_are_defined_directly_on_each_node():
    for cls in ACCESS_CLASSES:
        assert "run" in cls.__dict__, f"{cls.__name__} inherits run"
        assert "dry_run" in cls.__dict__, f"{cls.__name__} inherits dry_run"


def test_access_errors_are_lok_errors():
    assert issubclass(AccessLimitError, LokError)
    assert issubclass(AccessBinaryError, LokError)
    assert AccessLimitError.__module__.endswith("nodes.access._base")
    assert AccessBinaryError.__module__.endswith("nodes.access._base")


def test_access_errors_exported_from_package():
    import lokdocsqr.nodes.access as access_pkg

    assert getattr(access_pkg, "AccessLimitError", None) is AccessLimitError
    assert getattr(access_pkg, "AccessBinaryError", None) is AccessBinaryError


def test_read_bytes_limited_rejects_negative_limit(tmp_path):
    node = ListFilesNode()
    with pytest.raises(AccessLimitError):
        node._read_bytes_limited(tmp_path / "missing.bin", -1)


def test_read_bytes_limited_raises_on_oversize(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"x" * 11)
    node = ListFilesNode()
    with pytest.raises(AccessLimitError, match="exceeds max_bytes"):
        node._read_bytes_limited(path, 10)


def test_dry_run_does_not_change_fs_for_all_access_nodes(tmp_path):
    ctx = make_ctx(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")

    before = _snapshot(tmp_path)
    ListFilesNode().dry_run(ctx, {"path": str(root), "recursive": True})
    ReadMetadataNode().dry_run(
        ctx, {"paths": [str(root / "a.txt")], "include_hash": True}
    )
    ReadContentNode().dry_run(
        ctx, {"paths": [str(root / "a.txt")], "max_bytes": 10}
    )
    after = _snapshot(tmp_path)

    assert before == after
