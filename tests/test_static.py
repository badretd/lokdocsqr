"""Phase 5a — source hygiene and script acceptance (checklist items 2, 5, 7)."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FS_OPS_DIR = PROJECT_ROOT / "src" / "lokdocsqr" / "nodes" / "fs_ops"
CHECK_DEPS = PROJECT_ROOT / "scripts" / "check_system_deps.py"

PATH_MUTATORS = frozenset({
    "write_text", "write_bytes", "rename", "touch",
    "mkdir", "rmdir", "unlink", "chmod", "symlink_to", "hardlink_to",
})
OS_MUTATORS = frozenset({
    "remove", "unlink", "rename", "replace", "rmdir", "mkdir",
    "makedirs", "chmod", "link", "symlink",
})
SHUTIL_MUTATORS = frozenset({
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
})


def _attr_chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return list(reversed(parts))
    return None


def _open_write_mode(call: ast.Call) -> str | None:
    args = list(call.args[1:]) + [k.value for k in call.keywords if k.arg == "mode"]
    for a in args:
        if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                and a.value and a.value[0] in "wax":
            return a.value
    return None


def _find_ast_issues(path: Path) -> list[tuple[int, str, str]]:
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src, filename=str(path))
    lines = src.splitlines()
    issues: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        line = lines[node.lineno - 1].strip() if 1 <= node.lineno <= len(lines) else ""
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _open_write_mode(node)
            if mode is not None:
                issues.append((node.lineno, line, f"open(..., {mode!r})"))
            continue
        chain = _attr_chain(func) or []
        if len(chain) < 2:
            continue
        root, attr = chain[0], chain[-1]
        if root == "os" and attr in OS_MUTATORS:
            issues.append((node.lineno, line, ".".join(chain)))
        elif root == "shutil" and attr in SHUTIL_MUTATORS:
            issues.append((node.lineno, line, ".".join(chain)))
        elif attr in PATH_MUTATORS and "Path" in chain:
            issues.append((node.lineno, line, ".".join(chain)))
    return issues


def _fs_ops_sources() -> list[Path]:
    if not FS_OPS_DIR.is_dir():
        return []
    return sorted(p for p in FS_OPS_DIR.rglob("*.py") if p.name != "__init__.py")


_FS_OPS_SOURCES = _fs_ops_sources()


def test_fs_ops_dir_exists() -> None:
    assert FS_OPS_DIR.is_dir(), f"missing {FS_OPS_DIR}"


@pytest.mark.parametrize("path", _FS_OPS_SOURCES, ids=lambda p: p.name)
def test_no_forbidden_fs_calls_ast(path: Path) -> None:
    issues = _find_ast_issues(path)
    assert not issues, (
        f"{path.name}: forbidden call(s) (checklist item 2):\n"
        + "\n".join(f"  L{ln}: {src_line}   # {reason}" for ln, src_line, reason in issues)
    )


# --- Registry (checklist item 5) -------------------------------------------

def _registry_ids() -> set[str]:
    from lokdocsqr.core.node_api.registry import registry
    return {item["id"] for item in registry.list()}


@pytest.mark.parametrize("node_id", ["fs.make_dir", "fs.write_file"])
def test_registry_includes_fs_node(node_id: str) -> None:
    pytest.importorskip("lokdocsqr.nodes.fs_ops")
    ids = _registry_ids()
    assert node_id in ids, (
        f"{node_id!r} missing; registry contains {sorted(ids)}. "
        f"Check fs_ops/__init__.py imports both make_dir and write_file."
    )


# --- check_system_deps.py (checklist item 7) -------------------------------

def test_check_system_deps_script_exists() -> None:
    assert CHECK_DEPS.is_file(), f"missing {CHECK_DEPS}"


def _run_check_deps(env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECK_DEPS)],
        capture_output=True, text=True,
        env=env, cwd=str(PROJECT_ROOT), timeout=60,
    )


def test_check_system_deps_exit_code_is_0_or_1() -> None:
    result = _run_check_deps(env=os.environ.copy())
    assert result.returncode in (0, 1), (
        f"expected rc in (0,1), got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    if result.returncode == 1:
        assert result.stderr.strip(), "missing-tesseract path must print install hint to stderr"


def test_check_system_deps_missing_tesseract(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    result = _run_check_deps(env=env)
    assert result.returncode == 1, (
        f"expected exit 1 with tesseract unreachable, got {result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    hint = (result.stderr or "").lower()
    assert "tesseract" in hint or "install" in hint, f"missing install hint: {result.stderr!r}"
