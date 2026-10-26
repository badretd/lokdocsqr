"""Archive / Extract — zip / 7z with zip-slip protection.

Node id: ``fs.archive``. Two operations:

* ``create``  — pack ``sources`` into a single archive at ``target``.
  Archive bytes are assembled entirely in memory (``io.BytesIO``) and
  committed with a single ``safe_fs.atomic_write`` call.

* ``extract`` — unpack ``source`` into ``target_dir``. Every archive
  entry is validated *before any write*: absolute paths, ``..``
  segments, or paths that resolve outside ``target_dir`` raise
  ``ScopeViolation`` and nothing is extracted. Each extracted file is
  written via ``safe_fs.atomic_write``; directories via
  ``safe_fs.make_dir``.

Invariant 9 (mutation ban): the only mutating calls are
``safe_fs.atomic_write`` and ``safe_fs.make_dir``. ``zipfile.ZipFile``
is only opened in ``"r"`` mode against a filesystem path; archives for
writing are built in memory. ``zipfile.ZipFile.extract`` /
``extractall`` are never called.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import (
    NodeExecutionError,
    SafeFsError,
    ScopeViolation,
)
from lokdocsqr.core.models import Risk
from lokdocsqr.core.node_api.decorators import register_node

from ._common import (
    build_action_log,
    check_scope,
    render_and_check,
    resolve_conflict_policy,
    risk_for_decision,
)
from ._base import FsNode


SUPPORTED_FORMATS = ("zip", "7z")


# ── zip-slip protection ─────────────────────────────────────────────

def _entry_is_safe(name: str) -> bool:
    """Return True if ``name`` is a safe *relative* archive entry path.

    Rejects absolute paths (leading ``/`` or ``\\``), Windows
    drive-letter paths (``C:\\foo``), and any entry whose path
    components contain ``..``.
    """
    if not name:
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    if len(name) >= 2 and name[1] == ":":
        return False
    parts = name.replace("\\", "/").split("/")
    if ".." in parts:
        return False
    return True


def _resolve_entry_target(target_dir: Path, entry_name: str) -> Path:
    """Map ``entry_name`` to its final on-disk path under ``target_dir``.

    Raises ``ScopeViolation`` (with the offending entry name) for
    absolute paths, ``..`` segments, or anything that resolves outside
    ``target_dir``. This is the zip-slip gate; it runs for **every**
    entry before **any** extraction begins.
    """
    if not _entry_is_safe(entry_name):
        raise ScopeViolation(
            f"unsafe archive entry {entry_name!r}: "
            "absolute path or '..' segment"
        )
    base = target_dir.resolve()
    final = (target_dir / entry_name).resolve()
    if not final.is_relative_to(base):
        raise ScopeViolation(
            f"archive entry escapes target_dir: {entry_name!r} -> {final}"
        )
    return final


# ── in-memory archive builders (create) ─────────────────────────────

def _build_zip_bytes(sources: list[Path]) -> bytes:
    """Assemble a zip archive in memory from ``sources``.

    Note: the ``"w"`` mode below targets an *in-memory* ``BytesIO``,
    never a filesystem path. The final bytes are committed through
    ``safe_fs.atomic_write`` by the caller.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sources:
            zf.write(src, arcname=src.name)
    return buf.getvalue()


def _build_7z_bytes(sources: list[Path]) -> bytes:
    """Assemble a 7z archive in memory (requires the ``py7zr`` extra).

    ``_plan_create`` guarantees ``py7zr`` is importable before this
    function is reached; the import here is therefore plain.
    """
    import py7zr  # noqa: F401  (guarded by _plan_create)

    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as zf:
        for src in sources:
            zf.write(src, arcname=src.name)
    return buf.getvalue()


# ── node ────────────────────────────────────────────────────────────

@register_node
class ArchiveNode(FsNode):
    """Archive / Extract — zip / 7z with zip-slip protection."""

    id = "fs.archive"
    title = "Archive / Extract"
    inputs = ("file",)
    outputs = ("file",)

    def __init__(
        self,
        operation: str = "create",
        sources: tuple[str, ...] = (),
        target: str = "",
        source: str = "",
        target_dir: str = "",
        format: str = "zip",
        overwrite: bool = False,
        conflict_policy: str = "skip",
    ) -> None:
        self.operation = operation
        self.sources = sources
        self.target = target
        self.source = source
        self.target_dir = target_dir
        self.format = format
        self.overwrite = overwrite
        self.conflict_policy = conflict_policy

    def _describe_payload(self) -> dict[str, Any]:
        return {
            "params": {
                "operation": {
                    "type": "enum",
                    "values": ["create", "extract"],
                    "default": self.operation,
                },
                "sources": {
                    "type": "list[str]",
                    "default": list(self.sources),
                    "description": "create: files to pack.",
                },
                "target": {
                    "type": "string",
                    "default": self.target,
                    "description": "create: destination archive path.",
                },
                "source": {
                    "type": "string",
                    "default": self.source,
                    "description": "extract: source archive path.",
                },
                "target_dir": {
                    "type": "string",
                    "default": self.target_dir,
                    "description": "extract: destination directory.",
                },
                "format": {
                    "type": "enum",
                    "values": list(SUPPORTED_FORMATS),
                    "default": self.format,
                    "description": "create only.",
                },
                "overwrite": {
                    "type": "bool",
                    "default": self.overwrite,
                    "description": (
                        "create: refuse to overwrite an existing target "
                        "archive when False."
                    ),
                },
                "conflict_policy": {
                    "type": "enum",
                    "values": ["skip", "rename", "overwrite", "ask"],
                    "default": self.conflict_policy,
                    "description": "extract only.",
                },
            },
        }

    # ── create ────────────────────────────────────────────────────

    def _plan_create(self, ctx, payload):
        sources_raw = payload.get("sources")
        if sources_raw is None:
            sources_raw = list(self.sources)
        if not sources_raw:
            raise NodeExecutionError("archive.create: 'sources' is empty")

        target_raw = payload.get("target") or self.target
        if not target_raw:
            raise NodeExecutionError("archive.create: 'target' is required")

        fmt = str(payload.get("format", self.format)).lower()
        if fmt not in SUPPORTED_FORMATS:
            raise NodeExecutionError(
                f"unsupported archive format: {fmt!r}; "
                f"expected one of {SUPPORTED_FORMATS}"
            )

        # Fail fast in BOTH run() and dry_run() if 7z backend is missing.
        if fmt == "7z":
            try:
                import py7zr  # noqa: F401
            except ImportError as exc:
                raise NodeExecutionError(
                    "7z_backend_unavailable: "
                    "install hint: pip install py7zr"
                ) from exc

        target = render_and_check(
            ctx, target_raw, payload.get("variables") or {}
        )
        overwrite = bool(payload.get("overwrite", self.overwrite))
        if target.exists() and not overwrite:
            raise SafeFsError(
                f"target_exists: {target} "
                "(pass overwrite=True to replace)"
            )

        sources: list[Path] = []
        for raw in sources_raw:
            src = Path(raw)
            check_scope(ctx, str(src))
            if not src.exists():
                raise SafeFsError(f"source missing: {src}")
            sources.append(src)

        return sources, target, fmt

    def _create_log(self, ctx, sources, target, fmt, dry_run: bool):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="archive_create",
            source=",".join(str(s) for s in sources),
            target=str(target),
            reason=f"archive_create:{fmt}",
            confidence=1.0,
            risk=Risk.LOW,
            dry_run=dry_run,
        )

    def _run_create(self, ctx, payload):
        sources, target, fmt = self._plan_create(ctx, payload)
        data = (
            _build_zip_bytes(sources)
            if fmt == "zip"
            else _build_7z_bytes(sources)
        )
        safe_fs.atomic_write(target, data)
        log = self._create_log(ctx, sources, target, fmt, dry_run=False)
        ctx.audit.append(log)
        return log

    def _dry_create(self, ctx, payload):
        sources, target, fmt = self._plan_create(ctx, payload)
        return self._create_log(ctx, sources, target, fmt, dry_run=True)

    # ── extract ───────────────────────────────────────────────────

    def _plan_extract(self, ctx, payload):
        source_raw = payload.get("source") or self.source
        if not source_raw:
            raise NodeExecutionError("archive.extract: 'source' is required")

        target_dir_raw = payload.get("target_dir") or self.target_dir
        if not target_dir_raw:
            raise NodeExecutionError(
                "archive.extract: 'target_dir' is required"
            )

        source = Path(source_raw)
        check_scope(ctx, str(source))
        if not source.exists():
            raise SafeFsError(f"source missing: {source}")

        target_dir = render_and_check(
            ctx, target_dir_raw, payload.get("variables") or {}
        )
        check_scope(ctx, str(target_dir))

        policy = str(payload.get("conflict_policy", self.conflict_policy))
        return source, target_dir, policy

    def _extract_log(self, ctx, source, decision, dry_run: bool):
        return build_action_log(
            run_id=ctx.run_id,
            automation_id=ctx.automation_id,
            node_id=self.id,
            action_type="archive_extract",
            source=str(source),
            target=str(decision.final_dst),
            reason=decision.reason or "extracted",
            confidence=1.0,
            risk=risk_for_decision(decision),
            dry_run=dry_run,
        )

    def _run_extract(self, ctx, payload):
        source, target_dir, policy = self._plan_extract(ctx, payload)

        # Phase 1: validate every entry BEFORE any writes (zip-slip gate).
        with zipfile.ZipFile(source, "r") as zf:
            entries = []
            for info in zf.infolist():
                final = _resolve_entry_target(target_dir, info.filename)
                check_scope(ctx, str(final))
                entries.append((info, final))

            # Phase 2: create target_dir and extract.
            safe_fs.make_dir(target_dir)
            logs = []
            for info, final in entries:
                if info.is_dir() or info.filename.endswith("/"):
                    safe_fs.make_dir(final)
                    continue
                decision = resolve_conflict_policy(policy, final)
                if decision.action == "proceed":
                    check_scope(ctx, str(decision.final_dst))
                if decision.action == "skip":
                    logs.append(
                        self._extract_log(
                            ctx, source, decision, dry_run=False
                        )
                    )
                    continue
                data = zf.read(info)
                safe_fs.atomic_write(decision.final_dst, data)
                logs.append(
                    self._extract_log(
                        ctx, source, decision, dry_run=False
                    )
                )

        for log in logs:
            ctx.audit.append(log)
        return logs

    def _dry_extract(self, ctx, payload):
        source, target_dir, policy = self._plan_extract(ctx, payload)
        logs = []
        # Read-only pass: enumerate entries, plan targets, do not write.
        with zipfile.ZipFile(source, "r") as zf:
            for info in zf.infolist():
                final = _resolve_entry_target(target_dir, info.filename)
                check_scope(ctx, str(final))
                if info.is_dir() or info.filename.endswith("/"):
                    continue
                decision = resolve_conflict_policy(policy, final)
                logs.append(
                    self._extract_log(ctx, source, decision, dry_run=True)
                )
        return logs

    # ── Node ABC ─────────────────────────────────────────────────
    def run(self, ctx, payload):
        op = str(payload.get("operation", self.operation))
        if op == "create":
            return self._run_create(ctx, payload)
        if op == "extract":
            return self._run_extract(ctx, payload)
        raise NodeExecutionError(f"archive: unknown operation {op!r}")

    def dry_run(self, ctx, payload):
        op = str(payload.get("operation", self.operation))
        if op == "create":
            return self._dry_create(ctx, payload)
        if op == "extract":
            return self._dry_extract(ctx, payload)
        raise NodeExecutionError(f"archive: unknown operation {op!r}")