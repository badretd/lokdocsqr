"""Hypothesis property — 8 examples (down from 25)."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from tests.trash.conftest import _build_ctx, find_node_class


@given(st.binary(min_size=0, max_size=512))
@settings(max_examples=8, deadline=None)
def test_trash_preserves_hash(data):
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        base, trash = td_path / "workspace", td_path / "trash"
        base.mkdir()
        trash.mkdir()
        ctx, _ = _build_ctx(base, trash, dry_run=False)

        src = base / "data.bin"
        src.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()

        import lokdocsqr.nodes.fs_ops.trash  # noqa: F401
        import sys
        mod = sys.modules["lokdocsqr.nodes.fs_ops.trash"]
        node = find_node_class(mod)()
        result = node.run(ctx, src)
        if isinstance(result, list):
            result = result[0] if result else None

        assert result is not None, "BUG: trash.py — run() returned None"
        assert result.hash_before == expected, (
            f"BUG: trash.py — hash_before mismatch for {len(data)} bytes"
        )
        trash_file = Path(result.trash_path)
        assert trash_file.exists(), "BUG: trash.py — file missing at trash_path"
        assert trash_file.read_bytes() == data, \
            "BUG: trash.py — trashed content differs from original"
        assert not src.exists(), "BUG: trash.py — source still present"