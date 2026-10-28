"""Safe filesystem operations — mutation layer.

This module is the single place in the project where filesystem **mutations**
happen: writes, atomic replace, move, rename, quarantine/trash, and the
internal source cleanup that completes a cross-device move. Every operation
below is written with the following invariants in mind.

Read-only filesystem access (``Path.exists``, ``Path.read_text``, ``Path.stat``,
``Path.resolve``, ``Path.is_symlink``, ``os.path.*`` normalisation) is
permitted in other production modules — config loading, logging setup, path
resolution, scope checking, the audit reader — because none of those can
corrupt data by themselves. If such a module ever needs to *write*, *move*,
*rename* or *remove*, it must call into this module, not into
``open(..., 'w'/'a')``, ``os.replace``, ``shutil.*`` or ``Path.unlink``
directly.

Known read-only callers (this list is enforced by
``tests/test_architecture.py::test_invariant_9_mutation_ban``):

    * ``core/config.py``  — reads the JSON config via ``path.read_text``;
      writes go through :func:`atomic_write`.
    * ``core/logging.py`` — creates ``logs_dir`` and opens the rotating log
      file directly. This is the single documented exemption: logging must
      bootstrap before :mod:`safe_fs` is usable, and a failure in
      ``safe_fs`` must not be able to silence log output. The exemption is
      scoped to this file only.
    * ``core/paths.py``   — normalises and validates path strings
      (``os.path.abspath``, ``Path.is_absolute``).
    * ``core/scope.py``   — resolves paths and inspects symlinks as part of
      the scope gate, before any mutation is attempted.
    * ``core/audit.py``   — reads via :func:`read_bytes` and appends via
      :func:`atomic_append`; the empty-file probe uses ``path.exists()`` to
      avoid the cost of an exception-driven read.
    * ``core/graph/validator.py`` — inspects path-shaped node parameters as
      strings via ``render_template``; never opens the filesystem.
    * ``core/preview.py`` — reads bytes via :func:`read_bytes` to
      build before/after diffs for Phase 7b previews; never writes.

Invariants (enforced by tests/test_architecture.py):
    * All mutations go through this module. No public deletion API is
      exported (no delete/remove/unlink/rmtree names).
      ``cleanup_stale_temp_files`` is the only public function that removes
      files, and it is strictly bounded to the orphaned-temp pattern produced
      by ``atomic_write`` and to files older than ``max_age_seconds``.
    * File writes are atomic: temp file -> write -> flush -> fsync ->
      ``_safe_replace`` (which wraps ``os.replace`` with Windows-aware
      transient-lock retries bounded by a monotonic-time deadline).
    * Deletions are implemented as quarantine/trash (moves into trash_dir).
    * Cross-device ``safe_move`` is the only place that internally removes a
      source path, and only after ``copy_file`` has durably persisted the
      destination data. This is the minimum necessary to complete a genuine
      move across volumes and is documented in ``_complete_cross_device_move``.

Test code (``tests/``) is exempt from this policy; it uses ``Path.write_text``
and friends freely.

Windows notes
-------------
``os.replace`` on Windows can transiently fail with ``PermissionError``
(``WinError 32`` sharing violation, ``33`` lock violation, ``5`` access
denied, ``170`` busy, ``1175`` unable-to-remove-replaced) while antivirus,
the search indexer, or a sync client holds a handle on either side. The
helpers below retry only for those codes, only up to a hard deadline and
a fixed attempt budget, and never for structural problems (read-only
destination, vanished source, ACL denial that doesn't clear).
"""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import stat as stat_module
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

from lokdocsqr.core.errors import SafeFsError


_log = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Retry policy defaults (per-operation; individual callers can override).
_DEFAULT_REPLACE_TIMEOUT = 10.0
_DEFAULT_REPLACE_MAX_ATTEMPTS = 12
_DEFAULT_REPLACE_INITIAL_DELAY = 0.02
_DEFAULT_REPLACE_MAX_DELAY = 1.0

_DEFAULT_OPEN_TIMEOUT = 5.0
_DEFAULT_UNLINK_TIMEOUT = 5.0

_TEMP_FILE_PATTERN = ".*.tmp.*"

# WinError codes that may clear on their own under Windows.
# 5   ERROR_ACCESS_DENIED              (antivirus / SYSTEM owner race)
# 32  ERROR_SHARING_VIOLATION          (another process holds a handle)
# 33  ERROR_LOCK_VIOLATION             (byte-range lock)
# 170 ERROR_BUSY
# 1175 ERROR_UNABLE_TO_REMOVE_REPLACED (pending delete on target)
_TRANSIENT_WINERRORS = frozenset({5, 32, 33, 170, 1175})

__all__ = [
    "atomic_write",
    "atomic_append",
    "copy_file",
    "safe_move",
    "safe_rename",
    "make_dir",
    "quarantine",
    "trash",
    "stat",
    "hash_file",
    "read_bytes",
    "list_dir",
    "cleanup_stale_temp_files",
]


# ---------------------------------------------------------------------------
# Internal retry helpers (Windows-aware)
# ---------------------------------------------------------------------------

def _is_transient(exc: OSError) -> bool:
    """Return True if ``exc`` is likely to clear on its own."""
    if sys.platform == "win32":
        return getattr(exc, "winerror", None) in _TRANSIENT_WINERRORS
    # POSIX: EBUSY is the only realistic transient rename/unlink failure.
    return exc.errno in (errno.EBUSY, errno.EAGAIN)


def _is_readonly_dst(dst: Path) -> bool:
    """Return True if ``dst`` exists and lacks the owner-write bit."""
    try:
        st = dst.stat()
    except OSError:
        return False
    return not (st.st_mode & stat_module.S_IWRITE)


def _safe_replace(
    src: Path,
    dst: Path,
    *,
    context: str = "replace",
    timeout: float = _DEFAULT_REPLACE_TIMEOUT,
    max_attempts: int = _DEFAULT_REPLACE_MAX_ATTEMPTS,
) -> None:
    """Atomically replace ``dst`` with ``src``, retrying transient locks.

    Retries only while *all* of the following hold:
      * the failure is transient (``_is_transient`` is True);
      * the attempt budget (``max_attempts``) is not exhausted;
      * the monotonic-time deadline (``timeout`` seconds) is not exceeded.

    Non-transient errors, read-only destinations, and vanished sources
    cause an immediate ``SafeFsError`` — no retry.
    """
    deadline = time.monotonic() + timeout
    delay = _DEFAULT_REPLACE_INITIAL_DELAY
    attempt = 0
    last_exc: OSError | None = None

    while attempt < max_attempts:
        attempt += 1
        if not src.exists():
            raise SafeFsError(
                f"{context}: source vanished before replace: {src}"
            )
        try:
            os.replace(src, dst)
            if attempt > 1:
                _log.info(
                    "%s: replace succeeded after %d attempt(s): %s",
                    context, attempt, dst,
                )
            return
        except OSError as exc:
            last_exc = exc
            winerr = getattr(exc, "winerror", None)

            # Read-only destination: WinError 5 will never clear.
            if winerr == 5 and _is_readonly_dst(dst):
                raise SafeFsError(
                    f"{context}: destination is read-only: {dst}"
                ) from exc

            if not _is_transient(exc):
                raise SafeFsError(
                    f"{context}: {src} -> {dst}: {exc}"
                ) from exc

            if attempt >= max_attempts:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            sleep_for = min(delay, remaining)
            _log.warning(
                "%s: transient lock (attempt %d/%d, winerror=%r); "
                "retrying in %.3fs: %s",
                context, attempt, max_attempts, winerr, sleep_for, dst,
            )
            time.sleep(sleep_for)
            delay = min(delay * 2, _DEFAULT_REPLACE_MAX_DELAY)

    assert last_exc is not None
    winerr = getattr(last_exc, "winerror", None)
    _log.error(
        "%s: replace gave up after %d attempt(s), winerror=%r: %s",
        context, attempt, winerr, last_exc,
    )
    raise SafeFsError(
        f"{context}: too many transient failures replacing {dst}: {last_exc}"
    ) from last_exc


def _safe_unlink(
    path: Path,
    *,
    context: str = "unlink",
    timeout: float = _DEFAULT_UNLINK_TIMEOUT,
) -> None:
    """Remove ``path``, retrying transient Windows locks. Idempotent."""
    deadline = time.monotonic() + timeout
    delay = 0.02
    while True:
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if not _is_transient(exc):
                raise SafeFsError(
                    f"{context}: cannot remove {path}: {exc}"
                ) from exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SafeFsError(
                    f"{context}: transient lock not released for {path}: {exc}"
                ) from exc
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.5)


def _open_with_retry(
    path: Path,
    mode: str,
    *,
    timeout: float = _DEFAULT_OPEN_TIMEOUT,
    **kwargs,
):
    """Open ``path`` in ``mode``, retrying transient Windows locks."""
    deadline = time.monotonic() + timeout
    delay = 0.02
    while True:
        try:
            return open(path, mode, **kwargs)
        except OSError as exc:
            if not _is_transient(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.5)


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------

def atomic_write(path: Path, data: str | bytes, encoding: str = "utf-8") -> None:
    """Atomically write ``data`` to ``path``.

    Uses a temporary file in the same directory so that ``os.replace`` is
    atomic. If ``os.replace`` succeeds, the temporary file no longer exists.
    On Windows, ``os.replace`` is wrapped by ``_safe_replace`` so transient
    antivirus/indexer locks do not surface as data loss.
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
        _safe_replace(tmp, path, context=f"atomic_write({path})")
    except SafeFsError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SafeFsError(f"atomic_write failed for {path}: {exc}") from exc


def atomic_append(path: Path, data: str | bytes, encoding: str = "utf-8") -> None:
    """Append ``data`` to ``path`` with flush + fsync.

    Note: not atomic in the strict sense — a crash mid-write can leave a
    partial tail. Use :func:`atomic_write` with the full reconstructed
    contents when strict atomicity is required.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            handle = _open_with_retry(path, "ab")
        else:
            handle = _open_with_retry(path, "a", encoding=encoding)
        with handle:
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
    Open calls go through ``_open_with_retry`` to survive short antivirus
    locks on either side.
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
    except SafeFsError:
        raise
    except OSError as exc:
        raise SafeFsError(f"copy_file: precheck failed for {dst}: {exc}") from exc

    try:
        with _open_with_retry(src, "rb") as fsrc, _open_with_retry(dst, "wb") as fdst:
            while True:
                chunk = fsrc.read(_CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
            fdst.flush()
            os.fsync(fdst.fileno())
    except SafeFsError:
        raise
    except OSError as exc:
        raise SafeFsError(f"copy_file {src} -> {dst}: {exc}") from exc


def _complete_cross_device_move(src: Path) -> None:
    """Remove the source after a successful cross-device copy.

    This is the single internal deletion performed by safe_fs and is only
    reached after ``copy_file`` has fully persisted the destination data.
    Renaming across volumes is impossible without removing the source, so
    this helper exists specifically to complete ``safe_move`` across devices.
    """
    _safe_unlink(src, context="cross-device move: source cleanup")


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
        _safe_replace(src, dst, context=f"safe_move {src} -> {dst}")
        return
    except SafeFsError as exc:
        cause = exc.__cause__
        if not (isinstance(cause, OSError) and cause.errno == errno.EXDEV):
            raise
        # Cross-device: fall through to copy + remove.

    copy_file(src, dst)
    _complete_cross_device_move(src)


def safe_rename(path: Path, new_name: str) -> Path:
    """Rename ``path`` to ``path.parent / new_name`` in place.

    ``target.exists()`` is checked before the rename for a clear error
    message; the replace itself overwrites if the target appears in the
    tiny window between check and replace (same TOCTOU as the original).
    """
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
    _safe_replace(path, target, context=f"safe_rename {path} -> {target}")
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
    # ``time.time()`` is patched by freezegun, so this yields the frozen
    # wall clock under tests and real time in production. Formatting it
    # as YYYYmmddTHHMMSS keeps the trash path human-readable, sortable
    # by name, and lets test_trash_time assert on the year substring.
    stamp = datetime.fromtimestamp(time.time(), tz=timezone.utc).strftime(
        "%Y%m%dT%H%M%S"
    )
    base_target = trash_dir / f"{stamp}_{path.name}"
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
        "timestamp": time.time(),
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


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def cleanup_stale_temp_files(
    root: Path,
    *,
    max_age_seconds: int = 3600,
    filename_pattern: str = _TEMP_FILE_PATTERN,
) -> list[Path]:
    """Best-effort removal of orphaned temp files under ``root``.

    Only touches files whose name matches ``filename_pattern`` (by default
    the ``.{name}.tmp.{pid}.{ns}`` files produced by :func:`atomic_write`)
    *and* whose ``mtime`` is older than ``max_age_seconds``. Intended to be
    called on application startup; per-file failures are logged and skipped
    so a single stubborn file cannot abort the sweep.
    """
    root = Path(root)
    if not root.is_dir():
        return []

    cutoff = time.time() - max_age_seconds
    removed: list[Path] = []
    try:
        candidates = list(root.glob(filename_pattern))
    except OSError as exc:
        _log.warning("cleanup_stale_temp_files: glob failed in %s: %s", root, exc)
        return removed

    for entry in candidates:
        try:
            st = entry.stat()
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            continue
        try:
            _safe_unlink(entry, context="cleanup_stale_temp_files", timeout=2.0)
            removed.append(entry)
            _log.info("cleanup_stale_temp_files: removed stale temp file %s", entry)
        except SafeFsError as exc:
            _log.warning(
                "cleanup_stale_temp_files: cannot remove %s: %s", entry, exc
            )
    return removed