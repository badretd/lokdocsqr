"""Phase 6c — ExtractText image handlers and OCR dispatch tests."""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.extract_text import (
    ExtractTextNode,
    HANDLERS,
    register_handler,
)


def make_ctx(tmp_path: Path):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path],
        writable=[tmp_path],
        allow_symlinks=False,
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
        audit=SimpleNamespace(append=lambda log: None),
    )


class FakePilImage:
    def load(self) -> None:
        return None


class FakeImage:
    @staticmethod
    def open(fp):
        return FakePilImage()


def install_fake_pil(monkeypatch) -> None:
    pil = types.ModuleType("PIL")
    pil.Image = FakeImage
    monkeypatch.setitem(sys.modules, "PIL", pil)


def install_fake_pytesseract(monkeypatch, text: str = "ocr from extract"):
    mod = types.ModuleType("pytesseract")

    class TesseractNotFoundError(Exception):
        pass

    mod.TesseractNotFoundError = TesseractNotFoundError

    def image_to_string(image, lang="eng", config=""):
        return text

    mod.image_to_string = image_to_string
    monkeypatch.setitem(sys.modules, "pytesseract", mod)
    return mod


@pytest.mark.parametrize("ext", ["png", "jpg", "jpeg", "tif", "tiff", "bmp"])
def test_ac13_image_handlers_are_registered(ext: str):
    assert ext in HANDLERS
    assert callable(HANDLERS[ext])


def test_ac13_register_handler_is_module_level_function():
    assert inspect.isfunction(register_handler)
    assert not inspect.ismethod(register_handler)
    assert register_handler.__module__.endswith("nodes.access.extract_text")


def test_ac13_extract_text_png_dispatch_uses_ocr(tmp_path, monkeypatch):
    png = tmp_path / "a.png"
    png.write_bytes(b"fake png bytes")
    install_fake_pil(monkeypatch)
    install_fake_pytesseract(monkeypatch, text="ocr text")

    result = ExtractTextNode().run(make_ctx(tmp_path), {"paths": str(png)})
    item = result["items"][0]

    assert item["text"] == "ocr text"
    assert item.get("error") is None


def test_ac13_extract_text_png_without_pytesseract_is_per_item_error(
    tmp_path, monkeypatch
):
    png = tmp_path / "a.png"
    png.write_bytes(b"x")
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    result = ExtractTextNode().run(make_ctx(tmp_path), {"paths": str(png)})
    item = result["items"][0]

    assert "pytesseract/Pillow not installed" in item["error"]
    assert item["text"] is None
