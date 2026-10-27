"""Node-id to policy-operation registry for Phase 7a."""
from __future__ import annotations

__all__ = ["DEFAULT_OPERATION_MAP", "classify_node"]

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


def classify_node(node_id: str) -> str:
    """Return the policy operation for node_id, or 'unknown' on a miss."""
    return DEFAULT_OPERATION_MAP.get(node_id, "unknown")
