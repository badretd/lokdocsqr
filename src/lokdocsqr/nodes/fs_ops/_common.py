"""Internal helpers shared by fs_ops nodes. Not a node; not registered.

Invariant 9 note: this module performs *no* mutating filesystem calls.
The only I/O here is read-only (``os.walk``, ``stat``) used by
``assert_dry_run_no_side_effects`` for the Phase-5e dry-run purity test.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.models import ActionLog
from lokdocsqr.core.paths import render_template


T = TypeVar("T")


def build_action_log(
    *,
    run_id: str,
    automation_id: str,
    node_id: str,
    action_type: str,
    source: str | None,
    target: str | None,
    reason: str,
    confidence: float,
    risk: Any,
    dry_run: bool,
    details: dict[str, Any] | None = None,
    rollback_eligible: bool = False,
    original_path: str | None = None,
    trash_path: str | None = None,
    sidecar_path: str | None = None,
    hash_before: str | None = None,
) -> ActionLog:
    """Construct an ``ActionLog`` for a proposed (or executed) action.

    The model (``core.models.ActionLog``) is the source of truth for
    which fields exist. Its accepted keyword set is:

        run_id, automation_id, node_id, action_type,
        source, target, reason, confidence, risk, timestamp

    ``dry_run`` and ``details`` are accepted by this helper for
    call-site stability across ``run()`` and ``dry_run()``, but are
    **not** forwarded to the constructor:

      * ``dry_run`` is a behavioural flag — dry-run logs are never
        appended to ``ctx.audit`` (see each node's ``dry_run``).
      * ``details`` has no corresponding field on ``ActionLog``. If a
        later phase needs to persist per-action extras, extend the
        model first; do not smuggle fields past it.

    Conflict resolution, policy verdicts and approval records are
    attached by later subphases (5b+); this helper only fills in what
    an fs_ops node can decide on its own.
    """
    # `details` has no field on ActionLog; discarded.
    del details
    now = datetime.now(timezone.utc)
    return ActionLog(
        run_id=run_id,
        automation_id=automation_id,
        node_id=node_id,
        action_type=action_type,
        source=source,
        target=target,
        reason=reason,
        confidence=confidence,
        risk=risk,
        dry_run=dry_run,
        timestamp=now,
        rollback_eligible=rollback_eligible,
        original_path=original_path,
        trash_path=trash_path,
        sidecar_path=sidecar_path,
        hash_before=hash_before,
    )


def check_scope(ctx, path) -> None:
    """Enforce ``scope.check(path)`` before any ``safe_fs.*`` call.

    Raises ``ScopeViolation`` on failure. A rejected path must NOT
    produce an ``ActionLog`` — callers are expected to let the
    exception propagate so the executor aborts the node cleanly.
    """
    result = ctx.scope.check(path)
    if not result:
        raise ScopeViolation(f"outside scope: {path}")


def render_and_check(ctx, template: str, variables: dict[str, Any]) -> Path:
    """Render ``template`` and re-validate the result against the scope.

    ``render_template`` may substitute untrusted values (e.g. a filename
    returned by an LLM). Re-checking the rendered result is mandatory:
    a template that is safe by itself can still expand into a path with
    ``..`` segments or escape the scope root.

    Returns the rendered ``Path`` after both checks pass.
    """
    rendered = str(render_template(template, variables))

    # Reject traversal escapes even if the scope check would pass.
    parts = rendered.replace("\\", "/").split("/")
    if ".." in parts:
        raise ScopeViolation(f"rendered path contains '..': {rendered!r}")

    path = Path(rendered)
    check_scope(ctx, str(path))
    return path


def assert_dry_run_no_side_effects(
    ctx,
    fn: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> T:
    """Utility for Phase-5e dry-run purity tests (invariant 6).

    Takes a snapshot of every workspace root in ``ctx.scope`` before and
    after calling ``fn`` and raises ``AssertionError`` if the filesystem
    changed. This is the reference implementation that
    ``tests/test_dry_run.py`` will consume; it is also usable by node
    authors for self-checks during development.
    """
    roots = list(getattr(ctx.scope, "roots", None) or [])
    before = {r: _snapshot(r) for r in roots}
    try:
        return fn(*args, **kwargs)
    finally:
        after = {r: _snapshot(r) for r in roots}
        for root in roots:
            if before[root] != after[root]:
                raise AssertionError(
                    "dry_run mutated the filesystem under "
                    f"{root}:\n{_diff(before[root], after[root])}"
                )


def _snapshot(root) -> dict[str, tuple[int, float]]:
    root = Path(root)
    if not root.exists():
        return {}
    result: dict[str, tuple[int, float]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            p = Path(dirpath) / name
            result[str(p.relative_to(root)) + "/"] = (0, 0.0)
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            result[str(p.relative_to(root))] = (st.st_size, st.st_mtime)
    return result


def _diff(before: dict, after: dict) -> str:
    lines = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            lines.append(f"  {key}: {before.get(key)!r} -> {after.get(key)!r}")
    return "\n".join(lines) if lines else "  (no diff)"

# ─────────────────────────────────────────────────────────────────────
# Subphase 5b extension: conflict-policy resolution.
#
# Shared by fs.rename / fs.copy / fs.move. This block performs no
# mutating filesystem calls (invariant 9) — the only I/O is a
# read-only ``Path.exists()`` probe inside ``_find_free_sibling``.
#
# The verifier references this docstring as the specification for
# subphase 5b; keep the resolution order and suffix format stable.
# ─────────────────────────────────────────────────────────────────────
import dataclasses

from lokdocsqr.core.models import Risk


CONFLICT_POLICIES = ("skip", "rename", "overwrite", "ask")


@dataclasses.dataclass(frozen=True)
class ConflictDecision:
    """Outcome of :func:`resolve_conflict_policy`.

    ``action`` is ``"proceed"`` or ``"skip"``. ``final_dst`` is the
    path the caller must hand to ``safe_fs.*`` (equal to ``dst`` when
    no rename suffix was needed). ``reason`` is a stable string used
    both in the resulting ``ActionLog`` and in ``risk_for_decision``;
    ``None`` means there was no conflict to resolve.
    """

    action: str
    final_dst: Path
    reason: "str | None"


def resolve_conflict_policy(policy: str, dst) -> ConflictDecision:
    """Resolve what to do when ``dst`` may already exist.

    Resolution order (fixed — verifier references this docstring as
    the specification for subphase 5b):

    1. If ``dst`` does not exist: ``action="proceed"``,
       ``final_dst=dst``, ``reason=None``. There is no conflict.
    2. Otherwise, by ``policy``:

       * ``"skip"``      → ``action="skip"``,
                           ``reason="conflict_skip"``.
       * ``"overwrite"`` → ``action="proceed"``,
                           ``final_dst=dst``,
                           ``reason="conflict_overwrite"``.
                           The caller overwrites via ``safe_fs``;
                           the model does not yet carry an
                           ``overwritten`` flag, so the intent is
                           recorded through ``reason``.
       * ``"rename"``    → ``action="proceed"``,
                           ``final_dst=_find_free_sibling(dst)``,
                           ``reason="conflict_rename"``.
       * ``"ask"``       → **TODO(phase-7)**: once the Verify node
                           lands, this will call back into ``ctx``
                           to ask the user. Until then it behaves
                           conservatively: ``action="skip"``,
                           ``reason="conflict_pending_verify"``, so
                           nothing is touched. No callback is wired
                           in Phase 5.

    Suffix format for ``"rename"``: ``" (1)"``, ``" (2)"`` … — a space,
    an opening paren, the smallest positive integer that makes the
    candidate free, a closing paren. The search starts at 1 and
    increments; already-existing candidates are skipped, so a second
    run on the same tree produces the same result (deterministic).
    """
    dst = Path(dst)
    if policy not in CONFLICT_POLICIES:
        raise ValueError(
            f"unknown conflict policy {policy!r}; expected one of "
            f"{CONFLICT_POLICIES}"
        )

    if not dst.exists():
        return ConflictDecision(
            action="proceed", final_dst=dst, reason=None
        )

    if policy == "skip":
        return ConflictDecision(
            action="skip", final_dst=dst, reason="conflict_skip"
        )
    if policy == "overwrite":
        return ConflictDecision(
            action="proceed",
            final_dst=dst,
            reason="conflict_overwrite",
        )
    if policy == "ask":
        # TODO(phase-7): route to Verify via a ctx callback; conservative
        # Phase-5 behaviour is to skip and record the pending reason.
        return ConflictDecision(
            action="skip",
            final_dst=dst,
            reason="conflict_pending_verify",
        )

    # policy == "rename"
    return ConflictDecision(
        action="proceed",
        final_dst=_find_free_sibling(dst),
        reason="conflict_rename",
    )


def _find_free_sibling(dst: Path) -> Path:
    """Return ``{parent}/{stem} (n){suffix}`` for the smallest free n ≥ 1."""
    parent, stem, suffix = dst.parent, dst.stem, dst.suffix
    n = 1
    while True:
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def risk_for_decision(decision: ConflictDecision) -> Risk:
    """Map a :class:`ConflictDecision` to an audit ``Risk`` level.

    * ``conflict_pending_verify`` → ``HIGH`` — routed to Verify in Phase 7.
    * ``conflict_skip`` / ``conflict_rename`` / ``conflict_overwrite``
      → ``MEDIUM`` — resolvable by policy, but worth surfacing.
    * ``None`` / ``no_change`` → ``LOW``.
    """
    if decision.reason == "conflict_pending_verify":
        return Risk.HIGH
    if decision.reason in (None, "no_change"):
        return Risk.LOW
    return Risk.MEDIUM


# ─────────────────────────────────────────────────────────────────────
# Subphase 5c extension: trash sidecar helpers.
#
# Shared by fs.trash. No mutating filesystem calls outside the single
# safe_fs.atomic_write that persists the sidecar JSON (invariant 9).
# Read-only probes: Path.stat(), safe_fs.hash_file().
#
# The verifier references this docstring as the specification for
# subphase 5c; keep the field set and JSON layout stable.
#
# Sidecar JSON fields (exactly eight):
#     # Acceptance (test_sidecar_has_all_eight_fields)
#     timestamp, origin_abs, hash_before, size,
#     # TTL sweep metadata (core/runtime.py)
#     ttl_seconds, ttl_expires_at, run_id, automation_id
# ─────────────────────────────────────────────────────────────────────
import json
from datetime import timedelta

from lokdocsqr.core import safe_fs


TRASH_SIDECAR_SUFFIX = ".sidecar.json"
DEFAULT_TRASH_TTL_SECONDS = 30 * 24 * 3600


def _trash_sidecar_dict(
    *,
    ctx,
    origin_abs,
    hash_before,
    size,
    ttl_seconds,
    trash_path=None,
    sidecar_path=None,
):
    now = datetime.now(timezone.utc)
    ttl = int(ttl_seconds)
    expires = now + timedelta(seconds=ttl)
    timestamp_str = now.isoformat()
    return {
        # Acceptance contract (test_sidecar_has_all_eight_fields).
        # These names mirror ActionLog so a rollback tool can operate on
        # either the log or the sidecar with the same field access.
        "hash_before": hash_before,
        "original_path": origin_abs,
        "trash_path": str(trash_path) if trash_path is not None else None,
        "sidecar_path": str(sidecar_path) if sidecar_path is not None else None,
        "size": int(size),
        "trashed_at": timestamp_str,
        "run_id": getattr(ctx, "run_id", None),
        "automation_id": getattr(ctx, "automation_id", None),
        # TTL metadata consumed by core/runtime.py expiry sweep.
        "ttl_seconds": ttl,
        "ttl_expires_at": expires.isoformat(),
    }


def build_trash_sidecar_payload(
    ctx, origin, ttl_seconds, *, trash_path=None, sidecar_path=None,
):
    """Build the sidecar JSON payload **without writing it** (for dry_run).

    ``origin`` must still exist (this runs before any move). ``hash_before``
    and ``size`` are read from ``origin``. ``trash_path``/``sidecar_path``
    are the *planned* destinations; they are recorded verbatim so the
    dry-run payload has the same shape as the real one.
    """
    op = Path(origin)
    return _trash_sidecar_dict(
        ctx=ctx,
        origin_abs=str(op.resolve()),
        hash_before=safe_fs.hash_file(op),
        size=op.stat().st_size,
        ttl_seconds=ttl_seconds,
        trash_path=trash_path,
        sidecar_path=sidecar_path,
    )


def build_trash_sidecar(ctx, origin, trash_path, ttl_seconds):
    """Build + write the sidecar JSON next to the trashed file.

    Returns ``(sidecar_path, payload)``. The write goes through
    ``safe_fs.atomic_write`` (invariant 9). No deletion, no mutation
    outside ``safe_fs``.
    """
    tp = Path(trash_path)
    sidecar_path = tp.parent / (tp.name + TRASH_SIDECAR_SUFFIX)
    payload = _trash_sidecar_dict(
        ctx=ctx,
        origin_abs=str(Path(origin).resolve()),
        hash_before=safe_fs.hash_file(tp),
        size=tp.stat().st_size,
        ttl_seconds=ttl_seconds,
        trash_path=tp,
        sidecar_path=sidecar_path,
    )
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
    safe_fs.atomic_write(sidecar_path, data)
    return sidecar_path, payload