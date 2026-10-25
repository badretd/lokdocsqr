"""Phase 5a — per-node behavior (items 1, 3, 4, inv.5).

Runtime tests use MagicMock for ctx and monkeypatch for safe_fs spies. The
FS-purity and ordering tests mock `build_action_log` because they don't test
log construction (see test_build_action_log_constructs_action_log for that).

Known code bug (out of scope for these tests, flagged by the smoke test):
    _common.build_action_log passes `details=` to ActionLog, but
    ActionLog.__init__ has no `details` parameter. Coder must reconcile.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.models import Risk
from lokdocsqr.nodes.fs_ops._common import (
    assert_dry_run_no_side_effects,
    build_action_log,
    render_and_check,
)
from lokdocsqr.nodes.fs_ops.make_dir import MakeDirNode
from lokdocsqr.nodes.fs_ops.write_file import WriteFileNode


_NODES = [
    pytest.param(MakeDirNode, id="make_dir"),
    pytest.param(WriteFileNode, id="write_file"),
]


def _make_ctx(*, allow_scope: bool = True, roots: list[Path] | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.run_id = "run-test"
    ctx.automation_id = "auto-test"
    ctx.scope.check.return_value = allow_scope
    ctx.scope.roots = list(roots or [])
    ctx.audit = []
    return ctx


def _payload(node_cls: type, path: str, **extra) -> dict:
    payload = {"path_template": path}
    if node_cls is WriteFileNode:
        payload["content"] = "hello"
    payload.update(extra)
    return payload


@pytest.fixture
def render_template_identity(monkeypatch):
    monkeypatch.setattr(
        "lokdocsqr.nodes.fs_ops._common.render_template",
        lambda template, variables: template,
    )


@pytest.fixture
def safe_fs_spy(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def _spy(name):
        def _impl(*args, **kwargs):
            calls.append((name, args))
        return _impl

    for name in ("make_dir", "atomic_write", "atomic_append"):
        monkeypatch.setattr(f"lokdocsqr.core.safe_fs.{name}", _spy(name))
    return calls


@pytest.fixture
def mute_build_action_log(monkeypatch):
    """Mute build_action_log for tests that verify FS behavior, not logging.

    Also mutes the module-level import in make_dir / write_file so the
    ActionLog(model) bug doesn't cascade into these assertions.
    """
    for mod in ("make_dir", "write_file"):
        monkeypatch.setattr(
            f"lokdocsqr.nodes.fs_ops.{mod}.build_action_log",
            lambda **kwargs: None,
        )


# --------------------------------------------------------------------------
# Smoke: ActionLog / build_action_log contract
# --------------------------------------------------------------------------

def test_build_action_log_constructs_action_log() -> None:
    """build_action_log must be able to construct the model it targets.

    If this fails with `unexpected keyword argument 'details'`, then
    core.models.ActionLog and _common.build_action_log are out of sync —
    a code fix, not a test fix. Check `inspect.signature(ActionLog)`.
    """
    log = build_action_log(
        run_id="r", automation_id="a", node_id="fs.make_dir",
        action_type="make_dir", source=None, target="x",
        reason="test", confidence=1.0, risk=Risk.LOW, dry_run=True,
    )
    assert log is not None


# --------------------------------------------------------------------------
# Invariant 5
# --------------------------------------------------------------------------

@pytest.mark.parametrize("node_cls", _NODES)
def test_run_and_dry_run_declared_per_class(node_cls: type) -> None:
    assert "run" in node_cls.__dict__
    assert "dry_run" in node_cls.__dict__
    assert callable(node_cls.__dict__["run"])
    assert callable(node_cls.__dict__["dry_run"])


# --------------------------------------------------------------------------
# render_and_check (items 3 & 4)
# --------------------------------------------------------------------------

def test_render_and_check_rejects_dotdot_after_render(render_template_identity) -> None:
    ctx = _make_ctx()
    with pytest.raises(ScopeViolation):
        render_and_check(ctx, "../escape", {})
    ctx.scope.check.assert_not_called()


def test_render_and_check_rejects_out_of_scope() -> None:
    ctx = _make_ctx(allow_scope=False)
    with pytest.raises(ScopeViolation):
        render_and_check(ctx, "safe/file", {})
    ctx.scope.check.assert_called_once()


def test_render_and_check_returns_path_on_valid() -> None:
    ctx = _make_ctx(allow_scope=True)
    result = render_and_check(ctx, "safe/file", {})
    assert isinstance(result, Path)
    # Path == Path normalizes separators on Windows; str == str would not.
    assert result == Path("safe/file")


# --------------------------------------------------------------------------
# run ordering & scope guard (items 3, 4, 7)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("node_cls", _NODES)
def test_run_rejects_dotdot_without_mutation(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True)
    with pytest.raises(ScopeViolation):
        node_cls().run(ctx, _payload(node_cls, "../escape"))
    assert safe_fs_spy == []
    assert ctx.audit == []


@pytest.mark.parametrize("node_cls", _NODES)
def test_run_rejects_out_of_scope_without_mutation(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=False)
    with pytest.raises(ScopeViolation):
        node_cls().run(ctx, _payload(node_cls, "outside/file"))
    assert safe_fs_spy == []
    assert ctx.audit == []


@pytest.mark.parametrize("node_cls", _NODES)
def test_run_scope_check_precedes_mutation(
    node_cls, monkeypatch, render_template_identity, mute_build_action_log,
) -> None:
    calls: list[tuple[str, str]] = []
    ctx = _make_ctx(allow_scope=True)
    ctx.scope.check.side_effect = lambda p: (calls.append(("scope.check", str(p))), True)[1]

    def _spy(name):
        def _impl(*args, **kwargs):
            calls.append((f"safe_fs.{name}", str(args[0])))
        return _impl
    for name in ("make_dir", "atomic_write", "atomic_append"):
        monkeypatch.setattr(f"lokdocsqr.core.safe_fs.{name}", _spy(name))

    if node_cls is WriteFileNode:
        payload = _payload(node_cls, "safe/file", create_parents=False)
    else:
        payload = _payload(node_cls, "safe/dir")
    node_cls().run(ctx, payload)

    assert calls and calls[0][0] == "scope.check", calls
    first_safe_fs = next(i for i, (n, _) in enumerate(calls) if n.startswith("safe_fs."))
    assert first_safe_fs > 0
    assert all(calls[i][0] == "scope.check" for i in range(first_safe_fs)), calls


# --------------------------------------------------------------------------
# dry_run purity (item 1)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("node_cls", _NODES)
def test_dry_run_no_side_effects_snapshot(
    node_cls, tmp_path, render_template_identity, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True, roots=[tmp_path])
    payload = _payload(node_cls, str(tmp_path / "would_create.txt"))
    assert_dry_run_no_side_effects(ctx, node_cls().dry_run, ctx, payload)
    assert not (tmp_path / "would_create.txt").exists()


@pytest.mark.parametrize("node_cls", _NODES)
def test_dry_run_does_not_call_safe_fs(
    node_cls, render_template_identity, safe_fs_spy, mute_build_action_log,
) -> None:
    ctx = _make_ctx(allow_scope=True)
    node_cls().dry_run(ctx, _payload(node_cls, "any/path"))
    assert safe_fs_spy == []
