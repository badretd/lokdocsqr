"""Internal helpers shared by trigger nodes (not a registered node).

Trigger nodes are read-only: they describe the event that caused a run (or the
files currently visible to a scan) and never mutate the filesystem. The actual
watcher/cron loops live in :mod:`lokdocsqr.core.runtime`; a trigger node only
turns an injected runtime event or a fresh scan into ordinary output ports.
"""
from __future__ import annotations

import fnmatch
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from lokdocsqr.core.errors import NodeExecutionError, ScopeViolation


def as_string_tuple(value: Any, default: Sequence[str] = ()) -> tuple[str, ...]:
    """Normalize a string / sequence / ``None`` into a tuple of strings."""
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else tuple(default)
    if isinstance(value, Sequence):
        result = tuple(str(item).strip() for item in value)
        return tuple(item for item in result if item) or tuple(default)
    return tuple(default)


def trigger_event(ctx: Any) -> dict[str, Any]:
    """Return the runtime event injected as ``ctx.variables['trigger']``."""
    variables = getattr(ctx, "variables", None)
    if not isinstance(variables, Mapping):
        return {}
    event = variables.get("trigger")
    return dict(event) if isinstance(event, Mapping) else {}


def event_payload(ctx: Any) -> dict[str, Any]:
    event = trigger_event(ctx)
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _base_dir(ctx: Any) -> Path | None:
    config = getattr(ctx, "config", None)
    raw = getattr(config, "base_dir", None)
    return Path(raw) if isinstance(raw, (str, Path)) else None


def resolve_read_path(ctx: Any, raw: str | Path) -> Path:
    """Resolve a read-only path against the workflow base and scope-check it."""
    path = Path(raw)
    if not path.is_absolute():
        base = _base_dir(ctx)
        if base is not None:
            path = base / path
    scope = getattr(ctx, "scope", None)
    if scope is None:
        return path
    try:
        checked = scope.check(path, write=False)
    except ScopeViolation as exc:
        raise NodeExecutionError(f"trigger path outside scope: {path}") from exc
    return Path(checked) if isinstance(checked, (str, Path)) else path


def _matches(entry: Path, root: Path, patterns: Sequence[str]) -> bool:
    try:
        relative = entry.relative_to(root).as_posix()
    except ValueError:
        relative = entry.name
    name = entry.name
    for pattern in patterns:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(relative, pattern):
            return True
    return False


def _file_entry(entry: Path) -> dict[str, Any]:
    try:
        stat = entry.stat()
    except OSError:
        return {"path": str(entry), "size": 0, "is_dir": False}
    return {
        "path": str(entry),
        "size": int(stat.st_size),
        "is_dir": False,
    }


def scan_files_counted(
    ctx: Any,
    raw_path: str | Path | None,
    *,
    patterns: Sequence[str] = ("*",),
    recursive: bool = True,
    limit: int = 10_000,
) -> tuple[list[dict[str, Any]], int]:
    """Return matching files below ``raw_path`` and the number examined."""
    if raw_path is None or not str(raw_path):
        return [], 0
    root = resolve_read_path(ctx, raw_path)
    try:
        if root.is_file():
            matched = (
                [_file_entry(root)]
                if _matches(root, root.parent, patterns)
                else []
            )
            return matched, 1
        if not root.is_dir():
            return [], 0
    except OSError:
        return [], 0

    result: list[dict[str, Any]] = []
    scanned = 0
    stack: list[Path] = [root]
    while stack and len(result) < limit:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir():
                    if recursive and not entry.is_symlink():
                        stack.append(entry)
                    continue
                if not entry.is_file():
                    continue
            except OSError:
                continue
            scanned += 1
            if not _matches(entry, root, patterns):
                continue
            result.append(_file_entry(entry))
            if len(result) >= limit:
                break
    return result, scanned


def scan_files(
    ctx: Any,
    raw_path: str | Path | None,
    *,
    patterns: Sequence[str] = ("*",),
    recursive: bool = True,
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Return matching regular files below ``raw_path`` (read-only)."""
    files, _scanned = scan_files_counted(
        ctx,
        raw_path,
        patterns=patterns,
        recursive=recursive,
        limit=limit,
    )
    return files


def normalise_files(value: Any) -> list[dict[str, Any]]:
    """Normalize a files value into a list of ``{path, size, is_dir}`` dicts."""
    if value is None:
        return []

    if isinstance(value, Mapping):
        items: Sequence[Any] = [value]
    elif isinstance(value, (str, Path)):
        items = [value]
    elif isinstance(value, Sequence):
        items = list(value)
    else:
        return []

    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            raw_path = item.get("path")
            if not isinstance(raw_path, (str, Path)) or not str(raw_path):
                continue
            result.append(
                {
                    **dict(item),
                    "path": str(raw_path),
                    "size": int(item.get("size", 0) or 0),
                    "is_dir": bool(item.get("is_dir", False)),
                }
            )
        elif isinstance(item, (str, Path)):
            path = Path(item)
            result.append(_file_entry(path))
    return result


def files_from_event(ctx: Any) -> list[dict[str, Any]]:
    """Files supplied by the runtime event (watch paths / manual selection)."""
    event = trigger_event(ctx)
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return []
    value = payload.get("files")
    if value is None:
        value = payload.get("paths")
    return normalise_files(value)


def event_changes(ctx: Any) -> list[dict[str, Any]]:
    """Change records supplied by a folder-watcher event."""
    payload = event_payload(ctx)
    changes = payload.get("changes")
    if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)):
        return []
    return [dict(item) for item in changes if isinstance(item, Mapping)]


def empty_files_result() -> dict[str, Any]:
    return {
        "files": [],
        "paths": [],
    }


__all__ = [
    "as_string_tuple",
    "empty_files_result",
    "event_changes",
    "event_payload",
    "files_from_event",
    "normalise_files",
    "resolve_read_path",
    "scan_files",
    "scan_files_counted",
    "trigger_event",
]
