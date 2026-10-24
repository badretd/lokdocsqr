"""Verify the contract and phase-state documents are present and useful."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

INVARIANT_KEYWORDS = (
    "Layering",
    "GUI isolation",
    "Delete ban",
    "No-delete API",
    "Node contract",
    "Dry-run purity",
    "Scope-first",
    "Executor isolation",
)


def _contract_path() -> Path:
    """Return the contract file location if it exists, else the root default."""
    for candidate in (ROOT / "CONTRACT.md", ROOT / "docs" / "CONTRACT.md"):
        if candidate.is_file():
            return candidate
    return DOCS / "CONTRACT.md"


def test_contract_doc_exists() -> None:
    """CONTRACT.md must exist at the project root (or docs/)."""
    assert _contract_path().is_file(), "CONTRACT.md not found"


def test_contract_doc_mentions_all_invariants() -> None:
    """The contract must name each of the eight invariants."""
    text = _contract_path().read_text(encoding="utf-8")
    missing = [kw for kw in INVARIANT_KEYWORDS if kw not in text]
    assert not missing, f"CONTRACT.md missing keywords: {missing}"