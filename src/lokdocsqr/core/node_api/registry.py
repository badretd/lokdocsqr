"""Registry of node classes available to the executor and CLI."""

from __future__ import annotations

from typing import Any

from .base import Node


class NodeRegistry:
    """Single source of truth for registered node classes."""

    def __init__(self) -> None:
        self._nodes: dict[str, type[Node]] = {}

    def register(self, node_cls: type[Node]) -> None:
        """Register ``node_cls`` under its ``id``.

        Raises:
            TypeError: if ``node_cls.id`` is not a non-empty ``str``.
            ValueError: if a node with the same ``id`` is already registered.
        """
        node_id = node_cls.id
        if not isinstance(node_id, str) or not node_id:
            raise TypeError(
                f"Node class {node_cls.__name__!r} must define 'id' as a "
                f"non-empty string, got {node_id!r}"
            )
        if node_id in self._nodes:
            raise ValueError(
                f"Node with id {node_id!r} is already registered"
            )
        self._nodes[node_id] = node_cls

    def get(self, node_id: str) -> type[Node]:
        """Return the node class registered under ``node_id``.

        Raises:
            KeyError: if no node with ``node_id`` is registered.
        """
        try:
            return self._nodes[node_id]
        except KeyError as exc:
            raise KeyError(
                f"Node with id {node_id!r} is not registered"
            ) from exc

    def list(self) -> list[dict[str, Any]]:
        """Return ``describe()`` metadata for every registered node."""
        return [node_cls().describe() for node_cls in self._nodes.values()]

    def clear(self) -> None:
        """Remove all registered nodes (used by tests)."""
        self._nodes.clear()


# Global singleton registry populated by @register_node decorators.
registry = NodeRegistry()
