"""Safe filesystem operations.

This is the single module in the project allowed to touch the filesystem.
It is the most carefully written module in the codebase.

Invariants (enforced by tests/test_architecture.py):
    * No public deletion API is exported (no delete/remove/unlink/rmtree names).
    * File writes are atomic: temp file -> write -> flush -> fsync -> os.replace.
    * Deletions are implemented as quarantine/trash (moves into trash_dir).
    * Cross-device ``safe_move`` is the only place that internally removes a
      source path, and only after ``copy_file`` has durably persisted the
      destination data. This is the minimum necessary to complete a genuine
      move across volumes and is documented in ``_complete_cross_device_move``.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from lokdocsqr.core.errors import SafeFsError


_CHUNK_SIZE = 1024 * 1024  # 1 MiB


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: str | bytes, encoding: str = "utf-8") -> None:
    """Atomically write ``data`` to ``path``.

    Uses a temporary file in the same directory so that ``os.replace`` is
    atomic. If ``os.replace`` succeeds, the temporary file no longer exists.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SafeFsError(f"atomic_write: cannot create parent of {path}: {exc}") from exc

    tmp = path.parent / f".{path.name}.tmp.{os.getpid()}.{time.time_ns()}"
    try:
        if isinstance(data, bytes):
            with open(tmp, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with open(tmp, "w", encoding=encoding) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise SafeFsError(f"atomic_write failed for {path}: {exc}") from exc


def atomic_append(path: Path, data: str | bytes, encoding: str = "utf-8") -> None:
    """Append ``data`` to ``path`` with flush + fsync."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            with open(path, "ab") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with open(path, "a", encoding=encoding) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
    except OSError as exc:
        raise SafeFsError(f"atomic_append failed for {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Copy / move / rename
# ---------------------------------------------------------------------------

def copy_file(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst`` byte-for-byte, flushing and fsyncing the target.

    Performs a best-effort free-space check on the destination volume.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise SafeFsError(f"copy_file: source does not exist: {src}")

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src_size = src.stat().st_size
        usage = shutil.disk_usage(dst.parent)
        if usage.free < src_size:
            raise SafeFsError(
                f"copy_file: insufficient space on {dst.parent}: "
                f"need {src_size}, free {usage.free}"
            )
    except OSError as exc:
        raise SafeFsError(f"copy_file: precheck failed for {dst}: {exc}") from exc

    try:
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(_CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
            fdst.flush()
            os.fsync(fdst.fileno())
    except OSError as exc:
        raise SafeFsError(f"copy_file {src} -> {dst}: {exc}") from exc


def _complete_cross_device_move(src: Path) -> None:
    """Remove the source after a successful cross-device copy.

    This is the single internal deletion performed by safe_fs and is only
    reached after ``copy_file`` has fully persisted the destination data.
    Renaming across volumes is impossible without removing the source, so
    this helper exists specifically to complete ``safe_move`` across devices.
    """
    try:
        src.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SafeFsError(
            f"cross-device move: source cleanup failed for {src}: {exc}"
        ) from exc


def safe_move(src: Path, dst: Path) -> None:
    """Move ``src`` to ``dst``, falling back to copy+remove across volumes."""
    src = Path(src)
    dst = Path(dst)
    if not src.exists():
        raise SafeFsError(f"safe_move: source does not exist: {src}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SafeFsError(f"safe_move: cannot create parent of {dst}: {exc}") from exc

    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise SafeFsError(f"safe_move {src} -> {dst}: {exc}") from exc

    copy_file(src, dst)
    _complete_cross_device_move(src)


def safe_rename(path: Path, new_name: str) -> Path:
    """Rename ``path`` to ``path.parent / new_name`` in place."""
    path = Path(path)
    if not new_name:
        raise SafeFsError("safe_rename: new_name is empty")
    if "/" in new_name or "\\" in new_name:
        raise SafeFsError(f"safe_rename: new_name must not contain separators: {new_name!r}")
    if new_name in (".", ".."):
        raise SafeFsError(f"safe_rename: new_name cannot be {new_name!r}")
    target = path.parent / new_name
    if target.exists():
        raise SafeFsError(f"safe_rename: target already exists: {target}")
    try:
        os.replace(path, target)
    except OSError as exc:
        raise SafeFsError(f"safe_rename {path} -> {target}: {exc}") from exc
    return target


def make_dir(path: Path) -> None:
    """Create a directory (and parents) if it does not yet exist."""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SafeFsError(f"make_dir failed for {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Quarantine / trash
# ---------------------------------------------------------------------------

def quarantine(path: Path, trash_dir: Path, reason: str = "quarantined") -> Path:
    """Move ``path`` into ``trash_dir`` and write a JSON sidecar."""
    path = Path(path)
    trash_dir = Path(trash_dir)
    if not path.exists():
        raise SafeFsError(f"quarantine: path does not exist: {path}")

    make_dir(trash_dir)
    timestamp = time.time()
    base_target = trash_dir / f"{time.time_ns()}_{path.name}"
    target = base_target
    counter = 0
    while target.exists():
        counter += 1
        target = base_target.with_name(
            f"{base_target.stem}_{counter}{base_target.suffix}"
        )

    safe_move(path, target)

    sidecar = target.parent / f"{target.name}.meta.json"
    meta = {
        "original_path": str(path.absolute()),
        "reason": reason,
        "timestamp": timestamp,
    }
    atomic_write(sidecar, json.dumps(meta, indent=2) + "\n")
    return target


def trash(path: Path, trash_dir: Path) -> Path:
    """Move ``path`` into the trash directory (convenience wrapper)."""
    return quarantine(path, trash_dir, reason="trashed")


# ---------------------------------------------------------------------------
# Read-only helpers
# ---------------------------------------------------------------------------

def stat(path: Path) -> os.stat_result:
    """Return ``os.stat_result`` for ``path`` or raise SafeFsError."""
    path = Path(path)
    try:
        return path.stat()
    except OSError as exc:
        raise SafeFsError(f"stat failed for {path}: {exc}") from exc


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    """Return the hex digest of ``path`` using ``algorithm`` (default sha256)."""
    path = Path(path)
    try:
        digest = hashlib.new(algorithm)
    except ValueError as exc:
        raise SafeFsError(f"hash_file: unknown algorithm {algorithm!r}: {exc}") from exc
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise SafeFsError(f"hash_file failed for {path}: {exc}") from exc
    return digest.hexdigest()


def read_bytes(path: Path, limit: int = -1) -> bytes:
    """Read up to ``limit`` bytes (default: all) from ``path``."""
    path = Path(path)
    try:
        with open(path, "rb") as handle:
            if limit < 0:
                return handle.read()
            return handle.read(limit)
    except OSError as exc:
        raise SafeFsError(f"read_bytes failed for {path}: {exc}") from exc


def list_dir(path: Path) -> list[Path]:
    """Return the sorted entries of directory ``path``."""
    path = Path(path)
    try:
        return sorted(path.iterdir())
    except OSError as exc:
        raise SafeFsError(f"list_dir failed for {path}: {exc}") from exc
