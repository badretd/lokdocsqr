"""Build an ActionPlan from a resolved graph (Phase 7b, extended in 7d-a).

Invariants:
    * Only ``Node.dry_run`` is called; ``Node.run`` is never invoked (R33).
    * No filesystem mutation: ``build_action_plan`` reads ``graph``/``ctx``
      and calls :func:`preview_diff`, which itself only reads (R28).
    * Determinism (R32): the plan is a pure function of (graph, ctx, policy).
      Node order follows ``graph.ordered_node_ids``; ``target_paths`` inside
      each proposal are sorted by ``extract_paths``.
    * No partial success: a node either contributes (proposal, decision,
      preview) or appears as a :class:`PlanError`. Never both.

Phase 7d-a additions (flat graphs only):
    * Payloads can be resolved from upstream dry-run outputs using
      ``document.edges``. The edge semantics mirror
      ``core.executor.engine.Executor._build_payload``: source outputs are
      read by ``(edge.source, edge.source_port)`` and assigned to
      ``edge.target_port``; multiple inbound edges to one target port become
      a list. There is no ``$node.port`` string syntax in the raw graph.
    * A local ``planning_vars`` dict is threaded through the dry-run pass.
      For real :class:`ExecutionContext` instances a shallow copy is created
      with ``dataclasses.replace(ctx, variables=dict(planning_vars))`` so the
      node cannot observe or mutate ``ctx.variables``. Duck-typed / mocked
      contexts (not dataclass instances) are passed through unchanged to
      preserve 7b backward compatibility.
    * After a dry-run, any dict result carrying ``"variables": dict`` updates
      ``planning_vars``. This is intentionally universal, not keyed by
      node_id, so future variable-producing nodes do not require another
      builder change. ``ctx.variables`` is never mutated.
    * Nested graphs remain fail-closed in 7d-a: subflow/loop_batch dry_run
      raises ``NodeExecutionError`` and build_action_plan records PlanError.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from ..models import ActionProposal, PolicyAction, PolicyDecision
from ..policy.engine import PolicyEngine
from ..preview import ChangePreview, preview_diff
from .plan import ActionPlan, PlanError
from .registry import classify_node, extract_paths
from .validator import ProposalValidationError, validate_proposal

__all__ = ["build_action_plan"]

_DYNAMIC_PAYLOAD_NODE_TYPES: frozenset[str] = frozenset(
    {"utils.path_template", "utils.set_variables"}
)


def _graph_needs_dynamic_payload(specs: dict[str, Any]) -> bool:
    """True when the graph contains nodes participating in 7d-a payload flow."""
    for spec in specs.values():
        node_type = getattr(spec, "type", "")
        if isinstance(node_type, str) and node_type in _DYNAMIC_PAYLOAD_NODE_TYPES:
            return True
    return False


def _inbound_edges_by_target(doc: Any) -> dict[str, list[Any]]:
    """Group document edges by target node id."""
    grouped: dict[str, list[Any]] = {}
    edges = getattr(doc, "edges", None)
    if not isinstance(edges, list):
        return grouped
    for edge in edges:
        target = getattr(edge, "target", None)
        if isinstance(target, str) and target:
            grouped.setdefault(target, []).append(edge)
    return grouped


def _isolated_ctx(ctx: Any, planning_vars: dict[str, Any]) -> Any:
    """Return a context view with isolated planning variables.

    For real dataclass contexts (``ExecutionContext`` and friends) this is a
    shallow copy whose ``variables`` field is a fresh ``dict(planning_vars)``.
    Duck-typed/mocked contexts (not dataclass instances, or dataclasses
    without a ``variables`` field) are returned unchanged so 7b callers keep
    their original behaviour and their mocks keep their identities.
    """
    if not dataclasses.is_dataclass(ctx) or isinstance(ctx, type):
        return ctx
    try:
        return dataclasses.replace(ctx, variables=dict(planning_vars))
    except (TypeError, ValueError):
        return ctx


def _node_payload(
    node_spec: Any,
    *,
    inbound_edges: list[Any] | None = None,
    outputs_by_node: dict[str, dict[str, Any]] | None = None,
    resolve_edges: bool = False,
) -> dict[str, Any]:
    """Return the node payload, optionally resolving inbound edge values.

    The static part is ``node_spec.params``. When ``resolve_edges`` is true,
    inbound edges are resolved by ``(source, source_port) -> target_port``
    using upstream dry-run outputs. This mirrors the edge semantics of
    ``core.executor.engine.Executor._build_payload``; there is no
    ``$node.port`` string syntax in the raw graph.
    """
    params = getattr(node_spec, "params", None)
    payload: dict[str, Any] = dict(params) if isinstance(params, dict) else {}
    if not resolve_edges:
        return payload

    port_values: dict[str, list[Any]] = {}
    outputs = outputs_by_node or {}
    for edge in inbound_edges or []:
        source = getattr(edge, "source", None)
        source_port = getattr(edge, "source_port", None)
        target_port = getattr(edge, "target_port", None)
        if not (
            isinstance(source, str)
            and isinstance(source_port, str)
            and isinstance(target_port, str)
        ):
            continue
        source_outputs = outputs.get(source)
        if source_outputs is None:
            continue
        port_values.setdefault(target_port, []).append(
            source_outputs.get(source_port)
        )

    for port_name, values in port_values.items():
        payload[port_name] = values[0] if len(values) == 1 else values

    inputs = getattr(node_spec, "inputs", None)
    if isinstance(inputs, list):
        for port_spec in inputs:
            name = getattr(port_spec, "name", None)
            if not isinstance(name, str) or name in payload:
                continue
            default = getattr(port_spec, "default", None)
            if default is not None:
                payload[name] = default
                continue
            required = getattr(port_spec, "required", True)
            if required:
                raise ValueError(
                    f"required input port {name!r} has no edge and no default"
                )

    return payload


def _dry_run_outputs(result: Any) -> dict[str, Any]:
    """Return output-port values from a dry-run result."""
    if isinstance(result, dict):
        return dict(result)
    outputs = getattr(result, "outputs", None)
    if isinstance(outputs, dict):
        return dict(outputs)
    return {}


def _merge_planning_vars(
    planning_vars: dict[str, Any], result: Any
) -> None:
    """Merge non-reserved ``result["variables"]`` into planning variables.

    Universal by design: any node dry-run result that is a dict and carries
    a ``variables`` dict contributes to the planning pass. Reserved keys
    (``RESERVED_VARIABLES`` from :mod:`core.executor.context`) are filtered
    out here as defense-in-depth; ``utils.set_variables._preview`` also
    filters them on its own.
    """
    if not isinstance(result, dict):
        return
    variables = result.get("variables")
    if not isinstance(variables, dict):
        return
    from ..executor.context import RESERVED_VARIABLES
    for key, value in variables.items():
        if key not in RESERVED_VARIABLES:
            planning_vars[key] = value


def build_action_plan(
    graph: Any,
    ctx: Any,
    policy: PolicyEngine,
) -> ActionPlan:
    """Build an :class:`ActionPlan` for ``graph`` without running any node.

    Phase 7d-a keeps a local ``planning_vars`` mapping. For real dataclass
    contexts every ``node.dry_run`` receives a shallow copy created via
    ``dataclasses.replace(ctx, variables=dict(planning_vars))``; mocked or
    duck-typed contexts are passed through unchanged (7b compat). After the
    call, a dict result containing ``"variables"`` updates ``planning_vars``.
    ``ctx.variables`` is never mutated.
    """
    doc = getattr(graph, "document", None)
    specs: dict[str, Any] = {}
    if doc is not None:
        for spec in getattr(doc, "nodes", []) or []:
            node_id = getattr(spec, "id", "")
            if isinstance(node_id, str) and node_id:
                specs[node_id] = spec

    handlers = getattr(graph, "handlers", {}) or {}
    ordered_ids = list(getattr(graph, "ordered_node_ids", []) or [])

    resolve_edges = _graph_needs_dynamic_payload(specs)
    edges_by_target: dict[str, list[Any]] = (
        _inbound_edges_by_target(doc) if resolve_edges else {}
    )

    planning_vars: dict[str, Any] = {}
    outputs_by_node: dict[str, dict[str, Any]] = {}

    proposals: list[ActionProposal] = []
    decisions: list[PolicyDecision] = []
    previews: list[ChangePreview] = []
    errors: list[PlanError] = []

    for node_id in ordered_ids:
        node = handlers.get(node_id)
        if node is None:
            errors.append(
                PlanError(
                    node_id=node_id,
                    error="no handler in resolved graph",
                    exc_type="KeyError",
                )
            )
            continue

        # Defensive: some registries hand out classes, not instances.
        if isinstance(node, type):
            try:
                node = node()
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    PlanError(
                        node_id=node_id,
                        error=f"cannot instantiate handler: {exc}",
                        exc_type=type(exc).__name__,
                    )
                )
                continue

        dry_run = getattr(node, "dry_run", None)
        if dry_run is None or not callable(dry_run):
            errors.append(
                PlanError(
                    node_id=node_id,
                    error="node has no callable dry_run method",
                    exc_type="AttributeError",
                )
            )
            continue

        # Build the payload first; if the graph asks for a required input
        # port with neither edge nor default, this raises here and is
        # reported as a per-node PlanError, same as the executor does.
        try:
            payload = _node_payload(
                specs.get(node_id),
                inbound_edges=edges_by_target.get(node_id, []),
                outputs_by_node=outputs_by_node,
                resolve_edges=resolve_edges,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(
                PlanError(
                    node_id=node_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
            )
            continue

        # Isolation is best-effort and MUST NOT be reported as a node
        # failure. If the context is not a real dataclass we fall back to
        # the original object (7b behaviour).
        node_ctx = _isolated_ctx(ctx, planning_vars)

        try:
            dry_run_result = dry_run(node_ctx, payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                PlanError(
                    node_id=node_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
            )
            continue

        _merge_planning_vars(planning_vars, dry_run_result)
        outputs_by_node[node_id] = _dry_run_outputs(dry_run_result)

        try:
            # 7d-a-fix: registry lookups are keyed by NodeSpec.type,
            # not instance id. Keep proposal.node_id = instance id.
            node_spec = specs.get(node_id)
            node_type = getattr(node_spec, "type", None) or node_id
            target_paths = extract_paths(payload, node_type)
            operation = classify_node(node_type)
            summary = f"{operation}: {', '.join(target_paths) or 'n/a'}"
            proposal = ActionProposal(
                node_id=node_id,
                operation=operation,
                target_paths=target_paths,
                payload=dict(payload),
                summary=summary,
            )
            validate_proposal(proposal, ctx)
        except ProposalValidationError as exc:
            errors.append(
                PlanError(
                    node_id=node_id,
                    error=str(exc),
                    exc_type="ProposalValidationError",
                )
            )
            continue
        except Exception as exc:  # noqa: BLE001
            errors.append(
                PlanError(
                    node_id=node_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
            )
            continue

        decision = policy.decide(proposal)
        preview = preview_diff(proposal, ctx)

        proposals.append(proposal)
        decisions.append(decision)
        previews.append(preview)

    blocked = (
        any(d.action == PolicyAction.BLOCK for d in decisions) or bool(errors)
    )

    return ActionPlan(
        proposals=tuple(proposals),
        decisions=tuple(decisions),
        previews=tuple(previews),
        errors=tuple(errors),
        blocked=blocked,
    )
