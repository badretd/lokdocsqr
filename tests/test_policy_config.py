"""Tests for PolicyConfig and PolicyEngine.from_json."""
from __future__ import annotations

from pathlib import Path

import pytest

from lokdocsqr.core.errors import PolicyConfigError
from lokdocsqr.core.models import ActionProposal, PolicyAction
from lokdocsqr.core.policy import PolicyConfig, PolicyEngine


def _write_policy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot(root: Path):
    return {
        p.relative_to(root).as_posix(): (
            p.is_dir(),
            p.stat().st_size if p.is_file() else 0,
            p.stat().st_mtime_ns,
        )
        for p in root.rglob("*")
    }


def test_policy_config_defaults():
    config = PolicyConfig()
    assert config.default_action is PolicyAction.BLOCK
    assert config.high_risk_operations == frozenset(
        {"write", "move", "copy", "mkdir"}
    )
    assert config.forbidden_operations == frozenset()


def test_from_json_missing_file_returns_default(tmp_path):
    engine = PolicyEngine.from_json(tmp_path / "missing.json")
    assert engine.decide(ActionProposal(operation="read")).action is PolicyAction.ALLOW
    assert engine.decide(ActionProposal(operation="write")).action is PolicyAction.REQUIRE_VERIFY


def test_from_json_empty_file_returns_default(tmp_path):
    path = _write_policy(tmp_path, "")
    engine = PolicyEngine.from_json(path)
    assert engine.decide(ActionProposal(operation="read")).action is PolicyAction.ALLOW
    assert engine.decide(ActionProposal(operation="write")).action is PolicyAction.REQUIRE_VERIFY


def test_from_json_empty_object_returns_default(tmp_path):
    path = _write_policy(tmp_path, "{}")
    engine = PolicyEngine.from_json(path)
    assert engine.decide(ActionProposal(operation="read")).action is PolicyAction.ALLOW
    assert engine.decide(ActionProposal(operation="write")).action is PolicyAction.REQUIRE_VERIFY


def test_from_json_partial_merges_with_default(tmp_path):
    path = _write_policy(tmp_path, '{"forbidden_operations": ["delete"]}')
    engine = PolicyEngine.from_json(path)
    assert engine.decide(ActionProposal(operation="read")).action is PolicyAction.ALLOW
    assert engine.decide(ActionProposal(operation="write")).action is PolicyAction.REQUIRE_VERIFY
    assert engine.decide(ActionProposal(operation="delete")).action is PolicyAction.BLOCK


def test_from_json_broken_json_raises(tmp_path):
    path = _write_policy(tmp_path, "{not json")
    with pytest.raises(PolicyConfigError):
        PolicyEngine.from_json(path)


def test_from_json_invalid_default_action_raises(tmp_path):
    path = _write_policy(tmp_path, '{"default_action": "foo"}')
    with pytest.raises(PolicyConfigError):
        PolicyEngine.from_json(path)


def test_from_json_invalid_forbidden_type_raises(tmp_path):
    path = _write_policy(tmp_path, '{"forbidden_operations": "str"}')
    with pytest.raises(PolicyConfigError):
        PolicyEngine.from_json(path)


def test_from_json_non_object_json_raises(tmp_path):
    path = _write_policy(tmp_path, "[1,2,3]")
    with pytest.raises(PolicyConfigError):
        PolicyEngine.from_json(path)


def test_from_json_node_overrides_merge(tmp_path):
    path = _write_policy(tmp_path, '{"node_overrides": {"special": "allow"}}')
    engine = PolicyEngine.from_json(path)
    decision = engine.decide(
        ActionProposal(node_id="special", operation="write")
    )
    assert decision.action is PolicyAction.ALLOW


def test_from_json_default_action_override(tmp_path):
    path = _write_policy(tmp_path, '{"default_action": "allow"}')
    engine = PolicyEngine.from_json(path)
    decision = engine.decide(ActionProposal(operation="foo"))
    assert decision.action is PolicyAction.ALLOW


def test_from_json_does_not_mutate_fs(tmp_path):
    path = _write_policy(tmp_path, '{"forbidden_operations": ["delete"]}')
    before = _snapshot(tmp_path)
    PolicyEngine.from_json(path)
    after = _snapshot(tmp_path)
    assert before == after
