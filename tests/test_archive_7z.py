"""7z is explicitly unsupported (F-08).

The zip backend remains supported. 7z is not advertised and creation
fails with a clear message before any write.
"""
import pytest
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.nodes.fs_ops.archive import ArchiveNode


def test_archive_7z_unsupported(ctx, tmp_path):
    """F-08 acceptance: 7z is rejected explicitly, before any write."""
    src = tmp_path / "a.txt"
    src.write_bytes(b"data")
    target = tmp_path / "out.7z"
    with pytest.raises(NodeExecutionError) as exc:
        ArchiveNode().run(ctx, {
            "operation": "create",
            "sources": [str(src)],
            "target": "out.7z",
            "format": "7z",
        })
    message = str(exc.value)
    assert "unsupported archive format" in message
    assert "7z" in message
    assert not target.exists()
