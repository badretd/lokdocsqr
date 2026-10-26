"""Append-only audit log, rollback planning, and rollback execution."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import SafeFsError
from lokdocsqr.core.models import ActionLog, RollbackOp
from lokdocsqr.core.scope import ScopeManager

logger = logging.getLogger(__name__)

__all__ = ["AuditLog"]


def _json_default(obj: Any) -> Any:
    """Fallback serializer for dataclass fields that json can't handle.

    ActionLog carries ``datetime`` timestamps (and potentially ``Path``
    objects from older node implementations). Both are routinely written
    to the on-disk audit log and re-read verbatim, so ISO-8601 / str
    are the canonical representations.
    """
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(
        f"Object of type {type(obj).__name__} is not JSON serializable"
    )


class AuditLog:
    """JSONL audit log stored on disk.

    The file is append-only: existing lines are never rewritten. Every write
    goes through :func:`lokdocsqr.core.safe_fs.atomic_append`.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, log: ActionLog) -> None:
        """Append one ActionLog as a single JSON line."""
        line = json.dumps(
            log.to_dict(),
            ensure_ascii=False,
            default=_json_default,
        ) + "\n"
        safe_fs.atomic_append(self.path, line)

    def iter_all(self) -> Iterator[ActionLog]:
        """Yield every parseable ActionLog, skipping malformed lines."""

        try:
            raw_bytes = safe_fs.read_bytes(self.path)
        except SafeFsError as exc:
            logger.warning("audit: cannot read %s: %s", self.path, exc)
            return

        text = raw_bytes.decode("utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "audit: skipping malformed JSON line %d in %s: %s",
                    line_no,
                    self.path,
                    exc,
                )
                continue
            if not isinstance(raw, dict):
                logger.warning(
                    "audit: skipping non-object line %d in %s",
                    line_no,
                    self.path,
                )
                continue
            try:
                yield ActionLog.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "audit: skipping invalid ActionLog line %d in %s: %s",
                    line_no,
                    self.path,
                    exc,
                )

    def lookup_by_run(self, run_id: str) -> list[ActionLog]:
        """Return all log entries for ``run_id`` in append order."""
        return [log for log in self.iter_all() if log.run_id == run_id]

    def lookup_by_automation(self, automation_id: str) -> list[ActionLog]:
        """Return all log entries for ``automation_id`` in append order."""
        return [
            log for log in self.iter_all() if log.automation_id == automation_id
        ]

    def lookup_by_source(self, path: str | Path) -> list[ActionLog]:
        """Return all log entries whose ``source`` equals ``path``."""
        target = str(Path(path))
        return [log for log in self.iter_all() if log.source == target]

    def build_rollback_plan(self, run_id: str) -> list[RollbackOp]:
        """Build a rollback plan for applied, rollback-eligible actions.

        The order follows the append order of the entries for ``run_id``.
        ``original_log_index`` is the zero-based index among that run's
        entries, which is stable and easy to assert in tests.
        """
        plan: list[RollbackOp] = []
        run_logs = [log for log in self.iter_all() if log.run_id == run_id]

        for index, log in enumerate(run_logs):
            if log.status != "applied" or not log.rollback_eligible:
                continue
            if not log.source or not log.target:
                continue

            if log.action_type == "move":
                plan.append(
                    RollbackOp(
                        action="move_back",
                        source=Path(log.target),
                        target=Path(log.source),
                        original_log_index=index,
                    )
                )
            elif log.action_type == "trash":
                plan.append(
                    RollbackOp(
                        action="restore_from_trash",
                        source=Path(log.target),
                        target=Path(log.source),
                        original_log_index=index,
                    )
                )
            elif log.action_type == "rename":
                plan.append(
                    RollbackOp(
                        action="move_back",
                        source=Path(log.target),
                        target=Path(log.source),
                        original_log_index=index,
                    )
                )
            elif log.action_type == "copy":
                # Copy does not remove the source, so it is not rollback-eligible.
                continue

        return plan

    def apply_rollback(
        self, plan: list[RollbackOp], scope: ScopeManager
    ) -> list[ActionLog]:
        """Execute ``plan`` through safe_fs and return new audit entries.

        Both source and destination are checked against ``scope`` with
        ``write=True``. A rollback never overwrites an existing destination:
        it raises :class:`SafeFsError` instead.
        """
        results: list[ActionLog] = []

        for op in plan:
            source = scope.check(op.source, write=True)
            target = scope.check(op.target, write=True)

            if target.exists():
                raise SafeFsError(
                    f"rollback: target already exists, refusing to overwrite: {target}"
                )

            safe_fs.safe_move(source, target)

            results.append(
                ActionLog(
                    run_id="",
                    node_id="",
                    automation_id="",
                    action_type="move",
                    source=str(source),
                    target=str(target),
                    status="applied",
                    reason="rollback",
                    confidence=1.0,
                    rollback_eligible=False,
                    extra={
                        "original_log_index": op.original_log_index,
                        "rollback_action": op.action,
                    },
                )
            )

        return results