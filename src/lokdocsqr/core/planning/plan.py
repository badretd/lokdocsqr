"""Plan-level types for Phase 7b."""
from __future__ import annotations

from dataclasses import dataclass

from ..models import ActionProposal, PolicyDecision
from ..preview import ChangePreview

__all__ = ["PlanError", "ActionPlan"]


@dataclass(frozen=True)
class PlanError:
    """One node's failure while building the plan.

    The corresponding node is absent from ``proposals``, ``decisions`` and
    ``previews`` of the containing :class:`ActionPlan`.
    """

    node_id: str
    error: str
    exc_type: str


@dataclass(frozen=True)
class ActionPlan:
    """Result of building a dry-run action plan.

    ``proposals``, ``decisions`` and ``previews`` are parallel by index:
    ``proposals[i]`` corresponds to ``decisions[i]`` and ``previews[i]``.
    Nodes that failed appear only in ``errors``.

    ``blocked`` is True if any decision is BLOCK or if any errors occurred.
    """

    proposals: tuple[ActionProposal, ...]
    decisions: tuple[PolicyDecision, ...]
    previews: tuple[ChangePreview, ...]
    errors: tuple[PlanError, ...]
    blocked: bool
