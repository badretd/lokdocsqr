"""Application configuration load/save.

Writes go through core.safe_fs.atomic_write (architectural invariant).
Reads use pathlib + json only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from lokdocsqr.core.errors import ConfigError
from lokdocsqr.core.safe_fs import atomic_write


@dataclass
class AppConfig:
    """Top-level application configuration.

    ``trash_dir`` and ``logs_dir`` default to ``base_dir / ".trash"`` and
    ``base_dir / ".logs"`` respectively; the defaults are materialised in
    ``__post_init__`` so that the field type stays ``Path`` for callers.
    """

    base_dir: Path
    trash_dir: Path = field(default_factory=Path)
    logs_dir: Path = field(default_factory=Path)
    scope_roots: list[Path] = field(default_factory=list)
    default_dry_run: bool = True

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir)
        if self.trash_dir == Path():
            self.trash_dir = self.base_dir / ".trash"
        else:
            self.trash_dir = Path(self.trash_dir)
        if self.logs_dir == Path():
            self.logs_dir = self.base_dir / ".logs"
        else:
            self.logs_dir = Path(self.logs_dir)
        self.scope_roots = [Path(p) for p in self.scope_roots]


def load_config(path: Path) -> AppConfig:
    """Load configuration from a JSON file.

    Raises ConfigError on any structural or I/O problem.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Config root must be a JSON object: {path}")

    try:
        base_raw = data["base_dir"]
    except KeyError as exc:
        raise ConfigError(f"Missing required key 'base_dir' in {path}") from exc

    try:
        return AppConfig(
            base_dir=Path(base_raw),
            trash_dir=Path(data["trash_dir"]) if data.get("trash_dir") else Path(),
            logs_dir=Path(data["logs_dir"]) if data.get("logs_dir") else Path(),
            scope_roots=[Path(p) for p in data.get("scope_roots", [])],
            default_dry_run=bool(data.get("default_dry_run", True)),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid config structure in {path}: {exc}") from exc


def save_config(config: AppConfig, path: Path) -> None:
    """Serialize configuration to JSON atomically via safe_fs.atomic_write."""
    path = Path(path)
    data = {
        "base_dir": str(config.base_dir),
        "trash_dir": str(config.trash_dir),
        "logs_dir": str(config.logs_dir),
        "scope_roots": [str(p) for p in config.scope_roots],
        "default_dry_run": bool(config.default_dry_run),
    }
    text = json.dumps(data, indent=2, sort_keys=True) + ""
    atomic_write(path, text)
