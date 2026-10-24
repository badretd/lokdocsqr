"""Verify the shared domain dataclasses and enums."""
from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

from lokdocsqr.core import models
from lokdocsqr.core.models import (
    ActionLog,
    ActionProposal,
    Automation,
    Edge,
    FileRef,
    NodeResult,
    NodeSpec,
    NodeState,
    RawGraph,
    ResolvedGraph,
    Risk,
    WorkspaceScope,
)

EXPECTED_ALL = {
    "ActionLog",
    "ActionProposal",
    "Automation",
    "Edge",
    "FileRef",
    "NodeResult",
    "NodeSpec",
    "NodeState",
    "RawGraph",
    "ResolvedGraph",
    "Risk",
    "WorkspaceScope",
}

ALL_DATACLASSES = (
    FileRef,
    ActionProposal,
    ActionLog,
    NodeSpec,
    Edge,
    Automation,
    RawGraph,
    ResolvedGraph,
    NodeResult,
    WorkspaceScope,
)


def test_models_all_surface() -> None:
    """models.__all__ must exactly declare the public surface."""
    assert set(models.__all__) == EXPECTED_ALL
    missing = [name for name in EXPECTED_ALL if not hasattr(models, name)]
    assert not missing, f"models missing attributes: {missing}"


def test_all_models_are_dataclasses() -> None:
    """Every domain model must be a dataclass."""
    non_dataclasses = [cls.__name__ for cls in ALL_DATACLASSES if not is_dataclass(cls)]
    assert not non_dataclasses, f"not dataclasses: {non_dataclasses}"


# --- Enums ---------------------------------------------------------------

def test_node_state_values() -> None:
    """NodeState is a str-Enum with the documented values."""
    assert issubclass(NodeState, str)
    assert {s.value for s in NodeState} == {
        "enabled", "disabled", "skipped", "failed",
    }


def test_risk_values() -> None:
    """Risk is a str-Enum with low/medium/high."""
    assert issubclass(Risk, str)
    assert {r.value for r in Risk} == {"low", "medium", "high"}


def test_enum_members_compare_equal_to_strings() -> None:
    """str-Enum members must equal their underlying string."""
    assert NodeState.ENABLED == "enabled"
    assert Risk.HIGH == "high"


# --- FileRef -------------------------------------------------------------

def test_fileref_defaults() -> None:
    """FileRef defaults must match the contract."""
    f = FileRef(id="f1", path=Path("/tmp/x"))
    assert f.hash is None
    assert f.mime is None
    assert f.size == 0
    assert f.timestamps == {}
    assert f.metadata == {}


def test_fileref_mutable_defaults_isolated() -> None:
    """Per-instance dicts must not be shared between instances."""
    a = FileRef(id="a", path=Path("/a"))
    b = FileRef(id="b", path=Path("/b"))
    a.timestamps["x"] = 1.0
    a.metadata["y"] = 2
    assert b.timestamps == {}
    assert b.metadata == {}


# --- ActionProposal / ActionLog ------------------------------------------

def test_actionproposal_defaults() -> None:
    """ActionProposal defaults must match the contract."""
    p = ActionProposal(type="move")
    assert p.source is None
    assert p.target is None
    assert p.reason == ""
    assert p.confidence == 1.0
    assert p.risk is Risk.LOW
    assert p.extra == {}


def test_actionlog_defaults() -> None:
    """ActionLog defaults must match the contract."""
    log = ActionLog(
        run_id="r1", automation_id="a1", node_id="n1",
        action="move", status="ok", timestamp=0.0,
    )
    assert log.source is None
    assert log.target is None
    assert log.confirmed_by == "auto"
    assert log.rollback is None
    assert log.meta == {}


# --- NodeSpec ------------------------------------------------------------

def test_nodespec_defaults() -> None:
    """NodeSpec must default to ENABLED with empty params."""
    spec = NodeSpec(id="n1", type="move")
    assert spec.params == {}
    assert spec.state is NodeState.ENABLED


def test_nodespec_params_isolated() -> None:
    """params must be per-instance."""
    a = NodeSpec(id="a", type="t")
    b = NodeSpec(id="b", type="t")
    a.params["x"] = 1
    assert b.params == {}


# --- Automation ----------------------------------------------------------

def test_automation_defaults() -> None:
    """Automation defaults must be empty collections."""
    a = Automation(id="auto1")
    assert a.name == ""
    assert a.nodes == []
    assert a.edges == []
    assert a.meta == {}


def test_automation_collections_isolated() -> None:
    """nodes/edges/meta must not leak between automations."""
    a = Automation(id="a")
    b = Automation(id="b")
    a.nodes.append(NodeSpec(id="n", type="t"))
    a.edges.append(Edge(src="x", dst="y"))
    a.meta["k"] = "v"
    assert b.nodes == []
    assert b.edges == []
    assert b.meta == {}


# --- ResolvedGraph / NodeResult / WorkspaceScope -------------------------

def test_resolvedgraph_defaults() -> None:
    """ResolvedGraph defaults must be empty/None as documented."""
    rg = ResolvedGraph()
    assert rg.ordered_node_ids == []
    assert rg.handlers == {}
    assert rg.states == {}
    assert rg.scope is None
    assert rg.templates == {}


def test_noderesult_defaults() -> None:
    """NodeResult requires node_id/status only; the rest default."""
    r = NodeResult(node_id="n", status="ok")
    assert r.outputs == {}
    assert r.actions == []
    assert r.error is None


def test_workspacescope_defaults() -> None:
    """WorkspaceScope defaults must match the contract."""
    ws = WorkspaceScope()
    assert ws.roots == []
    assert ws.writable == []
    assert ws.allow_symlinks is False
    assert ws.policy_ref is None


def test_workspacescope_lists_isolated() -> None:
    """roots/writable lists must not be shared between instances."""
    a = WorkspaceScope()
    b = WorkspaceScope()
    a.roots.append(Path("/a"))
    a.writable.append(Path("/a"))
    assert b.roots == []
    assert b.writable == []
