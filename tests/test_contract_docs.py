"""Verify that the contract and phase-state documents are complete."""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def test_contract_exists() -> None:
    """CONTRACT.md must exist."""
    assert (DOCS / "CONTRACT.md").is_file()


@pytest.mark.parametrize("invariant_id", list(range(1, 9)))
def test_contract_lists_invariant(invariant_id: int) -> None:
    """Every invariant 1..8 must have a row in the contract table."""
    text = (DOCS / "CONTRACT.md").read_text(encoding="utf-8")
    assert f"| {invariant_id} |" in text, (
        f"CONTRACT.md missing invariant {invariant_id}"
    )


def test_contract_mentions_key_invariants() -> None:
    """The contract must spell out the critical invariants by name."""
    text = (DOCS / "CONTRACT.md").read_text(encoding="utf-8")
    for keyword in (
        "Layering",
        "GUI isolation",
        "Delete ban",
        "No-delete API",
        "Node contract",
        "Dry-run purity",
        "Scope-first",
        "Executor isolation",
    ):
        assert keyword in text, f"CONTRACT.md missing keyword: {keyword}"


def test_contract_mentions_safe_fs() -> None:
    """safe_fs is the designated exception to the delete-ban."""
    text = (DOCS / "CONTRACT.md").read_text(encoding="utf-8")
    assert "core/safe_fs.py" in text or "core.safe_fs" in text


def test_phase_state_exists() -> None:
    """PHASE_STATE.md must exist."""
    assert (DOCS / "PHASE_STATE.md").is_file()


def test_phase_state_current_is_zero() -> None:
    """Phase-0 state must report Current: 0."""
    text = (DOCS / "PHASE_STATE.md").read_text(encoding="utf-8")
    assert "Current: 0" in text


def test_phase_state_notes_contract_fixed() -> None:
    """Phase-0 notes must mention the contract is fixed."""
    text = (DOCS / "PHASE_STATE.md").read_text(encoding="utf-8")
    assert "contract fixed" in text or "contract frozen" in text
