"""7z tests: missing backend must fail before any write; create gated by py7zr.

Note: 7z *extract* is not implemented in ``_run_extract`` (always uses
``zipfile.ZipFile``); this matches the archive node's Phase-5d scope.
"""
import builtins

import pytest

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.nodes.fs_ops.archive import ArchiveNode


def test_missing_backend_raises_before_any_write(ctx, tmp_path, monkeypatch):
    """Simulate 'py7zr not installed' by stubbing builtins.__import__."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "py7zr":
            raise ImportError("simulated missing py7zr")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    src = tmp_path / "a.txt"
    src.write_bytes(b"data")
    target = tmp_path / "out.7z"
    with pytest.raises(NodeExecutionError) as ei:
        ArchiveNode().run(ctx, {
            "operation": "create",
            "sources": [str(src)],
            "target": "out.7z",
            "format": "7z",
        })
    assert "7z_backend_unavailable" in str(ei.value)
    assert not target.exists()


def test_7z_create_roundtrip(ctx, tmp_path):
    pytest.importorskip(
        "py7zr",
        reason="py7zr not installed; install lokdocsqr[7z] to run 7z tests",
    )
    src = tmp_path / "a.txt"
    src.write_bytes(b"hello")
    archive = tmp_path / "out.7z"
    ArchiveNode().run(ctx, {
        "operation": "create",
        "sources": [str(src)],
        "target": "out.7z",
        "format": "7z",
    })
    assert archive.exists()
