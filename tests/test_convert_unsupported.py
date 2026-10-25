"""Unimplemented MIME pair must raise converter_not_available with hint."""
import pytest

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.nodes.fs_ops.convert import CONVERTERS, ConvertNode


def test_no_pdf_source_converter_is_registered():
    assert all(src != "application/pdf" for src, _ in CONVERTERS), (
        f"pdf converters must not be silently registered: {list(CONVERTERS)}"
    )


def test_pdf_to_txt_raises_with_hint(ctx, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 not-a-real-pdf")
    target = tmp_path / "doc.txt"
    with pytest.raises(NodeExecutionError) as ei:
        ConvertNode().run(ctx, {
            "source": str(src),
            "target": "doc.txt",
            "from_mime": "application/pdf",
            "to_mime": "text/plain",
        })
    msg = str(ei.value)
    assert "converter_not_available" in msg
    assert "pip install lokdocsqr[convert]" in msg
    assert not target.exists()


def test_pdf_converter_still_absent_even_if_pypdf_installed(ctx, tmp_path):
    """Explicit reason for importorskip: pdf backend is intentionally not
    wired into CONVERTERS yet; this test only runs where pypdf is present
    to prove the absence is deliberate and not a masking side-effect."""
    pytest.importorskip(
        "pypdf",
        reason="pypdf not installed; skipping the 'pdf backend present but "
               "converter not registered yet' scenario",
    )
    assert ("application/pdf", "text/plain") not in CONVERTERS
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 not-a-real-pdf")
    with pytest.raises(NodeExecutionError) as ei:
        ConvertNode().run(ctx, {
            "source": str(src),
            "target": "out.txt",
            "from_mime": "application/pdf",
            "to_mime": "text/plain",
        })
    assert "converter_not_available" in str(ei.value)


def test_unknown_pair_raises(ctx, tmp_path):
    src = tmp_path / "x.foo"
    src.write_bytes(b"data")
    with pytest.raises(NodeExecutionError) as ei:
        ConvertNode().run(ctx, {
            "source": str(src),
            "target": "y.bar",
            "from_mime": "application/x-unknown",
            "to_mime": "application/x-other",
        })
    assert "converter_not_available" in str(ei.value)
