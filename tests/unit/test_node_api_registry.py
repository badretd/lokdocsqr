"""Unit tests for lokdocsqr.core.node_api.registry."""

from __future__ import annotations

import pytest

from lokdocsqr.core.node_api import Node, NodeRegistry


class _BaseNode(Node):
    id = "base"

    @property
    def title(self) -> str:
        return "Base"

    def run(self, ctx, payload):
        return payload

    def dry_run(self, ctx, payload):
        return payload


def _node_with_id(node_id: str):
    return type(f"Node_{node_id}", (_BaseNode,), {"id": node_id})


@pytest.fixture()
def reg() -> NodeRegistry:
    """A fresh registry per test (independent from the global singleton)."""
    return NodeRegistry()


def test_register_adds_node(reg):
    cls = _node_with_id("alpha")
    reg.register(cls)
    assert reg.get("alpha") is cls


def test_register_duplicate_id_raises_value_error(reg):
    cls = _node_with_id("dup")
    reg.register(cls)
    with pytest.raises(ValueError):
        reg.register(cls)


@pytest.mark.parametrize("bad_id", ["", None, 0, 123])
def test_register_invalid_id_raises_type_error(reg, bad_id):
    cls = type("BadNode", (_BaseNode,), {"id": bad_id})
    with pytest.raises(TypeError):
        reg.register(cls)


def test_get_returns_registered_class(reg):
    cls = _node_with_id("lookup")
    reg.register(cls)
    assert reg.get("lookup") is cls


def test_get_missing_id_raises_key_error(reg):
    with pytest.raises(KeyError):
        reg.get("nonexistent")


def test_list_returns_descriptors(reg):
    reg.register(_node_with_id("a"))
    reg.register(_node_with_id("b"))
    descriptors = reg.list()
    ids = {d["id"] for d in descriptors}
    assert ids == {"a", "b"}
    for descriptor in descriptors:
        assert set(descriptor) == {"id", "title", "inputs", "outputs"}


def test_clear_removes_all_nodes(reg):
    reg.register(_node_with_id("gone"))
    reg.clear()
    assert reg.list() == []
    with pytest.raises(KeyError):
        reg.get("gone")
