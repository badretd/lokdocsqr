"""Synchronous, framework-agnostic event bus for the LokDocSQR core."""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Well-known event type identifiers (avoids magic strings elsewhere).
EVENT_NODE_STARTED = "node_started"
EVENT_NODE_FINISHED = "node_finished"
EVENT_RUN_STARTED = "run_started"
EVENT_RUN_FINISHED = "run_finished"
EVENT_AUDIT_LOGGED = "audit_logged"


class EventBus:
    """Minimal synchronous observer with per-subscriber error isolation."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[Any], None]]] = {}

    def subscribe(
        self, event_type: str, callback: Callable[[Any], None]
    ) -> None:
        """Register ``callback`` to be invoked when ``event_type`` is emitted."""
        self._subscribers.setdefault(event_type, []).append(callback)

    def emit(self, event_type: str, payload: Any) -> None:
        """Invoke every subscriber for ``event_type``, isolating failures.

        A raising subscriber must not prevent the remaining subscribers from
        receiving the event; the failure is logged and dispatch continues.
        """
        # Snapshot so subscribers may (un)subscribe during dispatch safely.
        for callback in list(self._subscribers.get(event_type, ())):
            try:
                callback(payload)
            except Exception:
                logger.exception(
                    "Event subscriber failed for event_type=%r", event_type
                )

    def clear(self) -> None:
        """Remove all registered subscribers."""
        self._subscribers.clear()


# Global singleton bus used throughout the application.
bus = EventBus()
