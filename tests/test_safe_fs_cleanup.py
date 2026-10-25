"""Tests for lokdocsqr.core.safe_fs.cleanup_stale_temp_files."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import SafeFsError


def _touch(path: Path, *, age_seconds: float) -> None:
    """Create ``path`` and backdate its mtime by ``age_seconds``."""
    path.write_text("x", encoding="utf-8")
    target = time.time() - age_seconds
    os.utime(path, (target, target))


def test_cleanup_returns_empty_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert safe_fs.cleanup_stale_temp_files(missing) == []


def test_cleanup_returns_empty_when_root_is_file(tmp_path: Path) -> None:
    root = tmp_path / "not_a_dir.txt"
    root.write_text("x", encoding="utf-8")
    assert safe_fs.cleanup_stale_temp_files(root) == []


def test_cleanup_with_no_matches(tmp_path: Path) -> None:
    (tmp_path / "regular.txt").write_text("x", encoding="utf-8")
    assert safe_fs.cleanup_stale_temp_files(tmp_path) == []


def test_cleanup_removes_old_temp_files(tmp_path: Path) -> None:
    old = tmp_path / ".data.txt.tmp.123.456"
    _touch(old, age_seconds=7200)
    other = tmp_path / ".other.tmp.1.2"
    _touch(other, age_seconds=7200)

    removed = safe_fs.cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)

    assert set(removed) == {old, other}
    assert not old.exists()
    assert not other.exists()


def test_cleanup_keeps_fresh_temp_files(tmp_path: Path) -> None:
    fresh = tmp_path / ".fresh.tmp.1.2"
    _touch(fresh, age_seconds=10)

    removed = safe_fs.cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)

    assert removed == []
    assert fresh.exists()


def test_cleanup_ignores_non_matching_pattern(tmp_path: Path) -> None:
    unrelated = tmp_path / "report.txt"
    _touch(unrelated, age_seconds=7200)

    removed = safe_fs.cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)

    assert removed == []
    assert unrelated.exists()


def test_cleanup_respects_custom_pattern(tmp_path: Path) -> None:
    target = tmp_path / "orphan.log"
    _touch(target, age_seconds=7200)
    (tmp_path / ".noise.tmp.1.2").write_text("x", encoding="utf-8")

    removed = safe_fs.cleanup_stale_temp_files(
        tmp_path, max_age_seconds=3600, filename_pattern="orphan.*"
    )

    assert removed == [target]
    assert not target.exists()


def test_cleanup_tolerates_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    good = tmp_path / ".good.tmp.1.1"
    bad = tmp_path / ".bad.tmp.2.2"
    _touch(good, age_seconds=7200)
    _touch(bad, age_seconds=7200)

    real_unlink = safe_fs._safe_unlink

    def flaky_unlink(path, **kwargs):
        if Path(path).name == bad.name:
            raise SafeFsError("simulated lock")
        return real_unlink(path, **kwargs)

    monkeypatch.setattr(safe_fs, "_safe_unlink", flaky_unlink)

    with caplog.at_level(logging.WARNING, logger="lokdocsqr.core.safe_fs"):
        removed = safe_fs.cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)

    assert removed == [good]
    assert not good.exists()
    assert bad.exists()
    assert any(
        "cannot remove" in rec.getMessage().lower() for rec in caplog.records
    )


def test_cleanup_handles_stat_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that vanishes between glob and stat must not abort the sweep."""
    vanishing = tmp_path / ".gone.tmp.9.9"
    _touch(vanishing, age_seconds=7200)
    keep = tmp_path / ".stay.tmp.8.8"
    _touch(keep, age_seconds=10)

    real_stat = Path.stat

    def flaky_stat(self, *args, **kwargs):
        if self.name == vanishing.name:
            raise FileNotFoundError("race")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)
    removed = safe_fs.cleanup_stale_temp_files(tmp_path, max_age_seconds=3600)
    assert removed == []
    assert keep.exists()
