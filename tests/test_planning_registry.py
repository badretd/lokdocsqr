"""Tests for Phase 7a planning registry."""
from __future__ import annotations

import pytest

from lokdocsqr.core.planning import DEFAULT_OPERATION_MAP, classify_node

EXPECTED_INVENTORY = {
    "access.extract_text": "read",
    "access.index_search": "read",
    "access.list_files": "read",
    "access.ocr": "read",
    "access.read_content": "read",
    "access.read_metadata": "read",
    "fs.copy": "write",
    "fs.make_dir": "write",
    "fs.move": "write",
    "fs.rename": "write",
    "fs.write_file": "write",
}


def test_default_operation_map_covers_inventory_and_classify():
    problems = []
    for node_id, expected in EXPECTED_INVENTORY.items():
        actual = DEFAULT_OPERATION_MAP.get(node_id)
        classified = classify_node(node_id)
        if actual != expected:
            problems.append(
                f"{node_id}: DEFAULT_OPERATION_MAP={actual!r}, expected {expected!r}"
            )
        if classified != expected:
            problems.append(
                f"{node_id}: classify_node={classified!r}, expected {expected!r}"
            )
    assert not problems, "\n".join(problems)


def test_default_operation_map_is_dict_of_strings():
    assert isinstance(DEFAULT_OPERATION_MAP, dict)
    for key, value in DEFAULT_OPERATION_MAP.items():
        assert isinstance(key, str)
        assert isinstance(value, str)


def test_default_operation_map_values_are_whitelisted():
    allowed = {"read", "write", "move", "copy", "mkdir", "unknown"}
    assert set(DEFAULT_OPERATION_MAP.values()) <= allowed


def test_default_operation_map_no_typo_values():
    allowed = {"read", "write", "move", "copy", "mkdir", "unknown"}
    bad = sorted(
        value for value in DEFAULT_OPERATION_MAP.values() if value not in allowed
    )
    assert not bad, f"unexpected operation values: {bad}"


def test_classify_node_unknown_nonexistent():
    assert classify_node("nonexistent.node") == "unknown"


def test_classify_node_empty():
    assert classify_node("") == "unknown"


def test_classify_node_returns_string_for_existing_key():
    assert classify_node("access.extract_text") == "read"


def test_default_operation_map_keys_are_strings():
    assert all(isinstance(key, str) for key in DEFAULT_OPERATION_MAP)
