"""Phase 7b — extract_paths and PAYLOAD_PATH_KEYS contracts.

PAYLOAD_PATH_KEYS is compared against the authoritative actual-code snapshot
(16 nodes). The §6 inventory in the original prompt was a placeholder for
5 nodes (fs.copy/make_dir/move/rename/write_file) and is superseded by the
values shipped in core/planning/registry.py.

All tests green after this file is applied. No expected red.
"""
from __future__ import annotations

import pytest

from lokdocsqr.core.planning import (
    DEFAULT_PATH_KEYS,
    PAYLOAD_PATH_KEYS,
    extract_paths,
)


# Authoritative snapshot of PAYLOAD_PATH_KEYS shipped in
# lokdocsqr.core.planning.registry (16 nodes).
ACTUAL_PAYLOAD_PATH_KEYS = {
    "access.list_files": ("path",),
    "access.extract_text": ("paths",),
    "access.hash_dedupe": ("paths",),
    "access.index_search": ("paths",),
    "access.ocr": ("paths",),
    "access.read_content": ("paths",),
    "access.read_metadata": ("paths",),
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


EXTRACT_CASES = [
    ("access.list_files", {"path": "p"}, ("p",)),
    ("access.extract_text", {"paths": ["a", "b"]}, ("a", "b")),
    ("access.hash_dedupe", {"paths": ["a"]}, ("a",)),
    ("access.index_search", {"paths": ["a"]}, ("a",)),
    ("access.ocr", {"paths": ["a"]}, ("a",)),
    ("access.read_content", {"paths": ["a"]}, ("a",)),
    ("access.read_metadata", {"paths": ["a"]}, ("a",)),
    ("fs.make_dir", {"path_template": "d"}, ("d",)),
    ("fs.write_file", {"path_template": "f"}, ("f",)),
    ("fs.copy", {"source": "s"}, ("s",)),
    ("fs.move", {"source": "s"}, ("s",)),
    ("fs.rename", {"source": "s"}, ("s",)),
    (
        "fs.archive",
        {"sources": ["a"], "target": "b", "source": "c", "target_dir": "d"},
        ("a", "b", "c", "d"),
    ),
    ("fs.convert", {"source": "s", "target": "t"}, ("s", "t")),
    ("fs.tag", {"path": "p"}, ("p",)),
    ("fs.trash", {"path": "p"}, ("p",)),
]


def test_payload_path_keys_actual_contract():
    """PAYLOAD_PATH_KEYS must match the 16-node actual-code snapshot exactly."""
    assert PAYLOAD_PATH_KEYS == ACTUAL_PAYLOAD_PATH_KEYS


@pytest.mark.parametrize(
    "node_id,expected",
    sorted(ACTUAL_PAYLOAD_PATH_KEYS.items()),
    ids=sorted(ACTUAL_PAYLOAD_PATH_KEYS),
)
def test_payload_path_keys_per_node(node_id, expected):
    assert PAYLOAD_PATH_KEYS[node_id] == expected


def test_default_path_keys_exact():
    assert DEFAULT_PATH_KEYS == (
        "path",
        "source",
        "target",
        "paths",
        "source_path",
        "target_path",
    )


@pytest.mark.parametrize(
    "node_id,payload,expected",
    EXTRACT_CASES,
    ids=[item[0] for item in EXTRACT_CASES],
)
def test_extract_paths_for_all_nodes(node_id, payload, expected):
    assert extract_paths(payload, node_id) == expected


def test_extract_paths_dedup():
    assert extract_paths({"paths": ["a", "a", "b"]}, "access.extract_text") == (
        "a",
        "b",
    )


def test_extract_paths_sorted():
    assert extract_paths({"paths": ["b", "a"]}, "access.extract_text") == (
        "a",
        "b",
    )


def test_extract_paths_ignores_non_strings():
    assert extract_paths(
        {"paths": ["a", 1, None, "b"]},
        "access.extract_text",
    ) == ("a", "b")


def test_extract_paths_missing_key_returns_empty():
    assert extract_paths({}, "access.extract_text") == ()


def test_extract_paths_fallback_unknown_node():
    assert extract_paths({"path": "x"}, "unknown.node") == ("x",)


def test_extract_paths_fs_archive_all_keys():
    payload = {
        "sources": ["a"],
        "target": "b",
        "source": "c",
        "target_dir": "d",
    }
    assert extract_paths(payload, "fs.archive") == ("a", "b", "c", "d")


def test_extract_paths_never_raises_on_bad_payload():
    assert extract_paths(None, "access.list_files") == ()
    assert extract_paths([], "access.list_files") == ()
