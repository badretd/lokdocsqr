"""Manual Run trigger node (``triggers.manual_run``).

The runtime drives manual triggers through :meth:`Runtime.trigger`; this node
normalizes the selected files / payload into ordinary output ports so the rest
of the graph does not need to know how the run started. Read-only.
"""
from __future__ import annotations

from typing import Any, ClassVar

from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._base import event_payload, files_from_event, normalise_files


@register_node
class ManualRunNode(Node):
    """Turn a manual invocation's files and payload into node outputs."""

    id = "triggers.manual_run"
    title = "Manual Run"
    description = "Start an automation with user-selected files or a payload."
    retry_safe = True

    inputs: ClassVar[dict[str, Any]] = {
        "files": {"type": "array", "required": False},
        "payload": {"type": "object", "required": False},
    }
    outputs: ClassVar[dict[str, Any]] = {
        "files": {"type": "FileRef[]"},
        "paths": {"type": "string[]"},
        "event": {"type": "object"},
        "payload": {"type": "object"},
    }

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    @staticmethod
    def _collect(ctx: Any, payload: Any) -> dict[str, Any]:
        values = payload if isinstance(payload, dict) else {}
        raw_files = values.get("files")
        if raw_files is None:
            raw_files = values.get("paths")
        files = normalise_files(raw_files)
        if not files:
            files = files_from_event(ctx)

        event_payload_value = values.get("payload")
        if not isinstance(event_payload_value, dict):
            outer = event_payload(ctx)
            nested = outer.get("payload")
            event_payload_value = nested if isinstance(nested, dict) else outer

        event = {
            "type": "manual_run",
            "automation_id": getattr(ctx, "automation_id", ""),
            "files": [item["path"] for item in files],
            "payload": dict(event_payload_value),
        }
        return {
            "files": files,
            "paths": [item["path"] for item in files],
            "event": event,
            "payload": dict(event_payload_value),
        }
