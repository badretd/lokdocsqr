"""Domain dataclasses for Workflow specification v1.0."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

__all__ = [
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
