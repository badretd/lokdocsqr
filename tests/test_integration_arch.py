"""Phase 4 integration tests using tests/fixtures/golden.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tests.helpers import make_ctx, resolved  # noqa: E402

from lokdocsqr.core import safe_fs  # noqa: E402
from lokdocsqr.core.executor.engine import execute  # noqa: E402
from lokdocsqr.core.graph import parse_graph  # noqa: E402
from lokdocsqr.core.models import (  # noqa: E402
    ActionLog,
    NodeResult,
    NodeState,
    ResolvedGraph,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden.json"


def _resolved_golden(handlers) -> ResolvedGraph:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    doc = parse_graph(data)
    states = {node.id: node.state for node in doc.nodes if node.id}
    ordered = [node.id for node in doc.nodes if node.id and node.state is NodeState.ENABLED]
    return resolved(doc, ordered, handlers, states=states)


def _snapshot(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


class _DryMove:
    def __init__(self, src: Path, dst: Path) -> None:
        self.src = src
        self.dst = dst

    def dry_run(self, ctx, payload):
        return NodeResult(
            node_id="move",
            state=NodeState.ENABLED,
            action_logs=[
                ActionLog(
                    node_id="move",
                    action_type="move",
                    source=str(self.src),
                    target=str(self.dst),
                    status="planned",
                    rollback_eligible=True,
                )
            ],
        )

    def run(self, ctx, payload):
        raise AssertionError("run must not be called in dry-run")


class _RealMove:
    def __init__(self, src: Path, dst: Path) -> None:
        self.src = src
        self.dst = dst

    def run(self, ctx, payload):
        safe_fs.safe_move(self.src, self.dst)
        return NodeResult(
            node_id="move",
            state=NodeState.ENABLED,
            action_logs=[
                ActionLog(
                    node_id="move",
                    action_type="move",
                    source=str(self.src),
                    target=str(self.dst),
                    status="planned",
                    rollback_eligible=True,
                )
            ],
        )

    def dry_run(self, ctx, payload):
        raise AssertionError("dry_run must not be called in real mode")


def test_golden_dry_run_expected_actions_and_fs_unchanged(tmp_path: Path) -> None:
    work = tmp_path / "work"
    src = work / "in" / "a.txt"
    dst = work / "out" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("payload", encoding="utf-8")

    graph = _resolved_golden({"move": _DryMove(src, dst)})
    ctx = make_ctx(tmp_path, dry_run=True)
    before = _snapshot(work)

    result = execute(graph, ctx)

    assert _snapshot(work) == before
    logs = ctx.audit.lookup_by_run("run-1")
    assert [(entry.node_id, entry.action_type, entry.status) for entry in logs] == [
        ("move", "move", "dry_run")
    ]
    assert result.action_logs[0].status == "dry_run"


def test_golden_real_move_then_rollback_restores(tmp_path: Path) -> None:
    work = tmp_path / "work"
    src = work / "in" / "a.txt"
    dst = work / "out" / "a.txt"
    src.parent.mkdir(parents=True)
    src.write_text("payload", encoding="utf-8")

    graph = _resolved_golden({"move": _RealMove(src, dst)})
    ctx = make_ctx(tmp_path)

    result = execute(graph, ctx)

    assert dst.read_text(encoding="utf-8") == "payload"
    assert not src.exists()
    assert len(result.rollback_plan) == 1

    ctx.audit.apply_rollback(result.rollback_plan, ctx.scope)

    assert src.read_text(encoding="utf-8") == "payload"
    assert not dst.exists()
