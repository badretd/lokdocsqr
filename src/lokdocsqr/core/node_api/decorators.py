"""Class decorators for registering node implementations."""

from __future__ import annotations

from typing import Any

from .base import Node
from .registry import registry

_N = type[Node]


def _register(cls: _N, name: str | None) -> _N:
    """Validate and register ``cls``; optionally store an explicit name."""
    if not (isinstance(cls, type) and issubclass(cls, Node)):
        raise TypeError(f"{cls!r} must be a subclass of Node")

    if name is not None:
        cls.node_name = name  # type: ignore[attr-defined]

    registry.register(cls)
    return cls


def register_node(cls_or_name: _N | str) -> Any:
    """Class decorator that registers ``cls`` with the global ``registry``.
    Raises:
        TypeError: if the decorated object is not a subclass of :class:`Node`.
    """
    if isinstance(cls_or_name, str):
        name = cls_or_name

        def decorator(cls: _N) -> _N:
            return _register(cls, name)

        return decorator

    return _register(cls_or_name, None)
