from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import NodeExecutionError, ScopeViolation
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.list_files import ListFilesNode


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


def test_defaults_and_basic_glob_extensions_recursive(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.log").write_text("bb", encoding="utf-8")
    (root / "sub" / "c.txt").write_text("ccc", encoding="utf-8")
    (root / "sub" / "d.PDF").write_text("dddd", encoding="utf-8")

    node = ListFilesNode()
    assert node.follow_symlinks is False

    result = node.run(
        make_ctx(tmp_path),
        {
            "path": str(root),
            "pattern": "*",
            "recursive": True,
            "extensions": ["txt"],
        },
    )
    names = {Path(item["path"]).name for item in result["files"]}
    assert names == {"a.txt", "c.txt"}

    result_pdf = node.run(
        make_ctx(tmp_path),
        {
            "path": str(root),
            "recursive": True,
            "extensions": ["pdf"],
        },
    )
    assert {Path(item["path"]).name for item in result_pdf["files"]} == {"d.PDF"}


def test_min_max_size_filters(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "tiny.txt").write_bytes(b"a")
    (root / "mid.txt").write_bytes(b"abcd")
    (root / "big.txt").write_bytes(b"a" * 10)

    result = ListFilesNode().run(
        make_ctx(tmp_path),
        {"path": str(root), "min_size": 2, "max_size": 5},
    )
    assert {Path(item["path"]).name for item in result["files"]} == {"mid.txt"}


def test_include_dirs_and_recursive_false(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("b", encoding="utf-8")

    result = ListFilesNode().run(
        make_ctx(tmp_path),
        {
            "path": str(root),
            "include_dirs": True,
            "recursive": False,
        },
    )
    items = {Path(item["path"]).name: item for item in result["files"]}
    assert "sub" in items and items["sub"]["is_dir"] is True
    assert "a.txt" in items
    assert "b.txt" not in items


def test_limit_more_than_limit_sets_truncated(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.txt").write_text("x", encoding="utf-8")

    result = ListFilesNode().run(
        make_ctx(tmp_path), {"path": str(root), "limit": 2}
    )
    assert len(result["files"]) == 2
    assert result["truncated"] is True


def test_limit_exactly_limit_does_not_set_truncated(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for index in range(2):
        (root / f"{index}.txt").write_text("x", encoding="utf-8")

    result = ListFilesNode().run(
        make_ctx(tmp_path), {"path": str(root), "limit": 2}
    )
    assert len(result["files"]) == 2
    assert result["truncated"] is False


def test_limit_must_be_positive(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(NodeExecutionError, match="limit"):
        ListFilesNode().run(
            make_ctx(tmp_path), {"path": str(root), "limit": 0}
        )


def test_root_must_be_directory(tmp_path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(NodeExecutionError, match="not a directory"):
        ListFilesNode().run(make_ctx(tmp_path), {"path": str(file_path)})


def test_out_of_scope_root_is_rejected(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside-list-files"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ScopeViolation):
        ListFilesNode().run(make_ctx(tmp_path), {"path": str(outside)})


def test_scope_check_called_for_root_and_every_result(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")

    ctx = make_ctx(tmp_path)
    calls = []
    original_check = ctx.scope.check

    def checking(path, write=False):
        calls.append((Path(path).resolve(), write))
        return original_check(path, write=write)

    ctx.scope.check = checking
    result = ListFilesNode().run(ctx, {"path": str(root)})

    assert all(write is False for _path, write in calls)
    checked = {path for path, _write in calls}
    assert root.resolve() in checked
    for item in result["files"]:
        assert Path(item["path"]).resolve() in checked


def test_symlink_is_not_followed_by_default_and_node_does_not_crash(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform")

    result = ListFilesNode().run(
        make_ctx(tmp_path),
        {
            "path": str(root),
            "include_dirs": True,
            "follow_symlinks": False,
        },
    )
    assert all(
        Path(item["path"]).name != "link.txt" for item in result["files"]
    )
