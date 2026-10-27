"""Policy configuration dataclass for Phase 7a."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ..models import PolicyAction

__all__ = ["PolicyConfig"]


@dataclass(frozen=True)
class PolicyConfig:
    """Declarative policy configuration.

    ``default_action`` is BLOCK by default: fail-closed (R26).
    ``node_overrides`` is a Mapping so callers may pass MappingProxyType.
    """

    forbidden_operations: frozenset[str] = frozenset()
    high_risk_operations: frozenset[str] = frozenset(
        {"write", "move", "copy", "mkdir"}
    )
    default_action: PolicyAction = PolicyAction.BLOCK
    node_overrides: Mapping[str, PolicyAction] = field(default_factory=dict)
