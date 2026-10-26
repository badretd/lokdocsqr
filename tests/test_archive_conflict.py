"""Conflict-policy behaviour during extract."""
import os
import zipfile

import pytest

from lokdocsqr.nodes.fs_ops.archive import ArchiveNode


# CWD guard — see note in tests/test_archive_7z.py.
_ORIGINAL_CWD = os.getcwd()


@pytest.fixture(autouse=True)
def _restore_cwd():
    try:
        yield
    finally:
        os.chdir(_ORIGINAL_CWD)


def _make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_skip_policy_does_not_overwrite_and_logs_conflict_skip(ctx, tmp_path):
    archive = _make_zip(tmp_path / "a.zip", {"file.txt": b"new"})
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    existing = target_dir / "file.txt"
    existing.write_bytes(b"old")

    logs = ArchiveNode().run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
        "conflict_policy": "skip",
    })
    assert existing.read_bytes() == b"old"
    assert any("conflict_skip" in (log.reason or "") for log in logs), (
        f"expected 'conflict_skip' in ActionLog reasons, got: "
        f"{[log.reason for log in logs]}"
    )


def test_overwrite_policy_replaces_existing(ctx, tmp_path):
    archive = _make_zip(tmp_path / "a.zip", {"file.txt": b"new"})
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    (target_dir / "file.txt").write_bytes(b"old")

    ArchiveNode().run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
        "conflict_policy": "overwrite",
    })
    assert (target_dir / "file.txt").read_bytes() == b"new"


def test_rename_policy_keeps_both_files(ctx, tmp_path):
    archive = _make_zip(tmp_path / "a.zip", {"file.txt": b"new"})
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    (target_dir / "file.txt").write_bytes(b"old")

    ArchiveNode().run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
        "conflict_policy": "rename",
    })
    names = {p.name for p in target_dir.iterdir()}
    assert "file.txt" in names
    assert (target_dir / "file.txt").read_bytes() == b"old"
    assert len(names) >= 2, f"rename should yield a second file, got: {names}"