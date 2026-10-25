"""Path-parameter validation in lokdocsqr.core.graph.validator.

Covers ``_is_path_key``, ``_looks_absolute``, ``_iter_string_values`` and
``_validate_path_value`` through the public ``validate_graph`` API.
"""
from __future__ import annotations

import pytest

from lokdocsqr.core.graph import parse_graph, validate_graph


def _node(nid: str = "n", ntype: str = "fake", **params) -> dict:
    return {"id": nid, "type": ntype, "params": params}


def _doc(nodes) -> dict:
    return {
        "spec_version": "1.0",
        "metadata": {"id": "auto", "name": "Test"},
        "nodes": nodes,
        "edges": [],
    }


def _errors_for(node_dict: dict) -> list[str]:
    return validate_graph(parse_graph(_doc([node_dict])))


def test_relative_path_param_is_accepted() -> None:
    assert _errors_for(_node(path="sub/file.txt")) == []


def test_parent_traversal_in_path_param_is_flagged() -> None:
    errors = _errors_for(_node(path="../escape"))
    assert any("segment" in e for e in errors)


def test_embedded_parent_traversal_is_flagged() -> None:
    errors = _errors_for(_node(path="sub/../../escape"))
    assert any("segment" in e for e in errors)


def test_absolute_posix_path_param_is_flagged() -> None:
    errors = _errors_for(_node(path="/etc/passwd"))
    assert any("absolute" in e for e in errors)


def test_absolute_windows_forward_slash_path_is_flagged() -> None:
    errors = _errors_for(_node(path="C:/Windows/System32/file.dll"))
    assert any("absolute" in e for e in errors)


def test_absolute_windows_backslash_path_is_flagged() -> None:
    errors = _errors_for(_node(path="C:\\Windows\\file.dll"))
    assert any("absolute" in e for e in errors)


def test_tilde_path_param_is_flagged() -> None:
    errors = _errors_for(_node(path="~/secrets"))
    assert any("absolute" in e for e in errors)


@pytest.mark.parametrize(
    "key",
    ["path", "source", "target", "src", "dst", "dest", "destination",
     "folder", "directory", "dir", "template", "file", "filepath",
     "out", "in"],
)
def test_known_path_keys_are_validated(key: str) -> None:
    errors = _errors_for(_node(**{key: "../escape"}))
    assert any("segment" in e for e in errors), (
        f"key {key!r} was not validated"
    )


@pytest.mark.parametrize(
    "key",
    ["input_path", "output_template", "data_dir",
     "cache_folder", "result_file"],
)
def test_path_key_suffixes_are_validated(key: str) -> None:
    errors = _errors_for(_node(**{key: "../escape"}))
    assert any("segment" in e for e in errors), (
        f"suffix key {key!r} was not validated"
    )


def test_non_path_keys_are_ignored() -> None:
    errors = _errors_for(_node(comment="../not-a-path", label="/abs/label"))
    assert errors == []


def test_path_keys_with_nested_dict_values_are_validated() -> None:
    # ``path`` is a path key; its nested value is walked recursively.
    errors = _errors_for(_node(path={"sub": "../escape"}))
    assert any("segment" in e for e in errors)


def test_path_keys_with_list_values_are_validated() -> None:
    errors = _errors_for(_node(path=["ok.txt", "../escape"]))
    assert any("segment" in e for e in errors)


def test_deeply_nested_path_value_is_flagged() -> None:
    errors = _errors_for(_node(path={"a": {"b": ["ok", "../escape"]}}))
    assert any("segment" in e for e in errors)


def test_multiple_path_errors_are_reported() -> None:
    errors = _errors_for(_node(path="../escape", source="/abs/file"))
    assert any("segment" in e for e in errors)
    assert any("absolute" in e for e in errors)


def test_template_with_invalid_format_is_flagged() -> None:
    """``render_template`` must reject an unbalanced placeholder."""
    errors = _errors_for(_node(template="{unclosed"))
    assert any(
        "path policy" in e or "rendering" in e.lower() for e in errors
    )
