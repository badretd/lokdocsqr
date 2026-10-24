"""Verify the Workflow v1.0 domain dataclasses and enums."""
# === Phase 3.5 applied: workflow v1.0 spec-compliant tests ===
# === Phase 4 extensions: FileRef / ActionProposal / ActionLog / NodeResult /
#     RollbackOp / RunResult are part of the public `__all__` surface. ===
from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

from lokdocsqr.core import models
from lokdocsqr.core.models import (
    ActionLog,
    ActionProposal,
    EdgeSpec,
    FileRef,
    NodeResult,
    NodeSpec,
    NodeState,
    PortSpec,
    ResolvedGraph,
    Risk,
    RollbackOp,
    RunResult,
    TriggerSpec,
    VariableSpec,
    WorkflowDocument,
    WorkflowErrorHandling,
    WorkflowMetadata,
    WorkflowScopeSettings,
    WorkflowSettings,
    WorkspaceScope,
)

EXPECTED_ALL = {
    # --- Workflow specification v1.0 public surface ---
    "WorkflowMetadata",
    "WorkflowScopeSettings",
    "WorkflowErrorHandling",
    "WorkflowSettings",
    "VariableSpec",
    "TriggerSpec",
    "PortSpec",
    "NodeSpec",
    "EdgeSpec",
    "WorkflowDocument",
    "ResolvedGraph",
    "NodeState",
    "Risk",
    # --- Phase 4 runtime extensions ---
    "FileRef",
    "ActionProposal",
    "ActionLog",
    "NodeResult",
    "RollbackOp",
    "RunResult",
}

ALL_DATACLASSES = (
    # v1.0 surface (excluding enums NodeState / Risk)
    WorkflowMetadata,
    WorkflowScopeSettings,
    WorkflowErrorHandling,
    WorkflowSettings,
    VariableSpec,
    TriggerSpec,
    PortSpec,
    NodeSpec,
    EdgeSpec,
    WorkflowDocument,
    ResolvedGraph,
    # Phase 4 extensions
    FileRef,
    ActionProposal,
    ActionLog,
    NodeResult,
    RollbackOp,
    RunResult,
)


# --- Public surface ---------------------------------------------------------

def test_models_all_surface() -> None:
    """models.__all__ must exactly declare the public model surface."""
    assert set(models.__all__) == EXPECTED_ALL
    missing = [name for name in EXPECTED_ALL if not hasattr(models, name)]
    assert not missing, f"models missing attributes: {missing}"


def test_all_models_are_dataclasses() -> None:
    """Every declared public model must be a dataclass."""
    non_dataclasses = [
        cls.__name__ for cls in ALL_DATACLASSES if not is_dataclass(cls)
    ]
    assert not non_dataclasses, f"not dataclasses: {non_dataclasses}"


# --- Enums ------------------------------------------------------------------

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


# --- WorkflowMetadata -------------------------------------------------------

def test_workflowmetadata_defaults() -> None:
    meta = WorkflowMetadata(id="wf-1")
    assert meta.name == ""
    assert meta.description == ""
    assert meta.version == "1.0"
    assert meta.author == ""
    assert meta.created_at is None
    assert meta.updated_at is None
    assert meta.tags == []


def test_workflowmetadata_tags_isolated() -> None:
    a = WorkflowMetadata(id="a")
    b = WorkflowMetadata(id="b")
    a.tags.append("x")
    assert b.tags == []


# --- WorkflowScopeSettings --------------------------------------------------

def test_workflowscopesettings_defaults() -> None:
    settings = WorkflowScopeSettings()
    assert settings.roots == []
    assert settings.writable == []
    assert settings.allow_symlinks is False
    assert settings.policy_ref is None


def test_workflowscopesettings_lists_isolated() -> None:
    a = WorkflowScopeSettings()
    b = WorkflowScopeSettings()
    a.roots.append("/x")
    a.writable.append("/y")
    assert b.roots == []
    assert b.writable == []


# --- WorkflowErrorHandling --------------------------------------------------

def test_workflowerrorhandling_defaults() -> None:
    handling = WorkflowErrorHandling()
    assert handling.strategy == "fail"
    assert handling.max_retries == 0
    assert handling.retry_delay_seconds == 0.0
    assert handling.backoff == "none"


# --- WorkflowSettings -------------------------------------------------------

def test_workflowsettings_defaults() -> None:
    settings = WorkflowSettings()
    assert settings.default_dry_run is True
    assert isinstance(settings.scope, WorkflowScopeSettings)
    assert isinstance(settings.error_handling, WorkflowErrorHandling)
    assert settings.max_concurrency == 1


def test_workflowsettings_nested_defaults_isolated() -> None:
    a = WorkflowSettings()
    b = WorkflowSettings()
    assert a.scope is not b.scope
    assert a.error_handling is not b.error_handling
    a.scope.roots.append("/x")
    assert b.scope.roots == []


# --- VariableSpec -----------------------------------------------------------

def test_variablespec_defaults() -> None:
    var = VariableSpec()
    assert var.type == "string"
    assert var.value is None
    assert var.description == ""


# --- TriggerSpec ------------------------------------------------------------

def test_triggerspec_requires_id_and_type() -> None:
    trigger = TriggerSpec(id="t1", type="manual")
    assert trigger.title == ""
    assert trigger.params == {}


def test_triggerspec_params_isolated() -> None:
    a = TriggerSpec(id="a", type="manual")
    b = TriggerSpec(id="b", type="manual")
    a.params["k"] = "v"
    assert b.params == {}


# --- PortSpec ---------------------------------------------------------------

def test_portspec_defaults() -> None:
    port = PortSpec(name="files")
    assert port.type == "any"
    assert port.required is True
    assert port.description == ""
    assert port.default is None


# --- NodeSpec ---------------------------------------------------------------

def test_nodespec_defaults() -> None:
    spec = NodeSpec(id="n1", type="move_files")
    assert spec.title == ""
    assert spec.state is NodeState.ENABLED
    assert spec.params == {}
    assert spec.inputs == []
    assert spec.outputs == []
    assert spec.position == {}
    assert spec.description == ""


def test_nodespec_collections_isolated() -> None:
    a = NodeSpec(id="a", type="t")
    b = NodeSpec(id="b", type="t")
    a.params["x"] = 1
    a.inputs.append(PortSpec(name="i"))
    a.outputs.append(PortSpec(name="o"))
    a.position["x"] = 1.0
    assert b.params == {}
    assert b.inputs == []
    assert b.outputs == []
    assert b.position == {}


# --- EdgeSpec ---------------------------------------------------------------

def test_edgespec_defaults() -> None:
    edge = EdgeSpec(id="e1", source="a", target="b")
    assert edge.source_port == "out"
    assert edge.target_port == "in"
    assert edge.condition is None
    assert edge.label == ""


# --- WorkflowDocument -------------------------------------------------------

def test_workflowdocument_requires_spec_version_and_metadata() -> None:
    meta = WorkflowMetadata(id="wf")
    doc = WorkflowDocument(spec_version="1.0", metadata=meta)
    assert doc.settings.max_concurrency == 1
    assert doc.variables == {}
    assert doc.triggers == []
    assert doc.nodes == []
    assert doc.edges == []
    assert doc.extra_meta == {}


def test_workflowdocument_collections_isolated() -> None:
    a = WorkflowDocument(spec_version="1.0", metadata=WorkflowMetadata(id="a"))
    b = WorkflowDocument(spec_version="1.0", metadata=WorkflowMetadata(id="b"))
    a.variables["x"] = VariableSpec()
    a.triggers.append(TriggerSpec(id="t", type="manual"))
    a.nodes.append(NodeSpec(id="n", type="list_files"))
    a.edges.append(EdgeSpec(id="e", source="n", target="n"))
    a.extra_meta["k"] = "v"
    assert b.variables == {}
    assert b.triggers == []
    assert b.nodes == []
    assert b.edges == []
    assert b.extra_meta == {}


# --- ResolvedGraph ----------------------------------------------------------

def test_resolvedgraph_requires_document() -> None:
    doc = WorkflowDocument(spec_version="1.0", metadata=WorkflowMetadata(id="wf"))
    resolved = ResolvedGraph(document=doc)
    assert resolved.document is doc
    assert resolved.ordered_node_ids == []
    assert resolved.handlers == {}
    assert resolved.states == {}


def test_resolvedgraph_collections_isolated() -> None:
    doc_a = WorkflowDocument(spec_version="1.0", metadata=WorkflowMetadata(id="a"))
    doc_b = WorkflowDocument(spec_version="1.0", metadata=WorkflowMetadata(id="b"))
    a = ResolvedGraph(document=doc_a)
    b = ResolvedGraph(document=doc_b)
    a.ordered_node_ids.append("n")
    a.handlers["n"] = object()
    a.states["n"] = NodeState.ENABLED
    assert b.ordered_node_ids == []
    assert b.handlers == {}
    assert b.states == {}


# --- WorkspaceScope (compatibility, not in __all__) -------------------------

def test_workspacescope_defaults() -> None:
    scope = WorkspaceScope()
    assert scope.roots == []
    assert scope.writable == []
    assert scope.allow_symlinks is False
    assert scope.policy_ref is None


def test_workspacescope_lists_isolated() -> None:
    a = WorkspaceScope()
    b = WorkspaceScope()
    a.roots.append(Path("/a"))
    a.writable.append(Path("/a"))
    assert b.roots == []
    assert b.writable == []


def test_workspacescope_not_in_public_all() -> None:
    """WorkspaceScope is retained for core.scope but is not workflow surface."""
    assert "WorkspaceScope" not in models.__all__


# --- Phase 4 extensions -----------------------------------------------------

def test_phas4_dataclasses_in_public_all() -> None:
    """Phase 4 models must be explicitly exposed via ``models.__all__``."""
    for name in (
        "FileRef",
        "ActionProposal",
        "ActionLog",
        "NodeResult",
        "RollbackOp",
        "RunResult",
    ):
        assert name in models.__all__, f"{name} missing from __all__"


def test_actionlog_roundtrip_to_from_dict() -> None:
    """ActionLog should survive a to_dict → from_dict round-trip."""
    original = ActionLog(
        run_id="run-1",
        node_id="n-1",
        automation_id="auto-1",
        action_type="move",
        source="/src/a.txt",
        target="/dst/a.txt",
        status="done",
        reason="dedupe",
        confidence=0.85,
        risk=Risk.HIGH,
        rollback_eligible=True,
        extra={"k": "v", "nested": {"ok": True}},
    )
    payload = original.to_dict()
    assert payload["risk"] == "high"
    assert payload["rollback_eligible"] is True

    restored = ActionLog.from_dict(payload)
    assert restored.run_id == original.run_id
    assert restored.node_id == original.node_id
    assert restored.source == original.source
    assert restored.target == original.target
    assert restored.risk is Risk.HIGH
    assert restored.rollback_eligible is True
    assert restored.extra == {"k": "v", "nested": {"ok": True}}


def test_actionlog_from_dict_tolerates_bad_input() -> None:
    """from_dict should coerce unknown risk/confidence safely."""
    log = ActionLog.from_dict(
        {"action_type": "move", "risk": "NOPE", "confidence": "not-a-number"}
    )
    assert log.risk is Risk.LOW
    assert log.confidence == 0.0
    assert log.status == "planned"


def test_fileref_and_runresult_defaults_isolated() -> None:
    a = FileRef(id="f", path=Path("/tmp/a"))
    b = FileRef(id="g", path=Path("/tmp/b"))
    a.metadata["k"] = "v"
    assert b.metadata == {}

    ra = RunResult(run_id="r1", automation_id="a1", dry_run=True, success=True)
    rb = RunResult(run_id="r2", automation_id="a2", dry_run=False, success=False)
    ra.node_results["n"] = NodeResult(node_id="n", state=NodeState.ENABLED)
    ra.errors.append("boom")
    assert rb.node_results == {}
    assert rb.errors == []