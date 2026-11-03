"""Docs guard: docs/DSL.md examples must match executable behavior."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "verify_dsl_docs.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_dsl_docs", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_docs_examples() -> None:
    verifier = _load_verifier()
    counts = verifier.verify()
    assert counts["ldsq_blocks"] > 0
    assert counts["pairs"] >= 6
    assert counts["registered_nodes"] == 32
