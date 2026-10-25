"""Negative tests. Strict fake scope (conftest) makes these real negatives."""
from __future__ import annotations

import pytest

from tests.trash.conftest import run_trash


def test_path_traversal_rejected(trash_env):
    evil = trash_env.base / ".." / "escape.txt"
    evil.parent.mkdir(parents=True, exist_ok=True)
    evil.write_bytes(b"escape")

    from lokdocsqr.core.errors import ScopeViolation
    with pytest.raises((ScopeViolation, ValueError, PermissionError)):
        run_trash(trash_env, evil)


def test_outside_scope_rejected(trash_env, tmp_path):
    outside = tmp_path / "outside" / "file.txt"
    outside.parent.mkdir()
    outside.write_bytes(b"nope")

    from lokdocsqr.core.errors import ScopeViolation
    with pytest.raises((ScopeViolation, ValueError, PermissionError)):
        run_trash(trash_env, outside)


def test_missing_source_does_not_silently_succeed(trash_env):
    missing = trash_env.base / "ghost.txt"
    assert not missing.exists()

    from lokdocsqr.core.errors import NodeExecutionError, SafeFsError
    try:
        result = run_trash(trash_env, missing)
    except (NodeExecutionError, SafeFsError, FileNotFoundError):
        return
    assert result is not None, \
        "BUG: trash.py — silent None for missing source"
    state = getattr(result, "state", None)
    if state is not None:
        state_name = getattr(state, "name", str(state))
        assert state_name in ("FAILED", "SKIPPED"), (
            f"BUG: trash.py — missing source produced state {state_name}; "
            f"expected FAILED/SKIPPED"
        )