"""Registry classification (SPEC §6) + 7b freeze (SPEC §7)."""
import subprocess
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent

NEW_11 = [
    "flow.if_branch", "flow.filter", "flow.loop_batch", "flow.merge",
    "flow.wait", "flow.retry", "flow.subflow",
    "utils.set_variables", "utils.path_template",
    "utils.json_validate", "utils.notify",
]


def test_11_new_keys_classified_read():
    from lokdocsqr.core.planning.registry import DEFAULT_OPERATION_MAP
    for k in NEW_11:
        assert k in DEFAULT_OPERATION_MAP, f"missing: {k}"
        assert DEFAULT_OPERATION_MAP[k] == "read", \
            f"{k} -> {DEFAULT_OPERATION_MAP[k]!r}, expected 'read'"


def test_map_has_27_entries():
    from lokdocsqr.core.planning.registry import DEFAULT_OPERATION_MAP
    assert len(DEFAULT_OPERATION_MAP) == 27, \
        f"expected 27 (16 pre-8 + 11 new), got {len(DEFAULT_OPERATION_MAP)}"


def test_pre_8_16_entries_unchanged():
    from lokdocsqr.core.planning.registry import DEFAULT_OPERATION_MAP
    expected = {
        "access.extract_text": "read", "access.hash_dedupe": "read",
        "access.index_search": "read", "access.list_files": "read",
        "access.ocr": "read", "access.read_content": "read",
        "access.read_metadata": "read",
        "fs.archive": "write", "fs.convert": "write", "fs.copy": "write",
        "fs.make_dir": "write", "fs.move": "write", "fs.rename": "write",
        "fs.tag": "write", "fs.trash": "write", "fs.write_file": "write",
    }
    for k, v in expected.items():
        assert DEFAULT_OPERATION_MAP.get(k) == v, \
            f"{k} -> {DEFAULT_OPERATION_MAP.get(k)!r}, expected {v!r}"


def test_classify_node_unknown_returns_unknown():
    from lokdocsqr.core.planning.registry import classify_node
    assert classify_node("totally.made.up") == "unknown"