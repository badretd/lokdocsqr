"""Unit tests for lokdocsqr.core.node_api.base."""

from __future__ import annotations

import pytest

from lokdocsqr.core.node_api import Node


class _ConcreteNode(Node):
    id = "concrete"

    @property
    def title(self) -> str:
        return "Concrete Node"

    def run(self, ctx, payload):
        return payload

    def dry_run(self, ctx, payload):
        return payload


def test_node_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        Node()


def test_concrete_node_can_be_instantiated():
    node = _ConcreteNode()
    assert isinstance(node, Node)


def test_describe_returns_dict_with_expected_keys():
    node = _ConcreteNode()
    meta = node.describe()
    assert isinstance(meta, dict)
    assert meta["id"] == "concrete"
    assert meta["title"] == "Concrete Node"
    assert meta["inputs"] == {}
    assert meta["outputs"] == {}


def test_default_inputs_and_outputs_are_empty():
    node = _ConcreteNode()
    assert node.inputs == {}
    assert node.outputs == {}
