"""Phase 7b — validate_proposal / validate_plan contracts."""
from __future__ import annotations

from pathlib import Path

import pytest

from lokdocsqr.core.errors import PolicyError, ProposalValidationError
from lokdocsqr.core.models import ActionProposal
from lokdocsqr.core.planning import ActionPlan, validate_plan, validate_proposal


class _ScopeSpy:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def check(self, path, write=False):
        self.calls.append((Path(path), write))
        if self.exc is not None:
            raise self.exc
        return Path(path)


class _Ctx:
    def __init__(self, scope=None):
        self.scope = scope or _ScopeSpy()


def _proposal(**overrides):
    values = {
        "node_id": "n",
        "operation": "read",
        "target_paths": ("a",),
        "payload": {},
        "summary": "s",
    }
    values.update(overrides)
    return ActionProposal(**values)


def test_valid_proposal_does_not_raise():
    validate_proposal(_proposal(), _Ctx())


def test_node_id_must_be_str():
    proposal = _proposal()
    object.__setattr__(proposal, "node_id", 1)
    with pytest.raises(ProposalValidationError):
        validate_proposal(proposal, _Ctx())


def test_target_paths_must_be_tuple_not_list():
    proposal = _proposal()
    object.__setattr__(proposal, "target_paths", ["a"])
    with pytest.raises(ProposalValidationError):
        validate_proposal(proposal, _Ctx())


def test_operation_unknown_is_schema_valid():
    validate_proposal(_proposal(operation="unknown", target_paths=()), _Ctx())


def test_payload_must_be_dict():
    proposal = _proposal()
    object.__setattr__(proposal, "payload", ["x"])
    with pytest.raises(ProposalValidationError):
        validate_proposal(proposal, _Ctx())


def test_summary_must_be_str():
    proposal = _proposal()
    object.__setattr__(proposal, "summary", 1)
    with pytest.raises(ProposalValidationError):
        validate_proposal(proposal, _Ctx())


@pytest.mark.parametrize("operation", ["write", "move", "copy", "mkdir"])
def test_scope_check_called_for_each_mutating_path(operation):
    ctx = _Ctx()
    validate_proposal(
        _proposal(operation=operation, target_paths=("a", "b")),
        ctx,
    )
    assert len(ctx.scope.calls) == 2
    assert all(write is True for _, write in ctx.scope.calls)
    assert [path for path, _ in ctx.scope.calls] == [Path("a"), Path("b")]


def test_read_does_not_call_scope_check_write():
    ctx = _Ctx()
    validate_proposal(_proposal(operation="read", target_paths=("a",)), ctx)
    assert ctx.scope.calls == []


def test_out_of_scope_raises():
    ctx = _Ctx(_ScopeSpy(exc=ValueError("outside")))
    with pytest.raises(ProposalValidationError, match="scope violation"):
        validate_proposal(_proposal(operation="write", target_paths=("a",)), ctx)


def test_mutating_empty_target_paths_raises():
    with pytest.raises(ProposalValidationError, match="target_paths"):
        validate_proposal(_proposal(operation="write", target_paths=()), _Ctx())


def test_proposal_validation_error_location():
    assert ProposalValidationError.__module__.startswith("lokdocsqr.core.errors")
    assert issubclass(ProposalValidationError, PolicyError)


def test_validate_plan_informational_does_not_raise():
    plan = ActionPlan(
        proposals=(),
        decisions=(),
        previews=(),
        errors=(),
        blocked=True,
    )
    validate_plan(plan, _Ctx())
