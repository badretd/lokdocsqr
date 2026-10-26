"""Phase 6b — hash_dedupe node (R1, R2, AC-5, AC-7, AC-8, AC-9, AC-15, AC-16)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import LokError, NodeExecutionError
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.hash_dedupe import HashDedupeNode


def make_ctx(tmp_path, allow_symlinks=False):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path],
        writable=[tmp_path],
        allow_symlinks=allow_symlinks,
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
    )


# ---- AC-4 / AC-5 ----------------------------------------------------------

def test_import_via_package():
    from lokdocsqr.nodes.access import hash_dedupe as hd
    assert hd.HashDedupeNode is HashDedupeNode


def test_registered_under_expected_id():
    # AC-5: read from registry, not from the class attribute in the test
    class_map = {cls: cls.id for cls in [HashDedupeNode]}
    for cls, expected_id in class_map.items():
        assert registry.get(expected_id) is cls
    # Spec-level assertion
    assert HashDedupeNode.id == "access.hash_dedupe"


def test_r1_documented_in_docstring():
    """R1 must be documented; per spec an undocumented R1 is a bug."""
    from lokdocsqr.nodes.access import hash_dedupe as hd
    docstring = hd.__doc__ or ""
    assert "R1" in docstring and "too_large" in docstring


def test_r2_documented_in_docstring():
    from lokdocsqr.nodes.access import hash_dedupe as hd
    docstring = hd.__doc__ or ""
    assert "R2" in docstring


# ---- R1 — too_large -------------------------------------------------------

def test_r1_all_too_large_yields_empty_groups(tmp_path):
    ctx = make_ctx(tmp_path)
    for i in range(3):
        (tmp_path / f"big{i}.bin").write_bytes(b"x" * 100)
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "max_bytes_for_hash": 10}
    )
    assert result["groups"] == []
    assert len(result["too_large"]) == 3
    assert result["scanned"] == 3
    assert result["unique_count"] == 0
    assert result["duplicate_count"] == 0


def test_r1_mixed_sizes_count_correctly(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("aaa")
    (tmp_path / "unique.txt").write_text("unique")
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "max_bytes_for_hash": 10}
    )
    assert len(result["too_large"]) == 1
    assert len(result["groups"]) == 1
    assert len(result["groups"][0]["paths"]) == 2
    assert result["unique_count"] == 1
    assert result["duplicate_count"] == 2
    assert result["scanned"] == 4


def test_r1_too_large_does_not_form_group(tmp_path):
    ctx = make_ctx(tmp_path)
    # Two identical files, both too large → no group, both in too_large
    (tmp_path / "a.bin").write_bytes(b"y" * 100)
    (tmp_path / "b.bin").write_bytes(b"y" * 100)
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "max_bytes_for_hash": 10}
    )
    assert result["groups"] == []
    assert len(result["too_large"]) == 2
    assert result["unique_count"] == 0
    assert result["duplicate_count"] == 0


# ---- R2 — determinism -----------------------------------------------------

def test_r2_two_runs_byte_identical(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("aaa")
    (tmp_path / "c.txt").write_text("bbb")
    (tmp_path / "d.txt").write_text("bbb")
    r1 = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    r2 = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    assert r1 == r2


def test_r2_groups_sorted_by_size_then_hash(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "s2a.txt").write_text("aa")
    (tmp_path / "s2b.txt").write_text("aa")
    (tmp_path / "s3a.txt").write_text("bbb")
    (tmp_path / "s3b.txt").write_text("bbb")
    result = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    sizes = [g["size"] for g in result["groups"]]
    assert sizes == sorted(sizes)
    for i in range(len(result["groups"]) - 1):
        key_a = (result["groups"][i]["size"], result["groups"][i]["hash"])
        key_b = (result["groups"][i + 1]["size"], result["groups"][i + 1]["hash"])
        assert key_a <= key_b


def test_r2_paths_in_group_are_sorted(tmp_path):
    ctx = make_ctx(tmp_path)
    for name in ["c.txt", "a.txt", "b.txt"]:
        (tmp_path / name).write_text("same")
    result = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    assert len(result["groups"]) == 1
    assert result["groups"][0]["paths"] == sorted(result["groups"][0]["paths"])


def test_r2_too_large_sorted_by_path(tmp_path):
    ctx = make_ctx(tmp_path)
    for name in ["c.bin", "a.bin", "b.bin"]:
        (tmp_path / name).write_bytes(b"x" * 100)
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "max_bytes_for_hash": 10}
    )
    paths = [item["path"] for item in result["too_large"]]
    assert paths == sorted(paths)


# ---- algos ---------------------------------------------------------------

@pytest.mark.parametrize("algo", ["sha256", "md5", "blake2b"])
def test_allowed_algos(tmp_path, algo):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("hello")
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "algo": algo}
    )
    assert len(result["groups"]) == 1
    assert result["groups"][0]["algo"] == algo
    # Sanity: hash is what we expect
    expected = hashlib.new(algo, b"hello").hexdigest()
    assert result["groups"][0]["hash"] == expected


@pytest.mark.parametrize("bad_algo", ["sha3_999", "invalid", ""])
def test_unknown_algo_raises_lok_error(tmp_path, bad_algo):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(LokError):
        HashDedupeNode().run(ctx, {"paths": str(tmp_path), "algo": bad_algo})


# ---- include_unique ------------------------------------------------------

def test_include_unique_false_by_default(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "u.txt").write_text("unique")
    result = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    assert result["groups"] == []
    assert result["unique_count"] == 1


def test_include_unique_true_emits_singleton_group(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "u.txt").write_text("unique")
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "include_unique": True}
    )
    assert len(result["groups"]) == 1
    assert len(result["groups"][0]["paths"]) == 1
    assert result["unique_count"] == 1


# ---- AC-9 — dry_run ------------------------------------------------------

def test_dry_run_returns_none_counters(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("aaa")
    result = HashDedupeNode().dry_run(ctx, {"paths": str(tmp_path)})
    assert result["groups"] == []
    assert result["unique_count"] is None
    assert result["duplicate_count"] is None
    assert result["scanned"] == 2


def test_dry_run_reports_too_large_by_stat_only(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "big.bin").write_bytes(b"x" * 100)
    result = HashDedupeNode().dry_run(
        ctx, {"paths": str(tmp_path), "max_bytes_for_hash": 10}
    )
    assert result["groups"] == []
    assert len(result["too_large"]) == 1
    assert result["too_large"][0]["size"] == 100


# ---- AC-7 — dry_run must not hash ----------------------------------------

def test_dry_run_does_not_call_hash_file_or_read_bytes(tmp_path, monkeypatch):
    from lokdocsqr.core import safe_fs
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    counts = {"hash_file": 0, "read_bytes": 0}
    orig_hash = safe_fs.hash_file
    orig_read = safe_fs.read_bytes

    def fake_hash(*a, **k):
        counts["hash_file"] += 1
        return orig_hash(*a, **k)

    def fake_read(*a, **k):
        counts["read_bytes"] += 1
        return orig_read(*a, **k)

    monkeypatch.setattr(safe_fs, "hash_file", fake_hash)
    monkeypatch.setattr(safe_fs, "read_bytes", fake_read)
    HashDedupeNode().dry_run(ctx, {"paths": str(tmp_path)})
    assert counts["hash_file"] == 0
    assert counts["read_bytes"] == 0


# ---- AC-15 — scope.check for every path, write=False ---------------------

def test_scope_check_for_root_path(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    calls = []
    orig = ctx.scope.check

    def checking(path, write=False):
        calls.append((str(path), write))
        return orig(path, write=write)

    ctx.scope.check = checking
    HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    assert calls
    assert all(w is False for _, w in calls)


def test_scope_check_for_recursively_discovered_paths(tmp_path):
    ctx = make_ctx(tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("aaa")
    calls = []
    orig = ctx.scope.check

    def checking(path, write=False):
        calls.append((str(path), write))
        return orig(path, write=write)

    ctx.scope.check = checking
    HashDedupeNode().run(ctx, {"paths": str(tmp_path), "recursive": True})
    assert all(w is False for _, w in calls)
    assert any("a.txt" in p for p, _ in calls)


# ---- AC-16 — per-item errors do not fail the node -----------------------

def test_missing_and_ok_paths(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "ok.txt").write_text("aaa")
    missing = tmp_path / "missing.txt"
    result = HashDedupeNode().run(
        ctx, {"paths": [str(tmp_path / "ok.txt"), str(missing)]}
    )
    assert result["scanned"] == 1
    assert result["unique_count"] == 1
    assert any("missing.txt" in s["path"] for s in result["skipped"])


def test_broken_symlink_skipped_on_stat_failure(tmp_path):
    ctx = make_ctx(tmp_path, allow_symlinks=True)
    broken = tmp_path / "broken.txt"
    try:
        broken.symlink_to(tmp_path / "nonexistent.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    result = HashDedupeNode().run(
        ctx, {"paths": str(tmp_path), "skip_symlinks": False}
    )
    assert any("broken.txt" in s["path"] for s in result["skipped"])


# ---- misc ----------------------------------------------------------------

def test_no_paths_raises(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError, match="no paths"):
        HashDedupeNode().run(ctx, {"paths": []})


def test_empty_directory(tmp_path):
    ctx = make_ctx(tmp_path)
    result = HashDedupeNode().run(ctx, {"paths": str(tmp_path)})
    assert result["groups"] == []
    assert result["unique_count"] == 0
    assert result["duplicate_count"] == 0
    assert result["scanned"] == 0


def test_invalid_max_bytes_raises(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(NodeExecutionError, match="max_bytes_for_hash"):
        HashDedupeNode().run(
            ctx, {"paths": str(tmp_path), "max_bytes_for_hash": -1}
        )
