"""Phase 7b — preview type contracts."""
from __future__ import annotations

import dataclasses

import pytest

from lokdocsqr.core.models import ActionProposal
from lokdocsqr.core.preview import (
    ChangePreview,
    ChangeType,
    DiffEntry,
    NODE_CHANGE_KIND,
    _resolve_change_type,
)


def test_change_type_values():
    assert {item.value for item in ChangeType} == {
        "create",
        "modify",
        "move",
        "copy",
        "mkdir",
        "noop",
    }


def test_change_type_is_str_enum():
    assert issubclass(ChangeType, str)


def test_diff_entry_frozen_and_fields():
    names = {field.name for field in dataclasses.fields(DiffEntry)}
    assert names == {
        "old_path",
        "new_path",
        "change_type",
        "before_size",
        "after_size",
    }
    entry = DiffEntry(None, "a", ChangeType.CREATE, None, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.old_path = "x"


def test_change_preview_frozen_and_fields():
    names = {field.name for field in dataclasses.fields(ChangePreview)}
    assert names == {
        "path",
        "change_type",
        "before_summary",
        "after_summary",
        "text_diff",
        "before_size",
        "after_size",
    }
    preview = ChangePreview("a", ChangeType.NOOP, "b", "a", None, None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        preview.path = "b"


def test_node_change_kind_exact_keys():
    assert set(NODE_CHANGE_KIND) == {
        "fs.move",
        "fs.copy",
        "fs.rename",
        "fs.make_dir",
        "fs.write_file",
        "fs.trash",
        "fs.tag",
        "fs.convert",
        "fs.archive",
    }


def test_node_change_kind_values():
    assert all(isinstance(value, ChangeType) for value in NODE_CHANGE_KIND.values())


@pytest.mark.parametrize(
    "node_id,expected",
    [
        ("fs.move", ChangeType.MOVE),
        ("fs.copy", ChangeType.COPY),
        ("fs.rename", ChangeType.MOVE),
        ("fs.make_dir", ChangeType.MKDIR),
        ("fs.write_file", ChangeType.CREATE),
        ("fs.trash", ChangeType.MOVE),
        ("fs.tag", ChangeType.MODIFY),
        ("fs.convert", ChangeType.CREATE),
        ("fs.archive", ChangeType.CREATE),
    ],
)
def test_node_change_kind_contract(node_id, expected):
    assert NODE_CHANGE_KIND[node_id] is expected
    proposal = ActionProposal(node_id=node_id, operation="write")
    assert _resolve_change_type(proposal, False) is expected


@pytest.mark.parametrize("node_id", ["fs.write_file", "fs.convert", "fs.archive"])
def test_resolve_create_upgrades_to_modify_when_target_exists(node_id):
    proposal = ActionProposal(node_id=node_id, operation="write")
    assert _resolve_change_type(proposal, True) is ChangeType.MODIFY


@pytest.mark.parametrize("node_id", ["fs.write_file", "fs.convert", "fs.archive"])
def test_resolve_create_stays_create_when_target_missing(node_id):
    proposal = ActionProposal(node_id=node_id, operation="write")
    assert _resolve_change_type(proposal, False) is ChangeType.CREATE


def test_resolve_fallback_access_read_is_noop():
    proposal = ActionProposal(node_id="access.list_files", operation="read")
    assert _resolve_change_type(proposal, False) is ChangeType.NOOP


def test_resolve_fallback_unknown_is_noop():
    proposal = ActionProposal(node_id="custom.node", operation="unknown")
    assert _resolve_change_type(proposal, False) is ChangeType.NOOP


@pytest.mark.parametrize(
    "target_exists,expected",
    [(False, ChangeType.CREATE), (True, ChangeType.MODIFY)],
)
def test_resolve_fallback_write_without_map(target_exists, expected):
    proposal = ActionProposal(node_id="custom.node", operation="write")
    assert _resolve_change_type(proposal, target_exists) is expected
