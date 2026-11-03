"""Retention/cleanup tests: trash TTL sweep and audit-log pruning."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.models import ActionLog
from lokdocsqr.core.runtime import Runtime

cli_main = importlib.import_module("lokdocsqr.cli.main")


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


def _make_trash_entry(
    trash_dir: Path,
    name: str,
    *,
    ttl: str | None,
    trash_path: str | None = None,
) -> Path:
    trash_dir.mkdir(parents=True, exist_ok=True)
    file = trash_dir / name
    file.write_text("quarantined", encoding="utf-8")
    (trash_dir / f"{name}.meta.json").write_text("{}", encoding="utf-8")
    if ttl is not None:
        payload = {
            "ttl_expires_at": ttl,
            "trash_path": str(trash_path or file),
        }
        (trash_dir / f"{name}.sidecar.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    return file


# ---------------------------------------------------------------------------
# Trash TTL
# ---------------------------------------------------------------------------


def test_expired_trash_entries_only_returns_expired_with_valid_sidecar(
    tmp_path: Path,
) -> None:
    trash = tmp_path / ".trash"
    expired = _make_trash_entry(trash, "old.txt", ttl=_iso(-timedelta(days=1)))
    live = _make_trash_entry(trash, "new.txt", ttl=_iso(timedelta(days=1)))
    unknown = _make_trash_entry(trash, "unknown.txt", ttl=None)

    expired_entries = safe_fs.expired_trash_entries(trash)

    assert expired_entries == [expired]
    assert live not in expired_entries
    assert unknown not in expired_entries


def test_purge_expired_trash_removes_file_and_sidecars(tmp_path: Path) -> None:
    trash = tmp_path / ".trash"
    expired = _make_trash_entry(trash, "old.txt", ttl=_iso(-timedelta(days=1)))
    live = _make_trash_entry(trash, "new.txt", ttl=_iso(timedelta(days=1)))

    removed = safe_fs.purge_expired_trash(trash)

    assert removed == [expired]
    assert not expired.exists()
    assert not (trash / "old.txt.sidecar.json").exists()
    assert not (trash / "old.txt.meta.json").exists()
    assert live.exists()
    assert (trash / "new.txt.sidecar.json").exists()


def test_purge_expired_trash_ignores_sidecar_pointing_outside(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    trash = tmp_path / ".trash"
    _make_trash_entry(
        trash,
        "evil.txt",
        ttl=_iso(-timedelta(days=1)),
        trash_path=str(outside),
    )

    removed = safe_fs.purge_expired_trash(trash)

    assert removed == []
    assert outside.exists()


def test_purge_expired_trash_ignores_symlinks(tmp_path: Path) -> None:
    trash = tmp_path / ".trash"
    target = tmp_path / "real.txt"
    target.write_text("x", encoding="utf-8")
    trash.mkdir()
    link = trash / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    (trash / "link.txt.sidecar.json").write_text(
        json.dumps(
            {"ttl_expires_at": _iso(-timedelta(days=1)), "trash_path": str(link)}
        ),
        encoding="utf-8",
    )

    assert safe_fs.purge_expired_trash(trash) == []
    assert link.is_symlink()
    assert target.exists()


def test_cli_trash_sweep_dry_run_then_apply(tmp_path: Path, capsys) -> None:
    base = tmp_path / "ws"
    trash = base / ".trash"
    expired = _make_trash_entry(trash, "old.txt", ttl=_iso(-timedelta(days=1)))

    assert (
        cli_main.main(
            ["trash", "sweep", "--base-dir", str(base), "--dry-run"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "would remove" in out
    assert expired.exists()

    assert cli_main.main(["trash", "sweep", "--base-dir", str(base)]) == 0
    out = capsys.readouterr().out
    assert "removed 1 expired" in out
    assert not expired.exists()


def test_runtime_sweep_trash(tmp_path: Path) -> None:
    base = tmp_path / "ws"
    trash = base / ".trash"
    expired = _make_trash_entry(trash, "old.txt", ttl=_iso(-timedelta(days=1)))
    runtime = Runtime(
        AppConfig(base_dir=base),
        policy=None,
        lock_path=tmp_path / "runtime.lock",
    )

    removed = runtime.sweep_trash()

    assert removed == [expired]
    assert not expired.exists()


# ---------------------------------------------------------------------------
# Audit retention
# ---------------------------------------------------------------------------


def _log(run_id: str, *, timestamp: str, rollbackable: bool = True) -> ActionLog:
    return ActionLog(
        run_id=run_id,
        node_id="fs.move",
        automation_id="demo",
        action_type="move",
        source=f"/tmp/{run_id}-src",
        target=f"/tmp/{run_id}-dst",
        status="applied",
        reason="no_conflict",
        timestamp=timestamp,
        rollback_eligible=rollbackable,
    )


def _audit_with_three_runs(tmp_path: Path) -> tuple[AuditLog, Path]:
    path = tmp_path / ".logs" / "audit.jsonl"
    audit = AuditLog(path)
    audit.append(_log("old", timestamp=_iso(-timedelta(days=40))))
    audit.append(_log("mid", timestamp=_iso(-timedelta(days=10))))
    audit.append(_log("new", timestamp=_iso(-timedelta(days=1))))
    return audit, path


def test_prune_dry_run_does_not_rewrite(tmp_path: Path) -> None:
    audit, path = _audit_with_three_runs(tmp_path)
    before = path.read_text(encoding="utf-8")

    summary = audit.prune(keep_runs=1, dry_run=True)

    assert summary["dropped_runs"] == 2
    assert summary["kept_runs"] == 1
    assert summary["dropped_rollbackable"] == 2
    assert path.read_text(encoding="utf-8") == before


def test_prune_keep_runs_keeps_whole_runs(tmp_path: Path) -> None:
    audit, path = _audit_with_three_runs(tmp_path)

    summary = audit.prune(keep_runs=1, dry_run=False)

    assert summary["dropped_runs"] == 2
    assert summary["kept_entries"] == 1
    remaining = [
        ActionLog.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert [log.run_id for log in remaining] == ["new"]


def test_prune_older_than_seconds_keeps_young_runs(tmp_path: Path) -> None:
    audit, path = _audit_with_three_runs(tmp_path)

    summary = audit.prune(
        older_than_seconds=30 * 86400, dry_run=False
    )

    assert summary["dropped_runs"] == 1
    remaining = [
        json.loads(line)["run_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert remaining == ["mid", "new"]


def test_prune_keeps_rollback_entries_with_their_run(tmp_path: Path) -> None:
    path = tmp_path / ".logs" / "audit.jsonl"
    audit = AuditLog(path)
    audit.append(_log("old", timestamp=_iso(-timedelta(days=40))))
    audit.append(
        ActionLog(
            run_id="old",
            node_id="__rollback__",
            automation_id="demo",
            action_type="move",
            status="applied",
            reason="rollback",
            timestamp=_iso(-timedelta(days=39)),
        )
    )
    audit.append(_log("new", timestamp=_iso(-timedelta(days=1))))

    audit.prune(keep_runs=1, dry_run=False)

    remaining = [
        json.loads(line)["run_id"]
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert remaining == ["new"]


def test_prune_rejects_missing_policy(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError):
        audit.prune()


def test_cli_audit_prune_dry_run_then_apply(tmp_path: Path, capsys) -> None:
    _audit, path = _audit_with_three_runs(tmp_path)
    base = tmp_path

    assert (
        cli_main.main(
            [
                "audit",
                "prune",
                "--base-dir",
                str(base),
                "--keep-runs",
                "1",
                "--dry-run",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "would drop 2 run(s)" in out
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3

    assert (
        cli_main.main(
            ["audit", "prune", "--base-dir", str(base), "--keep-runs", "1"]
        )
        == 0
    )
    capsys.readouterr()
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
