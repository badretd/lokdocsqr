"""Base contract for automation nodes (framework-agnostic)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class Node(ABC):
    """Base contract for all automation nodes.

    Subclasses must declare a unique class-level ``id`` and implement
    ``title``, ``run`` and ``dry_run``. ``inputs`` and ``outputs`` describe
    the port type map used by the GUI editor and the graph validator.

    ``ctx`` is intentionally typed as ``Any`` to avoid a circular import
    with ``core.executor.context.ExecutionContext`` (introduced in Phase 4).
    """

    #: Unique, stable identifier of the node type (set by subclasses).
    id: ClassVar[str]

    @property
    @abstractmethod
    def title(self) -> str:
        """Human-readable label shown in the GUI editor."""
        ...

    @property
    def inputs(self) -> dict[str, str]:
        """Returns input port types (e.g., {'files': 'FileRef[]'})."""
        return {}

    @property
    def outputs(self) -> dict[str, str]:
        """Returns output port types."""
        return {}

    @abstractmethod
    def run(self, ctx: Any, payload: Any) -> Any:
        """Execute the node in normal (non dry-run) mode."""
        ...

    @abstractmethod
    def dry_run(self, ctx: Any, payload: Any) -> Any:
        """Simulate execution without mutating any external state."""
        ...

    def describe(self) -> dict[str, Any]:
        """Returns metadata for the GUI editor."""
        return {
            "id": self.id,
            "title": self.title,
            "inputs": self.inputs,
            "outputs": self.outputs,
        }
