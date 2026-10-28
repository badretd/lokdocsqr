"""Phase 7b — preview_diff contracts, R28 and D2.

Expected red (until fix_phase_7b_iter_2.py):
    test_write_new_file_fromfile_is_devnull
        iter-1 uses ``fromfile=f"a/{path}"`` even for CREATE; standard
        unified-diff output for a new file uses ``--- /dev/null``.
"""
from __future__ import annotations

import pytest

from lokdocsqr.core.models import ActionProposal
from lokdocsqr.core.preview import ChangeType, preview_diff


ALL_NODES = [
    ("access.list_files", "read", ChangeType.NOOP, False),
    ("access.extract_text", "read", ChangeType.NOOP, False),
    ("access.hash_dedupe", "read", ChangeType.NOOP, False),
    ("access.index_search", "read", ChangeType.NOOP, False),
    ("access.ocr", "read", ChangeType.NOOP, False),
    ("access.read_content", "read", ChangeType.NOOP, False),
    ("access.read_metadata", "read", ChangeType.NOOP, False),
    ("fs.move", "write", ChangeType.MOVE, True),
    ("fs.copy", "write", ChangeType.COPY, True),
    ("fs.rename", "write", ChangeType.MOVE, True),
    ("fs.make_dir", "write", ChangeType.MKDIR, False),
    ("fs.write_file", "write", ChangeType.CREATE, False),
    ("fs.trash", "write", ChangeType.MOVE, True),
    ("fs.tag", "write", ChangeType.MODIFY, True),
    ("fs.convert", "write", ChangeType.CREATE, False),
    ("fs.archive", "write", ChangeType.CREATE, False),
]


def _snapshot(root):
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir():
            result[rel] = (True, None, path.stat().st_mtime_ns)
        else:
            result[rel] = (False, path.read_bytes(), path.stat().st_mtime_ns)
    return result


def _proposal(node_id, operation, target, payload=None):
    return ActionProposal(
        node_id=node_id,
        operation=operation,
        target_paths=(str(target),),
        payload={} if payload is None else payload,
        summary="preview",
    )


@pytest.mark.parametrize(
    "node_id,operation,expected,create_target",
    ALL_NODES,
    ids=[item[0] for item in ALL_NODES],
)
def test_preview_diff_returns_expected_change_type(
    node_id, operation, expected, create_target, tmp_path
):
    target = tmp_path / "target.txt"
    if create_target:
        target.write_text("hello", encoding="utf-8")
    payload = {"content": "hello"} if node_id == "fs.write_file" else {}
    preview = preview_diff(_proposal(node_id, operation, target, payload), None)
    assert preview.change_type is expected


@pytest.mark.parametrize(
    "node_id,operation,expected,create_target",
    ALL_NODES,
    ids=[item[0] for item in ALL_NODES],
)
def test_preview_diff_is_read_only_for_node(
    node_id, operation, expected, create_target, tmp_path
):
    target = tmp_path / "target.txt"
    if create_target:
        target.write_text("hello", encoding="utf-8")
    payload = {"content": "hello"} if node_id == "fs.write_file" else {}
    before = _snapshot(tmp_path)
    preview_diff(_proposal(node_id, operation, target, payload), None)
    after = _snapshot(tmp_path)
    assert after == before


def test_write_file_new_target_create_and_diff(tmp_path):
    target = tmp_path / "new.txt"
    preview = preview_diff(
        _proposal("fs.write_file", "write", target, {"content": "hello\n"}),
        None,
    )
    assert preview.change_type is ChangeType.CREATE
    assert preview.text_diff is not None
    assert "+hello" in preview.text_diff
    assert preview.after_size == 6


def test_write_file_existing_target_modify_and_diff(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("old\n", encoding="utf-8")
    preview = preview_diff(
        _proposal("fs.write_file", "write", target, {"content": "new\n"}),
        None,
    )
    assert preview.change_type is ChangeType.MODIFY
    assert preview.text_diff is not None
    assert "-old" in preview.text_diff
    assert "+new" in preview.text_diff


def test_write_new_file_fromfile_is_devnull(tmp_path):
    """Standard unified diff for a new file: ``--- /dev/null`` (git convention).

    EXPECTED RED until fix_phase_7b_iter_2.py (iter-1 uses ``a/<path>``).
    """
    target = tmp_path / "brand-new.txt"
    preview = preview_diff(
        _proposal("fs.write_file", "write", target, {"content": "x\n"}),
        None,
    )
    assert preview.change_type is ChangeType.CREATE
    assert preview.text_diff is not None
    assert "--- /dev/null" in preview.text_diff


def test_move_preview(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    preview = preview_diff(_proposal("fs.move", "write", target), None)
    assert preview.change_type is ChangeType.MOVE
    assert preview.before_size == 3
    assert preview.after_size == 3
    assert preview.text_diff is None


def test_copy_preview(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    preview = preview_diff(_proposal("fs.copy", "write", target), None)
    assert preview.change_type is ChangeType.COPY
    assert preview.before_size == 3
    assert preview.after_size == 3
    assert preview.text_diff is None


def test_make_dir_preview(tmp_path):
    preview = preview_diff(_proposal("fs.make_dir", "write", tmp_path / "d"), None)
    assert preview.change_type is ChangeType.MKDIR
    assert preview.text_diff is None


def test_tag_preview(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("abc", encoding="utf-8")
    preview = preview_diff(_proposal("fs.tag", "write", target), None)
    assert preview.change_type is ChangeType.MODIFY
    assert preview.text_diff is None


def test_binary_content_yields_none_diff(tmp_path):
    target = tmp_path / "bin.dat"
    preview = preview_diff(
        _proposal("fs.write_file", "write", target, {"content": b"\x00\x01"}),
        None,
    )
    assert preview.change_type is ChangeType.CREATE
    assert preview.text_diff is None
    assert preview.after_size == 2


def test_missing_content_yields_content_unknown(tmp_path):
    target = tmp_path / "unknown.txt"
    preview = preview_diff(
        _proposal("fs.write_file", "write", target, {}),
        None,
    )
    assert preview.text_diff is None
    assert preview.after_summary == "content unknown"
    assert preview.after_size is None


def test_move_missing_source_does_not_raise(tmp_path):
    preview = preview_diff(
        _proposal("fs.move", "write", tmp_path / "missing.txt"),
        None,
    )
    assert preview.change_type is ChangeType.MOVE
    assert preview.before_size is None
    assert preview.after_size is None


def test_copy_missing_source_does_not_raise(tmp_path):
    preview = preview_diff(
        _proposal("fs.copy", "write", tmp_path / "missing.txt"),
        None,
    )
    assert preview.change_type is ChangeType.COPY
    assert preview.before_size is None
    assert preview.after_size is None


def test_preview_diff_does_not_mutate_existing_file(tmp_path):
    target = tmp_path / "old.txt"
    target.write_text("old", encoding="utf-8")
    before = _snapshot(tmp_path)
    preview_diff(
        _proposal("fs.write_file", "write", target, {"content": "new"}),
        None,
    )
    assert target.read_text(encoding="utf-8") == "old"
    assert _snapshot(tmp_path) == before
