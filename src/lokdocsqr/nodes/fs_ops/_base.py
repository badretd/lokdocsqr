"""Shared parent classes for fs_ops nodes.

Extracts boilerplate common to the fs_ops node set:

  * :class:`FsNode` — ``describe()`` assembly, template variable
    extraction, scope-checked ``path_template`` rendering.
  * :class:`FsTemplateNode` — the ``run`` / ``dry_run`` / ``_plan`` /
    ``_build_log`` pipeline used by the source→target template nodes
    (``fs.rename``, ``fs.move``).

Neither class is registered; both are internal to the ``fs_ops``
package.

Invariant 9 note: this module performs no filesystem mutation.
:meth:`FsTemplateNode._execute` is an abstract hook — subclasses route
the actual mutation through ``safe_fs``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.models import ActionLog
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.paths import render_template

from ._common import (
    ActionResult,
    ConflictDecision,
    build_action_log,
    check_scope,
    render_and_check,
    resolve_conflict_policy,
    resolve_path,
    risk_for_decision,
)

# The set of ``action_type`` values that
# :meth:`lokdocsqr.core.audit.AuditLog.build_rollback_plan` can reverse.
# Must stay in sync with the branches in ``core/audit.py``:
#
#   * "move"   → "move_back"
#   * "rename" → "move_back"
#   * "trash"  → "restore_from_trash"  (handled by fs.trash, not here)
#   * "copy"   → NOT reversible (source remains; audit skips it)
#
# ``fs.trash`` does not inherit from ``FsTemplateNode`` and sets
# ``rollback_eligible=True`` explicitly in its own ``run``.
_ROLLBACKABLE_ACTIONS = frozenset({"move", "rename"})


def _payload_str(
    payload: dict[str, Any], key: str, default: str, node_id: str
) -> str:
    """Return a string setting from ``payload``, falling back to ``default``.

    Payload values come from ``NodeSpec.params`` (and, for wired ports,
    from upstream outputs), so they are untrusted. Reject non-string
    values here rather than letting a malformed template reach
    ``render_template`` or a malformed policy reach conflict resolution.
    """
    value = payload.get(key, default)
    if not isinstance(value, str):
        raise ValueError(
            f"{node_id} payload key {key!r} must be a string, "
            f"got {type(value).__name__}"
        )
    return value


class FsNode(Node):
    """Shared plumbing for fs_ops nodes.

    Provides:

      * :meth:`describe` — assembled from ``id`` / ``title`` /
        ``description`` plus :meth:`_describe_payload`. The default
        payload is ``{"inputs": ..., "outputs": ...}``; nodes whose
        public surface is a parameter set (e.g. ``fs.rename``) override
        :meth:`_describe_payload` to return ``{"params": ...}`` instead.
      * :meth:`_resolve` — render ``payload["path_template"]`` and
        re-check the result against ``ctx.scope`` (invariant 7).
      * :meth:`_variables` — the standard template variable set
        (``name`` / ``ext`` / ``date`` / ``author`` / ``tags``).
    """

    description: str = ""

    # ── describe ─────────────────────────────────────────────────
    def describe(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
        }
        result.update(self._describe_payload())
        if self.description:
            result["description"] = self.description
        return result

    def _describe_payload(self) -> dict[str, Any]:
        """Node-specific payload appended to :meth:`describe`.

        Default exposes ``inputs`` / ``outputs``. Override for nodes
        whose public surface is parameter-based rather than port-based.
        """
        return {
            "inputs": self.inputs,
            "outputs": self.outputs,
        }

    # ── template helpers ─────────────────────────────────────────
    @staticmethod
    def _variables(
        src: Path, extra: dict[str, Any]
    ) -> dict[str, Any]:
        """Return the standard template variable dict for ``src``.

        Caller-supplied ``extra`` keys win; the defaults fill in only
        the keys the caller did not provide.
        """
        try:
            mtime = src.stat().st_mtime
            date = datetime.fromtimestamp(
                mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except OSError:
            date = ""
        variables: dict[str, Any] = dict(extra)
        variables.setdefault("name", src.stem)
        variables.setdefault("ext", src.suffix)
        variables.setdefault("date", date)
        variables.setdefault("author", "")
        variables.setdefault("tags", "")
        return variables

    @staticmethod
    def _resolve(ctx: Any, payload: dict[str, Any]) -> Path:
        """Render ``payload["path_template"]`` and scope-check the result."""
        template = payload["path_template"]
        variables = payload.get("variables") or {}
        return render_and_check(ctx, template, variables, write=True)


class FsTemplateNode(FsNode):
    """Base for template-driven source→target nodes (``fs.rename``, ``fs.move``).

    Subclasses provide:

      * ``action_type`` — the value recorded on :class:`ActionLog`.
      * ``template`` / ``conflict_policy`` — set in ``__init__``.
      * :meth:`_execute` — the *only* mutating call; must go through
        ``safe_fs`` (invariant 9).
      * optionally :meth:`_describe_payload` to expose a parameter set.

    The pipeline is:

        _plan      → render target, scope-check, resolve conflict
        run        → plan, execute if ``proceed``, return log
        dry_run    → plan, log only (no execution)
    """

    action_type: str = ""
    template: str = "{name}{ext}"
    conflict_policy: str = "skip"
    _source_write: bool = False

    # ── planning ─────────────────────────────────────────────────
    def _render_target(
        self,
        ctx: Any,
        src: Path,
        variables: dict[str, Any],
        template: str | None = None,
    ) -> Path:
        """Render ``template`` (or ``self.template``) and return the destination.

        Relative results resolve against ``src.parent``; absolute
        results are returned as-is. ``..`` segments and empty renders
        are rejected here, before any ``safe_fs.*`` call sees the path.
        Callers may pass a payload-supplied template; ``None`` preserves
        the instance-level default.
        """
        template = self.template if template is None else template
        allow_parent = bool(
            getattr(
                getattr(ctx, "scope", None),
                "allow_parent_traversal",
                False,
            )
        )
        rendered_str = str(
            render_template(
                template,
                variables,
                allow_parent_traversal=allow_parent,
            )
        )
        if not rendered_str.strip():
            raise ValueError(
                f"{self.id} template rendered to empty: "
                f"template={template!r}"
            )
        parts = rendered_str.replace("\\", "/").split("/")
        if not allow_parent and ".." in parts:
            raise ScopeViolation(
                f"rendered path contains '..': {rendered_str!r}"
            )
        rendered = Path(rendered_str)
        if not rendered.suffix and src.suffix:
            rendered = rendered.with_suffix(src.suffix)
        if rendered.is_absolute():
            return rendered
        return src.parent / rendered

    def _plan(
        self, ctx: Any, payload: dict[str, Any]
    ) -> tuple[Path, Path, ConflictDecision]:
        """Resolve source, destination and conflict for one action.

        ``template`` and ``conflict_policy`` are read from ``payload``
        first so a ``NodeSpec.params``-configured node behaves the same
        as a programmatically constructed one. Instance attributes remain
        the defaults (and the fallback for direct callers).
        """
        src = resolve_path(
            ctx, payload["source"], write=self._source_write
        )

        template = _payload_str(payload, "template", self.template, self.id)
        conflict_policy = _payload_str(
            payload, "conflict_policy", self.conflict_policy, self.id
        )

        variables = self._variables(src, payload.get("variables") or {})
        dst = self._render_target(ctx, src, variables, template=template)
        check_scope(ctx, dst, write=True)

        if dst == src:
            return src, dst, ConflictDecision(
                action="skip", final_dst=dst, reason="no_change"
            )

        decision = resolve_conflict_policy(conflict_policy, dst)
        if decision.action == "proceed":
            check_scope(ctx, decision.final_dst, write=True)
        return src, decision.final_dst, decision

    def _build_log(
        self,
        ctx: Any,
        src: Path,
        final_dst: Path,
        decision: ConflictDecision,
        dry_run: bool,
    ) -> ActionLog:
        """Assemble the :class:`ActionLog` for this step.

        ``status`` and ``rollback_eligible`` are derived from what the
        step actually did:

          * dry_run + proceed/skip → ``"planned"``  (nothing touched)
          * run    + proceed       → ``"applied"``  (mutation executed)
          * run    + skip          → ``"skipped"``  (nothing touched)

        ``rollback_eligible`` is set only for actions that
        :meth:`AuditLog.build_rollback_plan` knows how to reverse
        (``move`` / ``rename``) **and** that were actually applied.
        Both flags are fixed at append time; the audit log is
        append-only, so they cannot be corrected later.
        """
        if dry_run:
            status = "planned"
        elif decision.action == "proceed":
            status = "applied"
        else:  # decision.action == "skip"
            status = "skipped"

        rollback_eligible = (
            decision.action == "proceed"
            and self.action_type in _ROLLBACKABLE_ACTIONS
        )

        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type=self.action_type,
            source=str(src),
            target=str(final_dst),
            reason=decision.reason or "no_conflict",
            confidence=1.0,
            risk=risk_for_decision(decision),
            dry_run=dry_run,
            status=status,
            rollback_eligible=rollback_eligible,
        )

    # ── mutation hook ────────────────────────────────────────────
    def _execute(self, src: Path, dst: Path) -> None:
        """Perform the mutation. The only place a ``safe_fs`` write happens."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _execute()"
        )

    # ── Node ABC ─────────────────────────────────────────────────
    def _run_template(self, ctx: Any, payload: Any) -> ActionResult:
        src, final_dst, decision = self._plan(ctx, payload)
        if decision.action == "proceed":
            self._execute(src, final_dst)
        log = self._build_log(
            ctx, src, final_dst, decision, dry_run=False
        )
        return ActionResult(log, file=str(final_dst))

    def _dry_run_template(self, ctx: Any, payload: Any) -> ActionResult:
        src, final_dst, decision = self._plan(ctx, payload)
        log = self._build_log(
            ctx, src, final_dst, decision, dry_run=True
        )
        return ActionResult(log, file=str(final_dst))
