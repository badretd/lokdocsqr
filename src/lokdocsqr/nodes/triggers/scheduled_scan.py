"""Scheduled Scan trigger node (``triggers.scheduled_scan``).

The cron loop lives in :mod:`lokdocsqr.core.runtime`; this node performs the
read-only folder scan for a scheduled run and exposes the schedule metadata.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._base import (
    as_string_tuple,
    event_payload,
    files_from_event,
    scan_files,
    trigger_event,
)


@register_node
class ScheduledScanNode(Node):
    """Scan a folder when a cron/interval schedule fires."""

    id = "triggers.scheduled_scan"
    title = "Scheduled Scan"
    description = (
        "Run a read-only folder scan on a cron or fixed-interval schedule."
    )
    retry_safe = True

    inputs: ClassVar[dict[str, Any]] = {
        "path": {"type": "string", "required": False},
        "patterns": {"type": "array", "required": False},
        "recursive": {"type": "bool", "required": False, "default": True},
        "cron": {"type": "string", "required": False},
    }
    outputs: ClassVar[dict[str, Any]] = {
        "files": {"type": "FileRef[]"},
        "paths": {"type": "string[]"},
        "event": {"type": "object"},
        "payload": {"type": "object"},
        "due_at": {"type": "number"},
    }

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    @staticmethod
    def _collect(ctx: Any, payload: Any) -> dict[str, Any]:
        values = payload if isinstance(payload, dict) else {}
        event = trigger_event(ctx)
        injected = event_payload(ctx)

        raw_path = values.get("path", injected.get("path"))
        recursive = bool(values.get("recursive", injected.get("recursive", True)))
        patterns = as_string_tuple(
            values.get("patterns", values.get("pattern")),
            as_string_tuple(injected.get("patterns", injected.get("pattern")), ("*",)),
        )

        files = files_from_event(ctx)
        if not files and isinstance(raw_path, (str, Path)) and str(raw_path):
            files = scan_files(
                ctx,
                raw_path,
                patterns=patterns,
                recursive=recursive,
            )

        due_at = injected.get("scheduled_for")
        if not isinstance(due_at, (int, float)) or isinstance(due_at, bool):
            due_at = values.get("due_at")
        if not isinstance(due_at, (int, float)) or isinstance(due_at, bool):
            due_at = None

        return normalise_result(
            files=files,
            event=event or {"type": "scheduled_scan"},
            payload=dict(injected),
            due_at=due_at,
        )


def normalise_result(
    *,
    files: list[dict[str, Any]],
    event: dict[str, Any],
    payload: dict[str, Any],
    due_at: float | None,
) -> dict[str, Any]:
    return {
        "files": files,
        "paths": [item["path"] for item in files],
        "event": event,
        "payload": payload,
        "due_at": due_at,
    }
