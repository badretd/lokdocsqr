"""List Files / Glob node.

Node id: ``access.list_files``. Read-only: no content is read, including
in ``dry_run``. Every candidate path is scope-checked before stat/iterdir.

Truncation semantics: ``truncated`` is ``True`` only if a *matching* entry
is found after ``limit`` items have already been collected. Collecting
exactly ``limit`` matching entries and then exhausting the tree leaves
``truncated = False``.
"""
from __future__ import annotations

import fnmatch
import stat as stat_module
from pathlib import Path
from typing import Any

from lokdocsqr.core.errors import NodeExecutionError, ScopeViolation
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase


@register_node
class ListFilesNode(AccessBase):
    """List files/directories under a scoped root."""

    id = "access.list_files"
    title = "List Files / Glob"
    inputs = {"path": "str"}
    outputs = {"files": "FileRef[]"}

    def __init__(
        self,
        path: str = "",
        pattern: str = "*",
        recursive: bool = False,
        extensions: list[str] | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        include_dirs: bool = False,
        follow_symlinks: bool = False,
        limit: int = 10_000,
    ) -> None:
        self.path = path
        self.pattern = pattern
        self.recursive = recursive
        self.extensions = list(extensions) if extensions else None
        self.min_size = min_size
        self.max_size = max_size
        self.include_dirs = include_dirs
        self.follow_symlinks = follow_symlinks
        self.limit = limit

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._collect(ctx, payload)

    @staticmethod
    def _normalise_extensions(extensions: list[str] | None) -> set[str] | None:
        if not extensions:
            return None
        result: set[str] = set()
        for extension in extensions:
            text = str(extension).strip().lower()
            if not text:
                continue
            if not text.startswith("."):
                text = "." + text
            result.add(text)
        return result or None

    @staticmethod
    def _size_ok(
        size: int, min_size: int | None, max_size: int | None
    ) -> bool:
        if min_size is not None and size < min_size:
            return False
        if max_size is not None and size > max_size:
            return False
        return True

    def _collect(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        raw_path = payload.get("path", self.path)
        if not raw_path:
            raise NodeExecutionError("list_files: 'path' is required")

        pattern = str(payload.get("pattern", self.pattern))
        recursive = bool(payload.get("recursive", self.recursive))
        extensions = self._normalise_extensions(
            payload.get("extensions", self.extensions)
        )
        min_size = payload.get("min_size", self.min_size)
        max_size = payload.get("max_size", self.max_size)
        include_dirs = bool(payload.get("include_dirs", self.include_dirs))
        follow_symlinks = bool(
            payload.get("follow_symlinks", self.follow_symlinks)
        )
        limit = int(payload.get("limit", self.limit))
        if limit <= 0:
            raise NodeExecutionError("list_files: 'limit' must be positive")

        roots = self._resolve_input(ctx, raw_path)
        files: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        stopped = False
        visited_dirs: set[Path] = set()

        for root in roots:
            if stopped:
                break
            try:
                if not root.is_dir():
                    raise NodeExecutionError(
                        f"list_files: root is not a directory: {root}"
                    )
            except OSError as exc:
                raise NodeExecutionError(
                    f"list_files: cannot inspect root {root}: {exc}"
                ) from exc

            stack: list[Path] = [root]
            while stack and not stopped:
                current = stack.pop()
                try:
                    current_key = current.resolve(strict=False)
                except OSError:
                    current_key = current
                if current_key in visited_dirs:
                    continue
                visited_dirs.add(current_key)

                try:
                    entries = sorted(
                        current.iterdir(), key=lambda p: p.name.lower()
                    )
                except OSError:
                    continue

                for entry in entries:
                    try:
                        checked = ctx.scope.check(entry, write=False)
                    except ScopeViolation:
                        continue

                    try:
                        is_symlink = entry.is_symlink()
                    except OSError:
                        is_symlink = False
                    if is_symlink and not follow_symlinks:
                        continue

                    try:
                        st = checked.stat()
                    except OSError:
                        continue

                    scanned += 1
                    is_dir = stat_module.S_ISDIR(st.st_mode)
                    name = entry.name

                    matches = False
                    if is_dir:
                        if include_dirs and fnmatch.fnmatch(name, pattern):
                            if self._size_ok(st.st_size, min_size, max_size):
                                matches = True
                        if recursive:
                            stack.append(checked)
                    else:
                        if fnmatch.fnmatch(name, pattern):
                            if extensions is None or any(
                                name.lower().endswith(ext)
                                for ext in extensions
                            ):
                                if self._size_ok(
                                    st.st_size, min_size, max_size
                                ):
                                    matches = True

                    if not matches:
                        continue

                    if len(files) >= limit:
                        truncated = True
                        stopped = True
                        break

                    files.append(
                        {
                            "path": str(checked),
                            "size": st.st_size,
                            "is_dir": is_dir,
                        }
                    )

        return {
            "files": files,
            "truncated": truncated,
            "scanned": scanned,
        }