"""Phase 8.7 acceptance tests (F-04 … F-10A).

Each test is intentionally behavioural: it exercises the public path that
the corresponding finding called broken.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.clock import FakeClock
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.errors import PolicyBlockedError
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.models import (
    ActionSummary,
    NodeSpec,
    NodeState,
    ResolvedGraph,
    WorkflowDocument,
    WorkflowMetadata,
    WorkspaceScope,
)
from lokdocsqr.core.preview import ChangeType, preview_diff
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.fs_ops.make_dir import MakeDirNode
from lokdocsqr.nodes.fs_ops.tag import TagNode
from lokdocsqr.nodes.fs_ops.trash import TrashNode
from lokdocsqr.nodes.fs_ops.write_file import WriteFileNode
from tests.test_dry_run import _TestCtx


def _ctx(
    base: Path,
    *,
    dry_run: bool = False,
    policy: object | None = None,
    trash: Path | None = None,
    clock: FakeClock | None = None,
) -> ExecutionContext:
    writable = [base] if trash is None else [base, trash]
    return ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(
                roots=writable, writable=writable, allow_symlinks=False
            )
        ),
        config=AppConfig(base_dir=base, trash_dir=trash),
        audit=AuditLog(base / "audit.jsonl"),
        run_id="phase-8-7",
        automation_id="phase-8-7",
        dry_run=dry_run,
        events=EventBus(),
        policy=policy,
        clock=clock or FakeClock(start=1_700_000_000),
    )


def _graph(node_id: str, node_type: str, handler, params: dict) -> ResolvedGraph:
    spec = NodeSpec(id=node_id, type=node_type, params=params)
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="phase-8-7"),
        nodes=[spec],
    )
    return ResolvedGraph(
        document=doc,
        ordered_node_ids=[node_id],
        handlers={node_id: handler},
        states={node_id: NodeState.ENABLED},
    )


# F-04 -----------------------------------------------------------------------

def test_preview_change_kind_by_instance() -> None:
    """Preview keys off node type, not the graph instance id."""
    proposal = ActionSummary(
        node_id="n_move",
        node_type="fs.move",
        operation="write",
        target_paths=("/does/not/exist.txt",),
    )
    preview = preview_diff(proposal, SimpleNamespace())
    assert preview.change_type is ChangeType.MOVE


# F-05 -----------------------------------------------------------------------

def test_relative_paths_resolve_from_base_dir(tmp_path: Path, monkeypatch) -> None:
    base = tmp_path / "workspace"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    ctx = _ctx(base, policy=None)
    result = Executor(ctx).execute(
        _graph(
            "n_mk",
            "fs.make_dir",
            MakeDirNode,
            {"path_template": "made"},
        )
    )

    assert result.success, result.errors
    assert (base / "made").is_dir()
    assert not (elsewhere / "made").exists()


# F-06 -----------------------------------------------------------------------

def test_dry_run_trash_path_matches_real(tmp_path: Path) -> None:
    base = tmp_path / "base"
    trash = tmp_path / "trash"
    base.mkdir()
    trash.mkdir()
    src = base / "a.txt"
    src.write_text("hello", encoding="utf-8")

    clock = FakeClock(start=1_700_000_000)
    ctx = _ctx(base, trash=trash, clock=clock)

    dry = TrashNode().dry_run(ctx, {"path": str(src)})
    real = TrashNode().run(ctx, {"path": str(src)})

    assert dry.target == real.target
    assert dry.sidecar_path == real.sidecar_path
    assert Path(real.target).exists()


# F-07 -----------------------------------------------------------------------

def test_xattr_goes_through_safe_fs(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str, bytes]] = []

    def fake_set_xattr(path, name, value):
        calls.append((str(path), name, value))
        return True

    monkeypatch.setattr(safe_fs, "set_xattr", fake_set_xattr)
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")
    ctx = _TestCtx([tmp_path])

    TagNode().run(ctx, {"path": str(target), "tags": ["x"], "mode": "set"})

    assert calls
    assert calls[0][0] == str(target)


# F-10A ----------------------------------------------------------------------

def test_high_risk_without_verify_fails_closed(tmp_path: Path) -> None:
    """The production default must be a policy engine, not None."""
    ctx = ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(
                roots=[tmp_path], writable=[tmp_path], allow_symlinks=False
            )
        ),
        config=AppConfig(base_dir=tmp_path),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        run_id="fail-closed",
        automation_id="fail-closed",
        events=EventBus(),
    )
    assert ctx.policy is not None

    graph = _graph(
        "n_wr",
        "fs.write_file",
        WriteFileNode,
        {
            "path_template": "blocked.txt",
            "content": "no",
            "create_parents": False,
        },
    )
    with pytest.raises(PolicyBlockedError):
        Executor(ctx).execute(graph)
    assert not (tmp_path / "blocked.txt").exists()
