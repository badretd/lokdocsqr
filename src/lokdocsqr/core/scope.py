"""Workspace-scope enforcement.

Every path that a node or executor wants to use must pass through
``ScopeManager.check`` before any safe_fs call.

``ScopeManager`` performs read-only filesystem inspection
(``Path.resolve``, ``Path.is_symlink``) as part of the gate that runs
*before* any mutation. Actual mutations go through
:mod:`lokdocsqr.core.safe_fs` — see its docstring for the full read/write
policy.
"""
from __future__ import annotations

import os
from pathlib import Path

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.models import WorkspaceScope


class ScopeManager:
    """Enforces read/write boundaries declared by ``WorkspaceScope``.

    Accepts the Phase 0 dataclass from ``core.models`` and reads the usual
    fields (``roots``, ``writable``, ``allow_symlinks``), tolerating minor
    naming variations (``allowed_roots`` / ``writable_roots``).
    """

    def __init__(self, scope: WorkspaceScope) -> None:
        self._scope = scope

        roots = getattr(scope, "roots", None)
        if roots is None:
            roots = getattr(scope, "allowed_roots", []) or []
        self._roots: list[Path] = [Path(r).resolve() for r in roots]

        writable = getattr(scope, "writable", None)
        if writable is None:
            writable = getattr(scope, "writable_roots", None)
        if writable is None:
            self._writable: list[Path] = list(self._roots)
        else:
            self._writable = [Path(r).resolve() for r in writable]

        self._allow_symlinks = bool(getattr(scope, "allow_symlinks", False))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, path: Path, write: bool = False) -> Path:
        """Validate ``path`` against the scope and return a normalized Path.

        Raises ScopeViolation on any violation.
        """
        path = Path(path)
        if ".." in path.parts:
            raise ScopeViolation(f"Path must not contain '..': {path}")

        try:
            resolved = path.resolve(strict=False)
        except OSError as exc:
            raise ScopeViolation(f"Cannot resolve path {path}: {exc}") from exc

        # Reject a symlink *leaf* only when ``allow_symlinks`` is off.
        # Ancestor symlinks (Windows junctions in %TEMP%, /tmp → /private/tmp
        # on macOS, etc.) are followed by ``resolve()`` and then vetted by
        # the ``resolved.relative_to(root)`` check below, so they cannot
        # escape the scope. Rejecting ancestors here caused false positives
        # on hosts whose temp root is a reparse point.
        if not self._allow_symlinks:
            try:
                if path.is_symlink():
                    raise ScopeViolation(f"Symlink not allowed: {path}")
            except OSError:
                pass

        roots = self._writable if write else self._roots
        if not roots:
            raise ScopeViolation(
                f"No {'writable' if write else 'readable'} roots configured"
            )

        for root in roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue

        raise ScopeViolation(
            f"Path {path} (resolved: {resolved}) is outside the "
            f"{'writable' if write else 'readable'} roots: "
            f"{[str(r) for r in roots]}"
        )

    def is_safe(self, path: Path, write: bool = False) -> bool:
        """Return ``True`` if ``check`` would succeed, ``False`` otherwise."""
        try:
            self.check(path, write=write)
        except ScopeViolation:
            return False
        return True
