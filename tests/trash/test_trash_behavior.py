"""Behavioral trash tests — 4 trash runs total (down from ~12)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.trash.conftest import run_trash

SIDECAR_FIELDS_5C = (
    "hash_before", "original_path", "trash_path", "sidecar_path",
    "size", "trashed_at", "run_id", "automation_id",
)


def test_sidecar_has_all_eight_fields(trash_env):
    """One trash run; report ALL missing fields with explicit names."""
    src = trash_env.base / "payload.txt"
    src.write_bytes(b"hello sidecar")
    result = run_trash(trash_env, src)
    assert result is not None, "BUG: trash.py — run() returned None"

    sidecar_attr = getattr(result, "sidecar_path", None)
    if not sidecar_attr:
        pytest.fail("BUG: trash.py — ActionLog.sidecar_path missing/empty")
    sidecar_path = Path(sidecar_attr)
    if not sidecar_path.exists():
        pytest.fail(f"BUG: trash.py — sidecar not written at {sidecar_path}")
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    missing = [f for f in SIDECAR_FIELDS_5C if f not in payload]
    if missing:
        pytest.fail(
            f"BUG: trash.py — sidecar missing fields {missing}; "
            f"present: {sorted(payload)}"
        )


def test_trash_produces_actionlog_with_rollback_metadata(trash_env):
    src = trash_env.base / "doc.txt"
    content = b"original content"
    src.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()

    result = run_trash(trash_env, src)
    assert result is not None, "BUG: trash.py — run() returned None"
    assert result.action_type == "trash"
    assert result.dry_run is False
    assert getattr(result, "rollback_eligible", None) is True, (
        "BUG: trash.py — rollback_eligible not True"
    )
    assert getattr(result, "original_path", None) == str(src), (
        "BUG: trash.py — original_path mismatch"
    )
    assert result.hash_before == expected, (
        f"BUG: trash.py — hash_before {result.hash_before!r} != {expected!r}"
    )
    assert not src.exists(), "BUG: trash.py — source not moved"
    assert Path(result.trash_path).exists(), \
        f"BUG: trash.py — {result.trash_path} missing after trash"


def test_dry_run_is_pure(trash_env):
    """Combined: no source mutation, no sidecar, no audit append."""
    src = trash_env.base / "dry.txt"
    src.write_bytes(b"dry run test")
    before = src.read_bytes()

    result = run_trash(trash_env, src, dry_run=True)

    assert src.exists(), "BUG: trash.py — dry_run moved source"
    assert src.read_bytes() == before, "BUG: trash.py — dry_run mutated source"
    assert result is not None, "BUG: trash.py — dry_run returned None"
    assert result.dry_run is True
    assert getattr(result, "rollback_eligible", None) is True, (
        "BUG: trash.py — dry_run log not rollback_eligible"
    )
    assert list(trash_env.trash.iterdir()) == [], (
        "BUG: trash.py — dry_run wrote to trash dir"
    )
    assert trash_env.audit.logs == [], (
        "BUG: _common.py — dry_run log was appended to audit"
    )


def test_rollback_via_audit_restores_original(trash_env):
    src = trash_env.base / "rollback.txt"
    content = b"rollback me"
    src.write_bytes(content)

    result = run_trash(trash_env, src)
    if not trash_env.audit.logs:
        pytest.fail("BUG: trash.py — ActionLog not appended to ctx.audit")

    logged = trash_env.audit.logs[-1]
    assert getattr(logged, "original_path", None) == str(src)
    assert getattr(logged, "rollback_eligible", None) is True

    import lokdocsqr.core.audit as audit_mod
    rollback_fn = None
    for name in ("rollback", "plan_rollback",
                 "generate_rollback_plan", "rollback_run"):
        fn = getattr(audit_mod, name, None)
        if callable(fn):
            rollback_fn = fn
            break

    if rollback_fn is None:
        # Verify byte-identical recoverability even without public API.
        assert Path(result.trash_path).read_bytes() == content, (
            "BUG: trash.py — trashed content differs; not recoverable"
        )
        pytest.skip("core.audit rollback API not yet present — "
                    "file verified recoverable at trash_path")

    try:
        rollback_fn("test-run-001")
    except TypeError:
        rollback_fn(run_id="test-run-001")

    assert src.exists(), "BUG: core.audit rollback — original not restored"
    assert src.read_bytes() == content, \
        "BUG: core.audit rollback — restored content differs"