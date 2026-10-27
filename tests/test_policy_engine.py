"""Tests for PolicyEngine.default and PolicyEngine.decide."""
from __future__ import annotations

import pytest

from lokdocsqr.core.models import ActionProposal, PolicyAction, PolicyDecision
from lokdocsqr.core.policy import PolicyConfig, PolicyEngine


def _proposal(node_id="x", operation="read"):
    return ActionProposal(node_id=node_id, operation=operation)


def test_default_read_allow():
    decision = PolicyEngine.default().decide(_proposal(operation="read"))
    assert decision.action is PolicyAction.ALLOW


@pytest.mark.parametrize("operation", ["write", "move", "copy", "mkdir"])
def test_default_high_risk_requires_verify(operation):
    decision = PolicyEngine.default().decide(_proposal(operation=operation))
    assert decision.action is PolicyAction.REQUIRE_VERIFY


def test_default_unknown_block():
    decision = PolicyEngine.default().decide(_proposal(operation="unknown"))
    assert decision.action is PolicyAction.BLOCK


@pytest.mark.parametrize("operation", ["foo", ""])
def test_default_other_operations_block(operation):
    decision = PolicyEngine.default().decide(_proposal(operation=operation))
    assert decision.action is PolicyAction.BLOCK


def test_node_override_overrides_forbidden():
    engine = PolicyEngine(
        PolicyConfig(
            forbidden_operations=frozenset({"write"}),
            node_overrides={"special": PolicyAction.ALLOW},
        )
    )
    decision = engine.decide(_proposal(node_id="special", operation="write"))
    assert decision.action is PolicyAction.ALLOW


def test_node_override_overrides_default_action():
    engine = PolicyEngine(
        PolicyConfig(
            default_action=PolicyAction.ALLOW,
            node_overrides={"special": PolicyAction.BLOCK},
        )
    )
    decision = engine.decide(_proposal(node_id="special", operation="read"))
    assert decision.action is PolicyAction.BLOCK


def test_forbidden_overrides_high_risk():
    engine = PolicyEngine(
        PolicyConfig(
            forbidden_operations=frozenset({"write"}),
            high_risk_operations=frozenset({"write"}),
            default_action=PolicyAction.ALLOW,
        )
    )
    decision = engine.decide(_proposal(operation="write"))
    assert decision.action is PolicyAction.BLOCK


def test_forbidden_overrides_default_for_read():
    engine = PolicyEngine(
        PolicyConfig(
            forbidden_operations=frozenset({"read"}),
            default_action=PolicyAction.ALLOW,
        )
    )
    decision = engine.decide(_proposal(operation="read"))
    assert decision.action is PolicyAction.BLOCK


def test_unknown_overrides_default():
    engine = PolicyEngine(
        PolicyConfig(
            high_risk_operations=frozenset(),
            default_action=PolicyAction.ALLOW,
        )
    )
    decision = engine.decide(_proposal(operation="unknown"))
    assert decision.action is PolicyAction.BLOCK


def test_exact_match_not_substring_replace():
    engine = PolicyEngine(
        PolicyConfig(
            forbidden_operations=frozenset({"replace"}),
            high_risk_operations=frozenset(),
            default_action=PolicyAction.ALLOW,
        )
    )
    decision = engine.decide(_proposal(operation="str.replace"))
    assert decision.action is PolicyAction.ALLOW


def test_exact_match_not_substring_read_more():
    engine = PolicyEngine(
        PolicyConfig(
            forbidden_operations=frozenset({"read"}),
            high_risk_operations=frozenset(),
            default_action=PolicyAction.ALLOW,
        )
    )
    decision = engine.decide(_proposal(operation="read_more"))
    assert decision.action is PolicyAction.ALLOW


def test_determinism_same_proposal():
    engine = PolicyEngine.default()
    proposal = _proposal(node_id="n1", operation="read")
    first = engine.decide(proposal)
    second = engine.decide(proposal)
    assert first == second
    assert first.reason == second.reason


def test_determinism_two_equivalent_proposals():
    engine = PolicyEngine.default()
    first_proposal = ActionProposal(
        node_id="n1",
        operation="write",
        target_paths=("a",),
        payload={"k": "v"},
        summary="s",
    )
    second_proposal = ActionProposal(
        node_id="n1",
        operation="write",
        target_paths=("a",),
        payload={"k": "v"},
        summary="s",
    )
    assert engine.decide(first_proposal) == engine.decide(second_proposal)


def test_reason_non_empty_and_stable():
    engine = PolicyEngine.default()
    proposal = _proposal(operation="unknown")
    first = engine.decide(proposal)
    second = engine.decide(proposal)
    assert first.reason
    assert first.reason == second.reason


def test_decide_does_not_raise_for_unknown():
    decision = PolicyEngine.default().decide(_proposal(operation="unknown"))
    assert isinstance(decision, PolicyDecision)
    assert decision.action is PolicyAction.BLOCK


def test_empty_operation_returns_block():
    decision = PolicyEngine.default().decide(_proposal(operation=""))
    assert decision.action is PolicyAction.BLOCK
