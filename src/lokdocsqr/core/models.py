"""Domain dataclasses for Workflow specification v1.0 (plus Phase 4 extensions)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    # --- Workflow specification v1.0 public surface ---
    "WorkflowMetadata",
    "WorkflowScopeSettings",
    "WorkflowErrorHandling",
    "WorkflowSettings",
    "VariableSpec",
    "TriggerSpec",
    "PortSpec",
    "NodeSpec",
    "EdgeSpec",
    "WorkflowDocument",
    "ResolvedGraph",
    "NodeState",
    "Risk",
    # --- Phase 4 runtime extensions ---
    "FileRef",
    "ActionProposal",
    "ActionLog",
    "NodeResult",
    "RollbackOp",
    "RunResult",
]


class NodeState(str, Enum):
    """Execution state of a node in a workflow graph."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    SKIPPED = "skipped"
    FAILED = "failed"


class Risk(str, Enum):
    """Risk level attached to an action proposal."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class WorkflowMetadata:
    """Metadata block of a workflow document."""

    id: str
    name: str = ""
    description: str = ""
    version: str = "1.0"
    author: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class WorkflowScopeSettings:
    """Filesystem scope settings declared by a workflow."""

    roots: list[str] = field(default_factory=list)
    writable: list[str] = field(default_factory=list)
    allow_symlinks: bool = False
    policy_ref: str | None = None


@dataclass
class WorkflowErrorHandling:
    """Error-handling policy for a workflow."""

    strategy: str = "fail"
    max_retries: int = 0
    retry_delay_seconds: float = 0.0
    backoff: str = "none"


@dataclass
class WorkflowSettings:
    """Top-level workflow settings."""

    default_dry_run: bool = True
    scope: WorkflowScopeSettings = field(default_factory=WorkflowScopeSettings)
    error_handling: WorkflowErrorHandling = field(default_factory=WorkflowErrorHandling)
    max_concurrency: int = 1


@dataclass
class VariableSpec:
    """Declared workflow variable."""

    type: str = "string"
    value: Any = None
    description: str = ""


@dataclass
class TriggerSpec:
    """Workflow trigger declaration."""

    id: str
    type: str
    title: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortSpec:
    """Normalized node input or output port."""

    name: str
    type: str = "any"
    required: bool = True
    description: str = ""
    default: Any = None


@dataclass
class NodeSpec:
    """Declarative specification of a workflow node."""

    id: str
    type: str
    title: str = ""
    state: NodeState = NodeState.ENABLED
    params: dict[str, Any] = field(default_factory=dict)
    inputs: list[PortSpec] = field(default_factory=list)
    outputs: list[PortSpec] = field(default_factory=list)
    position: dict[str, float] = field(default_factory=dict)
    description: str = ""


@dataclass
class EdgeSpec:
    """Directed edge between two workflow nodes."""

    id: str
    source: str
    target: str
    source_port: str = "out"
    target_port: str = "in"
    condition: str | None = None
    label: str = ""


@dataclass
class WorkflowDocument:
    """Root object representing a workflow document."""

    spec_version: str
    metadata: WorkflowMetadata
    settings: WorkflowSettings = field(default_factory=WorkflowSettings)
    variables: dict[str, VariableSpec] = field(default_factory=dict)
    triggers: list[TriggerSpec] = field(default_factory=list)
    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    extra_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedGraph:
    """Graph prepared for execution."""

    document: WorkflowDocument
    ordered_node_ids: list[str] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)
    states: dict[str, NodeState] = field(default_factory=dict)


# Compatibility model retained for core.scope; not part of the v1.0 workflow
# public model surface.
@dataclass
class WorkspaceScope:
    """Resolved filesystem scope with writable roots and policy reference."""

    roots: list[Path] = field(default_factory=list)
    writable: list[Path] = field(default_factory=list)
    allow_symlinks: bool = False
    policy_ref: str | None = None


# --- Phase 4 models ---------------------------------------------------------
from datetime import datetime, timezone


def _utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    """Convert Paths and Enums into JSON-safe primitives recursively."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@dataclass
class FileRef:
    """Reference to a file known to a workflow."""

    id: str
    path: Path
    hash: str = ""
    mime: str = ""
    size: int = 0
    created_at: str | None = None
    modified_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionProposal:
    """Proposed action produced by a node before execution/approval."""

    type: str
    source: Path | None = None
    target: Path | None = None
    reason: str = ""
    confidence: float = 0.0
    risk: Risk = Risk.LOW


@dataclass
class ActionLog:
    """Auditable record of one action attempted by a node."""

    run_id: str = ""
    node_id: str = ""
    automation_id: str = ""
    action_type: str = ""
    source: str | None = None
    target: str | None = None
    status: str = "planned"
    reason: str = ""
    confidence: float = 0.0
    risk: Risk = Risk.LOW
    timestamp: str = field(default_factory=_utc_now_iso)
    rollback_eligible: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of this log entry."""
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "automation_id": self.automation_id,
            "action_type": self.action_type,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "reason": self.reason,
            "confidence": self.confidence,
            "risk": self.risk.value if isinstance(self.risk, Risk) else str(self.risk),
            "timestamp": self.timestamp,
            "rollback_eligible": self.rollback_eligible,
            "extra": _json_safe(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ActionLog":
        """Build an ActionLog from a JSON-decoded mapping.

        Raises:
            KeyError: if the required identity key ``action_type``
                is absent. Silent defaulting would mask corrupted
                audit lines and hide serialization bugs.
        """
        if "action_type" not in data:
            raise KeyError("action_type")
        risk_raw = data.get("risk", Risk.LOW)
        if isinstance(risk_raw, Risk):
            risk = risk_raw
        else:
            try:
                risk = Risk(str(risk_raw).lower())
            except ValueError:
                risk = Risk.LOW

        source = data.get("source")
        target = data.get("target")
        confidence_raw = data.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.0

        return cls(
            run_id=str(data.get("run_id") or ""),
            node_id=str(data.get("node_id") or ""),
            automation_id=str(data.get("automation_id") or ""),
            action_type=str(data.get("action_type") or ""),
            source=None if source is None else str(source),
            target=None if target is None else str(target),
            status=str(data.get("status") or "planned"),
            dry_run=bool(data.get("dry_run", False)),
            reason=str(data.get("reason") or ""),
            confidence=confidence,
            risk=risk,
            timestamp=str(data.get("timestamp") or _utc_now_iso()),
            rollback_eligible=bool(data.get("rollback_eligible", False)),
            extra=dict(data.get("extra") or {}),
        )


@dataclass
class NodeResult:
    """Result of executing one node."""

    node_id: str
    state: NodeState
    outputs: dict[str, Any] = field(default_factory=dict)
    action_logs: list[ActionLog] = field(default_factory=list)
    error: str | None = None
    attempts: int = 1
    duration_seconds: float = 0.0


@dataclass
class RollbackOp:
    """One reversible filesystem operation."""

    action: str
    source: Path
    target: Path
    original_log_index: int


@dataclass
class RunResult:
    """Result of a complete workflow run."""

    run_id: str
    automation_id: str
    dry_run: bool
    success: bool
    node_results: dict[str, NodeResult] = field(default_factory=dict)
    action_logs: list[ActionLog] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_utc_now_iso)
    finished_at: str | None = None
    rollback_plan: list[RollbackOp] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dictionary representation of this run."""
        return {
            "run_id": self.run_id,
            "automation_id": self.automation_id,
            "dry_run": self.dry_run,
            "success": self.success,
            "node_results": {
                node_id: {
                    "node_id": result.node_id,
                    "state": (
                        result.state.value
                        if isinstance(result.state, Enum)
                        else str(result.state)
                    ),
                    "outputs": _json_safe(result.outputs),
                    "action_logs": [
                        log.to_dict() for log in result.action_logs
                    ],
                    "error": result.error,
                    "attempts": result.attempts,
                    "duration_seconds": result.duration_seconds,
                }
                for node_id, result in self.node_results.items()
            },
            "action_logs": [log.to_dict() for log in self.action_logs],
            "errors": list(self.errors),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "rollback_plan": [
                {
                    "action": op.action,
                    "source": str(op.source),
                    "target": str(op.target),
                    "original_log_index": op.original_log_index,
                }
                for op in self.rollback_plan
            ],
        }
