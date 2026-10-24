#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def count_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {Path(sys.argv[0]).name} <directory>", file=sys.stderr)
        return 2

    directory = Path(sys.argv[1]).expanduser()
    if not directory.is_dir():
        print(f"Error: '{directory}' is not a directory", file=sys.stderr)
        return 2

    total = sum(count_lines(p) for p in directory.rglob("*.py") if p.is_file())
    print(total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())