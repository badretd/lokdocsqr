"""Build an ActionPlan from a resolved graph (Phase 7b).

Invariants:
    * Only ``Node.dry_run`` is called; ``Node.run`` is never invoked (R33).
    * No filesystem mutation: ``build_action_plan`` reads ``graph``/``ctx``
      and calls :func:`preview_diff`, which itself only reads (R28).
    * Determinism (R32): the plan is a pure function of (graph, ctx, policy).
      Node order follows ``graph.ordered_node_ids``; ``target_paths`` inside
      each proposal are sorted by ``extract_paths``.
    * No partial success: a node either contributes (proposal, decision,
      preview) or appears as a :class:`PlanError`. Never both.
"""
from __future__ import annotations

from typing import Any

from ..models import ActionProposal, PolicyAction, PolicyDecision
from ..policy.engine import PolicyEngine
from ..preview import ChangePreview, preview_diff
from .plan import ActionPlan, PlanError
from .registry import classify_node, extract_paths
from .validator import ProposalValidationError, validate_proposal

__all__ = ["build_action_plan"]


def _node_payload(node_spec: Any) -> dict[str, Any]:
    """Return the node's configured payload (from its ``params``)."""
    params = getattr(node_spec, "params", None)
    if isinstance(params, dict):
        return dict(params)
    return {}


def build_action_plan(
    graph: Any,
    ctx: Any,
    policy: PolicyEngine,
) -> ActionPlan:
    """Build an :class:`ActionPlan` for ``graph`` without running any node."""
    doc = getattr(graph, "document", None)
    specs: dict[str, Any] = {}
    if doc is not None:
        for spec in getattr(doc, "nodes", []) or []:
            node_id = getattr(spec, "id", "")
            if isinstance(node_id, str) and node_id:
                specs[node_id] = spec

    handlers = getattr(graph, "handlers", {}) or {}
    ordered_ids = list(getattr(graph, "ordered_node_ids", []) or [])

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

        payload = _node_payload(specs.get(node_id))

        try:
            dry_run(ctx, payload)
        except Exception as exc:  # noqa: BLE001
            errors.append(
                PlanError(
                    node_id=node_id,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
            )
            continue

        try:
            target_paths = extract_paths(payload, node_id)
            operation = classify_node(node_id)
            summary = (
                f"{operation}: {', '.join(target_paths) or 'n/a'}"
            )
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
