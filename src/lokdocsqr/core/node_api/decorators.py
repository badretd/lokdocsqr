"""Class decorators for registering node implementations."""

from __future__ import annotations

from typing import TypeVar

from .base import Node
from .registry import registry

_N = TypeVar("_N", bound=type[Node])


def register_node(cls: _N) -> _N:
    """Class decorator that registers ``cls`` with the global ``registry``.

    Raises:
        TypeError: if ``cls`` is not a subclass of :class:`Node`.
    """
    if not (isinstance(cls, type) and issubclass(cls, Node)):
        raise TypeError(f"{cls!r} must be a subclass of Node")
    registry.register(cls)
    return cls
