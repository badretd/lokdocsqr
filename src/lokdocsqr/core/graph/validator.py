"""Semantic validator for :class:`RawGraph`.

Never raises: always returns ``list[str]``. This is intentional — the GUI
wants to present *all* problems at once instead of stopping at the first.
"""

from __future__ import annotations

import re
from typing import Any

from ..models import RawGraph

# Optional import: :func:`core.paths.render_template` provides deeper
# template validation, but we degrade gracefully if it's unavailable or if
# the call signature is incompatible with our permissive probing context.
try:  # pragma: no cover - import-time probe
    from ..paths import render_template as _render_template
except Exception:  # noqa: BLE001
    _render_template = None


# Keys that are conventionally path templates.
_PATH_KEYS = frozenset(
    {
        "path",
        "source",
        "target",
        "src",
        "dst",
        "dest",
        "destination",
        "folder",
        "directory",
        "dir",
        "template",
        "file",
        "filepath",
        "out",
        "in",
    }
)
_PATH_KEY_SUFFIXES = ("_path", "_template", "_dir", "_folder", "_file")


def _is_path_key(key: str) -> bool:
    k = key.lower()
    if k in _PATH_KEYS:
        return True
    return k.endswith(_PATH_KEY_SUFFIXES)


def _looks_absolute(value: str) -> bool:
    if not value:
        return False
    if value.startswith(("/", "\\")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    if value.startswith("~"):
        return True
    return False


def _iter_string_values(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten a nested dict/list into ``(dotted.key[index], string)`` pairs."""
    out: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(_iter_string_values(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(_iter_string_values(v, f"{prefix}[{i}]"))
    elif isinstance(value, str):
        out.append((prefix, value))
    return out


def _validate_path_templates(node_id: str, params: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(params, dict):
        return errors

    for key, raw in params.items():
        if not _is_path_key(str(key)):
            continue
        for sub_key, value in _iter_string_values(raw, str(key)):
            normalized = value.replace("\\", "/")

            # 1) '..' segments are always forbidden (escape attempt).
            if ".." in normalized.split("/"):
                errors.append(
                    f"node {node_id!r} param {sub_key!r}: contains '..' segment"
                )

            if "{" in value:
                # Template string — use render_template (if available) only
                # to surface *security / syntax* rejections. Missing
                # placeholders are expected without a real context.
                if _render_template is not None:
                    try:
                        from collections import defaultdict

                        _render_template(value, defaultdict(str))
                    except (KeyError, IndexError):
                        pass
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc).lower()
                        if any(w in msg for w in ("invalid", "syntax", "forbidden", "unsafe")):
                            errors.append(
                                f"node {node_id!r} param {sub_key!r}: "
                                f"rejected template ({exc})"
                            )
            else:
                # Literal path — absolute paths are not permitted.
                if _looks_absolute(value):
                    errors.append(
                        f"node {node_id!r} param {sub_key!r}: "
                        f"absolute paths are not allowed"
                    )
    return errors


def validate_graph(graph: RawGraph) -> list[str]:
    """Validate ``graph`` and return a (possibly empty) list of error strings.

    Checks performed:
      * at least one node exists;
      * node ids are unique;
      * edges reference existing nodes (no dangling edges);
      * no self-loops;
      * node params that look like path templates contain no ``..`` and no
        absolute paths.
    """
    errors: list[str] = []
    nodes = list(getattr(graph, "nodes", []) or [])
    edges = list(getattr(graph, "edges", []) or [])

    if not nodes:
        errors.append("graph must contain at least one node")

    # ---- uniqueness -------------------------------------------------------
    seen: set[str] = set()
    node_ids: set[str] = set()
    for node in nodes:
        nid = getattr(node, "id", None)
        if not isinstance(nid, str) or not nid:
            errors.append("found a node with missing or empty 'id'")
            continue
        if nid in seen:
            errors.append(f"duplicate node id: {nid!r}")
        seen.add(nid)
        node_ids.add(nid)

    # ---- dangling edges / self-loops --------------------------------------
    for index, edge in enumerate(edges):
        src = getattr(edge, "source", None)
        tgt = getattr(edge, "target", None)
        if not isinstance(src, str) or not src:
            errors.append(f"edges[{index}] has no valid 'source'")
            continue
        if not isinstance(tgt, str) or not tgt:
            errors.append(f"edges[{index}] has no valid 'target'")
            continue
        if src not in node_ids:
            errors.append(f"edges[{index}] references unknown source node {src!r}")
        if tgt not in node_ids:
            errors.append(f"edges[{index}] references unknown target node {tgt!r}")
        if src == tgt:
            errors.append(f"edges[{index}] is a self-loop on node {src!r}")

    # ---- path templates ---------------------------------------------------
    for node in nodes:
        nid = getattr(node, "id", None)
        params = getattr(node, "params", {}) or {}
        if not isinstance(nid, str):
            continue
        errors.extend(_validate_path_templates(nid, params))

    return errors
