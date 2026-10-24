#!/usr/bin/env python3
"""
project_map.py — Builds a single file containing the contents of all .py files
in a project.

Usage:
    python project_map.py [project_root] [-o output_file]

Defaults:
    - project root = current directory
    - output file  = project_map.txt
"""

import argparse
import sys
from pathlib import Path

# Directories to skip entirely
EXCLUDE_DIRS = {
    ".venv", "venv", "env", ".env",
    "__pycache__", ".git", ".idea", ".vscode",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "node_modules", "build", "dist", ".eggs",
}

# File extensions to include
INCLUDE_EXTENSIONS = {".py", ".json"}


def should_skip_dir(path: Path) -> bool:
    return path.name in EXCLUDE_DIRS


def collect_files(root: Path):
    """Recursively walk root and return a sorted list of .py files."""
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in INCLUDE_EXTENSIONS:
            continue
        # Skip files located inside excluded directories
        if any(part in EXCLUDE_DIRS for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return sorted(files, key=lambda p: str(p.relative_to(root)))


def build_map(root: Path, output: Path) -> int:
    files = collect_files(root)

    if not files:
        print(f"Warning: no .py files found in {root} (after exclusions).")
        return 0

    with output.open("w", encoding="utf-8") as f:
        f.write("# Project map (Python)\n")
        f.write(f"# Root: {root.resolve()}\n")
        f.write(f"# Files: {len(files)}\n")
        f.write("=" * 80 + "\n\n")

        for file_path in files:
            rel = file_path.relative_to(root).as_posix()
            f.write(f"{'=' * 80}\n")
            f.write(f"### FILE: {rel}\n")
            f.write(f"{'=' * 80}\n")

            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                content = f"<<Error reading file: {e}>>"

            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
            f.write("\n\n")

    return len(files)


def main():
    parser = argparse.ArgumentParser(
        description="Collect all .py files in a project into a single map file."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Project root (default: current directory)",
    )
    parser.add_argument(
        "-o", "--output",
        default="project_map.txt",
        help="Output file name (default: project_map.txt)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Error: path is not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output).resolve()
    count = build_map(root, output)

    if count:
        size_kb = output.stat().st_size / 1024
        print(f"Done: {output}")
        print(f"Files: {count}, size: {size_kb:.1f} KB")


if __name__ == "__main__":
    main()