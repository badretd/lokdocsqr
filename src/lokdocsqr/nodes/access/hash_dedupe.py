"""Hash & Dedupe node.

Node id: ``access.hash_dedupe``. Read-only: hashes files up to
``max_bytes_for_hash`` and groups identical content.

pip deps: none (stdlib only)

R1 (too large): a file larger than ``max_bytes_for_hash`` is recorded in
``too_large``, never forms a group, and is counted as neither unique nor
duplicate. If every candidate is too large, ``groups`` is empty and the
node still succeeds.

R2 (determinism): ``groups`` are sorted by ``(size, hash)`` ascending;
``paths`` inside each group are sorted; ``too_large`` and ``skipped`` are
sorted by path.

``unique_count`` counts hashed files whose digest occurs exactly once;
``duplicate_count`` counts hashed files whose digest occurs more than
once. Files in ``too_large``/``skipped`` are counted in neither.

``dry_run`` stats candidates only. It never calls ``safe_fs.read_*`` and
never hashes: ``groups`` is empty, ``unique_count`` and
``duplicate_count`` are ``None``.
"""
from __future__ import annotations

import hashlib
import stat as stat_module
from pathlib import Path
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import LokError, NodeExecutionError, ScopeViolation
from lokdocsqr.core.node_api.decorators import register_node

from ._base import AccessBase, AccessFormatError

_ALLOWED_ALGOS = frozenset({"sha256", "md5", "blake2b"})


@register_node
class HashDedupeNode(AccessBase):
    """Hash files and group duplicate content digests."""

    id = "access.hash_dedupe"
    title = "Hash & Dedupe"
    inputs = {"paths": "str|list[str]"}
    outputs = {"groups": "dict[]"}

    def __init__(
        self,
        paths: list[str] | str | None = None,
        recursive: bool = True,
        algo: str = "sha256",
        max_bytes_for_hash: int = 50 * 1024 * 1024,
        skip_symlinks: bool = True,
        include_unique: bool = False,
    ) -> None:
        self.paths = paths
        self.recursive = recursive
        self.algo = algo
        self.max_bytes_for_hash = max_bytes_for_hash
        self.skip_symlinks = skip_symlinks
        self.include_unique = include_unique

    def run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> dict[str, Any]:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=False)

    def _preview(self, ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
        return self._process(ctx, payload, dry_run=True)

    @staticmethod
    def _raw_paths(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, (str, Path)):
            return [value]
        if isinstance(value, (list, tuple)):
            return list(value)
        raise NodeExecutionError(
            "hash_dedupe: 'paths' must be str, Path, or list of those"
        )

    @staticmethod
    def _validate_algo(algo: str) -> None:
        if algo not in _ALLOWED_ALGOS:
            raise AccessFormatError(
                f"hash_dedupe: unsupported algo {algo!r}; "
                f"expected one of {sorted(_ALLOWED_ALGOS)}"
            )
        try:
            hashlib.new(algo)
        except ValueError as exc:
            raise AccessFormatError(
                f"hash_dedupe: unsupported algo {algo!r}: {exc}"
            ) from exc

    def _process(
        self, ctx: Any, payload: dict[str, Any], dry_run: bool
    ) -> dict[str, Any]:
        raw_paths = self._raw_paths(payload.get("paths", self.paths))
        if not raw_paths:
            raise NodeExecutionError("hash_dedupe: no paths provided")

        recursive = bool(payload.get("recursive", self.recursive))
        algo = str(payload.get("algo", self.algo))
        self._validate_algo(algo)
        try:
            max_bytes_for_hash = int(
                payload.get("max_bytes_for_hash", self.max_bytes_for_hash)
            )
        except (TypeError, ValueError) as exc:
            raise NodeExecutionError(
                "hash_dedupe: max_bytes_for_hash must be an integer"
            ) from exc
        if max_bytes_for_hash < 0:
            raise NodeExecutionError(
                "hash_dedupe: max_bytes_for_hash must be non-negative"
            )
        skip_symlinks = bool(payload.get("skip_symlinks", self.skip_symlinks))
        include_unique = bool(
            payload.get("include_unique", self.include_unique)
        )

        too_large: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        hashed: dict[tuple[str, int], list[str]] = {}
        scanned = 0

        def handle_file(path: Path, st: Any) -> None:
            nonlocal scanned
            scanned += 1
            size = st.st_size
            if size > max_bytes_for_hash:
                too_large.append({"path": str(path), "size": size})
                return
            if dry_run:
                return
            try:
                digest = safe_fs.hash_file(path, algo)
            except LokError as exc:
                skipped.append({"path": str(path), "reason": str(exc)})
                return
            hashed.setdefault((digest, size), []).append(str(path))

        def scan_dir(directory: Path, recurse: bool) -> None:
            try:
                checked_dir = ctx.scope.check(directory, write=False)
            except ScopeViolation as exc:
                skipped.append(
                    {"path": str(directory), "reason": str(exc)}
                )
                return
            try:
                entries = safe_fs.list_dir(checked_dir)
            except LokError as exc:
                skipped.append(
                    {"path": str(checked_dir), "reason": str(exc)}
                )
                return
            for entry in entries:
                try:
                    checked = ctx.scope.check(entry, write=False)
                except ScopeViolation as exc:
                    skipped.append({"path": str(entry), "reason": str(exc)})
                    continue
                try:
                    is_symlink = entry.is_symlink()
                except OSError:
                    is_symlink = False
                if is_symlink and skip_symlinks:
                    skipped.append(
                        {"path": str(checked), "reason": "symlink"}
                    )
                    continue
                try:
                    st = safe_fs.stat(checked)
                except LokError as exc:
                    skipped.append(
                        {"path": str(checked), "reason": str(exc)}
                    )
                    continue
                if stat_module.S_ISDIR(st.st_mode):
                    if recurse:
                        scan_dir(checked, True)
                    continue
                handle_file(checked, st)

        for raw in raw_paths:
            try:
                roots = self._resolve_input(ctx, raw)
            except LokError as exc:
                skipped.append({"path": str(raw), "reason": str(exc)})
                continue
            for root in roots:
                try:
                    st = safe_fs.stat(root)
                except LokError as exc:
                    skipped.append({"path": str(root), "reason": str(exc)})
                    continue
                if stat_module.S_ISDIR(st.st_mode):
                    scan_dir(root, recursive)
                else:
                    handle_file(root, st)

        groups: list[dict[str, Any]] = []
        unique_count: int | None
        duplicate_count: int | None
        if dry_run:
            unique_count = None
            duplicate_count = None
        else:
            unique_count = 0
            duplicate_count = 0
            for (digest, size), paths in hashed.items():
                sorted_paths = sorted(paths)
                if len(sorted_paths) == 1:
                    unique_count += 1
                else:
                    duplicate_count += len(sorted_paths)
                if include_unique or len(sorted_paths) > 1:
                    groups.append(
                        {
                            "hash": digest,
                            "algo": algo,
                            "size": size,
                            "paths": sorted_paths,
                        }
                    )
            groups.sort(key=lambda group: (group["size"], group["hash"]))

        too_large.sort(key=lambda item: item["path"])
        skipped.sort(key=lambda item: item["path"])
        return {
            "groups": groups,
            "too_large": too_large,
            "skipped": skipped,
            "scanned": scanned,
            "unique_count": unique_count,
            "duplicate_count": duplicate_count,
        }
