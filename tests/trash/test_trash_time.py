"""Fake-clock test — isolated file so freezegun patch cost is paid once."""
from __future__ import annotations

import pytest
from freezegun import freeze_time

from tests.trash.conftest import run_trash


@freeze_time("2025-01-15 12:00:00")
def test_trash_path_embeds_frozen_timestamp(trash_env):
    src = trash_env.base / "clock.txt"
    src.write_bytes(b"z")
    result = run_trash(trash_env, src)
    joined = f"{result.trash_path} {result.sidecar_path}"
    assert "2025" in joined or "20250115" in joined, (
        "BUG: trash.py — trash_path/sidecar_path do not embed frozen "
        f"timestamp; trash={result.trash_path}, sidecar={result.sidecar_path}"
    )