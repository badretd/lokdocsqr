"""System dependency checker for LokDocSQR.

Lives under ``scripts/`` on purpose: it is NOT part of the
``lokdocsqr`` package and therefore falls outside the architecture
contract enforced by ``tests/test_architecture.py``.

This tool NEVER installs anything. It only reports whether required
system binaries are available, prints ``--version`` when present, and
emits a platform-agnostic install hint on failure.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Iterable


# Required system binaries. Extend this list when new nodes grow a
# system-level dependency (e.g. ``ffmpeg`` for a future Convert File).
BINARIES: list[tuple[str, str]] = [
    ("tesseract", "OCR engine (used by nodes/llm and nodes/access/ocr)"),
]

INSTALL_HINT = (
    "Install hints: see docs/ocr-setup.md "
    "(apt / brew / choco, plus a link to official releases)."
)


def _probe(binary: str) -> tuple[bool, str]:
    """Return (available, version_line). Never raises."""
    if shutil.which(binary) is None:
        return False, ""
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"failed to execute: {exc}"
    stream = (proc.stdout or proc.stderr or "").strip()
    first_line = stream.splitlines()[0] if stream else "(no version output)"
    return proc.returncode == 0, first_line


def main(argv: Iterable[str] | None = None) -> int:
    del argv  # unused; keeps the entry point uniform
    missing: list[str] = []
    for binary, purpose in BINARIES:
        ok, version = _probe(binary)
        if ok:
            print(f"[OK]   {binary}: {version}  ({purpose})")
        else:
            missing.append(binary)
            print(f"[MISS] {binary}: not available  ({purpose})")
    if missing:
        print(file=sys.stderr)
        print(
            f"Missing system dependencies: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
