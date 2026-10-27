"""Tests for Phase 6d1: access.index_search node.

Covers AC-1..AC-11, D2, D3, D5, D6, D7 behavioral fixes, and the
pyproject.toml grep-check. Black-box where possible; tmp_path + monkey-patch.
"""
from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.nodes.access import extract_text
from lokdocsqr.nodes.access.index_search import IndexSearchNode


# ── helpers ────────────────────────────────────────────────────────

def _build_ctx(tmp_path):
    """Build a minimal ExecutionContext-like stub scoped to tmp_path."""
    from lokdocsqr.core.scope import ScopeManager

    scope = ScopeManager(
        SimpleNamespace(
            roots=[str(tmp_path)],
            writable=[str(tmp_path)],
            allow_symlinks=False,
        )
    )
    return SimpleNamespace(
        scope=scope,
        config=SimpleNamespace(base_dir=tmp_path),
    )


def _snapshot_tree(root):
    """Snapshot file paths, sizes, and mtimes under root."""
    snap = {}
    for path in sorted(Path(root).rglob("*")):
        try:
            st = path.stat()
        except OSError:
            continue
        rel = str(path)
        if path.is_dir():
            snap[rel] = ("dir",)
        else:
            snap[rel] = ("file", st.st_size, st.st_mtime_ns)
    return snap


# ── AC-1: R13 read-only ────────────────────────────────────────────

def test_ac1_read_only_no_mutation(tmp_path):
    """AC-1 (R13): index_search never mutates the tree."""
    (tmp_path / "a.txt").write_text("needle in haystack")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("another needle")

    before = _snapshot_tree(tmp_path)

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle")
    result = node.run(ctx, {"paths": [str(tmp_path)]})

    after = _snapshot_tree(tmp_path)
    assert before == after
    assert len(result["matches"]) >= 2


# ── AC-2: R14 dispatch through HANDLERS ────────────────────────────

def test_ac2a_txt_handler_used(tmp_path):
    """AC-2(a): standard txt handler finds the needle."""
    (tmp_path / "doc.txt").write_text("hello needle world")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle")
    result = node.run(ctx, {"paths": [str(tmp_path)]})
    assert any(m["match"] == "needle" for m in result["matches"])


def test_ac2b_monkeypatched_handler_used(tmp_path, monkeypatch):
    """AC-2(b): monkey-patched HANDLERS['txt'] is invoked."""
    marker = "FAKEHANDLERMARKER9876"
    calls = []

    def fake_handler(data, opts):
        calls.append(len(data))
        return {"text": f"prefix {marker} suffix", "error": None}

    monkeypatch.setitem(extract_text.HANDLERS, "txt", fake_handler)

    (tmp_path / "doc.txt").write_text("nothing relevant here")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query=marker)
    result = node.run(ctx, {"paths": [str(tmp_path)]})

    assert calls, "fake handler was never invoked"
    assert any(m["match"] == marker for m in result["matches"])


# ── AC-3: R15 mode ─────────────────────────────────────────────────

def test_ac3_substring_matches_inside_word(tmp_path):
    (tmp_path / "f.txt").write_text("concatenation matters")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="caten", mode="substring")
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 1


def test_ac3_word_boundary_rejects_inside_word(tmp_path):
    (tmp_path / "f.txt").write_text("concatenation matters")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="caten", mode="word")
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 0


def test_ac3_word_boundary_matches_full_word(tmp_path):
    (tmp_path / "f.txt").write_text("the cat sat")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="cat", mode="word")
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 1


def test_ac3_regex_finditer(tmp_path):
    (tmp_path / "f.txt").write_text("abc123def456ghi")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query=r"\d+", mode="regex")
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 2
    assert [m["match"] for m in r["matches"]] == ["123", "456"]


def test_ac3_invalid_regex_fails_before_traversal(tmp_path, monkeypatch):
    """AC-3: invalid regex raises before any handler is invoked."""
    calls = []

    def tracking(data, opts):
        calls.append(len(data))
        return {"text": "", "error": None}

    monkeypatch.setitem(extract_text.HANDLERS, "txt", tracking)
    (tmp_path / "f.txt").write_text("hello")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="[unclosed", mode="regex")
    with pytest.raises(Exception):
        node.run(ctx, {"paths": [str(tmp_path)]})
    assert calls == [], "handler ran despite invalid regex"


def test_ac3_case_sensitive_true(tmp_path):
    (tmp_path / "f.txt").write_text("HELLO hello")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="hello", case_sensitive=True)
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 1


def test_ac3_case_sensitive_false_default(tmp_path):
    (tmp_path / "f.txt").write_text("HELLO hello")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="hello")  # default case_sensitive=False
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 2


# ── AC-4: R16 context_chars ────────────────────────────────────────

def test_ac4_context_middle_of_line_has_both_sides(tmp_path):
    (tmp_path / "f.txt").write_text("aaa bbb needle ccc ddd")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle", context_chars=5)
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    context = r["matches"][0]["context"]
    assert "needle" in context
    assert len(context) > len("needle")


def test_ac4_context_clipped_to_line(tmp_path):
    (tmp_path / "f.txt").write_text(
        "first line\nsecond needle here\nthird line"
    )
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle", context_chars=100)
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    context = r["matches"][0]["context"]
    assert "needle" in context
    assert "first" not in context
    assert "third" not in context


def test_ac4_context_zero_equals_match(tmp_path):
    (tmp_path / "f.txt").write_text("prefix needle suffix")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle", context_chars=0)
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    context = r["matches"][0]["context"]
    assert context == "needle"


# ── AC-5: R17 limits ───────────────────────────────────────────────

def test_ac5_max_matches_per_file(tmp_path):
    (tmp_path / "f.txt").write_text("a a a a a")  # 5 potential matches
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(
        query="a",
        max_matches_per_file=3,
        max_total_matches=1000,
    )
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 3
    assert r["truncated"] is True


def test_ac5_max_total_matches(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i:02d}.txt").write_text("a a a")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(
        query="a",
        max_matches_per_file=100,
        max_total_matches=5,
    )
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 5
    assert r["truncated"] is True


def test_ac5_max_bytes_per_file_skipped_not_read(tmp_path, monkeypatch):
    calls = []

    def tracking(data, opts):
        calls.append(len(data))
        return {"text": "", "error": None}

    monkeypatch.setitem(extract_text.HANDLERS, "txt", tracking)

    (tmp_path / "big.txt").write_text("x" * 1000)
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="x", max_bytes_per_file=100)
    r = node.run(ctx, {"paths": [str(tmp_path)]})

    assert any(s["reason"] == "too_large" for s in r["files_skipped"])
    assert calls == [], "handler was invoked for oversized file"


# ── AC-6: R18 determinism ──────────────────────────────────────────

def test_ac6_sorted_and_deterministic(tmp_path):
    (tmp_path / "b.txt").write_text("x x x")
    (tmp_path / "a.txt").write_text("x x x")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="x")
    r1 = node.run(ctx, {"paths": [str(tmp_path)]})
    r2 = node.run(ctx, {"paths": [str(tmp_path)]})

    assert r1 == r2
    paths = [m["path"] for m in r1["matches"]]
    assert paths == sorted(paths)
    keys = [(m["path"], m["line"], m["col"]) for m in r1["matches"]]
    assert keys == sorted(keys)


# ── AC-7: R19 lazy import ──────────────────────────────────────────

def test_ac7_lazy_import_no_optional_deps(tmp_path):
    """AC-7: index_search imports under stubbed pytesseract/pypdfium2."""
    here = Path(__file__).resolve()
    repo_root = here.parents[1]  # tests/../ = repo root
    src_dir = repo_root / "src"
    pypath_entry = str(src_dir) if src_dir.is_dir() else str(repo_root)

    env = os.environ.copy()
    env["PYTHONPATH"] = pypath_entry + os.pathsep + env.get("PYTHONPATH", "")

    code = (
        "import sys\n"
        "sys.modules['pytesseract'] = None\n"
        "sys.modules['pypdfium2'] = None\n"
        "import lokdocsqr.nodes.access.index_search as m\n"
        "assert m.IndexSearchNode is not None\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "OK" in result.stdout


# ── AC-8: AC-17 extensibility (main acceptance) ────────────────────

def test_ac8_new_handler_registration_extends_search(tmp_path):
    """AC-8 (AC-17): register_handler('fake', ...) works without code changes."""
    marker = "EXTENSIBILITYMARKER42"

    def fake_handler(data, opts):
        return {"text": f"hello {marker} world", "error": None}

    extract_text.register_handler("fake", fake_handler)
    try:
        (tmp_path / "doc.fake").write_bytes(b"arbitrary bytes")
        ctx = _build_ctx(tmp_path)
        node = IndexSearchNode(query=marker)
        r = node.run(ctx, {"paths": [str(tmp_path)]})
        assert len(r["matches"]) >= 1
        assert any(m["match"] == marker for m in r["matches"])
    finally:
        extract_text.HANDLERS.pop("fake", None)


# ── AC-9: .htm works as html ──────────────────────────────────────

def test_ac9_htm_finds_text(tmp_path):
    """AC-9: .htm files are handled by the html handler."""
    (tmp_path / "page.htm").write_text(
        "<html><body>hello world</body></html>"
    )
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="hello")
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) >= 1


# ── AC-10: unknown extension behavior ─────────────────────────────

def test_ac10_unknown_extension_goes_to_errors(tmp_path):
    (tmp_path / "doc.unk").write_bytes(b"some bytes")
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="bytes")
    r = node.run(ctx, {"paths": [str(tmp_path)]})

    assert len(r["matches"]) == 0
    assert len(r["errors"]) == 1
    err = r["errors"][0]
    assert "no handler for format" in err["error"]
    assert "unk" in err["error"]
    assert r["files_scanned"] == 0
    assert all("doc.unk" not in s["path"] for s in r["files_skipped"])


# ── AC-11: dry_run doesn't read bytes ─────────────────────────────

def test_ac11_dry_run_no_handler_no_bytes(tmp_path, monkeypatch):
    """AC-11: dry_run stats only; handler never invoked."""
    calls = []

    def tracking(data, opts):
        calls.append(len(data))
        return {"text": "hello", "error": None}

    monkeypatch.setitem(extract_text.HANDLERS, "txt", tracking)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="hello")
    r = node.dry_run(ctx, {"paths": [str(tmp_path)]})

    assert calls == [], "handler invoked during dry_run"
    assert r["matches"] == []
    assert r.get("files_scanned", 0) == 0
    assert r.get("files_would_scan") == 2


# ── D2: multiline regex context on start line ─────────────────────

def test_d2_multiline_regex_context_start_line_only(tmp_path):
    """D2: context clipped to start line, not match tail."""
    (tmp_path / "f.txt").write_text(
        "alpha\nbeta MATCHSTART continued\nGAMMA delta"
    )
    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(
        query=r"MATCHSTART continued\nGAMMA",
        mode="regex",
        context_chars=200,
    )
    r = node.run(ctx, {"paths": [str(tmp_path)]})
    assert len(r["matches"]) == 1
    context = r["matches"][0]["context"]
    assert "MATCHSTART" in context
    # Tail of multi-line match must NOT leak into context (D2).
    assert "GAMMA" not in context


# ── D3: symlink skipped ───────────────────────────────────────────

def test_d3_symlink_skipped(tmp_path):
    """D3: symlink entries are skipped during traversal."""
    real = tmp_path / "real.txt"
    real.write_text("hello")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="hello")
    r = node.run(ctx, {"paths": [str(tmp_path)]})

    matched = [m["path"] for m in r["matches"]]
    assert any("real.txt" in p for p in matched)
    assert not any("link.txt" in p for p in matched)


# ── D5: duplicate paths behavior ─────────────────────────────────

def test_d5_duplicate_paths_no_dedup(tmp_path):
    """D5: same file reached via dir + direct file path → 2 matches.

    The implemented traversal deduplicates *directories* via
    ``visited_dirs``; the same *file* reached via a dir scan and via an
    explicit file path is not deduplicated, and both matches carry the
    same ``path`` string. This test records the documented D5 behavior.
    """
    sub = tmp_path / "sub"
    sub.mkdir()
    x = sub / "x.txt"
    x.write_text("needle")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="needle")
    r = node.run(ctx, {"paths": [str(sub), str(x)]})

    assert len(r["matches"]) == 2
    assert r["matches"][0]["path"] == r["matches"][1]["path"]


# ── D6: dry_run field name ────────────────────────────────────────

def test_d6_dry_run_reports_files_would_scan(tmp_path):
    """D6: dry_run reports files_would_scan, not files_scanned."""
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")

    ctx = _build_ctx(tmp_path)
    node = IndexSearchNode(query="x")
    r = node.dry_run(ctx, {"paths": [str(tmp_path)]})

    assert "files_would_scan" in r
    assert r["files_would_scan"] == 2
    assert r["files_scanned"] == 0
    assert r["matches"] == []


# ── D7: DEFAULT_MAX_BYTES_PER_FILE from _base ────────────────────

def test_d7_default_max_bytes_in_base():
    from lokdocsqr.nodes.access import _base
    assert _base.DEFAULT_MAX_BYTES_PER_FILE == 1 * 1024 * 1024


def test_d7_index_search_value_matches_base():
    from lokdocsqr.nodes.access import _base, index_search
    assert (
        index_search.DEFAULT_MAX_BYTES_PER_FILE
        == _base.DEFAULT_MAX_BYTES_PER_FILE
        == 1 * 1024 * 1024
    )


def test_d7_index_search_imports_from_base():
    """D7 strict: must be imported from ._base, not redefined locally.

    If this test fails on the current tree, D7's stated "moved to
    _base.py, imported not duplicated" is not reflected in
    index_search.py — report as BUG per the phase rules.
    """
    from lokdocsqr.nodes.access import index_search

    src = inspect.getsource(index_search)
    pattern = (
        r"from\s+\._base\s+import\b[^;\n]*"
        r"\bDEFAULT_MAX_BYTES_PER_FILE\b"
    )
    assert re.search(pattern, src), (
        "D7 violation: DEFAULT_MAX_BYTES_PER_FILE is not imported "
        "from ._base in index_search.py"
    )


# ── pyproject.toml unchanged ──────────────────────────────────────

def test_pyproject_not_written_by_apply_script():
    """Grep: apply_phase_6d1.py does not write pyproject.toml."""
    here = Path(__file__).resolve().parent
    candidates = [
        Path.cwd() / "apply_phase_6d1.py",
        here.parent / "apply_phase_6d1.py",
        here / "apply_phase_6d1.py",
    ]
    apply_path = next((p for p in candidates if p.exists()), None)
    if apply_path is None:
        pytest.skip("apply_phase_6d1.py not found")
    content = apply_path.read_text(encoding="utf-8")
    main_body = content.split("def main()", 1)[-1]
    assert not re.search(
        r"(?:write_text|write_bytes|open)\s*\([^)]*pyproject",
        main_body,
        re.DOTALL,
    ), "apply script main() writes to pyproject.toml"


# ── registration smoke ───────────────────────────────────────────

def test_index_search_is_registered():
    from lokdocsqr.core.node_api.registry import registry
    cls = registry.get("access.index_search")
    assert cls.__name__ == "IndexSearchNode"
