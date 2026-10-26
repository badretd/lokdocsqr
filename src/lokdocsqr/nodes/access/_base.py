"""Shared base for read-only access nodes.

``AccessBase`` is internal to ``nodes/access`` and is deliberately not
registered. It centralises scope-first path resolution, bounded reads,
binary sniffing and dry-run preview formatting, and it provides the
``_run_template`` / ``_dry_run_template`` pipeline that concrete nodes
wrap with thin ``run`` / ``dry_run`` bodies.

Scope-first rule: every input path and every discovered path is passed
through ``ctx.scope.check(..., write=False)`` before ``stat``/``read``.

Pipeline shape (mirrors ``fs_ops.FsTemplateNode``):

    _run_template      → _execute
    _dry_run_template  → _preview  (defaults to _execute)

Because ``access.*`` nodes are read-only, no audit log is emitted and
no rollback bookkeeping is required.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.base import Node

__all__ = ["AccessBase", "AccessLimitError", "AccessBinaryError", "AccessFormatError"]


class AccessLimitError(LokError):
    """Raised when a read would exceed the node's configured byte limit."""


class AccessBinaryError(LokError):
    """Raised when binary content is encountered under ``binary_policy='error'``."""

class AccessFormatError(LokError):
    """Raised when a format, algorithm, or handler is unsupported."""


class AccessBase(Node):
    """Common scope-first helpers for ``access.*`` nodes.

    This class is intentionally not decorated with ``@register_node``.
    Concrete subclasses must declare both ``run`` and ``dry_run`` in
    their own class bodies; the recommended shape is a pair of thin
    wrappers around :meth:`_run_template` / :meth:`_dry_run_template`,
    with the actual read logic placed in :meth:`_execute` and (when it
    differs) :meth:`_preview`.
    """

    BINARY_SNIFF_BYTES = 8 * 1024
    title = "Access"

    # ── template pipeline ────────────────────────────────────────
    def _run_template(self, ctx: Any, payload: Any) -> dict[str, Any]:
        """Public ``run`` entry point; dispatches to :meth:`_execute`."""
        return self._execute(ctx, payload or {})

    def _dry_run_template(self, ctx: Any, payload: Any) -> dict[str, Any]:
        """Public ``dry_run`` entry point; dispatches to :meth:`_preview`."""
        return self._preview(ctx, payload or {})

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Read-only execution body. Must be implemented by subclasses."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement _execute()"
        )

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        """Dry-run body.

        Defaults to :meth:`_execute` because every ``access.*`` node is
        read-only and, for nodes that read nothing (``list_files``),
        preview and execution are indistinguishable. Subclasses whose
        dry-run must skip expensive work (e.g. hashing or content
        reads) override this and delegate to a shared
        ``_process(..., dry_run=True)`` helper.
        """
        return self._execute(ctx, payload)

    # ── shared helpers ───────────────────────────────────────────
    def _resolve_input(self, ctx: Any, value: Any) -> list[Path]:
        """Normalise and scope-check one path or a list of paths."""
        raw_values = value if isinstance(value, (list, tuple)) else [value]
        resolved: list[Path] = []
        for raw in raw_values:
            if not isinstance(raw, (str, Path)):
                raise NodeExecutionError(
                    f"path must be str or Path, got {type(raw).__name__}"
                )
            candidate = Path(raw)
            config = getattr(ctx, "config", None)
            base_dir = (
                getattr(ctx.config, "base_dir", None)
                if getattr(ctx, "config", None) is not None
                else None
            )
            if base_dir is not None and not candidate.is_absolute():
                candidate = Path(base_dir) / candidate
            checked = ctx.scope.check(candidate, write=False)
            resolved.append(Path(checked))
        return resolved

    def _read_bytes_limited(self, path: Path, max_bytes: int) -> bytes:
        """Read at most ``max_bytes``; raise ``AccessLimitError`` if larger."""
        if max_bytes < 0:
            raise AccessLimitError(
                f"max_bytes must be non-negative, got {max_bytes}"
            )
        size = safe_fs.stat(path).st_size
        if size > max_bytes:
            raise AccessLimitError(
                f"{path}: size {size} exceeds max_bytes {max_bytes}"
            )
        data = safe_fs.read_bytes(path, limit=max_bytes)
        return data[:max_bytes]

    @classmethod
    def _is_probably_binary(cls, chunk: bytes) -> bool:
        """Heuristic: NUL byte in the first ``BINARY_SNIFF_BYTES`` bytes."""
        return b"\x00" in chunk[: cls.BINARY_SNIFF_BYTES]

    @staticmethod
    def _emit_preview(items: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the shared dry-run envelope for item-oriented nodes."""
        return {"items": list(items)}