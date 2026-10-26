"""Roundtrip: zip create → extract preserves bytes; overwrite/refuse semantics."""
import os
import zipfile

import pytest

from lokdocsqr.core.errors import NodeExecutionError, SafeFsError
from lokdocsqr.nodes.fs_ops.archive import ArchiveNode


# CWD guard — see note in tests/test_archive_7z.py.
_ORIGINAL_CWD = os.getcwd()


@pytest.fixture(autouse=True)
def _restore_cwd():
    try:
        yield
    finally:
        os.chdir(_ORIGINAL_CWD)


def test_zip_create_extract_roundtrip(ctx, tmp_path):
    src1 = tmp_path / "a.txt"
    src1.write_bytes(b"hello")
    src2 = tmp_path / "b.bin"
    payload = bytes(range(256)) * 4
    src2.write_bytes(payload)

    archive = tmp_path / "out.zip"
    ArchiveNode().run(ctx, {
        "operation": "create",
        "sources": [str(src1), str(src2)],
        "target": "out.zip",
        "format": "zip",
    })
    assert archive.exists()
    assert zipfile.is_zipfile(archive)

    out_dir = tmp_path / "extracted"
    ArchiveNode().run(ctx, {
        "operation": "extract",
        "source": str(archive),
        "target_dir": "extracted",
    })
    assert (out_dir / "a.txt").read_bytes() == b"hello"
    assert (out_dir / "b.bin").read_bytes() == payload


def test_create_refuses_to_overwrite_by_default(ctx, tmp_path):
    src = tmp_path / "x.txt"
    src.write_bytes(b"data")
    target = tmp_path / "out.zip"
    target.write_bytes(b"already-here")
    with pytest.raises(SafeFsError):
        ArchiveNode().run(ctx, {
            "operation": "create",
            "sources": [str(src)],
            "target": "out.zip",
            "format": "zip",
            "overwrite": False,
        })
    assert target.read_bytes() == b"already-here"


def test_create_missing_source_raises(ctx, tmp_path):
    with pytest.raises(SafeFsError):
        ArchiveNode().run(ctx, {
            "operation": "create",
            "sources": [str(tmp_path / "missing.txt")],
            "target": "out.zip",
            "format": "zip",
        })


def test_create_rejects_unsupported_format(ctx, tmp_path):
    src = tmp_path / "x.txt"
    src.write_bytes(b"data")
    with pytest.raises(NodeExecutionError):
        ArchiveNode().run(ctx, {
            "operation": "create",
            "sources": [str(src)],
            "target": "out.tar",
            "format": "tar",
        })