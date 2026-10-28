"""Workflow execution engine."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from lokdocsqr.core.errors import (
    GraphValidationError,
    NodeExecutionError,
    PolicyBlockedError,
    PolicyError,
    PolicyTimeoutError,
)
from lokdocsqr.core.events import (
    EVENT_AUDIT_LOGGED,
    EVENT_NODE_FINISHED,
    EVENT_NODE_STARTED,
    EVENT_RUN_FINISHED,
    EVENT_RUN_STARTED,
)
from lokdocsqr.core.models import (
    ActionLog,
    ActionProposal,
    NodeResult,
    NodeState,
    PolicyAction,
    ResolvedGraph,
    RunResult,
    VerifyAction,
    VerifyResult,
    WorkflowErrorHandling,
)
from lokdocsqr.core.planning.registry import classify_node, extract_paths
from lokdocsqr.core.planning.validator import validate_proposal

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


def _check_policy_and_verify(
    node: Any,
    ctx: "ExecutionContext",
    payload: dict[str, Any],
    *,
    node_id: str | None = None,
) -> dict[str, Any]:
    """7c: enforce the policy + verify gate before ``node.run``.

    Returns the payload to execute with — the original ``payload`` on
    ALLOW / APPROVE / no-policy, or ``VerifyResult.new_payload`` after a
    validated EDIT.

    Raises a :class:`PolicyError` subclass on any non-ALLOW / non-APPROVE
    outcome (fail-closed, R26):

      * ``PolicyBlockedError`` — BLOCK, REQUIRE_VERIFY with no callback,
        or REJECT.
      * ``PolicyTimeoutError`` — verify TIMEOUT.
      * ``PolicyError`` — EDIT without ``new_payload``, or unknown
        ``VerifyAction``.

    Design notes:
      * R37: when ``ctx.policy is None``, returns ``payload`` unchanged
        and the caller behaves exactly as it did before 7c.
      * R38: called exactly once per node, *before* the retry loop, so
        retries reuse the same verdict and the same payload.
      * R39: only reads ``ctx`` — no mutations.
      * R35: an EDIT verdict is re-validated via ``validate_proposal``
        before returning the edited payload.
    """
    if ctx.policy is None:
        return payload

    if node_id is None:
        raw_id = getattr(node, "id", None)
        node_id = raw_id if isinstance(raw_id, str) else ""
    if not node_id:
        raise PolicyError(
            f"policy gate: cannot resolve node_id from "
            f"{type(node).__name__}"
        )

    operation = classify_node(node_id)
    target_paths = extract_paths(payload, node_id)
    proposal = ActionProposal(
        node_id=node_id,
        operation=operation,
        target_paths=target_paths,
        payload=dict(payload),
        summary=f"{operation}: {node_id}",
    )
    # Defense in depth: validate the proposal before the engine sees it.
    validate_proposal(proposal, ctx)

    decision = ctx.policy.decide(proposal)
    if decision.action == PolicyAction.ALLOW:
        return payload
    if decision.action == PolicyAction.BLOCK:
        raise PolicyBlockedError(f"{node_id}: {decision.reason}")

    # REQUIRE_VERIFY
    if ctx.verify_callback is None:
        raise PolicyBlockedError(
            f"{node_id}: verify required but no callback"
        )
    verify_result = ctx.verify_callback(proposal)
    if verify_result.action == VerifyAction.APPROVE:
        return payload
    if verify_result.action == VerifyAction.REJECT:
        raise PolicyBlockedError(
            f"{node_id}: rejected: {verify_result.reason}"
        )
    if verify_result.action == VerifyAction.TIMEOUT:
        raise PolicyTimeoutError(f"{node_id}: verify timeout")
    if verify_result.action == VerifyAction.EDIT:
        if verify_result.new_payload is None:
            raise PolicyError(f"{node_id}: EDIT without new_payload")
        edited = ActionProposal(
            node_id=node_id,
            operation=classify_node(node_id),
            target_paths=extract_paths(verify_result.new_payload, node_id),
            payload=dict(verify_result.new_payload),
            summary=f"{operation}: {node_id} (edited)",
        )
        # R35: do not trust the callback — re-validate the edit.
        validate_proposal(edited, ctx)
        return dict(verify_result.new_payload)

    raise PolicyError(
        f"{node_id}: unknown verify action {verify_result.action!r}"
    )


def _read_node_retry_policy(
    node_spec: Any, node_id: str
) -> dict[str, Any] | None:
    """Read and validate per-node ``retry_policy`` from ``spec.params``.

    Returns ``None`` when the node has no retry policy. A malformed policy
    raises ``NodeExecutionError``; the executor turns that into a node
    failure rather than an executor crash.
    """
    params = getattr(node_spec, "params", None)
    if params is None:
        return None
    if not isinstance(params, dict):
        raise NodeExecutionError(
            f"node {node_id!r} params must be a mapping for retry_policy"
        )
    policy = params.get("retry_policy")
    if policy is None:
        return None
    if not isinstance(policy, dict):
        raise NodeExecutionError(
            f"node {node_id!r} retry_policy must be a mapping"
        )
    try:
        attempts = int(policy.get("attempts", 1))
        backoff = float(policy.get("backoff", 0.0))
    except (TypeError, ValueError) as exc:
        raise NodeExecutionError(
            f"node {node_id!r} invalid retry_policy numeric field: {exc}"
        ) from exc
    if attempts < 1:
        raise NodeExecutionError(
            f"node {node_id!r} retry_policy attempts must be >= 1"
        )
    if backoff < 0:
        raise NodeExecutionError(
            f"node {node_id!r} retry_policy backoff must be >= 0"
        )
    raw_retry_on = policy.get("retry_on", ())
    if isinstance(raw_retry_on, str):
        retry_on = (raw_retry_on,)
    elif isinstance(raw_retry_on, (list, tuple)):
        retry_on = tuple(raw_retry_on)
        if any(not isinstance(name, str) for name in retry_on):
            raise NodeExecutionError(
                f"node {node_id!r} retry_policy retry_on must contain "
                f"exception class names"
            )
    else:
        raise NodeExecutionError(
            f"node {node_id!r} retry_policy retry_on must be a sequence"
        )
    return {
        "attempts": attempts,
        "backoff": backoff,
        "retry_on": retry_on,
    }


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

            # 7c: policy + verify gate. Dry-run is simulated and
            # deliberately unguarded; the gate runs once per node,
            # before the retry loop (R38). Payload may be replaced
            # by an EDIT verdict.
            if not self.ctx.dry_run:
                payload = _check_policy_and_verify(
                    node, self.ctx, payload, node_id=node_id
                )

            retry_policy: dict[str, Any] | None = None
            retry_policy_error: NodeExecutionError | None = None
            if not self.ctx.dry_run:
                try:
                    retry_policy = _read_node_retry_policy(
                        node_spec, node_id
                    )
                except NodeExecutionError as exc:
                    retry_policy_error = exc
                else:
                    if retry_policy is not None and not getattr(
                        node, "retry_safe", False
                    ):
                        retry_policy_error = NodeExecutionError(
                            f"retry_policy on non-retry-safe node "
                            f"{node_id!r} (retry_safe=False)"
                        )

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
                    if retry_policy_error is not None:
                        raise retry_policy_error
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

                    if retry_policy_error is not None:
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

                    if not self.ctx.dry_run:
                        if retry_policy is not None:
                            if (
                                type(exc).__name__
                                in retry_policy["retry_on"]
                                and attempts < retry_policy["attempts"]
                            ):
                                delay = retry_policy["backoff"] * (
                                    2 ** (attempts - 1)
                                )
                                if delay > 0:
                                    self.ctx.clock.sleep(delay)
                                continue
                        elif strategy == "retry" and attempts <= max(
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
