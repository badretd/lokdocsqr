"""Shared fixtures/helpers for Phase 5c acceptance tests.

Fake scope is STRICT: paths outside ``base_dir`` raise ``ScopeViolation``.
Without this, negative tests are silently positive.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


# ── context builder ────────────────────────────────────────────────
def _build_ctx(base: Path, trash: Path, *, dry_run: bool = False):
    base_resolved = base.resolve()
    trash_resolved = trash.resolve()
    # Scope allows BOTH the workspace (where the user's files live) and
    # the trash directory (its configured quarantine destination). They
    # are siblings in every test fixture, both are deliberate deployment
    # values, and fs.trash moves a file from base -> trash — a scope
    # that rejects trash would make the node impossible to use. The
    # ConfigError raised by fs.trash._check_trash_dir_in_scope still
    # fires for genuinely out-of-scope trash paths (e.g. C:\Windows).
    allowed_roots = (base_resolved, trash_resolved)

    class _StrictScope:
        def check(self, path):
            p = Path(path)
            try:
                resolved = p.resolve()
            except (OSError, RuntimeError):
                resolved = p
            for root in allowed_roots:
                try:
                    resolved.relative_to(root)
                    return p
                except ValueError:
                    continue
            from lokdocsqr.core.errors import ScopeViolation
            raise ScopeViolation(f"outside scope: {p}")

        def is_within(self, path):
            try:
                r = Path(path).resolve()
            except (OSError, RuntimeError):
                return False
            for root in allowed_roots:
                try:
                    r.relative_to(root)
                    return True
                except ValueError:
                    continue
            return False

    audit = MagicMock()
    audit.logs = []
    audit.append = lambda log, *a, **kw: audit.logs.append(log)

    return SimpleNamespace(
        run_id="test-run-001",
        automation_id="test-auto-001",
        scope=_StrictScope(),
        config=SimpleNamespace(base_dir=base, trash_dir=trash),
        audit=audit,
        dry_run=dry_run,
        logger=MagicMock(),
        events=MagicMock(),
        policy=MagicMock(),
        variables={},
    ), audit


@pytest.fixture
def trash_env(tmp_path):
    base = tmp_path / "workspace"
    trash = tmp_path / "trash"
    base.mkdir()
    trash.mkdir()
    ctx, audit = _build_ctx(base, trash, dry_run=False)
    return SimpleNamespace(
        base=base, trash=trash, ctx=ctx, config=ctx.config, audit=audit,
    )


# ── node loading ───────────────────────────────────────────────────
def find_node_class(module):
    candidates = []
    for name, obj in vars(module).items():
        if (isinstance(obj, type)
                and getattr(obj, "__module__", None) == module.__name__
                and not name.startswith("_")
                and name != "Node"
                and (hasattr(obj, "run") or name.endswith("Node"))):
            candidates.append((name, obj))
    if not candidates:
        pytest.skip(f"no node class in {module.__name__}")
    for name, cls in candidates:
        if name.endswith("Node"):
            return cls
    return candidates[0][1]


def _import_trash():
    import lokdocsqr.nodes.fs_ops.trash  # noqa: F401
    return sys.modules["lokdocsqr.nodes.fs_ops.trash"]


# ── the ONE helper every behavioral test uses ──────────────────────
def run_trash(env, source_path, *, dry_run: bool = False):
    """Invoke trash.run/dry_run and return the ActionLog.

    Always passes the canonical ``{"path": ...}`` payload. ``_as_payload``
    in the node also accepts a bare ``Path``/``str``, but we pass the
    canonical dict here so a real bug surfaces as itself instead of
    being masked by a retry (which would double-run the mutation).
    """
    mod = _import_trash()
    node = find_node_class(mod)()
    env.ctx.dry_run = dry_run
    method = node.dry_run if dry_run else node.run

    try:
        result = method(env.ctx, {"path": source_path})
    except TypeError as exc:
        msg = str(exc)
        if any(tok in msg for tok in ("NodeResult", "payload", "NodeState")):
            pytest.fail(f"BUG: trash node contract broken: {msg}")
        raise

    if isinstance(result, list):
        result = result[0] if result else None
    return result