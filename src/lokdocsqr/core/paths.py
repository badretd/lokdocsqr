"""Path resolution and safe template rendering.

Both functions here operate on strings and ``Path`` objects only —
``os.path.abspath`` and ``Path.is_absolute`` are used for normalisation, not
for I/O. Any actual filesystem mutation (move, write, quarantine) must go
through :mod:`lokdocsqr.core.safe_fs`.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

from lokdocsqr.core.errors import ScopeViolation

_FUNCTION_CALL = re.compile(
    r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*", re.DOTALL
)
_FUNCTION_NAMES = frozenset(
    {"basename", "dirname", "stem", "suffix", "join"}
)


class SafeDict(dict):
    """Dict that leaves missing ``{key}`` placeholders intact."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _split_function_args(raw: str) -> list[str] | None:
    """Split positional function arguments on commas outside quotes."""
    args: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escaped = False
    for char in raw:
        if escaped:
            buf.append(char)
            escaped = False
            continue
        if char == "\\" and quote is not None:
            buf.append(char)
            escaped = True
            continue
        if quote is not None:
            buf.append(char)
            if char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
            buf.append(char)
            continue
        if char == ",":
            args.append("".join(buf).strip())
            buf = []
            continue
        buf.append(char)
    if quote is not None:
        return None
    args.append("".join(buf).strip())
    return args


def _literal_arg(raw: str) -> str | None:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ("'", '"'):
        inner = raw[1:-1]
        if raw[0] == '"':
            try:
                import json

                return str(json.loads(raw))
            except (ValueError, TypeError):
                return None
        return inner.replace("\\'", "'").replace("\\\\", "\\")
    return None


def _resolve_arg(raw: str, context: dict[str, Any]) -> str | None:
    literal = _literal_arg(raw)
    if literal is not None:
        return literal
    if not raw or "(" in raw or ")" in raw or "{" in raw or "}" in raw:
        return None
    if raw in context:
        value = context[raw]
        if value is None:
            return None
        return value if isinstance(value, str) else str(value)
    return None


def _apply_function(
    name: str, args: list[str], context: dict[str, Any]
) -> str | None:
    resolved: list[str] = []
    for raw in args:
        value = _resolve_arg(raw, context)
        if value is None:
            return None
        resolved.append(value)

    if name == "join":
        if not resolved:
            return None
        return str(PurePosixPath(*resolved))
    if len(resolved) != 1:
        return None
    path = PurePosixPath(resolved[0])
    if name == "basename":
        return path.name
    if name == "dirname":
        return str(path.parent)
    if name == "stem":
        return path.stem
    if name == "suffix":
        return path.suffix
    return None


def _substitute_functions(
    template: str, context: dict[str, Any]
) -> tuple[str, dict[str, str]]:
    """Replace whitelisted ``{func(arg)}`` calls with safe placeholders.

    Unknown function names, unresolved arguments, and malformed calls are
    left as literal text (formatted as escaped braces). This mirrors
    ``SafeDict``'s behaviour for missing keys.
    """
    out: list[str] = []
    extra: dict[str, str] = {}
    index = 0
    i = 0
    length = len(template)
    while i < length:
        if template.startswith("{{", i) or template.startswith("}}", i):
            out.append(template[i : i + 2])
            i += 2
            continue
        if template[i] != "{":
            out.append(template[i])
            i += 1
            continue
        end = template.find("}", i + 1)
        if end == -1:
            out.append(template[i:])
            break
        content = template[i + 1 : end]
        match = _FUNCTION_CALL.fullmatch(content)
        if match is None or match.group(1) not in _FUNCTION_NAMES:
            out.append(template[i : end + 1])
            i = end + 1
            continue

        name = match.group(1)
        raw_args = _split_function_args(match.group(2))
        value = (
            None
            if raw_args is None
            else _apply_function(name, raw_args, context)
        )
        if value is None:
            # Keep an unrecognised/unresolved function call literal while
            # preventing str.format_map from parsing it as a field name.
            out.append("{{" + content + "}}")
        else:
            key = f"__lokdocsqr_fn_{index}"
            while key in context or key in extra:
                index += 1
                key = f"__lokdocsqr_fn_{index}"
            extra[key] = value
            out.append("{" + key + "}")
            index += 1
        i = end + 1
    return "".join(out), extra


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


def render_template(
    template: str,
    context: dict[str, Any],
    *,
    allow_parent_traversal: bool = False,
) -> str:
    """Safely substitute ``{key}`` placeholders in ``template``.

    Missing keys are left as literal ``{key}``. A small whitelist of
    string functions is also available: ``basename(path)``,
    ``dirname(path)``, ``stem(path)``, ``suffix(path)`` and
    ``join(a, b, ...)``. Arguments are quoted literals or context keys;
    nesting and expressions are not supported. Unrecognised function
    names are left literal.

    The rendered string is validated: absolute paths are always rejected;
    ``..`` segments are rejected unless ``allow_parent_traversal`` is true
    (the caller must then rely on the scope check).
    """
    substituted, extra = _substitute_functions(template, context)
    merged: dict[str, Any] = dict(context)
    merged.update(extra)
    try:
        rendered = substituted.format_map(SafeDict(merged))
    except (ValueError, KeyError) as exc:
        raise ScopeViolation(f"Template rendering failed: {exc}") from exc

    rendered_path = Path(rendered)
    if rendered_path.is_absolute():
        raise ScopeViolation(
            f"Rendered template is absolute: {rendered!r}"
        )

    if not allow_parent_traversal:
        normalized = os.path.normpath(rendered)
        if ".." in Path(normalized).parts:
            raise ScopeViolation(
                f"Rendered template escapes with '..': {rendered!r}"
            )

    return rendered
