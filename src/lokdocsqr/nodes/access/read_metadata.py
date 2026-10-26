"""Read Metadata node.

Node id: ``access.read_metadata``. Returns size/mtime/mode/is_dir/is_symlink
and optionally a hash. Per-path failures are recorded in the item's
``error`` field; the node only fails overall if every path fails.
``dry_run`` never computes hashes and never calls ``safe_fs.read_*``.
"""
from __future__ import annotations

import stat as stat_module
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase


@register_node
class ReadMetadataNode(AccessBase):
    """Read file/directory metadata, optionally with a hash."""

    id = "access.read_metadata"
    title = "Read Metadata"
    inputs = {"paths": "str|list[str]"}
    outputs = {"items": "Metadata[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        include_hash: bool = False,
        hash_algo: str = "sha256",
        max_bytes_for_hash: int = 50 * 1024 * 1024,
    ) -> None:
        self.paths = paths
        self.include_hash = include_hash
        self.hash_algo = hash_algo
        self.max_bytes_for_hash = max_bytes_for_hash

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=False)

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=True)

    def _raw_paths(self, payload: dict[str, Any]) -> list[Any]:
        value = payload.get("paths", self.paths)
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        raise NodeExecutionError(
            "read_metadata: 'paths' must be str, Path, or list of those"
        )

    def _error_item(
        self, raw: Any, exc: BaseException, include_hash: bool
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": str(raw),
            "size": None,
            "mtime": None,
            "mode": None,
            "is_dir": None,
            "is_symlink": None,
            "error": str(exc),
        }
        if include_hash:
            item["hash"] = None
        return item

    def _metadata_item(
        self,
        path: Path,
        include_hash: bool,
        hash_algo: str,
        max_bytes_for_hash: int,
        dry_run: bool,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": str(path),
            "size": None,
            "mtime": None,
            "mode": None,
            "is_dir": None,
            "is_symlink": None,
        }
        if include_hash:
            item["hash"] = None

        try:
            is_symlink = path.is_symlink()
            st = path.stat()
        except OSError as exc:
            item["error"] = str(exc)
            return item

        is_dir = stat_module.S_ISDIR(st.st_mode)
        item.update(
            {
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mode": st.st_mode,
                "is_dir": is_dir,
                "is_symlink": is_symlink,
            }
        )

        if include_hash and not dry_run and not is_dir:
            if st.st_size > max_bytes_for_hash:
                item["hash_error"] = (
                    f"size {st.st_size} exceeds "
                    f"max_bytes_for_hash {max_bytes_for_hash}"
                )
            else:
                try:
                    item["hash"] = safe_fs.hash_file(path, hash_algo)
                except LokError as exc:
                    item["hash_error"] = str(exc)

        return item

    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        include_hash = bool(payload.get("include_hash", self.include_hash))
        hash_algo = str(payload.get("hash_algo", self.hash_algo))
        max_bytes_for_hash = int(
            payload.get("max_bytes_for_hash", self.max_bytes_for_hash)
        )

        items: list[dict[str, Any]] = []
        for raw in self._raw_paths(payload):
            try:
                resolved = self._resolve_input(ctx, raw)
            except LokError as exc:
                items.append(self._error_item(raw, exc, include_hash))
                continue
            for path in resolved:
                items.append(
                    self._metadata_item(
                        path,
                        include_hash,
                        hash_algo,
                        max_bytes_for_hash,
                        dry_run,
                    )
                )

        if not items:
            raise NodeExecutionError("read_metadata: no paths provided")
        if all("error" in item for item in items):
            raise NodeExecutionError("read_metadata: all paths failed")
        return {"items": items}