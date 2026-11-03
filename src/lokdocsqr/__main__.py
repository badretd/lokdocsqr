"""Allow ``python -m lokdocsqr`` to run the CLI."""
from __future__ import annotations

from lokdocsqr.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
