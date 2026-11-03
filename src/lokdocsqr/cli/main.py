"""LokDocSQR command-line entry point."""
from __future__ import annotations

import argparse
import sys

from lokdocsqr import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lokdocsqr",
        description="LokDocSQR local automation engine.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI.

    Phase 8.7 only guarantees a working entry point and ``--version``;
    the automation ``run``/``serve`` commands arrive in Phase 11.
    """
    parser = build_parser()
    parser.parse_args(sys.argv[1:] if argv is None else argv)
    parser.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
