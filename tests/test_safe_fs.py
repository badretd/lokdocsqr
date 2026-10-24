"""Hard tests for lokdocsqr.core.safe_fs."""
from __future__ import annotations

import errno
import json
import os
import types
from pathlib import Path

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import SafeFsError
from lokdocsqr.core.safe_fs import (
    atomic_append,
    atomic_write,
    copy_file,
    hash_file,
    list_dir,
    make_dir,
    quarantine,
    safe_move,
    safe_rename,
    trash,
)


# ---------------------------------------------------------------------------
# atomic_write / atomic_append
# ---------------------------------------------------------------------------

def test_atomic_write_text(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "out.txt"
    atomic_write(target, "hello world")
    assert target.read_text() == "hello world"


def test_atomic_write_bytes(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    payload = b"\x00\x01\x02\xff" * 1024
    atomic_write(target, payload)
    assert target.read_bytes() == payload


def test_atomic_write_no_temp_files_left(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write(target, "hello")
    entries = {p.name for p in tmp_path.iterdir()}
    assert entries == {"out.txt"}


def test_atomic_append_appends_existing(tmp_path: Path) -> None:
    target = tmp_path / "log.txt"
    atomic_append(target, "first\n")
    atomic_append(target, "second\n")
    assert target.read_text() == "first\nsecond\n"


# ---------------------------------------------------------------------------
# copy_file / safe_move
# ---------------------------------------------------------------------------

def test_copy_file_copies_content(tmp_path: Path) -> None:
    src = tmp_path / "src.bin"
    payload = b"copy me please" * 1000
    src.write_bytes(payload)
    dst = tmp_path / "out" / "dst.bin"
    copy_file(src, dst)
    assert dst.read_bytes() == payload
    assert src.exists()  # copy must not remove the source


def test_copy_file_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(SafeFsError):
        copy_file(tmp_path / "nope", tmp_path / "out")


def test_safe_move_within_filesystem(tmp_path: Path) -> None:
    src = tmp_path / "src.txt"
    dst = tmp_path / "sub" / "dst.txt"
    src.write_text("payload")
    safe_move(src, dst)
    assert not src.exists()
    assert dst.read_text() == "payload"


def test_safe_move_missing_source_raises(tmp_path: Path) -> None:
    with pytest.raises(SafeFsError):
        safe_move(tmp_path / "missing", tmp_path / "dst")


def test_safe_move_cross_device_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force EXDEV on the first os.replace so safe_move falls back to copy+unlink."""
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    payload = b"cross-device payload"
    src.write_bytes(payload)

    real_replace = os.replace
    calls = {"n": 0}

    def fake_replace(s, d):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_replace(s, d)

    monkeypatch.setattr(os, "replace", fake_replace)

    safe_move(src, dst)

    assert calls["n"] >= 1
    assert not src.exists()
    assert dst.read_bytes() == payload


# ---------------------------------------------------------------------------
# safe_rename
# ---------------------------------------------------------------------------

def test_safe_rename_basic(tmp_path: Path) -> None:
    src = tmp_path / "old.txt"
    src.write_text("hi")
    result = safe_rename(src, "new.txt")
    assert result == tmp_path / "new.txt"
    assert result.read_text() == "hi"
    assert not src.exists()


@pytest.mark.parametrize(
    "bad_name", ["", "with/slash", "with\\backslash", ".", ".."]
)
def test_safe_rename_rejects_invalid_names(tmp_path: Path, bad_name: str) -> None:
    src = tmp_path / "file.txt"
    src.write_text("x")
    with pytest.raises(SafeFsError):
        safe_rename(src, bad_name)


def test_safe_rename_target_exists_raises(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("a")
    (tmp_path / "b.txt").write_text("b")
    with pytest.raises(SafeFsError):
        safe_rename(src, "b.txt")


# ---------------------------------------------------------------------------
# make_dir / quarantine / trash
# ---------------------------------------------------------------------------

def test_make_dir_creates_nested(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    make_dir(nested)
    assert nested.is_dir()
    make_dir(nested)  # idempotent


def test_quarantine_moves_and_writes_sidecar(tmp_path: Path) -> None:
    trash_dir = tmp_path / "trash"
    target = tmp_path / "file.txt"
    target.write_text("payload")

    result = quarantine(target, trash_dir, reason="test-reason")

    assert not target.exists()
    assert result.exists()
    assert result.read_text() == "payload"

    sidecar = result.parent / (result.name + ".meta.json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["original_path"] == str(target.absolute())
    assert meta["reason"] == "test-reason"
    assert "timestamp" in meta


def test_quarantine_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(SafeFsError):
        quarantine(tmp_path / "nope", tmp_path / "trash")


def test_quarantine_collision_creates_unique_names(tmp_path, monkeypatch):
    trash = tmp_path / "trash"

    src1 = tmp_path / "a.txt"
    src1.write_text("one")

    src2 = tmp_path / "sub" / "a.txt"
    src2.parent.mkdir()
    src2.write_text("two")

    # Force the same timestamp for both calls → same base_target → collision.
    FIXED_NS = 1_700_000_000_000_000_000
    monkeypatch.setattr(safe_fs.time, "time_ns", lambda: FIXED_NS)

    t1 = safe_fs.quarantine(src1, trash)
    t2 = safe_fs.quarantine(src2, trash)

    assert t1 != t2
    assert t1.name == f"{FIXED_NS}_a.txt"
    assert t2.name == f"{FIXED_NS}_a_1.txt"
    assert t1.read_text() == "one"
    assert t2.read_text() == "two"
    assert not src1.exists()
    assert not src2.exists()


def test_trash_uses_trash_reason(tmp_path: Path) -> None:
    trash_dir = tmp_path / "trash"
    target = tmp_path / "file.txt"
    target.write_text("x")
    result = trash(target, trash_dir)
    sidecar = result.parent / (result.name + ".meta.json")
    meta = json.loads(sidecar.read_text())
    assert meta["reason"] == "trashed"


# ---------------------------------------------------------------------------
# hash_file / list_dir
# ---------------------------------------------------------------------------

def test_hash_file_sha256(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"hello")
    assert (
        hash_file(target)
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_hash_file_unknown_algorithm_raises(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"hello")
    with pytest.raises(SafeFsError):
        hash_file(target, algorithm="not-a-real-hash")


def test_list_dir_sorted(tmp_path: Path) -> None:
    for name in ["c.txt", "a.txt", "b.txt"]:
        (tmp_path / name).write_text(name)
    result = list_dir(tmp_path)
    assert [p.name for p in result] == ["a.txt", "b.txt", "c.txt"]
