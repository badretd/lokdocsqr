"""Watch Folder trigger node (``triggers.watch_folder``).

The background watcher lives in :mod:`lokdocsqr.core.runtime`. This node
converts either the injected watcher event (``ctx.variables['trigger']``) or a
fresh read-only scan into output ports. It never starts a watcher itself and
never mutates the filesystem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._base import (
    as_string_tuple,
    event_changes,
    event_payload,
    files_from_event,
    normalise_files,
    scan_files_counted,
    trigger_event,
)


@register_node
class WatchFolderNode(Node):
    """Report files observed by a folder watcher (or a current scan)."""

    id = "triggers.watch_folder"
    title = "Watch Folder"
    description = (
        "React to create/modify/delete events in a folder, with glob filters "
        "and debounce handled by the runtime watcher."
    )
    retry_safe = True

    inputs: ClassVar[dict[str, Any]] = {
        "path": {"type": "string", "required": False},
        "patterns": {"type": "array", "required": False},
        "recursive": {"type": "bool", "required": False, "default": True},
        "events": {"type": "array", "required": False},
    }
    outputs: ClassVar[dict[str, Any]] = {
        "files": {"type": "FileRef[]"},
        "paths": {"type": "string[]"},
        "changes": {"type": "object[]"},
        "event": {"type": "object"},
        "payload": {"type": "object"},
    }

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        # A scan is read-only, so the preview is the same shape as a real run.
        return self._collect(ctx, payload)

    @staticmethod
    def _collect(ctx: Any, payload: Any) -> dict[str, Any]:
        values = payload if isinstance(payload, dict) else {}
        event = trigger_event(ctx)
        injected = event_payload(ctx)
        changes = event_changes(ctx)

        raw_path = values.get("path", injected.get("path"))
        recursive = bool(values.get("recursive", injected.get("recursive", True)))
        patterns = as_string_tuple(
            values.get("patterns", values.get("pattern")),
            as_string_tuple(injected.get("patterns", injected.get("pattern")), ("*",)),
        )

        files = files_from_event(ctx)
        scanned = len(files)
        if not files and changes:
            files = normalise_files(
                [
                    change.get("path")
                    for change in changes
                    if change.get("kind") != "deleted"
                    and isinstance(change.get("path"), (str, Path))
                ]
            )
            scanned = len(files)
        if not files and isinstance(raw_path, (str, Path)) and str(raw_path):
            files, scanned = scan_files_counted(
                ctx,
                raw_path,
                patterns=patterns,
                recursive=recursive,
            )

        return {
            "files": files,
            "paths": [item["path"] for item in files],
            "changes": changes,
            "event": event or {"type": "watch_folder"},
            "payload": dict(injected),
            "scanned": scanned,
        }
