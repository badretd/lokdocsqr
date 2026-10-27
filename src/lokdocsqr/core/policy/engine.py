"""Policy engine for Phase 7a.

No filesystem mutation, no executor, no Qt. ``from_json`` only reads a JSON
file; ``decide`` is a deterministic pure function of (proposal, config).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..errors import PolicyConfigError
from ..models import ActionProposal, PolicyAction, PolicyDecision
from .decision import PolicyConfig

__all__ = ["PolicyEngine"]


def _as_action(value: Any, field_name: str) -> PolicyAction:
    try:
        return PolicyAction(str(value).lower())
    except ValueError as exc:
        raise PolicyConfigError(
            f"invalid PolicyAction for {field_name}: {value!r}"
        ) from exc


def _as_str_set(value: Any, field_name: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple)):
        raise PolicyConfigError(f"{field_name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise PolicyConfigError(f"{field_name} must contain only strings")
        result.append(item)
    return frozenset(result)


class PolicyEngine:
    """Evaluate ActionProposal objects against a PolicyConfig."""

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self._allow_read = False

    @classmethod
    def default(cls) -> "PolicyEngine":
        """Built-in rules: read ALLOW; write/move/copy/mkdir REQUIRE_VERIFY;
        everything else (including unknown) BLOCK.
        """
        engine = cls(PolicyConfig())
        # PolicyConfig.default_action is BLOCK for fail-closed behaviour.
        # The built-in default engine additionally allows the read operation.
        engine._allow_read = True
        return engine

    @classmethod
    def from_json(cls, path: Path) -> "PolicyEngine":
        """Load policy configuration from JSON.

        # 7a-iter-1: from_json starts from default().config and overrides
        # only the fields present in the JSON object, so ``{}`` is
        # equivalent to a missing or empty file (R31).

        Broken JSON -> PolicyConfigError.
        Invalid schema or enum values -> PolicyConfigError.
        """
        base_engine = cls.default()
        base = base_engine.config

        if not path.exists():
            return base_engine

        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return base_engine

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PolicyConfigError(f"invalid policy JSON: {exc}") from exc

        if not isinstance(data, dict):
            raise PolicyConfigError("policy JSON root must be an object")

        if "forbidden_operations" in data:
            forbidden = _as_str_set(
                data["forbidden_operations"], "forbidden_operations"
            )
        else:
            forbidden = base.forbidden_operations

        if "high_risk_operations" in data:
            high_risk = _as_str_set(
                data["high_risk_operations"], "high_risk_operations"
            )
        else:
            high_risk = base.high_risk_operations

        if "default_action" in data:
            default_action = _as_action(
                data["default_action"], "default_action"
            )
        else:
            default_action = base.default_action

        overrides: dict[str, PolicyAction] = dict(base.node_overrides)
        if "node_overrides" in data:
            raw_overrides = data["node_overrides"]
            if not isinstance(raw_overrides, dict):
                raise PolicyConfigError("node_overrides must be an object")
            for key, value in raw_overrides.items():
                if not isinstance(key, str):
                    raise PolicyConfigError(
                        "node_overrides keys must be strings"
                    )
                overrides[key] = _as_action(
                    value, f"node_overrides[{key!r}]"
                )

        merged = PolicyConfig(
            forbidden_operations=forbidden,
            high_risk_operations=high_risk,
            default_action=default_action,
            node_overrides=MappingProxyType(overrides),
        )
        engine = cls(merged)
        # ``from_json`` starts from ``default()``; preserve its built-in
        # read-allowance. ``read`` can still be blocked by listing it in
        # ``forbidden_operations`` — forbidden is checked first in decide().
        engine._allow_read = True
        return engine

    def decide(self, proposal: ActionProposal) -> PolicyDecision:
        """Return a deterministic PolicyDecision for the proposal.

        Strict order (fail-closed):
          1. node override
          2. forbidden operation
          3. high-risk operation
          4. unknown operation
          5. built-in read allowance (only for PolicyEngine.default())
          6. config.default_action
        """
        if proposal.node_id in self.config.node_overrides:
            return PolicyDecision(
                self.config.node_overrides[proposal.node_id],
                "node override",
                proposal,
            )

        if proposal.operation in self.config.forbidden_operations:
            return PolicyDecision(
                PolicyAction.BLOCK,
                "forbidden operation",
                proposal,
            )

        if proposal.operation in self.config.high_risk_operations:
            return PolicyDecision(
                PolicyAction.REQUIRE_VERIFY,
                "high risk",
                proposal,
            )

        if proposal.operation == "unknown":
            return PolicyDecision(
                PolicyAction.BLOCK,
                "unknown operation",
                proposal,
            )

        if self._allow_read and proposal.operation == "read":
            return PolicyDecision(PolicyAction.ALLOW, "read", proposal)

        return PolicyDecision(
            self.config.default_action,
            "default",
            proposal,
        )
