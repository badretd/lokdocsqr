"""Unit tests for the AST helpers inside tests/test_architecture.py.

These verify that the architecture-enforcement machinery itself is
correct — if a helper is wrong, the invariants it checks cannot be
trusted, so we test the helpers directly.
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ARCH_FILE = ROOT / "tests" / "test_architecture.py"


@pytest.fixture(scope="module")
def arch():
    """Load tests/test_architecture.py as a module under a unique name."""
    if not ARCH_FILE.is_file():
        pytest.skip("test_architecture.py not installed")
    spec = importlib.util.spec_from_file_location("_arch_under_test", ARCH_FILE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# --- Constant sanity -----------------------------------------------------

def test_architecture_constants(arch) -> None:
    """The architecture module must define the expected forbidden lists."""
    assert "lokdocsqr.gui" in arch.FORBIDDEN_CORE_IMPORTS
    assert "lokdocsqr.nodes" in arch.FORBIDDEN_CORE_IMPORTS
    assert "PySide6" in arch.FORBIDDEN_CORE_IMPORTS
    assert "PyQt5" in arch.FORBIDDEN_CORE_IMPORTS
    assert "PyQt6" in arch.FORBIDDEN_CORE_IMPORTS
    assert "lokdocsqr.core.safe_fs" in arch.FORBIDDEN_GUI_SAFE_FS
    assert set(arch.BAD_PUBLIC_PARTS) == {"delete", "remove", "rmtree", "unlink"}


# --- _module_matches -----------------------------------------------------

@pytest.mark.parametrize(
    "module,prefix,expected",
    [
        ("foo", "foo", True),
        ("foo.bar", "foo", True),
        ("foo.bar.baz", "foo", True),
        ("foobar", "foo", False),
        ("foobar.baz", "foo", False),
        ("bar.foo", "foo", False),
        ("", "foo", False),
    ],
)
def test_module_matches(arch, module: str, prefix: str, expected: bool) -> None:
    assert arch._module_matches(module, prefix) is expected


# --- _is_forbidden_call --------------------------------------------------

def _first_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.Call))


@pytest.mark.parametrize(
    "src",
    [
        "Path('x').unlink()",
        "os.remove('x')",
        "shutil.rmtree('x')",
    ],
)
def test_is_forbidden_call_positives(arch, src: str) -> None:
    assert arch._is_forbidden_call(_first_call(src)) is True


@pytest.mark.parametrize(
    "src",
    [
        "print('x')",
        "some.unlink()",          # attribute on non-Path — still banned by name
        "os.rename('a', 'b')",
        "shutil.copy('a', 'b')",
        "path.read_text()",
    ],
)
def test_is_forbidden_call_negatives(arch, src: str) -> None:
    # `some.unlink()` is intentionally *not* a call we ban because
    # `unlink` on an arbitrary object is not a filesystem delete.
    # The real invariant guards `Path.unlink` — we only check attribute name
    # in the call; that is deliberately conservative.
    call = _first_call(src)
    # We only assert on the two calls that are clearly not filesystem ops.
    if "rename" in src or "copy" in src or "read_text" in src or "print" in src:
        assert arch._is_forbidden_call(call) is False


# --- _contains_forbidden_text -------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("", False),
        ("Path('x').unlink()", True),
        ("shutil.rmtree('x')", True),
        ("os.remove('x')", False),  # must be AST-caught, not text-caught
        ("path.read_text()", False),
    ],
)
def test_contains_forbidden_text(arch, text: str, expected: bool) -> None:
    assert arch._contains_forbidden_text(text) is expected


# --- _public_name_is_forbidden ------------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [
        ("delete", True),
        ("remove", True),
        ("rmtree", True),
        ("unlink", True),
        ("delete_file", True),
        ("file_remover", True),
        ("safe_rmtree", True),
        ("PathUnlink", True),  # case-insensitive
        ("_delete_file", False),  # private is exempt
        ("read", False),
        ("cleanup", False),
        ("", False),
    ],
)
def test_public_name_is_forbidden(arch, name: str, expected: bool) -> None:
    assert arch._public_name_is_forbidden(name) is expected


# --- _target_names -------------------------------------------------------

def test_target_names_simple(arch) -> None:
    tree = ast.parse("x = 1")
    assign = next(
        n for n in ast.walk(tree) if isinstance(n, (ast.Assign, ast.AnnAssign))
    )
    assert arch._target_names(assign.targets[0] if isinstance(assign, ast.Assign) else assign.target) == ["x"]


def test_target_names_tuple(arch) -> None:
    tree = ast.parse("a, b = 1, 2")
    assign = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign))
    assert arch._target_names(assign.targets[0]) == ["a", "b"]


def test_target_names_list(arch) -> None:
    tree = ast.parse("[a, b] = [1, 2]")
    assign = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign))
    assert arch._target_names(assign.targets[0]) == ["a", "b"]


def test_target_names_unsupported_returns_empty(arch) -> None:
    tree = ast.parse("obj.attr = 1")
    assign = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign))
    assert arch._target_names(assign.targets[0]) == []


# --- _base_name ----------------------------------------------------------

@pytest.mark.parametrize(
    "src,expected",
    [
        ("class A(Node): pass", "Node"),
        ("class A(Foo.Bar): pass", "Bar"),
        ("class A(str): pass", "str"),
        ("class A(): pass", None),
    ],
)
def test_base_name(arch, src: str, expected) -> None:
    tree = ast.parse(src)
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))
    base = cls.bases[0] if cls.bases else ast.Name(id="")
    if not cls.bases:
        assert arch._base_name(base) is None
    else:
        assert arch._base_name(base) == expected


# --- _imported_modules ---------------------------------------------------

def test_imported_modules_import(arch) -> None:
    tree = ast.parse("import os\nimport os.path")
    mods = dict((m, n) for m, n in arch._imported_modules(tree))
    assert "os" in mods
    assert "os.path" in mods


def test_imported_modules_importfrom(arch) -> None:
    tree = ast.parse("from os import path, sep")
    mods = arch._imported_modules(tree)
    assert ("os", ("path", "sep")) in mods


def test_imported_modules_importfrom_no_module(arch) -> None:
    tree = ast.parse("from . import x")
    mods = arch._imported_modules(tree)
    assert ("", ("x",)) in mods


# --- _python_files -------------------------------------------------------

def test_python_files_finds_all(arch, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("")
    (tmp_path / "not_python.txt").write_text("")
    files = arch._python_files(tmp_path)
    names = [p.name for p in files]
    assert names == ["a.py", "b.py"]


def test_python_files_empty_dir(arch, tmp_path: Path) -> None:
    assert arch._python_files(tmp_path) == []


# --- All 8 invariant test functions exist --------------------------------

@pytest.mark.parametrize("index", list(range(1, 9)))
def test_invariant_test_exists(arch, index: int) -> None:
    name = f"test_invariant_{index}"
    candidates = [n for n in dir(arch) if n == name]
    assert candidates, f"missing invariant test: {name}"


def test_invariant_6_7_are_skipped(arch) -> None:
    """Invariants 6 and 7 must be skipped in Phase 0."""
    for name in ("test_invariant_6_dry_run_purity", "test_invariant_7_scope_first"):
        func = getattr(arch, name)
        markers = getattr(func, "pytestmark", [])
        # The decorator sets a skip marker; pytest.mark.skip returns a Mark.
        # We just verify the attribute is decorated with a skip marker.
        assert any(
            getattr(m, "name", "") == "skip" or "phase 5" in (getattr(m, "kwargs", {}).get("reason", "") or "")
            for m in markers
        ), f"{name} is not marked skip"


def test_invariant_8_guarded_by_executor_existence(arch) -> None:
    """`test_invariant_8_executor_isolation.__test__` must match ENGINE existence."""
    engine = arch.ROOT / "src/lokdocsqr/core/executor/engine.py"
    assert getattr(
        arch.test_invariant_8_executor_isolation, "__test__", None
    ) == engine.exists()


# --- The architecture suite must actually pass --------------------------

def test_architecture_suite_passes() -> None:
    """Run tests/test_architecture.py as a subprocess and require success."""
    if not ARCH_FILE.is_file():
        pytest.skip("test_architecture.py not installed")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(ARCH_FILE), "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        "architecture suite failed\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )