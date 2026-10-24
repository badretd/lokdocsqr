"""Unit tests for lokdocsqr.core.plugins."""

from __future__ import annotations

from lokdocsqr.core import plugins
from lokdocsqr.core.node_api import Node, registry


class _BaseNode(Node):
    id = "base"

    @property
    def title(self) -> str:
        return "Base"

    def run(self, ctx, payload):
        return payload

    def dry_run(self, ctx, payload):
        return payload


def _make_node(node_id: str):
    return type(f"Node_{node_id}", (_BaseNode,), {"id": node_id})


def test_discover_returns_empty_list_without_plugins(monkeypatch):
    monkeypatch.setattr(plugins, "_load_builtin_nodes", lambda: None)
    monkeypatch.setattr(
        plugins.metadata, "entry_points", lambda group: ()
    )
    assert plugins.discover_plugins() == []


def test_discover_loads_entry_point_that_registers_node(monkeypatch):
    monkeypatch.setattr(plugins, "_load_builtin_nodes", lambda: None)

    class FakeEntryPoint:
        name = "external"

        def load(self):
            registry.register(_make_node("external_node"))

    monkeypatch.setattr(
        plugins.metadata,
        "entry_points",
        lambda group: [FakeEntryPoint()],
    )
    ids = plugins.discover_plugins()
    assert "external_node" in ids


def test_discover_skips_failing_entry_point(monkeypatch):
    monkeypatch.setattr(plugins, "_load_builtin_nodes", lambda: None)

    class BrokenEntryPoint:
        name = "broken"

        def load(self):
            raise RuntimeError("boom")

    class GoodEntryPoint:
        name = "good"

        def load(self):
            registry.register(_make_node("good_node"))

    monkeypatch.setattr(
        plugins.metadata,
        "entry_points",
        lambda group: [BrokenEntryPoint(), GoodEntryPoint()],
    )
    ids = plugins.discover_plugins()
    # Failing plugin must be skipped; the good one must still load.
    assert "good_node" in ids


def test_discover_tolerates_entry_points_query_error(monkeypatch):
    monkeypatch.setattr(plugins, "_load_builtin_nodes", lambda: None)

    def boom(group):
        raise RuntimeError("metadata failure")

    monkeypatch.setattr(plugins.metadata, "entry_points", boom)
    # Must not raise; returns whatever is currently registered (none).
    assert plugins.discover_plugins() == []
