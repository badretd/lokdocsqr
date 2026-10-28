"""Node-id -> policy-operation and payload-path registry (Phase 7b).

Phase 7a introduced :data:`DEFAULT_OPERATION_MAP` and :func:`classify_node`.
Phase 7b adds :data:`PAYLOAD_PATH_KEYS` (concrete payload keys per node) and
:func:`extract_paths` (deterministic, non-throwing path extraction).
"""
from __future__ import annotations

__all__ = [
    "DEFAULT_OPERATION_MAP",
    "DEFAULT_PATH_KEYS",
    "PAYLOAD_PATH_KEYS",
    "classify_node",
    "extract_paths",
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
    "fs.copy": ("source",),
    "fs.move": ("source",),
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


def extract_paths(payload: dict, node_id: str) -> tuple[str, ...]:
    """Extract target paths from ``payload`` for ``node_id``.

    - Uses :data:`PAYLOAD_PATH_KEYS` when the node is listed, otherwise
      :data:`DEFAULT_PATH_KEYS`.
    - Skips non-strings and non-list values silently.
    - Flattens one level of ``list``/``tuple``.
    - Deduplicates and sorts (deterministic, R32).
    - Never raises.
    """
    if not isinstance(payload, dict):
        return ()

    keys = PAYLOAD_PATH_KEYS.get(node_id, DEFAULT_PATH_KEYS)
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
