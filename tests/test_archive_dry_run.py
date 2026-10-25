"""dry_run() must not touch the filesystem and must never open ZipFile for write."""
import zipfile
from pathlib import Path

import pytest

from lokdocsqr.nodes.fs_ops.archive import ArchiveNode


def _snapshot(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        if p.is_dir():
            out[rel] = ("dir",)
        else:
            out[rel] = ("file", p.stat().st_size, p.read_bytes())
    return out


def test_dry_run_create_does_not_touch_tree(ctx, tmp_path):
    src = tmp_path / "a.txt"
    src.write_bytes(b"hello")
    target = tmp_path / "out.zip"

    before = _snapshot(tmp_path)
    ArchiveNode().dry_run(ctx, {
        "operation": "create",
        "sources": [str(src)],
        "target": "out.zip",
        "format": "zip",
    })
    assert _snapshot(tmp_path) == before
    assert not target.exists()


def test_dry_run_extract_does_not_touch_tree(ctx, tmp_path):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("f.txt", b"hello")
    target_dir = tmp_path / "out"

    before = _snapshot(tmp_path)
    logs = ArchiveNode().dry_run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
    })
    assert _snapshot(tmp_path) == before
    assert not target_dir.exists()
    assert logs, "dry_run must return ActionLog entries"
    for log in logs:
        assert log.dry_run is True
    assert any(log.target.endswith("f.txt") for log in logs)


def test_dry_run_extract_never_opens_zip_for_write(ctx, tmp_path, monkeypatch):
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("f.txt", b"hello")
    target_dir = tmp_path / "out"

    modes_seen: list[str] = []
    real_init = zipfile.ZipFile.__init__

    def spy_init(self, file, mode="r", *args, **kwargs):
        modes_seen.append(mode)
        return real_init(self, file, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "__init__", spy_init)

    ArchiveNode().dry_run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
    })

    assert modes_seen, "ZipFile must have been opened (in read mode)"
    assert "w" not in modes_seen, f"ZipFile opened in 'w' during dry_run: {modes_seen}"
    assert "a" not in modes_seen, f"ZipFile opened in 'a' during dry_run: {modes_seen}"
    assert all(m == "r" for m in modes_seen), f"unexpected modes: {modes_seen}"


def test_dry_run_extract_never_calls_mutators(ctx, tmp_path, monkeypatch):
    import lokdocsqr.core.safe_fs as safe_fs_mod

    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("f.txt", b"hello")
    target_dir = tmp_path / "out"

    calls: list[str] = []
    real_aw = safe_fs_mod.atomic_write
    real_md = safe_fs_mod.make_dir

    def spy_aw(*a, **kw):
        calls.append("atomic_write")
        return real_aw(*a, **kw)

    def spy_md(*a, **kw):
        calls.append("make_dir")
        return real_md(*a, **kw)

    monkeypatch.setattr(safe_fs_mod, "atomic_write", spy_aw)
    monkeypatch.setattr(safe_fs_mod, "make_dir", spy_md)

    ArchiveNode().dry_run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "out",
    })
    assert calls == [], f"dry_run must not mutate: {calls}"
