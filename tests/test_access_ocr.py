"""Phase 6c — OcrNode behaviour tests (mocked pytesseract/PIL)."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.ocr import OcrNode


def make_ctx(tmp_path: Path, allow_symlinks: bool = False):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path],
        writable=[tmp_path],
        allow_symlinks=allow_symlinks,
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
        audit=SimpleNamespace(append=lambda log: None),
    )


class TesseractNotFoundError(Exception):
    pass


class TesseractError(Exception):
    pass


def make_fake_pytesseract(
    *,
    text: str = "ocr text",
    conf=None,
    not_found: bool = False,
    tesseract_error: str | None = None,
):
    mod = types.ModuleType("pytesseract")

    class _Output:
        DICT = "DICT"

    mod.Output = _Output
    mod.TesseractNotFoundError = TesseractNotFoundError
    mod.TesseractError = TesseractError
    calls: list[tuple] = []

    def get_tesseract_version():
        if not_found:
            raise TesseractNotFoundError("tesseract missing")
        return "5.0.0"

    def image_to_string(image, lang="eng", config=""):
        calls.append(("image_to_string", image, lang, config))
        if tesseract_error is not None:
            raise TesseractError(tesseract_error)
        if not_found:
            raise TesseractNotFoundError("tesseract missing")
        return text

    def image_to_data(image, lang="eng", config="", output_type=None):
        calls.append(("image_to_data", image, lang, config, output_type))
        if conf is None:
            return {"conf": ["90", "80", "-1", "bad"]}
        return {"conf": conf}

    mod.get_tesseract_version = get_tesseract_version
    mod.image_to_string = image_to_string
    mod.image_to_data = image_to_data
    mod._calls = calls
    return mod


class FakePilImage:
    def __init__(self) -> None:
        self.mode = None

    def load(self) -> None:
        return None

    def convert(self, mode: str):
        self.mode = mode
        return self

    def point(self, fn):
        return self


class FakeImage:
    @staticmethod
    def open(path):
        return FakePilImage()


def install_fake_pil(monkeypatch) -> None:
    pil = types.ModuleType("PIL")
    pil.Image = FakeImage
    monkeypatch.setitem(sys.modules, "PIL", pil)


def test_ac5_ocr_node_is_registered_under_actual_id():
    assert OcrNode.id == "access.ocr"
    assert registry.get("access.ocr") is OcrNode


def test_ac6_run_dry_run_execute_preview_are_in_class_body():
    body = OcrNode.__dict__
    for name in ("run", "dry_run", "_execute", "_preview"):
        assert name in body, f"OcrNode must define {name} in its class body"


def test_ac6_dry_run_does_not_raise_notimplemented(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    result = OcrNode().dry_run(make_ctx(tmp_path), {"paths": str(p)})
    assert result["items"][0]["bytes"] == 1


def test_ac7_dry_run_does_not_read_bytes_or_import_pytesseract(
    tmp_path, monkeypatch
):
    p = tmp_path / "a.png"
    p.write_bytes(b"abc")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    monkeypatch.setitem(sys.modules, "pytesseract", None)

    def boom(*args, **kwargs):
        raise AssertionError("dry_run must not call safe_fs.read_bytes")

    monkeypatch.setattr(safe_fs, "read_bytes", boom)

    result = OcrNode().dry_run(
        make_ctx(tmp_path), {"paths": [str(p), str(pdf)]}
    )
    items = result["items"]
    assert items[0]["bytes"] == 3
    assert items[0]["text"] is None
    assert items[0]["confidence"] is None
    assert "format not supported in 6c" in items[1]["error"]


def test_ac8_dry_run_fields_and_structural_errors(tmp_path):
    ok = tmp_path / "ok.png"
    ok.write_bytes(b"1234")
    missing = tmp_path / "missing.png"
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")

    result = OcrNode().dry_run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(missing), str(pdf)]},
    )
    by_name = {Path(item["path"]).name: item for item in result["items"]}

    assert by_name["ok.png"]["text"] is None
    assert by_name["ok.png"]["confidence"] is None
    assert by_name["ok.png"]["bytes"] == 4
    assert by_name["ok.png"]["error"] is None

    assert by_name["missing.png"]["error"]
    assert "format not supported in 6c: .pdf" in by_name["doc.pdf"]["error"]


def test_ac9_run_with_mocked_pytesseract(tmp_path, monkeypatch):
    p = tmp_path / "a.png"
    p.write_bytes(b"abc")
    fake = make_fake_pytesseract(text="hello", conf=["90", "80", "-1"])
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    result = OcrNode().run(make_ctx(tmp_path), {"paths": str(p)})
    item = result["items"][0]

    assert item["text"] == "hello"
    assert isinstance(item["confidence"], float)
    assert item["confidence"] == pytest.approx(85.0)
    assert item["lang"] == "eng"
    assert item["bytes"] == 3
    assert item["error"] is None


def test_ac10_r8_tesseract_binary_missing_is_per_item_error(
    tmp_path, monkeypatch
):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    fake = make_fake_pytesseract(not_found=True)
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    result = OcrNode().run(make_ctx(tmp_path), {"paths": str(p)})
    item = result["items"][0]

    assert "docs/ocr-setup.md" in item["error"]
    assert item["text"] is None


def test_ac11_r11_language_pack_error_is_per_item_error(
    tmp_path, monkeypatch
):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    fake = make_fake_pytesseract(
        tesseract_error="Failed loading language 'rus'"
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    result = OcrNode().run(
        make_ctx(tmp_path), {"paths": str(p), "lang": "rus"}
    )
    item = result["items"][0]

    assert "Failed loading language 'rus'" in item["error"]
    assert item["text"] is None


@pytest.mark.parametrize("dry_run", [False, True])
def test_ac12_r9_pdf_is_unsupported_in_6c(tmp_path, monkeypatch, dry_run):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    if not dry_run:
        monkeypatch.setitem(
            sys.modules, "pytesseract", make_fake_pytesseract()
        )

    node = OcrNode()
    result = (
        node.dry_run(make_ctx(tmp_path), {"paths": str(pdf)})
        if dry_run
        else node.run(make_ctx(tmp_path), {"paths": str(pdf)})
    )
    item = result["items"][0]
    assert "format not supported in 6c: .pdf" in item["error"]


def test_ac16_per_item_errors_do_not_abort_the_node(tmp_path, monkeypatch):
    ok = tmp_path / "ok.png"
    ok.write_bytes(b"ok")
    missing = tmp_path / "missing.png"
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    monkeypatch.setitem(
        sys.modules, "pytesseract", make_fake_pytesseract(text="ok text")
    )

    result = OcrNode().run(
        make_ctx(tmp_path),
        {"paths": [str(ok), str(missing), str(pdf)]},
    )
    items = result["items"]

    assert len(items) == 3
    assert sum(item["text"] is not None for item in items) == 1
    assert sum(bool(item.get("error")) for item in items) == 2


def test_ac15_scope_check_write_false_for_each_input_path(
    tmp_path, monkeypatch
):
    ok = tmp_path / "ok.png"
    ok.write_bytes(b"ok")
    missing = tmp_path / "missing.png"
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.png"
    outside.write_bytes(b"x")

    ctx = make_ctx(tmp_path)
    checked: list[tuple[Path, bool]] = []
    original_check = ctx.scope.check

    def recording_check(path, write=False):
        checked.append((Path(path), write))
        return original_check(path, write=write)

    ctx.scope.check = recording_check
    monkeypatch.setitem(
        sys.modules, "pytesseract", make_fake_pytesseract(text="ok text")
    )

    OcrNode().run(
        ctx,
        {"paths": [str(ok), str(missing), str(pdf), str(outside)]},
    )

    attempted = {str(path) for path, _write in checked}
    assert {str(ok), str(missing), str(pdf), str(outside)} <= attempted
    assert all(write is False for _path, write in checked)


@pytest.mark.parametrize(
    "preprocessing", ["none", "grayscale", "grayscale+threshold"]
)
def test_ac14_preprocessing_modes(tmp_path, monkeypatch, preprocessing):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    fake = make_fake_pytesseract(text="t")
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    if preprocessing != "none":
        install_fake_pil(monkeypatch)

    result = OcrNode().run(
        make_ctx(tmp_path),
        {"paths": str(p), "preprocessing": preprocessing},
    )
    item = result["items"][0]
    assert item["text"] == "t"
    assert item.get("error") is None

    call = next(c for c in fake._calls if c[0] == "image_to_string")
    image_input = call[1]
    if preprocessing == "none":
        assert isinstance(image_input, str)
    else:
        assert not isinstance(image_input, str)
        assert image_input.mode == "L"


def test_validation_no_paths_raises(tmp_path):
    with pytest.raises(NodeExecutionError, match="no paths"):
        OcrNode().run(make_ctx(tmp_path), {"paths": []})


def test_validation_invalid_psm_raises(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    with pytest.raises(NodeExecutionError, match="psm"):
        OcrNode().run(make_ctx(tmp_path), {"paths": str(p), "psm": "bad"})


def test_validation_invalid_preprocessing_raises(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    with pytest.raises(NodeExecutionError, match="preprocessing"):
        OcrNode().run(
            make_ctx(tmp_path),
            {"paths": str(p), "preprocessing": "bad"},
        )
