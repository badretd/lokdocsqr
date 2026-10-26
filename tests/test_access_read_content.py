from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access._base import AccessBinaryError
from lokdocsqr.nodes.access.read_content import ReadContentNode


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


def test_utf8_text_default(tmp_path):
    path = tmp_path / "a.txt"
    text = "привет"
    path.write_text(text, encoding="utf-8")

    result = ReadContentNode().run(make_ctx(tmp_path), {"paths": str(path)})
    item = result["items"][0]
    assert item["text"] == text
    assert item["encoding"] == "utf-8"
    assert item["bytes"] == len(text.encode("utf-8"))
    assert item["truncated"] is False
    assert item["binary"] is False


def test_max_bytes_is_respected_per_item(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    big = tmp_path / "big.txt"
    big.write_bytes(b"a" * 11)

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(big)], "max_bytes": 10},
    )
    items = result["items"]
    assert len(items) == 2
    ok_item = [item for item in items if item["path"].endswith("ok.txt")][0]
    big_item = [item for item in items if item["path"].endswith("big.txt")][0]
    assert ok_item["text"] == "ok"
    assert big_item["bytes"] is None
    assert "exceeds max_bytes" in big_item["error"]


def test_binary_policy_error_raises(tmp_path):
    path = tmp_path / "bin.dat"
    path.write_bytes(b"\x00abc")
    with pytest.raises(AccessBinaryError):
        ReadContentNode().run(
            make_ctx(tmp_path),
            {"paths": str(path), "binary_policy": "error"},
        )


def test_binary_policy_skip(tmp_path):
    path = tmp_path / "bin.dat"
    path.write_bytes(b"\x00abc")
    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": str(path), "binary_policy": "skip"},
    )
    item = result["items"][0]
    assert item["binary"] is True
    assert "text" not in item
    assert "error" not in item


def test_binary_policy_base64_and_r3_boundary(tmp_path):
    path = tmp_path / "bin.dat"
    data = b"\x00" + b"a" * 9
    path.write_bytes(data)

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {
            "paths": str(path),
            "max_bytes": 10,
            "binary_policy": "base64",
        },
    )
    item = result["items"][0]
    assert item["bytes"] == 10
    assert item["binary"] is True
    assert item["text"] == base64.b64encode(data).decode("ascii")
    assert len(item["text"]) > 10
    assert "error" not in item


def test_exact_limit_and_zero_bytes_r2(tmp_path):
    exact = tmp_path / "exact.txt"
    exact.write_bytes(b"hello")
    zero = tmp_path / "zero.txt"
    zero.write_bytes(b"")

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": [str(exact), str(zero)], "max_bytes": 5},
    )
    exact_item = [
        item for item in result["items"] if item["path"].endswith("exact.txt")
    ][0]
    zero_item = [
        item for item in result["items"] if item["path"].endswith("zero.txt")
    ][0]
    assert exact_item["bytes"] == 5
    assert exact_item["truncated"] is False
    assert exact_item["text"] == "hello"
    assert zero_item["bytes"] == 0
    assert zero_item["truncated"] is False
    assert zero_item["text"] == ""


def test_toctou_read_returns_limit_bytes_marks_truncated(tmp_path, monkeypatch):
    path = tmp_path / "grow.txt"
    path.write_bytes(b"aaaa")

    def fake_read_bytes(p, limit=-1):
        return b"a" * 5

    monkeypatch.setattr(safe_fs, "read_bytes", fake_read_bytes)
    result = ReadContentNode().run(
        make_ctx(tmp_path), {"paths": str(path), "max_bytes": 5}
    )
    item = result["items"][0]
    assert "error" not in item
    assert item["bytes"] == 5
    assert item["truncated"] is True


def test_strict_decode_error_per_item(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe")

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(bad)], "errors": "strict"},
    )
    bad_item = [
        item for item in result["items"] if item["path"].endswith("bad.bin")
    ][0]
    assert "error" in bad_item
    assert "text" not in bad_item


def test_replace_decode_errors(tmp_path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe")

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": str(bad), "errors": "replace"},
    )
    item = result["items"][0]
    assert "error" not in item
    assert "\ufffd" in item["text"]


def test_per_item_isolation_ac17(tmp_path):
    ok = tmp_path / "ok.txt"
    ok.write_text("ok", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-content.txt"
    outside.write_text("outside", encoding="utf-8")

    result = ReadContentNode().run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(missing), str(outside)]},
    )
    assert len(result["items"]) == 3
    assert sum("error" in item for item in result["items"]) == 2
    assert sum("error" not in item for item in result["items"]) == 1


def test_scope_check_precedes_safe_fs_for_each_path(tmp_path, monkeypatch):
    path = tmp_path / "a.txt"
    path.write_text("a", encoding="utf-8")

    ctx = make_ctx(tmp_path)
    events = []
    original_check = ctx.scope.check

    def checking(p, write=False):
        events.append(("scope", str(p), write))
        return original_check(p, write=write)

    ctx.scope.check = checking

    original_stat = safe_fs.stat

    def stat(p):
        events.append(("safe_fs.stat", str(p)))
        return original_stat(p)

    monkeypatch.setattr(safe_fs, "stat", stat)

    original_read = safe_fs.read_bytes

    def read(p, limit=-1):
        events.append(("safe_fs.read_bytes", str(p), limit))
        return original_read(p, limit=limit)

    monkeypatch.setattr(safe_fs, "read_bytes", read)

    ReadContentNode().run(ctx, {"paths": str(path), "max_bytes": 10})

    target = str(path)
    scope_index = next(
        i for i, event in enumerate(events)
        if event[0] == "scope" and event[1] == target
    )
    fs_indices = [
        i for i, event in enumerate(events)
        if event[0].startswith("safe_fs") and event[1] == target
    ]
    assert fs_indices
    assert scope_index < min(fs_indices)
    assert all(event[2] is False for event in events if event[0] == "scope")


def test_invalid_binary_policy_raises(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("a", encoding="utf-8")
    with pytest.raises(NodeExecutionError, match="binary_policy"):
        ReadContentNode().run(
            make_ctx(tmp_path),
            {"paths": str(path), "binary_policy": "nope"},
        )


def test_no_paths_raises(tmp_path):
    with pytest.raises(NodeExecutionError, match="no paths"):
        ReadContentNode().run(make_ctx(tmp_path), {"paths": []})
