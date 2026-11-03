"""End-to-end integration test for the fs_ops family through the engine.

Runs ``tests/fixtures/golden_fs_ops.json`` through the real
``parse/build → Executor.execute`` path (real mode, no dry-run) and
asserts the resulting tree matches ``golden_fs_ops.expected.json``.
Then builds and applies a rollback plan for the move and trash actions.

The fixture's ``{base}`` marker is stripped: relative paths now resolve
against ``config.base_dir`` (F-05), so the test no longer changes CWD.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import lokdocsqr.nodes.fs_ops.archive
import lokdocsqr.nodes.fs_ops.copy
import lokdocsqr.nodes.fs_ops.make_dir
import lokdocsqr.nodes.fs_ops.move
import lokdocsqr.nodes.fs_ops.rename
import lokdocsqr.nodes.fs_ops.tag
import lokdocsqr.nodes.fs_ops.trash
import lokdocsqr.nodes.fs_ops.write_file  # noqa: F401
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph import build_graph, resolve_states
from lokdocsqr.core.models import (
    NodeSpec,
    WorkflowDocument,
    WorkflowMetadata,
    WorkspaceScope,
)
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_JSON = FIXTURES / "golden_fs_ops.json"
EXPECTED_JSON = FIXTURES / "golden_fs_ops.expected.json"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if rel.endswith(".tags.json") or rel.endswith(".zip"):
            continue
        if rel == ".audit.jsonl" or rel.startswith(".trash/") or rel.startswith(".logs/"):
            continue
        result[rel] = _hash_file(p)
    return result


def _make_ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(
                roots=[tmp_path], writable=[tmp_path], allow_symlinks=False
            )
        ),
        config=AppConfig(base_dir=tmp_path),
        audit=AuditLog(tmp_path / ".audit.jsonl"),
        run_id="golden-run",
        automation_id="golden",
        dry_run=False,
        events=EventBus(),
        policy=None,
    )


def _make_graph(golden: dict):
    nodes: list[NodeSpec] = []
    for node_def in golden["nodes"]:
        params = {
            key: (
                value.replace("{base}/", "")
                if isinstance(value, str)
                else [item.replace("{base}/", "") for item in value]
                if isinstance(value, list)
                else value
            )
            for key, value in (node_def.get("params") or {}).items()
        }
        nodes.append(
            NodeSpec(
                id=node_def["id"],
                type=node_def["type"],
                params=params,
            )
        )
    doc = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="golden_fs_ops"),
        nodes=nodes,
        edges=[],
    )
    states = resolve_states(doc)
    return build_graph(doc, states, registry)


def test_golden_fs_ops_integration(tmp_path: Path) -> None:
    golden = json.loads(GOLDEN_JSON.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED_JSON.read_text(encoding="utf-8"))
    expected = {k: v for k, v in expected.items() if not k.startswith("_")}

    ctx = _make_ctx(tmp_path)
    result = Executor(ctx).execute(_make_graph(golden))

    assert result.success, result.errors
    assert _tree_hashes(tmp_path) == expected
    assert len(result.action_logs) == len(golden["nodes"])

    # Rollback for move + trash only (they carry rollback_eligible=True).
    scope = ScopeManager(
        WorkspaceScope(roots=[tmp_path], writable=[tmp_path], allow_symlinks=False)
    )
    plan = ctx.audit.build_rollback_plan("golden-run")
    assert plan, "expected a non-empty rollback plan for move+trash"

    ctx.audit.apply_rollback(plan, scope)

    assert (tmp_path / "work" / "c.txt").exists()
    assert not (tmp_path / "work" / "d.txt").exists()
    assert (tmp_path / "work" / "z.txt").exists()
