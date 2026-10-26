"""Phase 6b — template pipeline (AC-6) and dry-run purity for access nodes."""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.extract_text import ExtractTextNode
from lokdocsqr.nodes.access.hash_dedupe import HashDedupeNode


def make_ctx(tmp_path):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path], writable=[tmp_path], allow_symlinks=False
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
    )


# ---- AC-6 — the four defs are present, thin wrappers -------------------

def test_all_four_methods_defined_in_body():
    for cls in (HashDedupeNode, ExtractTextNode):
        for method in ("run", "dry_run", "_execute", "_preview"):
            assert method in cls.__dict__, (
                f"{cls.__name__}.{method} not defined in class body"
            )


def test_run_and_dry_run_are_thin_wrappers():
    for cls in (HashDedupeNode, ExtractTextNode):
        for method_name, delegate in [
            ("run", "_run_template"),
            ("dry_run", "_dry_run_template"),
        ]:
            method = getattr(cls, method_name)
            src = inspect.getsource(method)
            lines = [l for l in src.split("\n") if l.strip()]
            assert len(lines) <= 5, (
                f"{cls.__name__}.{method_name} is too long:\n{src}"
            )
            assert delegate in src, (
                f"{cls.__name__}.{method_name} does not delegate to {delegate}"
            )


# ---- AC-6 — dry_run does not crash on inherited _execute --------------

def test_dry_run_does_not_raise_not_implemented(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("hello")

    r1 = HashDedupeNode().dry_run(ctx, {"paths": str(tmp_path)})
    assert "groups" in r1 and r1["groups"] == []

    r2 = ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "a.txt")})
    assert "items" in r2


# ---- dry-run purity — neither node touches the FS ----------------------

def _snapshot(root: Path) -> dict:
    result = {}
    for p in sorted(Path(root).rglob("*")):
        try:
            st = p.stat()
        except OSError:
            continue
        result[p.relative_to(root).as_posix()] = (
            p.is_dir(), st.st_size, st.st_mtime_ns,
        )
    return result


def test_dry_run_does_not_mutate_filesystem(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("aaa")
    (tmp_path / "c.html").write_text("<p>hi</p>")
    (tmp_path / "d.csv").write_text("a,b\n1,2\n")

    before = _snapshot(tmp_path)
    HashDedupeNode().dry_run(ctx, {"paths": str(tmp_path)})
    ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "a.txt")})
    ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "c.html")})
    ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "d.csv")})
    after = _snapshot(tmp_path)

    assert before == after
