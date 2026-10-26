"""Rename File(s) — template-driven filename rewriting.

Node id: ``fs.rename``. Supported template variables:
``{name}``, ``{ext}``, ``{date}``, ``{author}``, ``{tags}``.

Invariant 9: this module performs no mutating filesystem calls.
The only mutation is ``safe_fs.safe_rename`` / ``safe_fs.safe_move``.
Every path handed to ``safe_fs.*`` is first validated by
``ctx.scope.check`` (including after template expansion and after a
``..`` re-check on the rendered result — see
:meth:`FsTemplateNode._render_target`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsTemplateNode


@register_node
class RenameNode(FsTemplateNode):
    """Rename File(s) — rewrite the basename via ``template``."""

    id = "fs.rename"
    title = "Rename File(s)"
    action_type = "rename"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        template: str = "{name}{ext}",
        conflict_policy: str = "rename",
    ) -> None:
        self.template = template
        self.conflict_policy = conflict_policy

    def _describe_payload(self) -> dict[str, Any]:
        return {
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

    def _execute(self, src: Path, dst: Path) -> None:
        if dst.parent == src.parent:
            safe_fs.safe_rename(src, dst.name)
        else:
            safe_fs.safe_move(src, dst)

    def run(self, ctx, payload):
        return self._run_template(ctx, payload)

    def dry_run(self, ctx, payload):
        return self._dry_run_template(ctx, payload)