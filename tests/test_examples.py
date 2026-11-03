"""Keep the shipped ``examples/*.ldsq`` automations valid and runnable.

Every example must compile, validate, and complete a dry-run against a copy of
``examples/sample_data``. Dry-run is used so the test never mutates the
repository tree.
"""
from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
SAMPLE_DATA = EXAMPLES / "sample_data"

cli_main = importlib.import_module("lokdocsqr.cli.main")

LDSQ_FILES = sorted(EXAMPLES.glob("*.ldsq"))


def test_examples_directory_has_automations() -> None:
    assert LDSQ_FILES, "no .ldsq examples found"
    assert (EXAMPLES / "README.md").is_file(), "examples/README.md is missing"
    assert SAMPLE_DATA.is_dir(), "examples/sample_data is missing"


@pytest.mark.parametrize("path", LDSQ_FILES, ids=[p.name for p in LDSQ_FILES])
def test_example_validates(path: Path) -> None:
    assert cli_main.main(["validate", str(path)]) == 0


@pytest.mark.parametrize("path", LDSQ_FILES, ids=[p.name for p in LDSQ_FILES])
def test_example_dry_runs(path: Path, tmp_path: Path, capsys) -> None:
    base = tmp_path / "workspace"
    shutil.copytree(SAMPLE_DATA, base)

    assert (
        cli_main.main(
            [
                "run",
                str(path),
                "--base-dir",
                str(base),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True, result.get("errors")
    assert result["dry_run"] is True
