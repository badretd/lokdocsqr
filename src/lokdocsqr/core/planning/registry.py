"""Node-id -> policy-operation and payload-path registry (Phase 7b).

Phase 7a introduced :data:`DEFAULT_OPERATION_MAP` and :func:`classify_node`.
Phase 7b adds :data:`PAYLOAD_PATH_KEYS` (concrete payload keys per node) and
:func:`extract_paths` (deterministic, non-throwing path extraction).

Phase 10.6-followup adds :class:`ExtractionResult` and
:func:`extract_paths_detailed`: source/destination pairs for ``fs.move`` and
``fs.copy`` are derived from the *resolved payload*, and a source supplied
only by an inbound edge marks the proposal ``dynamic`` so the definitive
scope check can happen at runtime. :func:`extract_paths` remains the stable
two-argument tuple API.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_OPERATION_MAP",
    "DEFAULT_PATH_KEYS",
    "ExtractionResult",
    "PAYLOAD_PATH_KEYS",
    "classify_node",
    "extract_paths",
    "extract_paths_detailed",
    "has_inbound_source_edge",
]

# 7a-iter-2: §7.1 (16 nodes)
DEFAULT_OPERATION_MAP: dict[str, str] = {
    # read
    "access.extract_text": "read",
    "access.hash_dedupe": "read",
    "access.index_search": "read",
    "access.list_files": "read",
    "access.ocr": "read",
    "access.read_content": "read",
    "access.read_metadata": "read",
    # write
    "fs.archive": "write",
    "fs.convert": "write",
    "fs.copy": "write",
    "fs.make_dir": "write",
    "fs.move": "write",
    "fs.rename": "write",
    "fs.tag": "write",
    "fs.trash": "write",
    "fs.write_file": "write",
    # 8-pre: §6 — 11 phase-8 nodes, containers classified read; inner
    # nodes gated by their own 7c gate.
    "flow.if_branch": "read",
    "flow.filter": "read",
    "flow.loop_batch": "read",
    "flow.merge": "read",
    "flow.wait": "read",
    "flow.retry": "read",
    "flow.subflow": "read",
    "utils.set_variables": "read",
    "utils.path_template": "read",
    "utils.json_validate": "read",
    "utils.notify": "read",
    # 9: local LLM nodes are read-only (no filesystem mutations).
    "llm.local_llm": "read",
    # 10: trigger nodes report/scan; the runtime watcher/cron host does the
    # scheduling. They never mutate the filesystem.
    "triggers.watch_folder": "read",
    "triggers.scheduled_scan": "read",
    "triggers.manual_run": "read",
    "triggers.webhook": "read",
}


# 7b: §10.1 — payload path keys per node, priority order.
PAYLOAD_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "access.list_files": ("path",),
    "access.read_metadata": ("paths",),
    "access.read_content": ("paths",),
    "access.hash_dedupe": ("paths",),
    "access.extract_text": ("paths",),
    "access.ocr": ("paths",),
    "access.index_search": ("paths",),
    "fs.make_dir": ("path_template",),
    "fs.write_file": ("path_template",),
    "fs.copy": ("source", "target_dir"),
    "fs.move": ("source", "target_dir"),
    "fs.rename": ("source",),
    "fs.archive": ("sources", "target", "source", "target_dir"),
    "fs.convert": ("source", "target"),
    "fs.tag": ("path",),
    "fs.trash": ("path",),
}


# Fallback when a node_id is not listed in PAYLOAD_PATH_KEYS.
DEFAULT_PATH_KEYS: tuple[str, ...] = (
    "path",
    "source",
    "target",
    "paths",
    "source_path",
    "target_path",
)


def classify_node(node_id: str) -> str:
    """Return the policy operation for ``node_id``, or ``'unknown'`` on a miss."""
    return DEFAULT_OPERATION_MAP.get(node_id, "unknown")


@dataclass(frozen=True)
class ExtractionResult:
    """Paths derived from a resolved payload, plus the deferral signal.

    ``dynamic`` means the source was supplied by an inbound edge, so the
    proposal cannot be the authoritative scope check. The runtime
    :meth:`ScopeManager.check` performed by the node remains mandatory.
    """

    paths: tuple[str, ...] = ()
    dynamic: bool = False


def has_inbound_source_edge(edges: Any) -> bool:
    """True when ``edges`` contains a data edge targeting the ``source`` port."""
    for edge in edges or []:
        if getattr(edge, "kind", "data") == "order":
            continue
        if getattr(edge, "target_port", None) == "source":
            return True
    return False


def _extract_key_paths(
    payload: Mapping[str, Any], keys: tuple[str, ...]
) -> tuple[str, ...]:
    """Collect string values for ``keys`` from ``payload`` (never raises)."""
    seen: set[str] = set()
    result: list[str] = []

    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, str):
            if value not in seen:
                seen.add(value)
                result.append(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and item not in seen:
                    seen.add(item)
                    result.append(item)

    return tuple(sorted(result))


def _payload_value(
    payload: Mapping[str, Any],
    node_params: Mapping[str, Any],
    key: str,
) -> Any:
    """Resolved payload wins; static params are only a defensive fallback."""
    if key in payload:
        return payload.get(key)
    return node_params.get(key)


def _render_destination(
    source: str,
    target_dir: Any,
    template: Any,
) -> str | None:
    """Best-effort destination for an ``fs.move``/``fs.copy`` proposal.

    This mirrors the node's template variables (``name``/``ext``) closely
    enough to scope-check the proposal. The node itself remains the
    authority and performs the full render/conflict/scope pipeline.
    """
    from ..paths import render_template

    if not isinstance(source, str) or not source:
        return None
    if not isinstance(template, str) or not template.strip():
        template = "{name}{ext}"

    src_path = Path(source)
    variables = {"name": src_path.stem, "ext": src_path.suffix}
    try:
        rendered = render_template(
            template, variables, allow_parent_traversal=True
        )
    except Exception:
        return None
    if not rendered.strip() or "{" in rendered:
        return None

    rendered_path = Path(rendered)
    if not rendered_path.suffix and src_path.suffix:
        try:
            rendered_path = rendered_path.with_suffix(src_path.suffix)
        except ValueError:
            return None

    base: Path | None = None
    if isinstance(target_dir, str) and target_dir.strip():
        try:
            rendered_dir = render_template(
                target_dir, variables, allow_parent_traversal=True
            )
        except Exception:
            return None
        if not rendered_dir.strip() or "{" in rendered_dir:
            return None
        base = Path(rendered_dir)

    destination = base / rendered_path if base is not None else (
        src_path.parent / rendered_path
    )
    return str(destination)


def extract_paths_detailed(
    payload: dict[str, Any],
    node_id: str,
    *,
    has_source_edge: bool = False,
    node_params: Mapping[str, Any] | None = None,
) -> ExtractionResult:
    """Extract proposal paths from the *resolved* payload.

    For ``fs.move``/``fs.copy`` the result contains the source and the
    best-effort destination (honouring ``target_dir``). A source supplied
    by an inbound edge marks the result ``dynamic``; an empty source with
    no edge stays non-dynamic so the validator can keep failing closed.
    Other node types use :data:`PAYLOAD_PATH_KEYS` unchanged.
    """
    if not isinstance(payload, dict):
        return ExtractionResult()

    params: Mapping[str, Any] = (
        node_params if isinstance(node_params, Mapping) else {}
    )

    if node_id in {"fs.move", "fs.copy"}:
        source = _payload_value(payload, params, "source")
        target_dir = _payload_value(payload, params, "target_dir")
        template = _payload_value(payload, params, "template")

        source_text = source if isinstance(source, str) else ""
        dynamic = bool(has_source_edge)
        if not source_text:
            return ExtractionResult(paths=(), dynamic=dynamic)

        paths = [source_text]
        destination = _render_destination(source_text, target_dir, template)
        if destination and destination not in paths:
            paths.append(destination)
        return ExtractionResult(
            paths=tuple(sorted(set(paths))), dynamic=dynamic
        )

    keys = PAYLOAD_PATH_KEYS.get(node_id, DEFAULT_PATH_KEYS)
    return ExtractionResult(paths=_extract_key_paths(payload, keys))


def extract_paths(payload: dict[str, Any], node_id: str) -> tuple[str, ...]:
    """Extract target paths from ``payload`` for ``node_id``.

    - Uses :data:`PAYLOAD_PATH_KEYS` when the node is listed, otherwise
      :data:`DEFAULT_PATH_KEYS`.
    - Skips non-strings and non-list values silently.
    - Flattens one level of ``list``/``tuple``.
    - Deduplicates and sorts (deterministic, R32).
    - Never raises.

    Compatibility wrapper around :func:`extract_paths_detailed`.
    """
    return extract_paths_detailed(payload, node_id).paths
