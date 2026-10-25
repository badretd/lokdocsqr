"""Convert File — MIME-to-MIME converter registry.

Node id: ``fs.convert``. Phase 5d converters:

    text/csv         -> application/json
    application/json -> text/csv

Unimplemented pairs (pdf, docx, eml, ...) raise
``NodeExecutionError("converter_not_available: ...")`` with an install
hint — deliberately loud, so a misconfigured workflow cannot silently
produce empty output.

Invariant 9: reads via ``safe_fs.read_bytes``, writes via
``safe_fs.atomic_write``. No direct ``open(..., "w"|"a"|"x")``.
"""
from __future__ import annotations

import csv
import io
import json
import mimetypes
from pathlib import Path
from typing import Any, Callable

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError, SafeFsError
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

from ._common import (
    build_action_log,
    check_scope,
    render_and_check,
)


# ── converter implementations ───────────────────────────────────────

def _csv_to_json(data: bytes) -> bytes:
    """CSV (DictReader) -> JSON array of objects."""
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = [dict(row) for row in reader]
    return json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")


def _json_to_csv(data: bytes) -> bytes:
    """JSON array of objects -> CSV (DictWriter)."""
    obj = json.loads(data.decode("utf-8"))
    if not isinstance(obj, list):
        raise NodeExecutionError(
            "json_to_csv: top-level JSON value must be an array of objects"
        )
    if not obj:
        return b""
    fieldnames: list[str] = []
    seen: set[str] = set()
    for i, row in enumerate(obj):
        if not isinstance(row, dict):
            raise NodeExecutionError(
                f"json_to_csv: row {i} is not an object "
                f"(got {type(row).__name__})"
            )
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in obj:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return buf.getvalue().encode("utf-8")


CONVERTERS: dict[tuple[str, str], Callable[[bytes], bytes]] = {
    ("text/csv", "application/json"): _csv_to_json,
    ("application/json", "text/csv"): _json_to_csv,
}


# ── MIME helpers ────────────────────────────────────────────────────

def _guess_mime(path: Path) -> str:
    """Guess MIME from a file name (read-only; no filesystem I/O).

    Falls back to a small built-in map for platforms whose
    ``mimetypes`` database lacks an entry.
    """
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    ext = path.suffix.lower()
    return {
        ".csv": "text/csv",
        ".json": "application/json",
        ".txt": "text/plain",
    }.get(ext, "application/octet-stream")


# ── node ────────────────────────────────────────────────────────────

@register_node
class ConvertNode(Node):
    """Convert File — registry of (from_mime, to_mime) -> bytes converters."""

    id = "fs.convert"
    title = "Convert File"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        source: str = "",
        target: str = "",
        from_mime: str | None = None,
        to_mime: str | None = None,
        overwrite: bool = False,
    ) -> None:
        self.source = source
        self.target = target
        self.from_mime = from_mime
        self.to_mime = to_mime
        self.overwrite = overwrite

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "params": {
                "source": {
                    "type": "string",
                    "default": self.source,
                    "description": "Path to the source file.",
                },
                "target": {
                    "type": "string",
                    "default": self.target,
                    "description": "Path to the converted file.",
                },
                "from_mime": {
                    "type": "string",
                    "default": self.from_mime,
                    "description": (
                        "Source MIME; if None, guessed from the source "
                        "extension via mimetypes.guess_type."
                    ),
                },
                "to_mime": {
                    "type": "string",
                    "default": self.to_mime,
                    "description": (
                        "Destination MIME; if None, guessed from the "
                        "target extension."
                    ),
                },
                "overwrite": {
                    "type": "bool",
                    "default": self.overwrite,
                    "description": (
                        "If False and target exists, raise SafeFsError."
                    ),
                },
                "supported": {
                    "type": "list[str]",
                    "value": sorted(
                        f"{a} -> {b}" for a, b in CONVERTERS
                    ),
                },
            },
        }

    def _plan(self, ctx, payload):
        source_raw = payload.get("source") or self.source
        target_raw = payload.get("target") or self.target
        if not source_raw:
            raise NodeExecutionError("fs.convert: 'source' is required")
        if not target_raw:
            raise NodeExecutionError("fs.convert: 'target' is required")

        source = Path(source_raw)
        check_scope(ctx, str(source))
        if not source.exists():
            raise SafeFsError(f"source missing: {source}")

        target = render_and_check(
            ctx, target_raw, payload.get("variables") or {}
        )
        check_scope(ctx, str(target))

        from_mime = (
            payload.get("from_mime")
            or self.from_mime
            or _guess_mime(source)
        )
        to_mime = (
            payload.get("to_mime")
            or self.to_mime
            or _guess_mime(target)
        )

        converter = CONVERTERS.get((from_mime, to_mime))
        if converter is None:
            raise NodeExecutionError(
                f"converter_not_available: {from_mime} -> {to_mime}, "
                f"hint: pip install lokdocsqr[convert]"
            )

        overwrite = bool(payload.get("overwrite", self.overwrite))
        if target.exists() and not overwrite:
            raise SafeFsError(
                f"target_exists: {target} "
                "(pass overwrite=True to replace)"
            )

        return source, target, from_mime, to_mime, converter

    def _log(self, ctx, source, target, from_mime, to_mime, dry_run: bool):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="convert",
            source=str(source),
            target=str(target),
            reason=f"convert:{from_mime}->{to_mime}",
            confidence=1.0,
            risk=Risk.LOW,
            dry_run=dry_run,
        )

    def run(self, ctx, payload):
        source, target, from_mime, to_mime, converter = self._plan(
            ctx, payload
        )
        data = safe_fs.read_bytes(source)
        converted = converter(data)
        safe_fs.atomic_write(target, converted)
        log = self._log(
            ctx, source, target, from_mime, to_mime, dry_run=False
        )
        ctx.audit.append(log)
        return log

    def dry_run(self, ctx, payload):
        source, target, from_mime, to_mime, _converter = self._plan(
            ctx, payload
        )
        # Read-only probe (spec: dry_run reads source, does not write).
        safe_fs.read_bytes(source)
        return self._log(
            ctx, source, target, from_mime, to_mime, dry_run=True
        )
