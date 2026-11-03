"""PyInstaller entry script for the LokDocSQR CLI binary.

PyInstaller cannot use ``pyproject.toml`` console-script metadata directly, so
this file is the frozen equivalent of ``lokdocsqr.cli.main:main``.
"""

from __future__ import annotations

import multiprocessing

from lokdocsqr.cli.main import main

if __name__ == "__main__":
    # Required for frozen applications that ever start worker processes
    # (the Phase 10 runtime uses threads today; this keeps the entry point
    # future-proof and is harmless for thread-only builds).
    multiprocessing.freeze_support()
    raise SystemExit(main())
