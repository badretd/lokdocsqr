"""Extract Text node.

Node id: ``access.extract_text``. Extracts text/CSV from supported
formats using only the standard library.

pip deps: none (stdlib only)

6c (OCR) and 6d (pdf/docx/eml) register their handlers through
register_handler(); dispatch logic in 6b does not change.

R3 (HTML): only stdlib html.parser; no lxml/bs4/html5lib; no network.
Script/style blocks are skipped, entities are decoded with html.unescape,
and whitespace is collapsed unless html_keep_whitespace=True.

R4 (CSV): (a) csv_delimiter=None uses csv.Sniffer(); csv.Error falls
back to ","; (b) encoding defaults to "utf-8-sig"; (c) csv.Error in
Sniffer is not fatal, csv.Error in reader is a per-item error.

R6 (truncation): the max_bytes / max_bytes+1 boundary mirrors
read_content 6a. A read of max_bytes+1 bytes distinguishes a complete
read from a truncated one.

``dry_run`` stats only; it never calls safe_fs.read_* and never invokes a
handler. Unknown extensions under format="auto" are reported as per-item
errors in both run and dry_run.
"""
from __future__ import annotations

import csv
import html as html_module
import io
import re
import stat as stat_module
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase, AccessFormatError

Handler = Callable[[bytes, dict[str, Any]], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}


def register_handler(format_name: str, handler: Handler) -> None:
    """Register a canonical format handler for Extract Text.

    Public module-level extension point: 6c (OCR) and 6d
    (pdf/docx/eml) register their handlers here; dispatch logic in 6b
    does not change.
    """
    if not isinstance(format_name, str) or not format_name.strip():
        raise AccessFormatError("format_name must be a non-empty string")
    if not callable(handler):
        raise AccessFormatError("handler must be callable")
    HANDLERS[format_name.strip().lower()] = handler


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self.parts.append(f"&#{name};")


def _handle_txt(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    return {"text": data.decode("utf-8", errors="replace"), "csv": None}


def _handle_html(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    keep_whitespace = bool(params.get("html_keep_whitespace", False))
    source = data.decode("utf-8", errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(source)
    parser.close()
    text = "".join(parser.parts)
    text = html_module.unescape(text)
    if not keep_whitespace:
        text = re.sub(r"\s+", " ", text).strip()
    return {"text": text, "csv": None}


def _handle_csv(data: bytes, params: dict[str, Any]) -> dict[str, Any]:
    encoding = str(params.get("csv_encoding", "utf-8-sig"))
    source = data.decode(encoding, errors="replace")
    delimiter = params.get("csv_delimiter")
    if delimiter is None:
        try:
            dialect = csv.Sniffer().sniff(source[:4096])
            delimiter = dialect.delimiter
        except csv.Error:
            delimiter = ","
    else:
        delimiter = str(delimiter)
        if len(delimiter) != 1:
            raise AccessFormatError(
                f"csv_delimiter must be a single character, got {delimiter!r}"
            )
    reader = csv.reader(io.StringIO(source), delimiter=delimiter)
    return {"text": None, "csv": list(reader)}


register_handler("txt", _handle_txt)
register_handler("html", _handle_html)
register_handler("csv", _handle_csv)


@register_node
class ExtractTextNode(AccessBase):
    """Extract text/CSV from txt/html/csv; 6c/6d extend via register_handler()."""

    id = "access.extract_text"
    title = "Extract Text"
    inputs = {"paths": "str|list[str]"}
    outputs = {"items": "Text[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        format: str = "auto",
        max_bytes: int = 1 * 1024 * 1024,
        csv_delimiter: str | None = None,
        csv_encoding: str = "utf-8-sig",
        html_keep_whitespace: bool = False,
    ) -> None:
        self.paths = paths
        self.format = format
        self.max_bytes = max_bytes
        self.csv_delimiter = csv_delimiter
        self.csv_encoding = csv_encoding
        self.html_keep_whitespace = html_keep_whitespace

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=False)

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=True)

    @staticmethod
    def _raw_paths(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        raise NodeExecutionError(
            "extract_text: 'paths' must be str, Path, or list of those"
        )

    @staticmethod
    def _format_from_path(path: Path) -> str | None:
        suffix = path.suffix.lower().lstrip(".")
        if suffix == "htm":
            return "html"
        if suffix in ("txt", "html", "csv"):
            return suffix
        return None

    @staticmethod
    def _error_item(
        path: Any, exc: BaseException, fmt: str | None = None
    ) -> dict[str, Any]:
        return {
            "path": str(path),
            "format": fmt,
            "text": None,
            "csv": None,
            "bytes": None,
            "truncated": None,
            "error": str(exc),
        }

    @staticmethod
    def _item_skeleton(path: Path, fmt: str) -> dict[str, Any]:
        return {
            "path": str(path),
            "format": fmt,
            "text": None,
            "csv": None,
            "bytes": None,
            "truncated": None,
        }

    def _preview_item(
        self, path: Path, fmt: str, max_bytes: int
    ) -> dict[str, Any]:
        item = self._item_skeleton(path, fmt)
        try:
            st = safe_fs.stat(path)
        except LokError as exc:
            item["error"] = str(exc)
            return item
        if stat_module.S_ISDIR(st.st_mode):
            item["error"] = f"{path}: is a directory"
            return item
        item["bytes"] = st.st_size
        item["truncated"] = st.st_size > max_bytes
        return item

    def _run_item(
        self,
        path: Path,
        fmt: str,
        max_bytes: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        item = self._item_skeleton(path, fmt)
        try:
            st = safe_fs.stat(path)
        except LokError as exc:
            item["error"] = str(exc)
            return item
        if stat_module.S_ISDIR(st.st_mode):
            item["error"] = f"{path}: is a directory"
            return item

        try:
            data = safe_fs.read_bytes(path, limit=max_bytes + 1)
        except LokError as exc:
            item["error"] = str(exc)
            return item

        if len(data) > max_bytes:
            truncated = True
            data = data[:max_bytes]
        elif len(data) == max_bytes:
            truncated = st.st_size < max_bytes
        else:
            truncated = False

        item["bytes"] = len(data)
        item["truncated"] = truncated

        handler = HANDLERS.get(fmt)
        if handler is None:
            item["error"] = f"no handler registered for format {fmt!r}"
            return item
        try:
            extracted = handler(data, params)
        except Exception as exc:  # noqa: BLE001 - per-item error boundary
            item["error"] = str(exc)
            return item
        if "text" in extracted:
            item["text"] = extracted.get("text")
        if "csv" in extracted:
            item["csv"] = extracted.get("csv")
        return item

    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        format_name = str(payload.get("format", self.format)).strip().lower()
        try:
            max_bytes = int(payload.get("max_bytes", self.max_bytes))
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError(
                "extract_text: max_bytes must be an integer"
            ) from exc
        if max_bytes < 0:
            raise NodeExecutionError(
                "extract_text: max_bytes must be non-negative"
            )
        params = {
            "csv_delimiter": payload.get(
                "csv_delimiter", self.csv_delimiter
            ),
            "csv_encoding": str(
                payload.get("csv_encoding", self.csv_encoding)
            ),
            "html_keep_whitespace": bool(
                payload.get(
                    "html_keep_whitespace", self.html_keep_whitespace
                )
            ),
        }

        items: list[dict[str, Any]] = []
        for raw in self._raw_paths(payload.get("paths", self.paths)):
            try:
                resolved_paths = self._resolve_input(ctx, raw)
            except LokError as exc:
                items.append(self._error_item(raw, exc))
                continue
            for path in resolved_paths:
                if format_name == "auto":
                    fmt = self._format_from_path(path)
                    if fmt is None:
                        items.append(
                            self._error_item(
                                path,
                                AccessFormatError(
                                    "cannot infer format from extension "
                                    f"{path.suffix!r}"
                                ),
                            )
                        )
                        continue
                else:
                    fmt = format_name
                if fmt not in HANDLERS:
                    items.append(
                        self._error_item(
                            path,
                            AccessFormatError(
                                f"no handler registered for format {fmt!r}"
                            ),
                            fmt,
                        )
                    )
                    continue
                if dry_run:
                    items.append(self._preview_item(path, fmt, max_bytes))
                else:
                    items.append(
                        self._run_item(path, fmt, max_bytes, params)
                    )

        if not items:
            raise NodeExecutionError("extract_text: no paths provided")
        return {"items": items}


__all__ = ["ExtractTextNode", "HANDLERS", "register_handler"]
