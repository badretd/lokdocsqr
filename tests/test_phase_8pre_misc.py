"""Cross-cutting: pyproject, baseline regression, architecture (SPEC §general)."""
import subprocess
import sys
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent

PHASE_8PRE_FILES = [
    "tests/test_phase_8pre_clock.py",
    "tests/test_phase_8pre_catalog.py",
    "tests/test_phase_8pre_ctx.py",
    "tests/test_phase_8pre_retry_safe.py",
    "tests/test_phase_8pre_retry_policy.py",
    "tests/test_phase_8pre_registry.py",
    "tests/test_phase_8pre_misc.py",
]


def test_pyproject_diff_empty_vs_phase7():
    res = subprocess.run(
        ["git", "diff", "phase-7", "--", "pyproject.toml"],
        cwd=str(PROJ), capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        pytest.skip(f"git/phase-7 not available: {res.stderr.strip()}")
    assert res.stdout.strip() == "", f"pyproject.toml changed:{res.stdout}"


def test_architecture_green():
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_architecture.py", "-q", "--no-header"],
        cwd=str(PROJ), capture_output=True, text=True, timeout=180,
    )
    assert res.returncode == 0, \
        f"architecture failed:\n{res.stdout[-3000:]}\n{res.stderr[-1000:]}"


def test_baseline_0_7_regression():
    """Phase-7 baseline (930) must remain green; phase-8pre files excluded here."""
    args = [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"]
    for f in PHASE_8PRE_FILES:
        args.extend(["--ignore", f])
    res = subprocess.run(args, cwd=str(PROJ), capture_output=True, text=True, timeout=900)
    assert res.returncode == 0, \
        f"baseline regression red:\n{res.stdout[-3000:]}\n{res.stderr[-1000:]}"
