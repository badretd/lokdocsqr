"""Phase 4: core.models serialization + core.audit behaviour."""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.helpers import make_scope  # noqa: E402

from lokdocsqr.core import safe_fs  # noqa: E402
from lokdocsqr.core.audit import AuditLog  # noqa: E402
from lokdocsqr.core.errors import SafeFsError, ScopeViolation  # noqa: E402
from lokdocsqr.core.models import (  # noqa: E402
    ActionLog,
    ActionProposal,
    FileRef,
    NodeResult,
    NodeState,
    Risk,
    RollbackOp,
    RunResult,
)


# ---------------------------------------------------------------------------
# A. core/models.py
# ---------------------------------------------------------------------------

def test_actionlog_roundtrip_to_from_dict() -> None:
    original = ActionLog(
        run_id="run-1",
        node_id="n-1",
        automation_id="auto-1",
        action_type="move",
        source="/src/a.txt",
        target="/dst/a.txt",
        status="applied",
        reason="dedupe",
        confidence=0.85,
        risk=Risk.HIGH,
        rollback_eligible=True,
        extra={"k": "v", "nested": {"ok": True}},
    )
    payload = original.to_dict()
    assert payload["risk"] == "high"
    assert payload["rollback_eligible"] is True

    restored = ActionLog.from_dict(payload)
    assert restored.run_id == original.run_id
    assert restored.node_id == original.node_id
    assert restored.automation_id == original.automation_id
    assert restored.action_type == original.action_type
    assert restored.source == original.source
    assert restored.target == original.target
    assert restored.status == original.status
    assert restored.reason == original.reason
    assert restored.confidence == original.confidence
    assert restored.risk is Risk.HIGH
    assert restored.rollback_eligible is True
    assert restored.extra == {"k": "v", "nested": {"ok": True}}


def test_actionlog_roundtrip_preserves_none_source_target() -> None:
    original = ActionLog(action_type="copy", source=None, target=None)
    restored = ActionLog.from_dict(original.to_dict())
    assert restored.source is None
    assert restored.target is None


def test_actionlog_to_dict_converts_path_in_extra() -> None:
    relative = Path("relative/path.txt")
    log = ActionLog(extra={"p": relative, "risk": Risk.MEDIUM})
    data = log.to_dict()
    assert data["extra"]["p"] == str(relative)
    assert data["extra"]["risk"] == "medium"


def test_actionlog_from_dict_missing_key_raises_keyerror() -> None:
    with pytest.raises(KeyError):
        ActionLog.from_dict({})


def test_fileref_defaults() -> None:
    ref = FileRef(id="f1", path=Path("/tmp/a.txt"))
    assert ref.hash == ""
    assert ref.mime == ""
    assert ref.size == 0
    assert ref.created_at is None
    assert ref.modified_at is None
    assert ref.metadata == {}


def test_actionproposal_defaults() -> None:
    proposal = ActionProposal(type="move")
    assert proposal.source is None
    assert proposal.target is None
    assert proposal.reason == ""
    assert proposal.confidence == 0.0
    assert proposal.risk is Risk.LOW


def test_noderesult_defaults() -> None:
    result = NodeResult(node_id="n", state=NodeState.ENABLED)
    assert result.outputs == {}
    assert result.action_logs == []
    assert result.error is None
    assert result.attempts == 1
    assert result.duration_seconds == 0.0


def test_rollbackop_constructs() -> None:
    op = RollbackOp(
        action="move_back",
        source=Path("/dst/a.txt"),
        target=Path("/src/a.txt"),
        original_log_index=3,
    )
    assert op.action == "move_back"
    assert op.source == Path("/dst/a.txt")
    assert op.target == Path("/src/a.txt")
    assert op.original_log_index == 3


def test_runresult_to_dict_serializes_rollback_plan() -> None:
    result = RunResult(
        run_id="r",
        automation_id="a",
        dry_run=False,
        success=True,
        rollback_plan=[
            RollbackOp(
                action="move_back",
                source=Path("/dst/a.txt"),
                target=Path("/src/a.txt"),
                original_log_index=0,
            )
        ],
    )
    data = result.to_dict()
    assert isinstance(data["rollback_plan"], list)
    assert data["rollback_plan"][0]["action"] == "move_back"


# ---------------------------------------------------------------------------
# B. core/audit.py
# ---------------------------------------------------------------------------

def _log(**overrides) -> ActionLog:
    base = dict(
        run_id="r1",
        node_id="n1",
        automation_id="a1",
        action_type="move",
        source="/src/a.txt",
        target="/dst/a.txt",
        status="applied",
        rollback_eligible=True,
    )
    base.update(overrides)
    return ActionLog(**base)


def test_audit_append_writes_single_json_line(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(run_id="r1"))
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["run_id"] == "r1"


def test_audit_append_appends_not_overwrites(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(run_id="r1"))
    size1 = audit.path.stat().st_size
    audit.append(_log(run_id="r2"))
    size2 = audit.path.stat().st_size
    assert size2 > size1
    lines = audit.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["run_id"] == "r1"
    assert json.loads(lines[1])["run_id"] == "r2"


def test_audit_append_only_preserves_previous_lines(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(run_id="r1"))
    first = audit.path.read_bytes().splitlines(keepends=True)[0]
    audit.append(_log(run_id="r2"))
    audit.append(_log(run_id="r3"))
    after = audit.path.read_bytes().splitlines(keepends=True)
    assert after[0] == first


def test_audit_iter_all_returns_append_order(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    for run_id in ("r1", "r2", "r3"):
        audit.append(_log(run_id=run_id))
    assert [entry.run_id for entry in audit.iter_all()] == ["r1", "r2", "r3"]


def test_audit_iter_all_skips_malformed_line_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"run_id":"r1","node_id":"n1","action_type":"move","status":"applied"}\n'
        "NOT JSON\n"
        '{"run_id":"r2","node_id":"n2","action_type":"copy","status":"applied"}\n',
        encoding="utf-8",
    )
    audit = AuditLog(path)
    with caplog.at_level(logging.WARNING, logger="lokdocsqr.core.audit"):
        logs = list(audit.iter_all())
    assert [entry.run_id for entry in logs] == ["r1", "r2"]
    assert any("malformed" in rec.getMessage().lower() for rec in caplog.records)


def test_audit_lookup_by_run(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(run_id="r1", node_id="a"))
    audit.append(_log(run_id="r2", node_id="b"))
    audit.append(_log(run_id="r1", node_id="c"))
    assert [entry.node_id for entry in audit.lookup_by_run("r1")] == ["a", "c"]


def test_audit_lookup_by_automation(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(automation_id="auto-a"))
    audit.append(_log(automation_id="auto-b"))
    assert len(audit.lookup_by_automation("auto-a")) == 1


def test_audit_lookup_by_source(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    source = tmp_path / "src.txt"
    audit.append(_log(source=str(source)))
    audit.append(_log(source=str(tmp_path / "other.txt")))
    assert len(audit.lookup_by_source(source)) == 1


def test_audit_lookup_empty_returns_empty_list(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    assert audit.lookup_by_run("missing") == []
    assert audit.lookup_by_automation("missing") == []
    assert audit.lookup_by_source(tmp_path / "missing.txt") == []


def test_build_rollback_plan_filters_and_maps_actions(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    base = tmp_path / "base"
    trash = tmp_path / "trash"

    audit.append(_log(run_id="r", action_type="move", status="applied", rollback_eligible=True,
                      source=str(base / "a.txt"), target=str(base / "b.txt")))
    audit.append(_log(run_id="r", action_type="trash", status="applied", rollback_eligible=True,
                      source=str(base / "c.txt"), target=str(trash / "c.txt")))
    audit.append(_log(run_id="r", action_type="rename", status="applied", rollback_eligible=True,
                      source=str(base / "d.txt"), target=str(base / "e.txt")))
    audit.append(_log(run_id="r", action_type="copy", status="applied", rollback_eligible=True,
                      source=str(base / "f.txt"), target=str(base / "g.txt")))
    audit.append(_log(run_id="r", action_type="move", status="dry_run", rollback_eligible=True,
                      source=str(base / "h.txt"), target=str(base / "i.txt")))
    audit.append(_log(run_id="r", action_type="move", status="applied", rollback_eligible=False,
                      source=str(base / "j.txt"), target=str(base / "k.txt")))

    plan = audit.build_rollback_plan("r")
    assert [op.action for op in plan] == ["move_back", "restore_from_trash", "move_back"]
    assert [op.original_log_index for op in plan] == [0, 1, 2]
    assert plan[0].source == base / "b.txt"
    assert plan[0].target == base / "a.txt"
    assert plan[1].source == trash / "c.txt"
    assert plan[1].target == base / "c.txt"


def test_build_rollback_plan_order_matches_append_order(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append(_log(run_id="r", action_type="move", source="/a", target="/b"))
    audit.append(_log(run_id="r", action_type="move", source="/c", target="/d"))
    plan = audit.build_rollback_plan("r")
    assert [op.target for op in plan] == [Path("/a"), Path("/c")]


def test_build_rollback_plan_empty_run_id(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    assert audit.build_rollback_plan("missing") == []


def test_apply_rollback_move_back_restores_file(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    moved = tmp_path / "moved.txt"
    original = tmp_path / "original.txt"
    moved.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=moved, target=original, original_log_index=0)]

    audit.apply_rollback(plan, make_scope(tmp_path))

    assert original.read_text(encoding="utf-8") == "payload"
    assert not moved.exists()


def test_apply_rollback_restore_from_trash_restores_file(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    trash_dir = tmp_path / "trash"
    trash_dir.mkdir()
    trashed = trash_dir / "x.txt"
    original = tmp_path / "restored.txt"
    trashed.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="restore_from_trash", source=trashed, target=original, original_log_index=0)]

    audit.apply_rollback(plan, make_scope(tmp_path))

    assert original.read_text(encoding="utf-8") == "payload"
    assert not trashed.exists()


def test_apply_rollback_target_exists_raises_safe_fs_error(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    source = tmp_path / "src.txt"
    target = tmp_path / "dst.txt"
    source.write_text("src", encoding="utf-8")
    target.write_text("dst", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=source, target=target, original_log_index=0)]

    with pytest.raises(SafeFsError):
        audit.apply_rollback(plan, make_scope(tmp_path))
    assert source.exists()
    assert target.read_text(encoding="utf-8") == "dst"


def test_apply_rollback_outside_scope_raises_and_leaves_fs_untouched(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "outside_src.txt"
    target = tmp_path / "outside_dst.txt"
    source.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=source, target=target, original_log_index=0)]

    with pytest.raises(ScopeViolation):
        audit.apply_rollback(plan, make_scope(root))
    assert source.exists()
    assert not target.exists()


def test_apply_rollback_returns_applied_noneligible_logs(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    source = tmp_path / "moved.txt"
    target = tmp_path / "original.txt"
    source.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=source, target=target, original_log_index=0)]

    results = audit.apply_rollback(plan, make_scope(tmp_path))

    assert len(results) == 1
    assert results[0].status == "applied"
    assert "rollback" in results[0].reason.lower()
    assert results[0].rollback_eligible is False


def test_apply_rollback_repeat_does_not_move_file_back(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    source = tmp_path / "moved.txt"
    target = tmp_path / "original.txt"
    source.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=source, target=target, original_log_index=0)]

    audit.apply_rollback(plan, make_scope(tmp_path))
    assert target.exists()
    assert not source.exists()

    with pytest.raises(SafeFsError):
        audit.apply_rollback(plan, make_scope(tmp_path))
    assert target.exists()
    assert not source.exists()


def test_apply_rollback_calls_safe_fs_safe_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    source = tmp_path / "moved.txt"
    target = tmp_path / "original.txt"
    source.write_text("payload", encoding="utf-8")
    plan = [RollbackOp(action="move_back", source=source, target=target, original_log_index=0)]

    calls = []
    real_safe_move = safe_fs.safe_move

    def spy(src, dst):
        calls.append((Path(src), Path(dst)))
        return real_safe_move(src, dst)

    monkeypatch.setattr(safe_fs, "safe_move", spy)
    audit.apply_rollback(plan, make_scope(tmp_path))
    assert calls == [(source, target)]
