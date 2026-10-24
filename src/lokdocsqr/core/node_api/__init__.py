"""Public API surface for automation nodes."""

from __future__ import annotations

from .base import Node
from .decorators import register_node
from .registry import NodeRegistry, registry

__all__ = ["Node", "registry", "NodeRegistry", "register_node"]
