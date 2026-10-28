"""Preview primitives for Phase 7b.

Read-only: ``preview_diff`` may call ``safe_fs.read_bytes`` (a read-only
function) but never writes, moves, renames, or deletes. This is the
Phase 7b extension of invariant 6 (R28).

Binary / undecodable content yields ``text_diff = None`` (R34).
Deterministic: same input -> same output (R32).

Iteration 1 (7b-iter-1)
-----------------------
Change kind is derived primarily from :data:`NODE_CHANGE_KIND` (per
node_id). ``classify_node`` in :mod:`core.planning.registry` is not
touched; it feeds the policy engine (7a).

Iteration 2 (7b-iter-2)
-----------------------
Unified diffs for new files (``before is None``) use the Git-standard
``fromfile="/dev/null"`` header. Existing files keep ``a/<path>`` /
``b/<path>``. Literal ``"/dev/null"`` on every platform.

Iteration 3 (7b-iter-3)
-----------------------
B1: ``_unified_diff`` now uses difflib's default ``lineterm="\n"`` with
``"".join(...)``, producing a valid unified diff (newlines between every
header and every content line). The previous ``lineterm=""`` +
``"".join(...)`` combination dropped newlines after headers.

B2: text detection is no longer "UTF-8 decode succeeds". After decoding,
the text is checked for null bytes and control-char density (see
:func:`_text_has_acceptable_controls`). Failing content yields
``text_diff = None`` and ``after_summary = "content unknown"`` in
:func:`preview_diff`; ``change_type`` is unchanged.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .safe_fs import read_bytes as _safe_read_bytes

if TYPE_CHECKING:
    from .models import ActionProposal

__all__ = [
    "ChangeType",
    "DiffEntry",
    "ChangePreview",
    "NODE_CHANGE_KIND",
    "preview_diff",
]


class ChangeType(str, Enum):
    """Kind of change a proposal would produce on the filesystem."""

    CREATE = "create"
    MODIFY = "modify"
    MOVE = "move"
    COPY = "copy"
    MKDIR = "mkdir"
    NOOP = "noop"


#: Per-node change kind. Overrides the coarse operation-level mapping
#: (``classify_node`` returns "write" for move/copy/rename/trash, which
#: would surface as CREATE/MODIFY in previews without this override).
NODE_CHANGE_KIND: dict[str, ChangeType] = {
    "fs.move": ChangeType.MOVE,
    "fs.copy": ChangeType.COPY,
    "fs.rename": ChangeType.MOVE,
    "fs.make_dir": ChangeType.MKDIR,
    "fs.write_file": ChangeType.CREATE,   # runtime upgrade to MODIFY if target exists
    "fs.trash": ChangeType.MOVE,
    "fs.tag": ChangeType.MODIFY,
    "fs.convert": ChangeType.CREATE,
    "fs.archive": ChangeType.CREATE,
}


@dataclass(frozen=True)
class DiffEntry:
    """Path-level diff for a single proposal."""

    old_path: str | None
    new_path: str | None
    change_type: ChangeType
    before_size: int | None
    after_size: int | None


@dataclass(frozen=True)
class ChangePreview:
    """Human-readable preview of a single proposal's effect."""

    path: str
    change_type: ChangeType
    before_summary: str
    after_summary: str
    text_diff: str | None
    before_size: int | None
    after_size: int | None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

#: Control characters that mark content as binary. Excludes ordinary
#: whitespace: tab (0x09), LF (0x0a), CR (0x0d).
_CONTROL_CHARS = frozenset(
    list(range(0x01, 0x09))
    + [0x0b, 0x0c]
    + list(range(0x0e, 0x20))
    + [0x7f]
)

_CONTROL_RATIO_THRESHOLD = 0.05   # 5 %
_CONTROL_RUN_THRESHOLD = 3        # >= 3 consecutive control chars -> binary


def _safe_read(p: Path) -> bytes | None:
    try:
        if not p.exists():
            return None
    except OSError:
        return None
    try:
        return _safe_read_bytes(p)
    except Exception:
        return None


def _decode(data: bytes | None) -> str | None:
    """Decode bytes as UTF-8. ``None`` on failure (or on ``None`` input)."""
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _payload_content(payload: dict[str, Any]) -> bytes | None:
    for key in ("content", "text"):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
    return None


def _text_has_acceptable_controls(text: str) -> bool:
    """True if ``text`` looks like legitimate text content.

    Rejects:
      * any ``\x00`` (canonical binary marker);
      * any run of >= ``_CONTROL_RUN_THRESHOLD`` consecutive control chars;
      * control-char ratio >= ``_CONTROL_RATIO_THRESHOLD``.

    Whitespace (tab / LF / CR) is not treated as a control char, so
    ordinary multi-line text survives.
    """
    if "\x00" in text:
        return False
    if not text:
        return True
    control_count = 0
    run = 0
    for ch in text:
        if ord(ch) in _CONTROL_CHARS:
            control_count += 1
            run += 1
            if run >= _CONTROL_RUN_THRESHOLD:
                return False
        else:
            run = 0
    return control_count / len(text) < _CONTROL_RATIO_THRESHOLD


def _is_text_bytes(data: bytes) -> bool:
    """True if ``data`` decodes as UTF-8 AND looks like text."""
    text = _decode(data)
    if text is None:
        return False
    return _text_has_acceptable_controls(text)


def _unified_diff(
    before: bytes | None, after: bytes | None, path: str
) -> str | None:
    """Return a unified diff, or ``None`` if either side is not text.

    B1 fix: difflib's default ``lineterm="\n"`` combined with
    ``"".join(...)``. This is the *only* combination that produces valid
    output for both the header lines and the content lines, because content
    lines from ``str.splitlines(keepends=True)`` already carry their own
    ``\n`` and difflib does not append ``lineterm`` to them. Using
    ``lineterm=""`` (as in iter-2) drops the newline after every header
    and produces ``'--- /dev/null+++ b/path@@ ... @@+content'``.

    B2 fix: text-ness is enforced via ``_is_text_bytes`` (UTF-8 decode plus
    control-char analysis). Non-text on either side -> ``None``.

    New-file previews (``before is None``) emit the Git-standard
    ``fromfile="/dev/null"``. The literal string is used on every platform
    (``os.devnull`` / ``"NUL"`` are deliberately not used) so the diff text
    is byte-identical on Windows and Linux.
    """
    if after is None:
        return None
    a = _decode(after)
    if a is None or not _text_has_acceptable_controls(a):
        return None

    if before is None:
        b = ""
        fromfile = "/dev/null"
    else:
        b = _decode(before)
        if b is None or not _text_has_acceptable_controls(b):
            return None
        fromfile = f"a/{path}"

    return "".join(
        difflib.unified_diff(
            b.splitlines(keepends=True),
            a.splitlines(keepends=True),
            fromfile=fromfile,
            tofile=f"b/{path}",
        )
    )


def _primary_path(paths: tuple[str, ...]) -> str:
    return paths[0] if paths else "n/a"


def _target_exists(primary: str) -> tuple[Path | None, bool]:
    if not primary or primary == "n/a":
        return None, False
    try:
        p = Path(primary)
        return p, p.exists()
    except OSError:
        return None, False


def _change_type_from_operation(
    operation: str, target_exists: bool
) -> ChangeType:
    """Fallback mapping when a node_id is not in :data:`NODE_CHANGE_KIND`."""
    if operation in ("read", "unknown"):
        return ChangeType.NOOP
    if operation == "mkdir":
        return ChangeType.MKDIR
    if operation == "write":
        return ChangeType.MODIFY if target_exists else ChangeType.CREATE
    if operation == "move":
        return ChangeType.MOVE
    if operation == "copy":
        return ChangeType.COPY
    return ChangeType.NOOP


def _resolve_change_type(
    proposal: "ActionProposal", target_exists: bool
) -> ChangeType:
    """Return the :class:`ChangeType` for ``proposal``.

    Preference order:
      1. :data:`NODE_CHANGE_KIND` (per node_id);
         ``CREATE`` upgrades to ``MODIFY`` if the target already exists.
      2. ``_change_type_from_operation(proposal.operation, ...)`` fallback.
    """
    base = NODE_CHANGE_KIND.get(proposal.node_id)
    if base is not None:
        if base == ChangeType.CREATE and target_exists:
            return ChangeType.MODIFY
        return base
    return _change_type_from_operation(proposal.operation, target_exists)


def _build_noop(path: str, before: str, after: str) -> ChangePreview:
    return ChangePreview(
        path=path,
        change_type=ChangeType.NOOP,
        before_summary=before,
        after_summary=after,
        text_diff=None,
        before_size=None,
        after_size=None,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def preview_diff(proposal: "ActionProposal", ctx: Any) -> ChangePreview:
    """Build a ChangePreview for ``proposal`` without touching the filesystem.

    ``ctx`` is accepted for API symmetry with ``build_action_plan`` and is
    not read. Only ``safe_fs.read_bytes`` and ``Path.exists`` are used.
    """
    primary = _primary_path(proposal.target_paths)
    p, target_exists = _target_exists(primary)
    ct = _resolve_change_type(proposal, target_exists)

    if ct == ChangeType.NOOP:
        return _build_noop(primary, "read-only", "read-only")

    if ct == ChangeType.MKDIR:
        if target_exists:
            return ChangePreview(
                path=primary,
                change_type=ChangeType.MKDIR,
                before_summary="directory exists",
                after_summary="no change",
                text_diff=None,
                before_size=None,
                after_size=None,
            )
        return ChangePreview(
            path=primary,
            change_type=ChangeType.MKDIR,
            before_summary="does not exist",
            after_summary="would create directory",
            text_diff=None,
            before_size=None,
            after_size=None,
        )

    if ct == ChangeType.MOVE:
        before = _safe_read(p) if p is not None else None
        if before is not None:
            before_summary = f"exists, {len(before)} bytes"
        else:
            before_summary = "does not exist"
        return ChangePreview(
            path=primary,
            change_type=ChangeType.MOVE,
            before_summary=before_summary,
            after_summary="would move file",
            text_diff=None,
            before_size=len(before) if before is not None else None,
            after_size=len(before) if before is not None else None,
        )

    if ct == ChangeType.COPY:
        before = _safe_read(p) if p is not None else None
        if before is not None:
            before_summary = f"exists, {len(before)} bytes"
        else:
            before_summary = "does not exist"
        return ChangePreview(
            path=primary,
            change_type=ChangeType.COPY,
            before_summary=before_summary,
            after_summary="would copy file",
            text_diff=None,
            before_size=len(before) if before is not None else None,
            after_size=len(before) if before is not None else None,
        )

    # CREATE or MODIFY
    before = _safe_read(p) if p is not None else None
    after = _payload_content(proposal.payload)

    if before is not None:
        before_summary = f"exists, {len(before)} bytes"
    else:
        before_summary = "does not exist"

    if after is None:
        after_summary = "content unknown"
        after_size: int | None = None
    elif not _is_text_bytes(after):
        # Binary / undecodable content: no preview possible (B2).
        after_summary = "content unknown"
        after_size = len(after)
    else:
        after_summary = f"would write {len(after)} bytes"
        after_size = len(after)

    return ChangePreview(
        path=primary,
        change_type=ct,
        before_summary=before_summary,
        after_summary=after_summary,
        text_diff=_unified_diff(before, after, primary),
        before_size=len(before) if before is not None else None,
        after_size=after_size,
    )
