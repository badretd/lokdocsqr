"""Subphase 5b: conflict-policy resolution, sibling naming, risk mapping."""
import dataclasses
from pathlib import Path

import pytest

try:
    from lokdocsqr.nodes.fs_ops._common import (
        CONFLICT_POLICIES,
        ConflictDecision,
        _find_free_sibling,
        resolve_conflict_policy,
        risk_for_decision,
    )
    from lokdocsqr.core.models import Risk
except ImportError as exc:  # pragma: no cover
    pytest.skip(
        f"subphase 5b not installed: {exc!r}",
        allow_module_level=True,
    )


# ── constants & dataclass contract ────────────────────────────────
def test_conflict_policies_exact_tuple():
    assert CONFLICT_POLICIES == ("skip", "rename", "overwrite", "ask")


def test_conflict_decision_is_frozen_dataclass():
    d = ConflictDecision(action="proceed", final_dst=Path("/x"), reason=None)
    assert dataclasses.is_dataclass(d)
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.action = "skip"  # type: ignore[misc]


# ── resolve_conflict_policy: no conflict → proceed, reason=None ───
@pytest.mark.parametrize("policy", CONFLICT_POLICIES)
def test_no_conflict_all_policies_proceed(policy, tmp_path):
    target = tmp_path / "file.txt"  # does not exist
    decision = resolve_conflict_policy(policy, target)
    assert decision.action == "proceed"
    assert decision.final_dst == target
    assert decision.reason is None


def test_accepts_str_dst(tmp_path):
    target = str(tmp_path / "file.txt")
    decision = resolve_conflict_policy("skip", target)
    assert isinstance(decision.final_dst, Path)
    assert decision.action == "proceed"
    assert decision.reason is None


# ── resolve_conflict_policy: conflict, each policy ────────────────
def test_conflict_skip(tmp_path):
    existing = tmp_path / "file.txt"
    existing.touch()
    decision = resolve_conflict_policy("skip", existing)
    assert decision.action == "skip"
    assert decision.final_dst == existing
    assert decision.reason == "conflict_skip"


def test_conflict_overwrite(tmp_path):
    existing = tmp_path / "file.txt"
    existing.touch()
    decision = resolve_conflict_policy("overwrite", existing)
    assert decision.action == "proceed"
    assert decision.final_dst == existing
    assert decision.reason == "conflict_overwrite"


def test_conflict_rename_finds_free_sibling(tmp_path):
    existing = tmp_path / "file.txt"
    existing.touch()
    decision = resolve_conflict_policy("rename", existing)
    assert decision.action == "proceed"
    assert decision.final_dst == tmp_path / "file (1).txt"
    assert decision.reason == "conflict_rename"


def test_conflict_ask_skips_for_verify(tmp_path):
    existing = tmp_path / "file.txt"
    existing.touch()
    decision = resolve_conflict_policy("ask", existing)
    assert decision.action == "skip"
    assert decision.final_dst == existing
    assert decision.reason == "conflict_pending_verify"


def test_invalid_policy_raises(tmp_path):
    with pytest.raises(ValueError, match="unknown conflict policy"):
        resolve_conflict_policy("bogus", tmp_path / "x")


# ── _find_free_sibling edge cases ─────────────────────────────────
def test_find_free_sibling_skips_existing(tmp_path):
    existing = tmp_path / "file.txt"
    existing.touch()
    (tmp_path / "file (1).txt").touch()
    (tmp_path / "file (2).txt").touch()
    assert _find_free_sibling(existing) == tmp_path / "file (3).txt"


def test_find_free_sibling_preserves_directory(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    existing = sub / "file.txt"
    existing.touch()
    assert _find_free_sibling(existing).parent == sub


def test_find_free_sibling_no_suffix(tmp_path):
    existing = tmp_path / "README"
    existing.touch()
    assert _find_free_sibling(existing) == tmp_path / "README (1)"


def test_find_free_sibling_deterministic(tmp_path):
    """Same tree → same result (contract in the docstring)."""
    existing = tmp_path / "file.txt"
    existing.touch()
    assert _find_free_sibling(existing) == _find_free_sibling(existing)


# ── risk_for_decision mapping ─────────────────────────────────────
@pytest.mark.parametrize("reason, expected", [
    ("conflict_pending_verify", "HIGH"),
    (None,                      "LOW"),
    ("no_change",               "LOW"),
    ("conflict_skip",           "MEDIUM"),
    ("conflict_rename",         "MEDIUM"),
    ("conflict_overwrite",      "MEDIUM"),
    ("unknown_reason",          "MEDIUM"),
])
def test_risk_for_decision(reason, expected):
    d = ConflictDecision(
        action="proceed", final_dst=Path("/x"), reason=reason,
    )
    assert getattr(risk_for_decision(d), "name", risk_for_decision(d)) == expected
