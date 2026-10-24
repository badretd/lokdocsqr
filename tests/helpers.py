"""Shared helpers for Phase-4 tests."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PortSpec,
    ResolvedGraph,
    WorkflowDocument,
    WorkflowErrorHandling,
    WorkflowMetadata,
    WorkflowSettings,
    WorkspaceScope,
)
from lokdocsqr.core.scope import ScopeManager


def make_scope(root: Path) -> ScopeManager:
    return ScopeManager(WorkspaceScope(roots=[root], writable=[root]))


def make_ctx(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    run_id: str = "run-1",
    automation_id: str = "auto-1",
    events: Any = None,
) -> ExecutionContext:
    scope = make_scope(tmp_path)
    return ExecutionContext(
        scope=scope,
        config=AppConfig(base_dir=tmp_path),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        run_id=run_id,
        automation_id=automation_id,
        dry_run=dry_run,
        events=events if events is not None else EventBus(),
    )


def port(name: str, *, required: bool = True, default: Any = None) -> PortSpec:
    return PortSpec(name=name, required=required, default=default)


def node_spec(
    node_id: str,
    *,
    inputs: list[PortSpec] | None = None,
    outputs: list[PortSpec] | None = None,
    type_: str = "fake",
) -> NodeSpec:
    return NodeSpec(
        id=node_id,
        type=type_,
        inputs=list(inputs or []),
        outputs=list(outputs or []),
    )


def edge(
    source: str,
    target: str,
    *,
    source_port: str = "out",
    target_port: str = "in",
) -> EdgeSpec:
    return EdgeSpec(
        id=f"{source}->{target}:{source_port}->{target_port}",
        source=source,
        target=target,
        source_port=source_port,
        target_port=target_port,
    )


def make_doc(
    nodes,
    edges,
    *,
    strategy: str = "fail",
    max_retries: int = 0,
    retry_delay_seconds: float = 0.0,
    backoff: str = "none",
) -> WorkflowDocument:
    return WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="auto-test"),
        settings=WorkflowSettings(
            error_handling=WorkflowErrorHandling(
                strategy=strategy,
                max_retries=max_retries,
                retry_delay_seconds=retry_delay_seconds,
                backoff=backoff,
            )
        ),
        nodes=list(nodes),
        edges=list(edges),
    )


def resolved(doc, ordered, handlers, states=None) -> ResolvedGraph:
    if states is None:
        states = {nid: NodeState.ENABLED for nid in ordered}
    return ResolvedGraph(
        document=doc,
        ordered_node_ids=list(ordered),
        handlers=dict(handlers),
        states=dict(states),
    )
