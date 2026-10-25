"""Move File(s) — template-driven move, conflict policy.

Node id: ``fs.move``. All cross-volume logic lives in
``safe_fs.safe_move`` (EXDEV → copy → fsync → replace). This node
never performs its own copy/fsync/replace.

Invariant 9: the only mutating call is ``safe_fs.safe_move``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._common import (
    ConflictDecision,
    build_action_log,
    check_scope,
    render_and_check,
    resolve_conflict_policy,
    risk_for_decision,
)


@register_node
class MoveNode(Node):
    """Move File(s) — template paths, conflict policy, cross-volume in safe_fs."""

    id = "fs.move"
    title = "Move File(s)"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        template: str = "{name}{ext}",
        conflict_policy: str = "skip",
        cross_volume: bool = True,
    ) -> None:
        self.template = template
        self.conflict_policy = conflict_policy
        # Reserved: safe_fs.safe_move already handles EXDEV transparently.
        # This flag is surfaced to describe() and will be wired through
        # once safe_fs exposes per-call control (phase 5c+).
        self.cross_volume = cross_volume

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "params": {
                "template": {
                    "type": "string",
                    "default": self.template,
                    "description": (
                        "Destination template with {name}, {ext}, "
                        "{date}, {author}, {tags}."
                    ),
                },
                "conflict_policy": {
                    "type": "enum",
                    "values": ["skip", "rename", "overwrite", "ask"],
                    "default": self.conflict_policy,
                },
                "cross_volume": {
                    "type": "bool",
                    "default": self.cross_volume,
                    "description": (
                        "Reserved: cross-volume copy+fsync+replace "
                        "handling lives in safe_fs.safe_move."
                    ),
                },
            },
        }

    # ── internal ────────────────────────────────────────────────────
    def _variables(self, src: Path, extra: dict[str, Any]) -> dict[str, Any]:
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

    def _render_target(
        self, ctx, src: Path, variables: dict[str, Any]
    ) -> Path:
        rendered = render_and_check(ctx, self.template, variables)
        if not rendered.suffix and src.suffix:
            rendered = rendered.with_suffix(src.suffix)
        return rendered

    def _plan(
        self, ctx, payload: dict[str, Any]
    ) -> tuple[Path, Path, ConflictDecision]:
        src = Path(payload["source"])
        check_scope(ctx, str(src))

        variables = self._variables(src, payload.get("variables") or {})
        dst = self._render_target(ctx, src, variables)
        check_scope(ctx, str(dst))

        decision = resolve_conflict_policy(self.conflict_policy, dst)
        if decision.action == "proceed":
            check_scope(ctx, str(decision.final_dst))
        return src, decision.final_dst, decision

    def _build_log(
        self, ctx, src: Path, final_dst: Path,
        decision: ConflictDecision, dry_run: bool,
    ):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="move",
            source=str(src),
            target=str(final_dst),
            reason=decision.reason or "no_conflict",
            confidence=1.0,
            risk=risk_for_decision(decision),
            dry_run=dry_run,
        )

    # ── Node ABC ────────────────────────────────────────────────────
    def run(self, ctx, payload):
        src, final_dst, decision = self._plan(ctx, payload)
        if decision.action == "skip":
            log = self._build_log(
                ctx, src, final_dst, decision, dry_run=False
            )
            ctx.audit.append(log)
            return log

        safe_fs.safe_move(src, final_dst)

        log = self._build_log(
            ctx, src, final_dst, decision, dry_run=False
        )
        ctx.audit.append(log)
        return log

    def dry_run(self, ctx, payload):
        src, final_dst, decision = self._plan(ctx, payload)
        return self._build_log(
            ctx, src, final_dst, decision, dry_run=True
        )
