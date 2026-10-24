"""Workflow execution engine."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from lokdocsqr.core.errors import GraphValidationError, NodeExecutionError
from lokdocsqr.core.events import (
    EVENT_AUDIT_LOGGED,
    EVENT_NODE_FINISHED,
    EVENT_NODE_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
)
from lokdocsqr.core.models import (
    ActionLog,
    NodeResult,
    NodeState,
    ResolvedGraph,
    RunResult,
    WorkflowErrorHandling,
)

from .context import ExecutionContext

__all__ = ["Executor", "execute"]


def _utc_now() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _node_instance(handler: Any) -> Any:
    """Instantiate a node class, or return an already-instantiated handler."""
    if isinstance(handler, type):
        return handler()
    return handler


def _normalize_result(
    node_id: str,
    raw: Any,
    node_spec: Any,
    attempts: int,
    duration_seconds: float,
) -> NodeResult:
    """Normalize whatever a node returned into a NodeResult."""
    if isinstance(raw, NodeResult):
        raw.node_id = raw.node_id or node_id
        raw.attempts = max(raw.attempts, attempts)
        raw.duration_seconds = max(raw.duration_seconds, duration_seconds)
        return raw

    if isinstance(raw, dict):
        return NodeResult(
            node_id=node_id,
            state=NodeState.ENABLED,
            outputs=dict(raw),
            attempts=attempts,
            duration_seconds=duration_seconds,
        )

    output_ports = list(getattr(node_spec, "outputs", []) or [])
    if len(output_ports) == 1:
        return NodeResult(
            node_id=node_id,
            state=NodeState.ENABLED,
            outputs={output_ports[0].name: raw},
            attempts=attempts,
            duration_seconds=duration_seconds,
        )

    if len(output_ports) == 0:
        return NodeResult(
            node_id=node_id,
            state=NodeState.ENABLED,
            outputs={},
            attempts=attempts,
            duration_seconds=duration_seconds,
        )

    raise NodeExecutionError(
        f"node {node_id!r} returned unsupported value "
        f"{type(raw).__name__!r} with {len(output_ports)} output ports"
    )


def _retry_delay(
    handling: WorkflowErrorHandling, failed_attempt: int
) -> float:
    """Compute the pause before the next retry."""
    base = max(0.0, float(handling.retry_delay_seconds))
    backoff = (handling.backoff or "none").lower()
    if backoff == "linear":
        return base * failed_attempt
    if backoff == "exponential":
        return base * (2 ** max(0, failed_attempt - 1))
    return base


class Executor:
    """Executes a ResolvedGraph in topological order."""

    def __init__(
        self,
        ctx: ExecutionContext,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.ctx = ctx
        self._sleep = sleep
        self._clock = clock

    def execute(self, resolved_graph: ResolvedGraph) -> RunResult:
        """Execute ``resolved_graph`` and return a RunResult."""
        document = resolved_graph.document
        handling = document.settings.error_handling
        strategy = (handling.strategy or "fail").lower()
        if strategy not in {"fail", "retry", "continue"}:
            strategy = "fail"

        started_at = _utc_now()
        run_logs: list[ActionLog] = []
        node_results: dict[str, NodeResult] = {}
        errors: list[str] = []
        success = True
        aborted = False

        self.ctx.emit(
            EVENT_RUN_STARTED,
            {
                "run_id": self.ctx.run_id,
                "automation_id": self.ctx.automation_id,
                "dry_run": self.ctx.dry_run,
            },
        )

        node_by_id = {node.id: node for node in document.nodes if node.id}

        inbound_edges: dict[str, list[Any]] = {
            node_id: [] for node_id in resolved_graph.ordered_node_ids
        }
        inbound_parents: dict[str, list[str]] = {
            node_id: [] for node_id in resolved_graph.ordered_node_ids
        }
        for edge in document.edges:
            if edge.target in inbound_edges:
                inbound_edges[edge.target].append(edge)
                inbound_parents[edge.target].append(edge.source)

        for node_id in resolved_graph.ordered_node_ids:
            if aborted:
                break

            node_spec = node_by_id.get(node_id)
            if node_spec is None:
                message = f"node {node_id!r} not found in document"
                errors.append(message)
                success = False
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    state=NodeState.FAILED,
                    outputs={},
                    error=message,
                    attempts=0,
                    duration_seconds=0.0,
                )
                if strategy == "fail":
                    aborted = True
                continue

            skip = False
            for parent in inbound_parents.get(node_id, []):
                parent_result = node_results.get(parent)
                if parent_result is not None:
                    if parent_result.state != NodeState.ENABLED:
                        skip = True
                        break
                else:
                    parent_state = resolved_graph.states.get(parent)
                    if parent_state in (
                        NodeState.DISABLED,
                        NodeState.SKIPPED,
                        NodeState.FAILED,
                    ):
                        skip = True
                        break

            if skip:
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    state=NodeState.SKIPPED,
                    outputs={},
                    action_logs=[],
                    error=None,
                    attempts=0,
                    duration_seconds=0.0,
                )
                continue

            payload = self._build_payload(
                node_id,
                node_spec,
                inbound_edges.get(node_id, []),
                node_results,
            )

            handler = resolved_graph.handlers.get(node_id)
            if handler is None:
                message = f"no handler for node {node_id!r}"
                errors.append(message)
                success = False
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    state=NodeState.FAILED,
                    outputs={},
                    error=message,
                    attempts=0,
                    duration_seconds=0.0,
                )
                if strategy == "fail":
                    aborted = True
                continue

            try:
                node = _node_instance(handler)
            except Exception as exc:  # pragma: no cover - defensive
                message = f"node {node_id!r} instantiation failed: {exc}"
                errors.append(message)
                success = False
                node_results[node_id] = NodeResult(
                    node_id=node_id,
                    state=NodeState.FAILED,
                    outputs={},
                    error=message,
                    attempts=0,
                    duration_seconds=0.0,
                )
                if strategy == "fail":
                    aborted = True
                continue

            attempts = 0
            result: NodeResult | None = None

            while True:
                attempts += 1
                self.ctx.emit(
                    EVENT_NODE_STARTED,
                    {
                        "run_id": self.ctx.run_id,
                        "node_id": node_id,
                        "attempt": attempts,
                    },
                )

                attempt_started = self._clock()
                try:
                    if self.ctx.dry_run:
                        raw = node.dry_run(self.ctx, payload)
                    else:
                        raw = node.run(self.ctx, payload)

                    duration = max(0.0, self._clock() - attempt_started)
                    result = _normalize_result(
                        node_id, raw, node_spec, attempts, duration
                    )
                    break
                except Exception as exc:
                    duration = max(0.0, self._clock() - attempt_started)
                    if not isinstance(exc, NodeExecutionError):
                        exc = NodeExecutionError(
                            f"{type(exc).__name__}: {exc}"
                        )

                    if strategy == "retry" and attempts <= max(
                        0, int(handling.max_retries)
                    ):
                        delay = _retry_delay(handling, attempts)
                        if delay > 0:
                            self._sleep(delay)
                        continue

                    result = NodeResult(
                        node_id=node_id,
                        state=NodeState.FAILED,
                        outputs={},
                        action_logs=[],
                        error=str(exc),
                        attempts=attempts,
                        duration_seconds=duration,
                    )
                    break

            assert result is not None
            node_results[node_id] = result

            self.ctx.emit(
                EVENT_NODE_FINISHED,
                {
                    "run_id": self.ctx.run_id,
                    "node_id": node_id,
                    "state": result.state.value,
                    "attempts": result.attempts,
                    "duration": result.duration_seconds,
                },
            )

            for log in result.action_logs:
                if not log.run_id:
                    log.run_id = self.ctx.run_id
                if not log.automation_id:
                    log.automation_id = self.ctx.automation_id

                if log.status == "planned":
                    if self.ctx.dry_run:
                        log.status = "dry_run"
                    elif result.state == NodeState.ENABLED:
                        log.status = "applied"
                    else:
                        log.status = "failed"

                self.ctx.audit.append(log)
                run_logs.append(log)
                self.ctx.emit(
                    EVENT_AUDIT_LOGGED,
                    {"run_id": self.ctx.run_id, "action_log": log},
                )

            if result.state == NodeState.FAILED:
                success = False
                if result.error:
                    errors.append(result.error)
                if strategy == "fail":
                    aborted = True
                    break

        finished_at = _utc_now()
        rollback_plan = self.ctx.audit.build_rollback_plan(self.ctx.run_id)
        run_result = RunResult(
            run_id=self.ctx.run_id,
            automation_id=self.ctx.automation_id,
            dry_run=self.ctx.dry_run,
            success=success and not aborted,
            node_results=node_results,
            action_logs=run_logs,
            errors=errors,
            started_at=started_at,
            finished_at=finished_at,
            rollback_plan=rollback_plan,
        )

        self.ctx.emit(
            EVENT_RUN_FINISHED,
            {
                "run_id": self.ctx.run_id,
                "success": run_result.success,
                "errors": list(errors),
            },
        )
        return run_result

    def _build_payload(
        self,
        node_id: str,
        node_spec: Any,
        edges: list[Any],
        node_results: dict[str, NodeResult],
    ) -> dict[str, Any]:
        """Build the input payload for ``node_id`` from upstream outputs.

        Default values are applied and required input ports are validated
        even when the node has no inbound edges (trigger nodes): a
        trigger with a required input port and no default is a graph
        error, and a trigger with a default-valued input port must
        receive that default. This is what the Phase-4 acceptance
        checklist requires.
        """
        port_values: dict[str, list[Any]] = {}
        for edge in edges:
            source_result = node_results.get(edge.source)
            if source_result is None or source_result.state != NodeState.ENABLED:
                continue
            value = source_result.outputs.get(edge.source_port)
            port_values.setdefault(edge.target_port, []).append(value)

        payload: dict[str, Any] = {}
        for port_name, values in port_values.items():
            payload[port_name] = values[0] if len(values) == 1 else values

        for port_spec in node_spec.inputs:
            if port_spec.name in payload:
                continue
            if port_spec.default is not None:
                payload[port_spec.name] = port_spec.default
            elif port_spec.required:
                raise GraphValidationError(
                    f"node {node_id!r} required input port "
                    f"{port_spec.name!r} has no edge and no default"
                )

        return payload


def execute(
    resolved_graph: ResolvedGraph,
    ctx: ExecutionContext,
    *,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.perf_counter,
) -> RunResult:
    """Public helper: execute ``resolved_graph`` with ``ctx``."""
    return Executor(ctx, sleep=sleep, clock=clock).execute(resolved_graph)
