"""Per-run execution context passed to every node."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.events import EventBus, bus
from lokdocsqr.core.models import ActionLog, Risk
from lokdocsqr.core.scope import ScopeManager

__all__ = ["ExecutionContext"]


@dataclass
class ExecutionContext:
    """Mutable state shared by the executor and nodes for one run."""

    scope: ScopeManager
    config: AppConfig
    audit: AuditLog
    run_id: str
    automation_id: str
    dry_run: bool = False
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("lokdocsqr")
    )
    events: EventBus = field(default_factory=lambda: bus)
    variables: dict[str, Any] = field(default_factory=dict)
    path_cache: dict[str, Path] = field(default_factory=dict)
    policy: Any = None
    verify_callback: Callable[..., Any] | None = None

    def resolved_path(self, scope: ScopeManager, raw: str | Path) -> Path:
        """Resolve and scope-check ``raw``, caching the result per run."""
        key = f"{id(scope)}:{raw!s}"
        cached = self.path_cache.get(key)
        if cached is not None:
            return cached

        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.config.base_dir / candidate

        resolved = scope.check(candidate, write=False)
        self.path_cache[key] = resolved
        return resolved

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an event through the configured event bus."""
        self.events.emit(event_type, payload)

    def new_action_log(
        self,
        *,
        node_id: str,
        action_type: str,
        source: str | Path | None = None,
        target: str | Path | None = None,
        status: str = "planned",
        reason: str = "",
        confidence: float = 0.0,
        risk: Risk = Risk.LOW,
        rollback_eligible: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> ActionLog:
        """Construct an ActionLog with this run's ids already filled in."""
        return ActionLog(
            run_id=self.run_id,
            node_id=node_id,
            automation_id=self.automation_id,
            action_type=action_type,
            source=str(source) if source is not None else None,
            target=str(target) if target is not None else None,
            status=status,
            reason=reason,
            confidence=confidence,
            risk=risk,
            rollback_eligible=rollback_eligible,
            extra=dict(extra or {}),
        )
