"""Allow ``python -m lokdocsqr.cli`` to run the CLI."""

from __future__ import annotations

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
