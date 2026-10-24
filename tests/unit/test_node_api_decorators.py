"""Unit tests for lokdocsqr.core.node_api.decorators."""

from __future__ import annotations

import pytest

from lokdocsqr.core.node_api import Node, register_node, registry


class _BaseNode(Node):
    id = "base"

    @property
    def title(self) -> str:
        return "Base"

    def run(self, ctx, payload):
        return payload

    def dry_run(self, ctx, payload):
        return payload


def test_register_node_registers_and_returns_class():
    cls = type("DecoratedNode", (_BaseNode,), {"id": "decorated"})
    returned = register_node(cls)
    assert returned is cls
    assert registry.get("decorated") is cls


def test_register_node_raises_for_non_node_class():
    with pytest.raises(TypeError):
        register_node(object)


def test_register_node_raises_for_plain_class():
    class NotANode:
        id = "not_a_node"

    with pytest.raises(TypeError):
        register_node(NotANode)
