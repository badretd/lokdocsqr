"""Domain dataclasses shared across LokDocSQR."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
    "ActionLog",
    "ActionProposal",
    "Automation",
    "Edge",
    "FileRef",
    "NodeResult",
    "NodeSpec",
    "NodeState",
    "RawGraph",
    "ResolvedGraph",
    "Risk",
    "WorkspaceScope",
]


class NodeState(str, Enum):
    """Execution state of a node in an automation graph."""

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
class FileRef:
    """Reference to a file known to the system."""

    id: str
    path: Path
    hash: str | None = None
    mime: str | None = None
    size: int = 0
    timestamps: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionProposal:
    """Proposed file action produced by planning or rules."""

    type: str
    source: Path | None = None
    target: Path | None = None
    reason: str = ""
    confidence: float = 1.0
    risk: Risk = Risk.LOW
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionLog:
    """Audit record describing an action that was applied, skipped, or failed."""

    run_id: str
    automation_id: str
    node_id: str
    action: str
    status: str
    timestamp: float
    source: Path | None = None
    target: Path | None = None
    confirmed_by: str = "auto"
    rollback: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeSpec:
    """Declarative specification of a graph node."""

    id: str
    type: str
    params: dict[str, Any] = field(default_factory=dict)
    state: NodeState = NodeState.ENABLED


@dataclass
class Edge:
    """Directed edge between two graph nodes."""

    src: str
    dst: str


@dataclass
class Automation:
    """Automation graph definition."""

    id: str
    name: str = ""
    nodes: list[NodeSpec] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RawGraph:
    """Unresolved graph as parsed from an automation file."""

    automation: Automation


@dataclass
class ResolvedGraph:
    """Graph prepared for execution."""

    ordered_node_ids: list[str] = field(default_factory=list)
    handlers: dict[str, Any] = field(default_factory=dict)
    states: dict[str, NodeState] = field(default_factory=dict)
    scope: WorkspaceScope | None = None
    templates: dict[str, str] = field(default_factory=dict)


@dataclass
class NodeResult:
    """Result returned by a node after execution or dry-run."""

    node_id: str
    status: str
    outputs: dict[str, Any] = field(default_factory=dict)
    actions: list[ActionLog] = field(default_factory=list)
    error: str | None = None


@dataclass
class WorkspaceScope:
    """Resolved filesystem scope with writable roots and policy reference."""

    roots: list[Path] = field(default_factory=list)
    writable: list[Path] = field(default_factory=list)
    allow_symlinks: bool = False
    policy_ref: str | None = None
