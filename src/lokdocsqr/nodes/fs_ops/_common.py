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
    # Accepted for API stability, not forwarded to the model.
    del dry_run, details
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
        timestamp=now,
    )


def check_scope(ctx, path) -> None:
    """Enforce ``scope.check(path)`` before any ``safe_fs.*`` call.

    Raises ``ScopeViolation`` on failure. A rejected path must NOT
    produce an ``ActionLog`` — callers are expected to let the
    exception propagate so the executor aborts the node cleanly.
    """
    if not ctx.scope.check(path):
        raise ScopeViolation(f"path outside workspace scope: {path!s}")


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
