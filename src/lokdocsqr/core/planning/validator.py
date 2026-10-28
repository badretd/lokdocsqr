"""Proposal validation for Phase 7b.

Rules (see spec §4):
    1. Schema: node_id/operation/target_paths/payload/summary present with
       strict types. ``operation == "unknown"`` is schema-valid.
    2. Mutating operations (MUTATING_OPERATIONS) require
       ``ctx.scope.check(path, write=True)`` for every target path.
    3. R27: a mutating proposal with empty ``target_paths`` is a validation
       error — scope cannot be checked without a path.
    4. Read proposals do not call ``scope.check(write=True)``; the read node
       already enforced scope on its own.

Iteration 1 (7b-iter-1): :class:`ProposalValidationError` now lives in
:mod:`lokdocsqr.core.errors` (parent: ``PolicyError`` when present,
otherwise ``PolicyViolation``, otherwise ``LokError``). This module
re-exports the class so existing importers keep working unchanged.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..errors import ProposalValidationError
from ..models import ActionProposal

if TYPE_CHECKING:
    from .plan import ActionPlan

__all__ = [
    "MUTATING_OPERATIONS",
    "ProposalValidationError",
    "validate_proposal",
    "validate_plan",
]

MUTATING_OPERATIONS: frozenset[str] = frozenset(
    {"write", "move", "copy", "mkdir"}
)


def validate_proposal(proposal: ActionProposal, ctx: Any) -> None:
    """Validate schema and (for mutating ops) scope of ``proposal``.

    Raises:
        ProposalValidationError: if any rule fails.
    """
    if not isinstance(proposal.node_id, str):
        raise ProposalValidationError(
            f"node_id must be str, got {type(proposal.node_id).__name__}"
        )
    if not isinstance(proposal.operation, str):
        raise ProposalValidationError(
            f"operation must be str, got {type(proposal.operation).__name__}"
        )
    if not isinstance(proposal.target_paths, tuple):
        raise ProposalValidationError(
            "target_paths must be tuple, got "
            f"{type(proposal.target_paths).__name__}"
        )
    for item in proposal.target_paths:
        if not isinstance(item, str):
            raise ProposalValidationError(
                "target_paths must contain str, got "
                f"{type(item).__name__}"
            )
    if not isinstance(proposal.payload, dict):
        raise ProposalValidationError(
            f"payload must be dict, got {type(proposal.payload).__name__}"
        )
    if not isinstance(proposal.summary, str):
        raise ProposalValidationError(
            f"summary must be str, got {type(proposal.summary).__name__}"
        )

    if proposal.operation not in MUTATING_OPERATIONS:
        return

    # R27: cannot scope-check a mutation without a path.
    if not proposal.target_paths:
        raise ProposalValidationError(
            "mutating proposal with empty target_paths"
        )

    for raw in proposal.target_paths:
        try:
            ctx.scope.check(Path(raw), write=True)
        except ProposalValidationError:
            raise
        except Exception as exc:
            raise ProposalValidationError(
                f"scope violation for {raw!r}: {exc}"
            ) from exc


def validate_plan(plan: "ActionPlan", ctx: Any) -> None:
    """Validate every proposal in ``plan``. Informational; ``blocked`` is not raised."""
    for proposal in plan.proposals:
        validate_proposal(proposal, ctx)
