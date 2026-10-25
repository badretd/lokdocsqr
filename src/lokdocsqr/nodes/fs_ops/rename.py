"""Rename File(s) — template-driven filename rewriting.

Node id: ``fs.rename``. Supported template variables:
``{name}``, ``{ext}``, ``{date}``, ``{author}``, ``{tags}``.

Invariant 9: this module performs no mutating filesystem calls.
The only mutation is ``safe_fs.safe_rename`` / ``safe_fs.safe_move``.
Every path handed to ``safe_fs.*`` is first validated by
``ctx.scope.check`` (including after template expansion and after a
``..`` re-check on the rendered result).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.core.paths import render_template

from ._common import (
    ConflictDecision,
    build_action_log,
    check_scope,
    resolve_conflict_policy,
    risk_for_decision,
)


@register_node
class RenameNode(Node):
    """Rename File(s) — rewrite the basename via ``template``."""

    id = "fs.rename"
    title = "Rename File(s)"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        template: str = "{name}{ext}",
        conflict_policy: str = "rename",
    ) -> None:
        self.template = template
        self.conflict_policy = conflict_policy

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "params": {
                "template": {
                    "type": "string",
                    "default": self.template,
                    "description": (
                        "Template with {name}, {ext}, {date}, "
                        "{author}, {tags}."
                    ),
                },
                "conflict_policy": {
                    "type": "enum",
                    "values": ["skip", "rename", "overwrite", "ask"],
                    "default": self.conflict_policy,
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
        self, src: Path, variables: dict[str, Any]
    ) -> Path:
        rendered_str = str(render_template(self.template, variables))
        if not rendered_str.strip():
            raise ValueError(
                f"rename template rendered to empty: "
                f"template={self.template!r}"
            )
        parts = rendered_str.replace("\\", "/").split("/")
        if ".." in parts:
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
        self, ctx, payload: dict[str, Any]
    ) -> tuple[Path, Path, ConflictDecision]:
        src = Path(payload["source"])
        check_scope(ctx, str(src))

        variables = self._variables(src, payload.get("variables") or {})
        dst = self._render_target(src, variables)
        check_scope(ctx, str(dst))

        if dst == src:
            return src, dst, ConflictDecision(
                action="skip", final_dst=dst, reason="no_change"
            )

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
            action_type="rename",
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

        if final_dst.parent == src.parent:
            safe_fs.safe_rename(src, final_dst.name)
        else:
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
