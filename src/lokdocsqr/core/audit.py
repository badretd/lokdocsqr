"""Append-only audit log, rollback planning, and rollback execution."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _try_json_object(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_after(timestamp: str, cutoff: float) -> bool:
    """True when ``timestamp`` parses and is at/after ``cutoff``.

    Empty or malformed timestamps are treated as "keep" (``True``) so a
    malformed entry is never pruned by age.
    """
    if not timestamp:
        return True
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() >= cutoff


class AuditLog:
    """JSONL audit log stored on disk.

    The file is append-only during normal operation: entries are only ever
    added, and every append goes through
    :func:`lokdocsqr.core.safe_fs.atomic_append`. The single exception is the
    explicit :meth:`prune` maintenance operation, which atomically rewrites
    the file to drop whole old runs (never part of a run).
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
        """Yield every parseable ActionLog, skipping malformed lines.

        A missing audit file means "no entries yet", not a warning. The
        executor asks for a rollback plan at the end of every run,
        including read-only runs that never wrote an audit entry; emitting
        a warning for that normal state makes every pure-read automation
        noisy (and spams daemon logs on every schedule tick).
        """

        try:
            if not self.path.exists():
                return
            raw_bytes = safe_fs.read_bytes(self.path)
        except SafeFsError as exc:
            cause = exc.__cause__
            if isinstance(cause, FileNotFoundError):
                return
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

    def lookup_by_path(self, path: str | Path) -> list[ActionLog]:
        """Return entries where ``path`` is either the source or the target.

        Useful for following one file across several runs (for example a file
        moved back and forth): unlike :meth:`lookup_by_source`, it also matches
        the destination side of a move/rename/trash entry.
        """
        target = str(Path(path))
        return [
            log
            for log in self.iter_all()
            if log.source == target or log.target == target
        ]

    def summarize_runs(self) -> list[dict[str, Any]]:
        """Group entries by run id, in first-seen (append) order."""
        runs: dict[str, dict[str, Any]] = {}
        for log in self.iter_all():
            run_id = log.run_id or ""
            entry = runs.get(run_id)
            if entry is None:
                entry = {
                    "run_id": run_id,
                    "automation_id": log.automation_id or "",
                    "entries": 0,
                    "applied": 0,
                    "rollbacks": 0,
                    "first": log.timestamp or "",
                    "last": log.timestamp or "",
                }
                runs[run_id] = entry
            entry["entries"] += 1
            if log.status == "applied":
                entry["applied"] += 1
            if log.reason == "rollback":
                entry["rollbacks"] += 1
            if log.timestamp:
                entry["last"] = log.timestamp
                if not entry["first"]:
                    entry["first"] = log.timestamp
        return list(runs.values())

    def _raw_lines(self) -> list[str]:
        try:
            if not self.path.exists():
                return []
            raw_bytes = safe_fs.read_bytes(self.path)
        except SafeFsError as exc:
            logger.warning("audit: cannot read %s: %s", self.path, exc)
            return []
        text = raw_bytes.decode("utf-8", errors="replace")
        return [line for line in text.splitlines() if line.strip()]

    def prune(
        self,
        *,
        older_than_seconds: float | None = None,
        keep_runs: int | None = None,
        now: float | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Drop whole runs from the audit log according to a retention policy.

        A run is removed only when *all* supplied conditions say so:

        * ``keep_runs`` — keep the newest ``N`` runs;
        * ``older_than_seconds`` — keep runs whose last entry is newer than
          the cutoff. With both options, a run is kept if it is in the newest
          ``N`` **or** newer than the cutoff.

        Whole-run granularity keeps each run's original actions and its
        rollback bookkeeping entries together, so a retained run still
        produces a correct rollback plan. Lines that cannot be parsed or have
        no run id are always kept (conservative). The rewrite is atomic and
        only happens when ``dry_run`` is false.

        Returns a summary dict; ``dropped_rollbackable`` counts the applied,
        rollback-eligible actions that will no longer be reversible.
        """
        if older_than_seconds is None and keep_runs is None:
            raise ValueError(
                "prune requires older_than_seconds and/or keep_runs"
            )
        if older_than_seconds is not None and older_than_seconds < 0:
            raise ValueError("older_than_seconds must be >= 0")
        if keep_runs is not None and keep_runs < 0:
            raise ValueError("keep_runs must be >= 0")

        lines = self._raw_lines()
        records: list[tuple[str, str]] = []
        runs: dict[str, dict[str, Any]] = {}
        for line in lines:
            raw = _try_json_object(line)
            run_id = raw.get("run_id") if raw is not None else None
            timestamp = raw.get("timestamp") if raw is not None else None
            run_id = run_id if isinstance(run_id, str) else ""
            timestamp = timestamp if isinstance(timestamp, str) else ""
            rollbackable = bool(
                raw is not None
                and raw.get("status") == "applied"
                and raw.get("rollback_eligible") is True
            )
            records.append((line, run_id))
            if not run_id:
                continue
            entry = runs.setdefault(
                run_id, {"last": "", "rollbackable": 0}
            )
            if timestamp:
                entry["last"] = timestamp
            if rollbackable:
                entry["rollbackable"] += 1

        ordered = list(runs)
        recent = (
            set(ordered[-keep_runs:]) if keep_runs is not None else set()
        )
        cutoff: float | None = None
        if older_than_seconds is not None:
            base = time.time() if now is None else float(now)
            cutoff = base - older_than_seconds

        kept_runs: set[str] = set()
        for run_id, info in runs.items():
            by_count = run_id in recent
            by_age = (
                True
                if cutoff is None
                else _is_after(info["last"], cutoff)
            )
            if keep_runs is not None and older_than_seconds is not None:
                keep = by_count or by_age
            elif keep_runs is not None:
                keep = by_count
            else:
                keep = by_age
            if keep:
                kept_runs.add(run_id)

        kept_lines = [
            line
            for line, run_id in records
            if not run_id or run_id in kept_runs
        ]
        dropped = [run_id for run_id in runs if run_id not in kept_runs]
        summary = {
            "total_runs": len(runs),
            "kept_runs": len(kept_runs),
            "dropped_runs": len(dropped),
            "total_entries": len(lines),
            "kept_entries": len(kept_lines),
            "dropped_entries": len(lines) - len(kept_lines),
            "dropped_rollbackable": sum(
                runs[run_id]["rollbackable"] for run_id in dropped
            ),
            "dry_run": dry_run,
        }
        if not dry_run and len(kept_lines) != len(lines):
            text = "\n".join(kept_lines)
            if text:
                text += "\n"
            safe_fs.atomic_write(self.path, text)
        return summary

    def build_rollback_plan(self, run_id: str) -> list[RollbackOp]:
        """Build a rollback plan for applied, rollback-eligible actions.

        The order follows the append order of the entries for ``run_id``.
        ``original_log_index`` is the zero-based index among that run's
        entries, which is stable and easy to assert in tests.
        """
        plan: list[RollbackOp] = []
        run_logs = [log for log in self.iter_all() if log.run_id == run_id]

        # Rollback is itself appended to the same run id. Record which
        # original entries have already been reversed so a second
        # ``audit rollback`` does not try to move the file back again.
        already_rolled_back: set[int] = set()
        for log in run_logs:
            if log.reason != "rollback":
                continue
            original = log.extra.get("original_log_index")
            if isinstance(original, int):
                already_rolled_back.add(original)

        for index, log in enumerate(run_logs):
            if index in already_rolled_back:
                continue
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
