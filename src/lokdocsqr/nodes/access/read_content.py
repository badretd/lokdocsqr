"""Read Content node.

Node id: ``access.read_content``. Reads text/binary content up to
``max_bytes``.

Size-limit behavior (documented contract):

* If the file's size is already larger than ``max_bytes`` at stat time,
  ``AccessLimitError`` is raised for that path and the outer handler
  records it as a per-item ``"error"`` — no content is read.
* Otherwise the reader is asked for ``max_bytes + 1`` bytes so we can tell
  a full read from a truncated one:
    - ``len(data) > max_bytes`` → certain truncation; return the first
      ``max_bytes`` bytes with ``truncated=True``.
    - ``len(data) == max_bytes`` → ambiguous. If the initial stat showed
      ``st_size < max_bytes``, the file grew between stat and read (TOCTOU)
      or the reader capped its output; be conservative and report
      ``truncated=True``. If the initial stat already showed
      ``st_size == max_bytes``, this is a complete read and
      ``truncated=False``.
    - ``len(data) < max_bytes`` → complete read, ``truncated=False``.

Binary policy:

* ``"error"`` — raise ``AccessBinaryError`` for the first binary file.
* ``"skip"``  — return the item with ``binary=True`` and no ``text``.
* ``"base64"`` — return base64 text.

``dry_run`` only stats files and never calls ``safe_fs.read_*``.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase, AccessBinaryError, AccessLimitError


@register_node
class ReadContentNode(AccessBase):
    """Read text or binary content with size and binary-policy controls."""

    id = "access.read_content"
    title = "Read Content"
    inputs = {"paths": "str|list[str]"}
    outputs = {"items": "Content[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        encoding: str = "utf-8",
        errors: str = "strict",
        max_bytes: int = 1 * 1024 * 1024,
        binary_policy: str = "error",
    ) -> None:
        self.paths = paths
        self.encoding = encoding
        self.errors = errors
        self.max_bytes = max_bytes
        self.binary_policy = binary_policy

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
            "read_content: 'paths' must be str, Path, or list of those"
        )

    def _error_item(
        self, raw: Any, exc: BaseException, encoding: str
    ) -> dict[str, Any]:
        return {
            "path": str(raw),
            "encoding": encoding,
            "bytes": None,
            "truncated": None,
            "binary": None,
            "error": str(exc),
        }

    def _preview_item(
        self,
        path: Path,
        encoding: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": str(path),
            "size": None,
            "encoding": encoding,
            "max_bytes": max_bytes,
            "bytes": None,
            "truncated": None,
            "binary": None,
        }
        try:
            st = path.stat()
        except OSError as exc:
            item["error"] = str(exc)
            return item
        item.update(
            {
                "size": st.st_size,
                "bytes": min(st.st_size, max_bytes),
                "truncated": st.st_size > max_bytes,
            }
        )
        return item

    def _read_item(
        self,
        path: Path,
        encoding: str,
        errors: str,
        max_bytes: int,
        binary_policy: str,
    ) -> dict[str, Any]:
        """Read one file with a strict ``max_bytes`` cap and TOCTOU tolerance.

        A file larger than ``max_bytes`` at stat time raises
        ``AccessLimitError``. A file that grows past ``max_bytes`` between
        stat and read is detected by reading ``max_bytes + 1`` bytes:

          * ``len(data) > max_bytes`` — certain truncation, trim to
            ``max_bytes`` and mark ``truncated=True``.
          * ``len(data) == max_bytes`` — ambiguous; if the initial stat
            said ``st_size < max_bytes`` we are in the TOCTOU window and
            mark ``truncated=True``; if it said ``st_size == max_bytes``
            this is a complete read and ``truncated=False``.
        """
        st = safe_fs.stat(path)
        if st.st_size > max_bytes:
            raise AccessLimitError(
                f"{path}: size {st.st_size} exceeds max_bytes {max_bytes}"
            )

        data = safe_fs.read_bytes(path, limit=max_bytes + 1)
        if len(data) > max_bytes:
            truncated = True
            data = data[:max_bytes]
        elif len(data) == max_bytes:
            # We asked for max_bytes + 1 and got exactly max_bytes back.
            # If the initial stat was smaller than max_bytes, either the
            # file grew between stat and read (TOCTOU) or the reader capped
            # its output — be conservative and report truncation. If the
            # initial stat already showed max_bytes, this is a complete
            # read.
            truncated = st.st_size < max_bytes
        else:
            truncated = False

        is_binary = self._is_probably_binary(data)
        item: dict[str, Any] = {
            "path": str(path),
            "encoding": encoding,
            "bytes": len(data),
            "truncated": truncated,
            "binary": is_binary,
        }

        if is_binary:
            if binary_policy == "error":
                raise AccessBinaryError(
                    f"{path}: binary content detected"
                )
            if binary_policy == "skip":
                return item
            item["text"] = base64.b64encode(data).decode("ascii")
            return item

        try:
            item["text"] = data.decode(encoding, errors=errors)
        except UnicodeDecodeError as exc:
            item["error"] = str(exc)
        return item

    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        encoding = str(payload.get("encoding", self.encoding))
        errors = str(payload.get("errors", self.errors))
        max_bytes = int(payload.get("max_bytes", self.max_bytes))
        binary_policy = str(
            payload.get("binary_policy", self.binary_policy)
        )
        if binary_policy not in ("error", "skip", "base64"):
            raise NodeExecutionError(
                "read_content: binary_policy must be one of "
                "'error', 'skip', 'base64'"
            )

        items: list[dict[str, Any]] = []
        for raw in self._raw_paths(payload):
            try:
                resolved = self._resolve_input(ctx, raw)
            except LokError as exc:
                items.append(self._error_item(raw, exc, encoding))
                continue
            for path in resolved:
                if dry_run:
                    items.append(
                        self._preview_item(path, encoding, max_bytes)
                    )
                    continue
                try:
                    items.append(
                        self._read_item(
                            path,
                            encoding,
                            errors,
                            max_bytes,
                            binary_policy,
                        )
                    )
                except AccessBinaryError:
                    raise
                except (AccessLimitError, LokError, OSError) as exc:
                    items.append(self._error_item(path, exc, encoding))

        if not items:
            raise NodeExecutionError("read_content: no paths provided")
        if all("error" in item for item in items):
            raise NodeExecutionError("read_content: all paths failed")
        return {"items": items}