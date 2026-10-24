"""Tests for lokdocsqr.core.config."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lokdocsqr.core.config import AppConfig, load_config, save_config
from lokdocsqr.core.errors import ConfigError


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------

def test_app_config_defaults(tmp_path: Path) -> None:
    cfg = AppConfig(base_dir=tmp_path)
    assert cfg.base_dir == tmp_path
    assert cfg.trash_dir == tmp_path / ".trash"
    assert cfg.logs_dir == tmp_path / ".logs"
    assert cfg.scope_roots == []
    assert cfg.default_dry_run is True


def test_app_config_explicit_paths(tmp_path: Path) -> None:
    cfg = AppConfig(
        base_dir=tmp_path,
        trash_dir=tmp_path / "custom_trash",
        logs_dir=tmp_path / "custom_logs",
        scope_roots=[tmp_path / "a", tmp_path / "b"],
        default_dry_run=False,
    )
    assert cfg.trash_dir == tmp_path / "custom_trash"
    assert cfg.logs_dir == tmp_path / "custom_logs"
    assert cfg.scope_roots == [tmp_path / "a", tmp_path / "b"]
    assert cfg.default_dry_run is False


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_valid(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(
        json.dumps(
            {
                "base_dir": str(tmp_path),
                "trash_dir": str(tmp_path / "trash"),
                "logs_dir": str(tmp_path / "logs"),
                "scope_roots": [str(tmp_path / "a")],
                "default_dry_run": False,
            }
        )
    )
    cfg = load_config(path)
    assert cfg.base_dir == tmp_path
    assert cfg.scope_roots == [tmp_path / "a"]
    assert cfg.default_dry_run is False


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.json")


def test_load_config_invalid_json_raises(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text("{not json")
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_missing_base_dir_raises(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps({"trash_dir": "x"}))
    with pytest.raises(ConfigError):
        load_config(path)


def test_load_config_not_object_raises(tmp_path: Path) -> None:
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(["not", "a", "dict"]))
    with pytest.raises(ConfigError):
        load_config(path)


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

def test_save_config_roundtrip(tmp_path: Path) -> None:
    cfg = AppConfig(
        base_dir=tmp_path,
        scope_roots=[tmp_path / "a", tmp_path / "b"],
        default_dry_run=False,
    )
    path = tmp_path / "cfg.json"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.base_dir == cfg.base_dir
    assert loaded.trash_dir == cfg.trash_dir
    assert loaded.logs_dir == cfg.logs_dir
    assert loaded.scope_roots == cfg.scope_roots
    assert loaded.default_dry_run == cfg.default_dry_run


def test_save_config_no_temp_files_left(tmp_path: Path) -> None:
    cfg = AppConfig(base_dir=tmp_path)
    save_config(cfg, tmp_path / "cfg.json")
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert leftovers == []
