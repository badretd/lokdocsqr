"""OCR node.

Node id: ``access.ocr``. Extracts text from image files using
Tesseract OCR via ``pytesseract``.

pip deps: lokdocsqr[ocr] (pytesseract, Pillow) — lazy, optional

Lazy import policy (R7)
-----------------------
Neither this module nor :mod:`nodes.access.extract_text` may fail to
import when the optional ``[ocr]`` extra is missing. Module level only
computes an availability flag via ``importlib.util.find_spec``; the
actual ``import pytesseract`` / ``from PIL import Image`` happens inside
the execution body (``_execute`` → ``_run_item``), never inside
``_preview``.

Failure modes (all per-item; the node never crashes)
----------------------------------------------------
* R8  — ``tesseract`` system binary missing: ``pytesseract`` raises
  ``TesseractNotFoundError``; converted to a per-item ``error`` string
  pointing at ``docs/ocr-setup.md``. A single preflight via
  ``pytesseract.get_tesseract_version()`` runs once per node (not per
  file) so all items report the same structural error without launching
  a subprocess per file.
* R9  — non-image formats (including ``.pdf`` / ``.docx``) are rejected
  per-item with ``format not supported in 6c: <ext>``. PDF/DOCX is 6d.
* R10 — tests mock ``pytesseract``; production code never depends on a
  specific Tesseract version.
* R11 — missing language pack: ``pytesseract`` raises
  ``TesseractError``; converted to a per-item ``error`` carrying the
  Tesseract message.
* R12 — ``preprocessing`` ∈ ``{"none", "grayscale",
  "grayscale+threshold"}``; Pillow is imported lazily and only when
  ``preprocessing != "none"``. With ``"none"`` the path is passed
  straight to ``pytesseract`` (it opens the file itself), so Pillow is
  not strictly required for that path.

``dry_run`` (:meth:`OcrNode._preview`) never imports ``pytesseract`` /
``PIL`` and never calls ``safe_fs.read_bytes``; it only stats each
path and reports structural errors (missing file, unsupported format)
so the user sees them before running.
"""
from __future__ import annotations

import importlib.util
import stat as stat_module
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase, AccessOcrUnavailableError


try:  # R7: no actual import — just availability check
    _PYTESSERACT_AVAILABLE: bool = (
        importlib.util.find_spec("pytesseract") is not None
    )
except (ImportError, ValueError):  # pragma: no cover — hostile envs
    _PYTESSERACT_AVAILABLE = False


_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "tif", "tiff", "bmp"})
_PREPROCESSING_CHOICES = ("none", "grayscale", "grayscale+threshold")
_INSTALL_HINT = "pytesseract/Pillow not installed — install lokdocsqr[ocr]"
_TESSERACT_MISSING = "tesseract binary not found — see docs/ocr-setup.md"


def _require_pytesseract() -> Any:
    """Lazily import ``pytesseract``; raise :class:`AccessOcrUnavailableError`."""
    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AccessOcrUnavailableError(_INSTALL_HINT) from exc
    return pytesseract


def _is_tesseract_not_found(exc: BaseException, pytesseract: Any) -> bool:
    """Return True if ``exc`` is a pytesseract.TesseractNotFoundError.

    Uses ``isinstance`` when the attribute exists, falls back to the
    class-name check so that mocks which only expose the class name
    still trigger the same per-item path.
    """
    cls = getattr(pytesseract, "TesseractNotFoundError", None)
    if cls is not None and isinstance(exc, cls):
        return True
    return type(exc).__name__ == "TesseractNotFoundError"


def _preflight_tesseract(pytesseract: Any) -> str | None:
    """Return error string if the tesseract binary is unavailable, else None.

    Called exactly once per node (from ``_process`` in run mode). Skipped
    when the mock does not expose ``get_tesseract_version``.
    """
    getter = getattr(pytesseract, "get_tesseract_version", None)
    if getter is None:
        return None
    try:
        getter()
    except Exception as exc:  # noqa: BLE001 — preflight boundary
        if _is_tesseract_not_found(exc, pytesseract):
            return _TESSERACT_MISSING
        return str(exc)
    return None


@register_node
class OcrNode(AccessBase):
    """OCR images via Tesseract; per-item errors, lazy pytesseract import."""

    id = "access.ocr"
    title = "OCR"
    inputs = {"paths": "str|list[str]"}
    outputs = {"items": "OcrItem[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        lang: str = "eng",
        psm: int = 3,
        oem: int = 3,
        max_bytes: int = 20 * 1024 * 1024,
        preprocessing: str = "none",
    ) -> None:
        self.paths = paths
        self.lang = lang
        self.psm = psm
        self.oem = oem
        self.max_bytes = max_bytes
        self.preprocessing = preprocessing

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=False)

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=True)

    # ── helpers ─────────────────────────────────────────────────
    @staticmethod
    def _raw_paths(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        raise NodeExecutionError(
            "ocr: 'paths' must be str, Path, or list of those"
        )

    @staticmethod
    def _ext_of(path: Path) -> str:
        return path.suffix.lower().lstrip(".")

    @classmethod
    def _is_image(cls, path: Path) -> bool:
        return cls._ext_of(path) in _IMAGE_EXTENSIONS

    @staticmethod
    def _unsupported_format_error(path: Path) -> str:
        ext = path.suffix.lower().lstrip(".")
        if ext:
            return f"format not supported in 6c: .{ext}"
        return "format not supported in 6c: <no extension>"

    @staticmethod
    def _error_item(
        path: Any,
        exc_or_msg: Any,
        *,
        lang: str,
        size: int | None = None,
    ) -> dict[str, Any]:
        message = (
            exc_or_msg if isinstance(exc_or_msg, str) else str(exc_or_msg)
        )
        return {
            "path": str(path),
            "text": None,
            "confidence": None,
            "lang": lang,
            "bytes": size,
            "error": message,
        }

    # ── preview (dry-run; no pytesseract / PIL, no read_bytes) ─
    def _preview_item(self, path: Path, lang: str) -> dict[str, Any]:
        item: dict[str, Any] = {
            "path": str(path),
            "text": None,
            "confidence": None,
            "lang": lang,
            "bytes": None,
            "error": None,
        }
        if not self._is_image(path):
            item["error"] = self._unsupported_format_error(path)
            return item
        try:
            st = safe_fs.stat(path)
        except LokError as exc:
            item["error"] = str(exc)
            return item
        if stat_module.S_ISDIR(st.st_mode):
            item["error"] = f"{path}: is a directory"
            return item
        item["bytes"] = st.st_size
        return item

    # ── execution (run; may lazily import pytesseract/PIL) ─────
    def _run_item(
        self,
        path: Path,
        lang: str,
        psm: int,
        oem: int,
        max_bytes: int,
        preprocessing: str,
        pytesseract: Any,
    ) -> dict[str, Any]:
        if not self._is_image(path):
            return self._error_item(
                path, self._unsupported_format_error(path), lang=lang
            )

        try:
            st = safe_fs.stat(path)
        except LokError as exc:
            return self._error_item(path, exc, lang=lang)
        if stat_module.S_ISDIR(st.st_mode):
            return self._error_item(
                path, f"{path}: is a directory", lang=lang
            )
        if st.st_size > max_bytes:
            return self._error_item(
                path,
                f"{path}: size {st.st_size} exceeds max_bytes {max_bytes}",
                lang=lang,
                size=st.st_size,
            )

        image_input: Any
        if preprocessing == "none":
            image_input = str(path)
        else:
            try:
                from PIL import Image  # type: ignore[import-not-found]
            except ImportError:
                return self._error_item(path, _INSTALL_HINT, lang=lang)
            try:
                pil_img = Image.open(str(path))
                pil_img.load()
            except Exception as exc:  # noqa: BLE001 — per-item boundary
                return self._error_item(
                    path, exc, lang=lang, size=st.st_size
                )
            if preprocessing == "grayscale":
                image_input = pil_img.convert("L")
            elif preprocessing == "grayscale+threshold":
                image_input = pil_img.convert("L").point(
                    lambda x: 0 if x < 128 else 255
                )
            else:  # unreachable — _process validates first
                return self._error_item(
                    path,
                    f"ocr: unsupported preprocessing {preprocessing!r}",
                    lang=lang,
                )

        config = f"--psm {psm} --oem {oem}"

        try:
            text = pytesseract.image_to_string(
                image_input, lang=lang, config=config
            )
        except Exception as exc:  # noqa: BLE001 — per-item boundary
            if _is_tesseract_not_found(exc, pytesseract):
                return self._error_item(
                    path,
                    _TESSERACT_MISSING,
                    lang=lang,
                    size=st.st_size,
                )
            return self._error_item(
                path, exc, lang=lang, size=st.st_size
            )

        confidence: float | None = None
        try:
            data_dict = pytesseract.image_to_data(
                image_input,
                lang=lang,
                config=config,
                output_type=pytesseract.Output.DICT,
            )
            confs: list[float] = []
            for c in data_dict.get("conf", []) or []:
                try:
                    value = float(c)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    confs.append(value)
            if confs:
                confidence = sum(confs) / len(confs)
        except Exception:  # noqa: BLE001 — confidence is best-effort
            confidence = None

        return {
            "path": str(path),
            "text": text,
            "confidence": confidence,
            "lang": lang,
            "bytes": st.st_size,
            "error": None,
        }

    # ── orchestration ───────────────────────────────────────────
    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        lang = str(payload.get("lang", self.lang))
        try:
            psm = int(payload.get("psm", self.psm))
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError("ocr: psm must be an integer") from exc
        try:
            oem = int(payload.get("oem", self.oem))
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError("ocr: oem must be an integer") from exc
        try:
            max_bytes = int(payload.get("max_bytes", self.max_bytes))
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError(
                "ocr: max_bytes must be an integer"
            ) from exc
        if max_bytes < 0:
            raise NodeExecutionError(
                "ocr: max_bytes must be non-negative"
            )
        preprocessing = str(
            payload.get("preprocessing", self.preprocessing)
        )
        if preprocessing not in _PREPROCESSING_CHOICES:
            raise NodeExecutionError(
                "ocr: preprocessing must be one of "
                f"{list(_PREPROCESSING_CHOICES)}"
            )

        raw_paths = self._raw_paths(payload.get("paths", self.paths))
        if not raw_paths:
            raise NodeExecutionError("ocr: no paths provided")

        # Preflight (run mode only): import once, check tesseract once.
        pytesseract: Any = None
        preflight_error: str | None = None
        if not dry_run:
            try:
                pytesseract = _require_pytesseract()
            except AccessOcrUnavailableError as exc:
                preflight_error = str(exc)
            else:
                preflight_error = _preflight_tesseract(pytesseract)

        items: list[dict[str, Any]] = []
        for raw in raw_paths:
            try:
                resolved = self._resolve_input(ctx, raw)
            except LokError as exc:
                items.append(self._error_item(raw, exc, lang=lang))
                continue
            for path in resolved:
                if dry_run:
                    items.append(self._preview_item(path, lang))
                elif preflight_error is not None:
                    items.append(
                        self._error_item(path, preflight_error, lang=lang)
                    )
                else:
                    items.append(
                        self._run_item(
                            path,
                            lang,
                            psm,
                            oem,
                            max_bytes,
                            preprocessing,
                            pytesseract,
                        )
                    )

        if not items:
            raise NodeExecutionError("ocr: no paths provided")
        return {"items": items}


__all__ = ["OcrNode"]
