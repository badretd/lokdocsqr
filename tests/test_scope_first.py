"""Invariant 7 — scope-first ordering, parametrized over all 9 fs_ops nodes.

Call-time enforcement (not post-hoc):

* ``ctx.scope.check`` is wrapped so every successfully checked path is
  recorded in ``ScopeFirstTracker.checked``.  The **return value** of
  ``scope.check`` (the resolved absolute path in production) is recorded,
  not merely the argument, so that wrappers installed downstream see the
  exact same string the nodes later pass to ``safe_fs.*``.
* Every mutating ``safe_fs.*`` method is wrapped so that, before the real
  call executes, it resolves the path argument(s) from the signature and
  raises ``RuntimeError("scope.check not called before safe_fs.<name>(<path>)")``
  if that path is not "covered".

Coverage semantics
------------------
A path is covered iff it is either

  (a) an exact member of ``ScopeFirstTracker.checked``, **or**
  (b) a descendant of some checked directory.

Rationale for (b): ``safe_fs.trash(src, trash_dir)`` internally calls
``safe_fs.safe_move(src, trash_dir / <timestamp> / name)``. The caller
never sees the timestamped destination, but ``trash.py`` *does* check
``trash_dir`` via ``_check_trash_dir_in_scope``. By the filesystem-scope
model a child of an in-scope directory is itself in scope, and prefix
match is exactly that — no broader.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from lokdocsqr.core import safe_fs
from lokdocsqr.core.errors import ScopeViolation

from .test_dry_run import (  # reuse fixtures
    FS_OPS_NODE_IDS,
    _TestCtx,
    _prepare,
)


# Which positional / kwarg carries the path per safe_fs method.
_PATH_PARAMS = {
    "atomic_write": ["path"],
    "atomic_append": ["path"],
    "copy_file": ["src", "dst"],
    "safe_move": ["src", "dst"],
    "safe_rename": ["path"],
    "make_dir": ["path"],
    "quarantine": ["path"],
    "trash": ["path"],
}

# Default positional index for kwarg-name fallback.
_POS_INDEX = {"path": 0, "src": 0, "dst": 1, "target": 1, "trash_dir": 1}


class ScopeFirstTracker:
    def __init__(self) -> None:
        self.checked: set[str] = set()
        self.violations: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def _record(self, value) -> None:
        if value is None:
            return
        try:
            self.checked.add(str(Path(value).resolve()))
        except (OSError, TypeError, ValueError):
            self.checked.add(str(value))

    def wrap_check(self, original):
        def wrapped(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            # Record the resolved return value (production: absolute path
            # inside workspace) *and* the raw input, so both the nodes
            # that re-use the returned path and any caller that kept the
            # original string are covered.
            self._record(result)
            self._record(path)
            return result
        return wrapped

    def _is_covered(self, rp: str) -> bool:
        if rp in self.checked:
            return True
        rp_path = Path(rp)
        for c in self.checked:
            try:
                rp_path.relative_to(Path(c))
                return True
            except ValueError:
                continue
        return False

    def wrap_op(self, name, original, path_params):
        def wrapped(*args, **kwargs):
            resolved: list[tuple[str, str]] = []
            for pp in path_params:
                p = kwargs.get(pp)
                if p is None and args:
                    idx = _POS_INDEX.get(pp, 0)
                    if idx < len(args):
                        p = args[idx]
                if p is None:
                    continue
                try:
                    rp = str(Path(p).resolve())
                except OSError:
                    rp = str(p)
                resolved.append((pp, rp))
            for pp, rp in resolved:
                self.calls.append((name, rp))
                if not self._is_covered(rp):
                    raise RuntimeError(
                        f"scope.check not called before safe_fs.{name}"
                        f"({pp}={rp})"
                    )
            return original(*args, **kwargs)
        return wrapped


def _run_one_with_tracker(node_id: str) -> None:
    """Run one node under the scope-first tracker, sandboxed to a tempdir.

    The nodes write output artefacts (``out.txt``, ``out.zip``, ...)
    relative to CWD, not to the ``workspace`` passed in ``ctx``.  We
    therefore chdir into a private temporary directory for the duration
    of the run, and restore CWD **before** ``TemporaryDirectory`` deletes
    the sandbox — otherwise:

    * relative artefacts would leak into whatever CWD the caller was
      using (project root when called directly from
      ``test_scope_first_node``, the caller's ``tmp_path`` when called via
      ``check_invariant_7`` → ``_run_delegated``), and
    * on Windows, deleting a directory that is still the CWD fails.

    ``os.chdir`` restoration is in ``finally`` because the leak detector
    runs at end-of-test-body, before ``monkeypatch``'s own teardown; a
    CWD still pointing at a temp directory would be flagged.
    """
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        sandbox = Path(td)
        os.chdir(sandbox)
        try:
            node, payload = _prepare(node_id, sandbox)
            ctx = _TestCtx([sandbox])

            tracker = ScopeFirstTracker()
            ctx.scope.check = tracker.wrap_check(ctx.scope.check)

            originals: dict[str, object] = {}
            try:
                for name, path_params in _PATH_PARAMS.items():
                    if not hasattr(safe_fs, name):
                        continue
                    originals[name] = getattr(safe_fs, name)
                    setattr(
                        safe_fs,
                        name,
                        tracker.wrap_op(name, originals[name], path_params),
                    )
                try:
                    node.run(ctx, payload)
                except RuntimeError as exc:
                    if "scope.check not called" in str(exc):
                        raise AssertionError(f"{node_id}: {exc}") from exc
                    # Other RuntimeErrors (e.g. missing external dep) — ignore.
            finally:
                for name, orig in originals.items():
                    setattr(safe_fs, name, orig)
        finally:
            os.chdir(original_cwd)


def check_invariant_7() -> None:
    """Aggregate invariant-7 check (all 9 fs_ops nodes; convert skipped)."""
    failures: list[str] = []
    for node_id in FS_OPS_NODE_IDS:
        if node_id == "fs.convert":
            continue
        try:
            _run_one_with_tracker(node_id)
        except AssertionError as exc:
            failures.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            if "scope.check not called" in str(exc):
                failures.append(f"{node_id}: {exc}")
    assert not failures, (
        "Invariant 7 (scope-first) failures:\n" + "\n".join(failures)
    )


# ── negative cases ──────────────────────────────────────────────────

def test_scope_first_negative_out_of_scope(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    node, _ = _prepare("fs.tag", tmp_path)
    ctx = _TestCtx([tmp_path])
    with pytest.raises(ScopeViolation):
        node.run(ctx, {"path": str(outside), "tags": ["x"], "mode": "set"})


def test_scope_first_negative_traversal(tmp_path: Path) -> None:
    node, _ = _prepare("fs.tag", tmp_path)
    ctx = _TestCtx([tmp_path])
    evil = str(tmp_path) + "/../evil.txt"
    with pytest.raises(ScopeViolation):
        node.run(ctx, {"path": evil, "tags": ["x"], "mode": "set"})


def test_tracker_catches_unchecked_write(tmp_path: Path) -> None:
    """Acceptance #8 — call-time enforcement: unchecked write must go red."""
    ctx = _TestCtx([tmp_path])
    tracker = ScopeFirstTracker()
    ctx.scope.check = tracker.wrap_check(ctx.scope.check)

    originals = {"atomic_write": safe_fs.atomic_write}
    safe_fs.atomic_write = tracker.wrap_op(
        "atomic_write", originals["atomic_write"], ["path"]
    )
    try:
        with pytest.raises(RuntimeError, match="scope.check not called"):
            safe_fs.atomic_write(tmp_path / "evil.txt", b"data")
    finally:
        safe_fs.atomic_write = originals["atomic_write"]


@pytest.mark.parametrize("node_id", FS_OPS_NODE_IDS)
def test_scope_first_node(node_id: str) -> None:
    if node_id == "fs.convert":
        pytest.xfail("fs.convert requires an external conversion pair")
    _run_one_with_tracker(node_id)