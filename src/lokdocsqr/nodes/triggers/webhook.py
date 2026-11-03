"""Webhook / external trigger node (``triggers.webhook``).

LokDocSQR is local-only: there is no built-in HTTP listener. A webhook
trigger is dispatched locally through the CLI
(``lokdocsqr trigger <automation> --type webhook --payload ...``) or through
:meth:`Runtime.trigger_webhook`. This node normalizes the received JSON
payload and headers for downstream nodes.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._base import event_payload, trigger_event


@register_node
class WebhookNode(Node):
    """Expose an externally supplied webhook payload as node outputs."""

    id = "triggers.webhook"
    title = "Webhook / External Trigger"
    description = "Accept an external JSON payload and pass it to the graph."
    retry_safe = True

    inputs: ClassVar[dict[str, Any]] = {
        "payload": {"type": "object", "required": False},
        "headers": {"type": "object", "required": False},
    }
    outputs: ClassVar[dict[str, Any]] = {
        "payload": {"type": "object"},
        "headers": {"type": "object"},
        "event": {"type": "object"},
    }

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._collect(ctx, payload)

    @staticmethod
    def _collect(ctx: Any, payload: Any) -> dict[str, Any]:
        values = payload if isinstance(payload, dict) else {}
        injected = event_payload(ctx)

        body = values.get("payload")
        if not isinstance(body, Mapping):
            body = injected.get("payload")
        if not isinstance(body, Mapping):
            body = injected

        headers = values.get("headers")
        if not isinstance(headers, Mapping):
            headers = injected.get("headers")
        if not isinstance(headers, Mapping):
            headers = {}

        event = trigger_event(ctx) or {"type": "webhook"}
        return {
            "payload": dict(body),
            "headers": dict(headers),
            "event": event,
        }
