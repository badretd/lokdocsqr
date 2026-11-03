"""Regression tests for Phase 10.6-followup proposal extraction (D24).

The policy/proposal layer must use the *resolved payload*, understand
``fs.move``/``fs.copy`` ``target_dir``, and defer to the runtime scope check
when a mutating node's source is supplied by an inbound edge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lokdocsqr.nodes  # noqa: F401  (register built-in node families)
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.errors import ProposalValidationError
from lokdocsqr.core.executor.engine import _check_policy_and_verify
from lokdocsqr.core.graph import build_graph, parse_graph, resolve_states
from lokdocsqr.core.models import PolicyAction
from lokdocsqr.core.node_api.registry import NodeRegistry
from lokdocsqr.core.node_api.registry import registry as global_registry
from lokdocsqr.core.planning.builder import build_action_plan
from lokdocsqr.core.policy import PolicyConfig, PolicyEngine
from lokdocsqr.core.runtime import Runtime
from tests.helpers import make_ctx

ALLOW_ACTION = PolicyAction.ALLOW


def _allow_policy() -> PolicyEngine:
    """A policy that allows reads and writes without a verify callback."""
    return PolicyEngine(
        PolicyConfig(
            high_risk_operations=frozenset(),
            default_action=ALLOW_ACTION,
        )
    )


class _RecordingAllowPolicy:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def decide(self, proposal: Any) -> Any:
        from lokdocsqr.core.models import PolicyDecision

        self.seen.append(proposal)
        return PolicyDecision(ALLOW_ACTION, "allow", proposal)


def _runtime(
    tmp_path: Path,
    *,
    policy: Any | None = None,
) -> Runtime:
    """Build a Runtime with an isolated registry and a non-None policy."""
    registry = NodeRegistry()
    for node_cls in global_registry._nodes.values():
        registry.register(node_cls)
    return Runtime(
        AppConfig(base_dir=tmp_path),
        registry=registry,
        policy=policy if policy is not None else _allow_policy(),
        single_instance=False,
    )


def _child_graph(
    *,
    source_type: str = "utils.path_template",
    source_params: dict[str, Any] | None = None,
    move_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source_params is None:
        source_params = {"template": "{item[path]}"}
    if move_params is None:
        move_params = {
            "template": "tmp/{name}{ext}",
            "conflict_policy": "rename",
        }
    return {
        "spec_version": "1.0",
        "metadata": {"id": "child"},
        "nodes": [
            {
                "id": "src",
                "type": source_type,
                "params": source_params,
                "outputs": [{"name": "path", "type": "string"}],
            },
            {
                "id": "mv",
                "type": "fs.move",
                "params": move_params,
                "inputs": ["source"],
                "outputs": [{"name": "file", "type": "string"}],
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "src",
                "target": "mv",
                "source_port": "path",
                "target_port": "source",
            }
        ],
    }


def _loop_document(
    child: dict[str, Any],
    items: list[Any],
    *,
    scope_roots: list[str] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "spec_version": "1.0",
        "metadata": {"id": "parent"},
        "nodes": [
            {
                "id": "loop",
                "type": "flow.loop_batch",
                "params": {"items": items, "graph": child},
            }
        ],
        "edges": [],
    }
    if scope_roots is not None:
        document["settings"] = {
            "scope": {"roots": scope_roots, "writable": scope_roots}
        }
    return document


def _run_loop(
    tmp_path: Path,
    child: dict[str, Any],
    items: list[Any],
    *,
    scope_roots: list[str] | None = None,
) -> Any:
    runtime = _runtime(tmp_path)
    runtime.register(
        _loop_document(child, items, scope_roots=scope_roots),
        automation_id="parent",
    )
    return runtime.run_once("parent", dry_run=False)


def _run_dynamic_child(tmp_path: Path) -> Any:
    """Run one child graph whose edge-supplied source is outside the scope."""
    document = _child_graph(
        source_params={"template": "outside.txt"},
        move_params={"template": "tmp/{name}{ext}"},
    )
    document["metadata"] = {"id": "dynamic-child"}
    document["settings"] = {
        "scope": {"roots": ["inside"], "writable": ["inside"]}
    }
    runtime = _runtime(tmp_path)
    runtime.register(document, automation_id="dynamic-child")
    return runtime.run_once("dynamic-child", dry_run=False)


# ---------------------------------------------------------------------------
# 1. Resolved payload supplies the source
# ---------------------------------------------------------------------------


def test_proposal_uses_resolved_payload_for_source(tmp_path: Path) -> None:
    (tmp_path / "result.txt").write_text("hello", encoding="utf-8")

    result = _run_loop(
        tmp_path,
        _child_graph(),
        [{"path": "result.txt"}],
    )

    assert result.success, result.errors
    assert not any(
        "ProposalValidationError" in error for error in result.errors
    )
    assert (tmp_path / "tmp" / "result.txt").read_text(
        encoding="utf-8"
    ) == "hello"
    assert not (tmp_path / "result.txt").exists()


# ---------------------------------------------------------------------------
# 2. Dynamic source defers to the runtime scope check
# ---------------------------------------------------------------------------


def test_proposal_dynamic_source_defers_to_scope_check(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "inside").mkdir()

    result = _run_dynamic_child(tmp_path)

    assert not result.success
    assert not any(
        "ProposalValidationError" in error for error in result.errors
    )
    assert any("ScopeViolation" in error for error in result.errors), (
        f"expected runtime ScopeViolation, got {result.errors!r}"
    )
    assert outside.exists(), "the rejected source must not be moved"


# ---------------------------------------------------------------------------
# 3. target_dir is recognised when computing the proposal destination
# ---------------------------------------------------------------------------


def _move_target_dir_graph() -> Any:
    document = parse_graph(
        {
            "spec_version": "1.0",
            "metadata": {"id": "target-dir"},
            "nodes": [
                {
                    "id": "src",
                    "type": "utils.path_template",
                    "params": {"template": "inbox/a.txt"},
                    "outputs": [{"name": "path", "type": "string"}],
                },
                {
                    "id": "mv",
                    "type": "fs.move",
                    "params": {
                        "target_dir": "tmp",
                        "template": "{name}{ext}",
                    },
                    "inputs": [{"name": "source", "type": "string"}],
                },
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "src",
                    "target": "mv",
                    "source_port": "path",
                    "target_port": "source",
                }
            ],
        }
    )
    return build_graph(document, resolve_states(document), global_registry)


def test_proposal_target_dir_recognized(tmp_path: Path) -> None:
    plan = build_action_plan(
        _move_target_dir_graph(), make_ctx(tmp_path), _allow_policy()
    )

    assert not plan.blocked, plan.errors
    proposal = next(
        item for item in plan.proposals if item.node_id == "mv"
    )
    assert proposal.target_paths == ("inbox/a.txt", "tmp/a.txt")
    assert proposal.dynamic is True


# ---------------------------------------------------------------------------
# 4. Genuinely empty source still fails closed
# ---------------------------------------------------------------------------


def test_proposal_empty_source_no_edge_still_errors(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    ctx = make_ctx(tmp_path)
    ctx.policy = _allow_policy()

    with pytest.raises(ProposalValidationError, match="empty target_paths"):
        _check_policy_and_verify(
            SimpleNamespace(id="fs.move"),
            ctx,
            {"template": "tmp/{name}{ext}"},
            node_id="mv",
        )


# ---------------------------------------------------------------------------
# 5. Dynamic proposals are visible as warnings
# ---------------------------------------------------------------------------


def test_proposal_dynamic_emits_warning(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (tmp_path / "inside").mkdir()

    result = _run_dynamic_child(tmp_path)

    assert any(
        "proposal paths are dynamic" in warning
        for warning in result.warnings
    ), result.warnings


# ---------------------------------------------------------------------------
# 6. Non-mutating nodes are unaffected
# ---------------------------------------------------------------------------


def test_proposal_non_mutating_unaffected(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_text("a", encoding="utf-8")

    policy = _RecordingAllowPolicy()
    runtime = _runtime(tmp_path, policy=policy)
    runtime.register(
        {
            "spec_version": "1.0",
            "metadata": {"id": "read-only"},
            "nodes": [
                {
                    "id": "src",
                    "type": "utils.path_template",
                    "params": {"template": "data"},
                    "outputs": [{"name": "path", "type": "string"}],
                },
                {
                    "id": "list",
                    "type": "access.list_files",
                    "inputs": [{"name": "path", "type": "string"}],
                },
            ],
            "edges": [
                {
                    "id": "e",
                    "source": "src",
                    "target": "list",
                    "source_port": "path",
                    "target_port": "path",
                }
            ],
        },
        automation_id="read-only",
    )

    result = runtime.run_once("read-only", dry_run=False)

    assert result.success, result.errors
    assert result.warnings == []
    assert not any(
        "ProposalValidationError" in error for error in result.errors
    )
    # No read proposal is dynamic; the mutating-only deferral did not leak.
    assert policy.seen
    assert all(not getattr(item, "dynamic", False) for item in policy.seen)
