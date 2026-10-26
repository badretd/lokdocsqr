"""Invariant 6 — dry-run purity, parametrized over all 9 fs_ops nodes.

Exports ``check_invariant_6()`` (called from ``tests/test_architecture.py``)
and a parametrized pytest test for granular reporting.

For each node we snapshot the workspace (relpath, size, mtime_ns, inode)
before and after ``node.dry_run(ctx, payload)``; any difference is a
violation. The ``fs.convert`` node is XFAIL — its minimal payload needs an
external conversion pair not available in Phase 5e.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import ScopeViolation


# Ensure every fs_ops node is registered at import time.
import lokdocsqr.nodes.fs_ops.make_dir    # noqa: F401
import lokdocsqr.nodes.fs_ops.write_file  # noqa: F401
import lokdocsqr.nodes.fs_ops.rename      # noqa: F401
import lokdocsqr.nodes.fs_ops.copy        # noqa: F401
import lokdocsqr.nodes.fs_ops.move        # noqa: F401
import lokdocsqr.nodes.fs_ops.trash       # noqa: F401
import lokdocsqr.nodes.fs_ops.archive     # noqa: F401
import lokdocsqr.nodes.fs_ops.convert     # noqa: F401
import lokdocsqr.nodes.fs_ops.tag         # noqa: F401

from lokdocsqr.core.node_api.registry import registry  # noqa: E402


FS_OPS_NODE_IDS = (
    "fs.make_dir",
    "fs.write_file",
    "fs.rename",
    "fs.copy",
    "fs.move",
    "fs.trash",
    "fs.archive",
    "fs.convert",
    "fs.tag",
)


# ── minimal test doubles ─────────────────────────────────────────────

class _TestScope:
    """Scope double that resolves relative inputs against ``workspace``.

    Production ``ctx.scope.check`` returns the *resolved absolute* path;
    ``render_and_check`` depends on that (it forwards the return value).
    Without workspace-relative resolution here, ``render_and_check``
    would resolve ``"work/a.txt"`` against CWD and reject it as
    out-of-scope.
    """

    def __init__(self, roots, workspace=None):
        self.roots = [Path(r).resolve() for r in roots]
        self.workspace = Path(
            workspace if workspace is not None else roots[0]
        ).resolve()

    def check(self, path, write: bool = False):
        p = Path(path)
        if ".." in p.parts:
            raise ScopeViolation(f"path contains '..': {path}")
        if not p.is_absolute():
            p = self.workspace / p
        resolved = p.resolve()
        for root in self.roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise ScopeViolation(f"{path} is outside scope {self.roots}")


class _RecordingAudit:
    def __init__(self):
        self.entries: list = []

    def append(self, log) -> None:
        self.entries.append(log)


class _TestCtx:
    def __init__(self, roots):
        self.workspace = Path(roots[0]).resolve()
        self.scope = _TestScope(roots, workspace=self.workspace)
        self.run_id = "test-run"
        self.automation_id = "test-auto"
        self.dry_run = True
        self.logger = logging.getLogger("test_dry_run")
        self.audit = _RecordingAudit()
        self.config = SimpleNamespace(trash_dir=self.workspace / ".trash")
        self.policy = None
        self.events = None


# ── snapshots ────────────────────────────────────────────────────────

def _snapshot(root: Path) -> dict[str, tuple[int, int, int]]:
    """Return {relpath: (size, mtime_ns, inode)} for everything under root."""
    result: dict[str, tuple[int, int, int]] = {}
    if not root.exists():
        return result
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
                rel = p.relative_to(root).as_posix() + "/"
                result[rel] = (0, st.st_mtime_ns, st.st_ino)
            except OSError:
                pass
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
                rel = p.relative_to(root).as_posix()
                result[rel] = (st.st_size, st.st_mtime_ns, st.st_ino)
            except OSError:
                pass
    return result


# ── fixtures per node ────────────────────────────────────────────────

def _prepare(node_id: str, tmp_path: Path):
    """Return (node_instance, payload).

    Template fields (``path_template`` / ``template`` / ``target`` /
    ``target_dir``) MUST be relative — ``render_and_check`` rejects
    absolute rendered templates by design. Absolute paths are only
    allowed for ``source`` / ``sources`` / ``path`` inputs, which are
    gated by ``scope.check``.
    """
    node_cls = registry.get(node_id)
    node = node_cls()

    if node_id == "fs.make_dir":
        return node, {
            "path_template": "newdir",
            "variables": {},
        }

    if node_id == "fs.write_file":
        return node, {
            "path_template": "out.txt",
            "content": "hello",
            "mode": "write",
            "create_parents": False,
        }

    if node_id in ("fs.rename", "fs.copy", "fs.move"):
        src = tmp_path / "src.txt"
        src.write_text("hello", encoding="utf-8")
        node.template = {
            "fs.rename": "renamed.txt",
            "fs.copy": "copied.txt",
            "fs.move": "moved.txt",
        }[node_id]
        if hasattr(node, "conflict_policy"):
            node.conflict_policy = "skip"
        return node, {"source": str(src), "variables": {}}

    if node_id == "fs.trash":
        src = tmp_path / "to_trash.txt"
        src.write_text("hello", encoding="utf-8")
        return node, {
            "path": str(src),
            "ttl_seconds": 3600,
        }

    if node_id == "fs.archive":
        a = tmp_path / "a.txt"
        a.write_text("a", encoding="utf-8")
        b = tmp_path / "b.txt"
        b.write_text("b", encoding="utf-8")
        return node, {
            "operation": "create",
            "sources": [str(a), str(b)],
            "target": "out.zip",   # relative to workspace
            "format": "zip",
            "overwrite": True,
        }

    if node_id == "fs.convert":
        raise RuntimeError("xfail_convert")

    if node_id == "fs.tag":
        src = tmp_path / "tagme.txt"
        src.write_text("hello", encoding="utf-8")
        return node, {
            "path": str(src),
            "tags": ["alpha", "beta"],
            "mode": "set",
        }

    raise RuntimeError(f"no fixture registered for node {node_id!r}")


# ── per-node check ───────────────────────────────────────────────────

def _check_one(node_id: str) -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        node, payload = _prepare(node_id, tmp_path)
        ctx = _TestCtx([tmp_path])

        before = _snapshot(tmp_path)
        try:
            node.dry_run(ctx, payload)
        except Exception as exc:  # noqa: BLE001
            after = _snapshot(tmp_path)
            if before != after:
                raise AssertionError(
                    f"{node_id}: dry_run raised {exc!r} AND mutated the FS"
                ) from exc
            # Purity invariant holds (no mutation). Raise-path coverage
            # lives in tests/test_scope_first.py and test_nodes.py.
            return
        after = _snapshot(tmp_path)
        if before != after:
            delta = sorted(set(before) ^ set(after)) or sorted(
                k for k in before if before[k] != after.get(k)
            )
            raise AssertionError(
                f"{node_id}: dry_run mutated the FS under {tmp_path}: "
                f"delta={delta}"
            )


def check_invariant_6() -> None:
    """Aggregate invariant-6 check (all 9 fs_ops nodes; convert skipped)."""
    failures: list[str] = []
    for node_id in FS_OPS_NODE_IDS:
        if node_id == "fs.convert":
            continue
        try:
            _check_one(node_id)
        except AssertionError as exc:
            failures.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{node_id}: unexpected error in dry_run: {exc!r}")
    assert not failures, (
        "Invariant 6 (dry-run purity) failures:\n" + "\n".join(failures)
    )


# ── parametrized mirror (for granular reporting) ────────────────────

@pytest.mark.parametrize("node_id", FS_OPS_NODE_IDS)
def test_dry_run_purity(node_id: str) -> None:
    if node_id == "fs.convert":
        pytest.xfail(
            "fs.convert: minimal payload requires an external conversion pair "
            "(docx/pdf) not shipped in Phase 5e"
        )
    _check_one(node_id)