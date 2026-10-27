"""Tests for Phase 7a policy types."""
from __future__ import annotations

import dataclasses

import pytest

from lokdocsqr.core.errors import (
    LokError,
    PolicyBlockedError,
    PolicyConfigError,
    PolicyError,
    PolicyTimeoutError,
    ProposalValidationError,
)
from lokdocsqr.core.models import (
    ActionProposal,
    PolicyAction,
    PolicyDecision,
    VerifyAction,
    VerifyResult,
)


def test_action_proposal_frozen():
    proposal = ActionProposal(node_id="n1", operation="read")
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.node_id = "n2"


def test_policy_decision_frozen():
    proposal = ActionProposal(node_id="n1", operation="read")
    decision = PolicyDecision(PolicyAction.ALLOW, "ok", proposal)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.action = PolicyAction.BLOCK


def test_verify_result_defaults_and_frozen():
    result = VerifyResult(VerifyAction.APPROVE)
    assert result.action is VerifyAction.APPROVE
    assert result.new_payload is None
    assert result.reason == ""
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.reason = "changed"


def test_policy_action_values():
    assert {item.value for item in PolicyAction} == {
        "allow",
        "block",
        "require_verify",
    }


def test_verify_action_values():
    assert {item.value for item in VerifyAction} == {
        "approve",
        "reject",
        "edit",
        "timeout",
    }


def test_action_proposal_constructor_with_7a_fields():
    proposal = ActionProposal(
        node_id="n1",
        operation="write",
        target_paths=("a.txt",),
        payload={"k": "v"},
        summary="do it",
    )
    assert proposal.node_id == "n1"
    assert proposal.operation == "write"
    assert proposal.target_paths == ("a.txt",)
    assert proposal.payload == {"k": "v"}
    assert proposal.summary == "do it"


def test_action_proposal_payload_is_copied():
    original = {"k": "v"}
    proposal = ActionProposal(payload=original)
    original["k"] = "changed"
    assert proposal.payload == {"k": "v"}


@pytest.mark.parametrize(
    "exc_type",
    [
        PolicyConfigError,
        PolicyBlockedError,
        PolicyTimeoutError,
        ProposalValidationError,
    ],
)
def test_policy_error_subclasses(exc_type):
    assert issubclass(exc_type, PolicyError)


def test_policy_error_inherits_lokerror():
    assert issubclass(PolicyError, LokError)
