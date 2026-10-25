"""Path resolution and safe template rendering.

Both functions here operate on strings and ``Path`` objects only —
``os.path.abspath`` and ``Path.is_absolute`` are used for normalisation, not
for I/O. Any actual filesystem mutation (move, write, quarantine) must go
through :mod:`lokdocsqr.core.safe_fs`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lokdocsqr.core.errors import ScopeViolation


class SafeDict(dict):
    """Dict that leaves missing ``{key}`` placeholders intact."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def resolve_path(base_dir: Path, path: str | Path) -> Path:
    """Resolve ``path`` against ``base_dir``; reject ``..`` escapes."""
    base_dir = Path(base_dir)
    candidate = Path(path)
    if candidate.is_absolute():
        result = candidate
    else:
        result = base_dir / candidate

    try:
        normalized = Path(os.path.abspath(str(result)))
        base_normalized = Path(os.path.abspath(str(base_dir)))
    except OSError as exc:
        raise ScopeViolation(f"Cannot normalize path {result}: {exc}") from exc

    try:
        normalized.relative_to(base_normalized)
    except ValueError as exc:
        raise ScopeViolation(
            f"Path {path!r} escapes base_dir {base_dir}"
        ) from exc

    return normalized


def render_template(template: str, context: dict[str, Any]) -> str:
    """Safely substitute ``{key}`` placeholders in ``template``.

    Missing keys are left as literal ``{key}``. The rendered string is then
    validated: absolute paths and ``..`` segments are rejected.
    """
    try:
        rendered = template.format_map(SafeDict(context))
    except (ValueError, KeyError) as exc:
        raise ScopeViolation(f"Template rendering failed: {exc}") from exc

    rendered_path = Path(rendered)
    if rendered_path.is_absolute():
        raise ScopeViolation(
            f"Rendered template is absolute: {rendered!r}"
        )

    normalized = os.path.normpath(rendered)
    if ".." in Path(normalized).parts:
        raise ScopeViolation(
            f"Rendered template escapes with '..': {rendered!r}"
        )

    return rendered
