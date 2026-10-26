"""Move File(s) — template-driven move, conflict policy.

Node id: ``fs.move``. All cross-volume logic lives in
``safe_fs.safe_move`` (EXDEV → copy → fsync → replace). This node
never performs its own copy/fsync/replace.

Invariant 9: the only mutating call is ``safe_fs.safe_move``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.node_api.decorators import register_node

from ._base import FsTemplateNode


@register_node
class MoveNode(FsTemplateNode):
    """Move File(s) — template paths, conflict policy, cross-volume in safe_fs."""

    id = "fs.move"
    title = "Move File(s)"
    action_type = "move"
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

    def _describe_payload(self) -> dict[str, Any]:
        return {
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

    def _execute(self, src: Path, dst: Path) -> None:
        safe_fs.safe_move(src, dst)

    def run(self, ctx, payload):
        return self._run_template(ctx, payload)

    def dry_run(self, ctx, payload):
        return self._dry_run_template(ctx, payload)