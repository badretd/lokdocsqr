"""Exception hierarchy for LokDocSQR."""
from __future__ import annotations

__all__ = [  # noqa: RUF022 - public order is part of the contract
    "ConfigError",
    "DryRunInterrupt",
    "GraphParseError",
    "GraphValidationError",
    "LokError",
    "NodeExecutionError",
    "PolicyViolation",
    "SafeFsError",
    "ScopeViolation",
    "PolicyError",
    "PolicyConfigError",
    "PolicyBlockedError",
    "PolicyTimeoutError",
    "ProposalValidationError",
    "RuntimeLockError",
]


class LokError(Exception):
    """Base exception for all LokDocSQR errors."""


class ConfigError(LokError):
    """Raised when configuration is invalid."""


class GraphParseError(LokError):
    """Raised when an automation graph cannot be parsed."""


class GraphValidationError(LokError):
    """Raised when an automation graph fails semantic validation."""


class ScopeViolation(LokError):  # noqa: N818 - public contract name
    """Raised when a path escapes the resolved workspace scope."""


class PolicyViolation(LokError):  # noqa: N818 - public contract name
    """Raised when an action is blocked by policy."""


class NodeExecutionError(LokError):
    """Raised when a node fails during execution."""


class SafeFsError(LokError):
    """Raised when a safe filesystem operation fails."""


class DryRunInterrupt(LokError):  # noqa: N818 - public contract name
    """Raised when dry-run mode intentionally interrupts a real operation."""


# ── 7a: policy errors ──────────────────────────────────────────────
# All policy failures inherit PolicyError so 7c can catch them together.


class PolicyError(LokError):
    """Base exception for policy / decision-layer failures."""


class PolicyConfigError(PolicyError):
    """Raised for broken JSON or an invalid policy configuration schema."""


class PolicyBlockedError(PolicyError):
    """Raised when an operation is blocked by policy or verify=REJECT."""


class PolicyTimeoutError(PolicyError):
    """Raised when a verify callback times out."""


class ProposalValidationError(PolicyError):
    """Raised when an ActionSummary is structurally invalid."""


# ── 10: runtime errors ───────────────────────────────────────────


class RuntimeLockError(LokError):
    """Raised when another runtime instance already holds the lock."""

