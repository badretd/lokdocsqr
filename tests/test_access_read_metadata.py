from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.read_metadata import ReadMetadataNode


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
    )


def test_fields_and_sha256(tmp_path):
    path = tmp_path / "a.txt"
    content = b"hello"
    path.write_bytes(content)

    result = ReadMetadataNode().run(
        make_ctx(tmp_path),
        {"paths": str(path), "include_hash": True},
    )
    item = result["items"][0]

    for field in ("path", "size", "mtime", "mode", "is_dir", "is_symlink"):
        assert field in item
    assert item["path"] == str(path.resolve())
    assert item["size"] == len(content)
    assert isinstance(item["mtime"], float)
    assert isinstance(item["mode"], int)
    assert item["is_dir"] is False
    assert item["is_symlink"] is False
    assert item["hash"] == hashlib.sha256(content).hexdigest()


def test_dry_run_does_not_hash(tmp_path, monkeypatch):
    path = tmp_path / "a.txt"
    path.write_text("hello", encoding="utf-8")

    def boom(*args, **kwargs):
        raise AssertionError("hash_file called during dry_run")

    monkeypatch.setattr(safe_fs, "hash_file", boom)
    result = ReadMetadataNode().dry_run(
        make_ctx(tmp_path),
        {"paths": str(path), "include_hash": True},
    )
    assert result["items"][0]["hash"] is None


def test_missing_file_is_per_item_error(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    missing = tmp_path / "missing.txt"

    result = ReadMetadataNode().run(
        make_ctx(tmp_path), {"paths": [str(ok), str(missing)]}
    )
    assert len(result["items"]) == 2
    missing_item = [
        item for item in result["items"] if item["path"].endswith("missing.txt")
    ][0]
    assert "error" in missing_item
    for field in ("size", "mtime", "mode", "is_dir", "is_symlink"):
        assert missing_item[field] is None


def test_all_missing_raises_node_execution_error(tmp_path):
    missing = tmp_path / "missing.txt"
    with pytest.raises(NodeExecutionError, match="all paths failed"):
        ReadMetadataNode().run(make_ctx(tmp_path), {"paths": str(missing)})


def test_out_of_scope_is_per_item_error_with_ok(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-meta.txt"
    outside.write_text("outside", encoding="utf-8")

    result = ReadMetadataNode().run(
        make_ctx(tmp_path), {"paths": [str(ok), str(outside)]}
    )
    assert len(result["items"]) == 2
    errors = [item for item in result["items"] if "error" in item]
    ok_items = [item for item in result["items"] if "error" not in item]
    assert len(errors) == 1
    assert len(ok_items) == 1


def test_max_bytes_for_hash(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 10)

    result = ReadMetadataNode().run(
        make_ctx(tmp_path),
        {
            "paths": str(path),
            "include_hash": True,
            "max_bytes_for_hash": 5,
        },
    )
    item = result["items"][0]
    assert item["hash"] is None
    assert "hash_error" in item
    assert "exceeds" in item["hash_error"]


def test_scope_check_precedes_first_path_stat(tmp_path, monkeypatch):
    path = tmp_path / "a.txt"
    path.write_text("a", encoding="utf-8")

    ctx = make_ctx(tmp_path)
    events = []
    original_check = ctx.scope.check

    def checking(p, write=False):
        events.append(("scope", str(p), write))
        return original_check(p, write=write)

    ctx.scope.check = checking

    original_stat = Path.stat

    def stat(self, *args, **kwargs):
        events.append(("stat", str(self)))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    ReadMetadataNode().run(ctx, {"paths": str(path)})

    target = str(path)
    scope_index = next(
        i for i, event in enumerate(events)
        if event[0] == "scope" and event[1] == target
    )
    stat_index = next(
        i for i, event in enumerate(events)
        if event[0] == "stat" and event[1] == target
    )
    assert scope_index < stat_index
    assert all(event[2] is False for event in events if event[0] == "scope")


def test_per_item_isolation_ok_missing_outside(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-meta2.txt"
    outside.write_text("outside", encoding="utf-8")

    result = ReadMetadataNode().run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(missing), str(outside)]},
    )
    assert len(result["items"]) == 3
    assert sum("error" in item for item in result["items"]) == 2
    assert sum("error" not in item for item in result["items"]) == 1
