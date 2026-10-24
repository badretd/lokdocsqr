"""Architecture contract tests for LokDocSQR."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "lokdocsqr"
CORE = SRC / "core"
GUI = SRC / "gui"
NODES = SRC / "nodes"
SAFE_FS = CORE / "safe_fs.py"
ENGINE = CORE / "executor" / "engine.py"

FORBIDDEN_CORE_IMPORTS = (
    "lokdocsqr.gui",
    "lokdocsqr.nodes",
    "PySide6",
    "PyQt5",
    "PyQt6",
)
FORBIDDEN_GUI_SAFE_FS = (
    "lokdocsqr.core.safe_fs",
    "core.safe_fs",
)
BAD_PUBLIC_PARTS = ("delete", "remove", "rmtree", "unlink")


def _python_files(base: Path) -> list[Path]:
    """Return sorted Python files below a directory."""
    return sorted(path for path in base.rglob("*.py") if path.is_file())


def _imported_modules(tree: ast.AST) -> list[tuple[str, tuple[str, ...]]]:
    """Return module names and imported names for all imports in a tree."""
    imports: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, ()))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = tuple(alias.name for alias in node.names)
            imports.append((module, names))
    return imports


def _module_matches(module: str, prefix: str) -> bool:
    """Return True when a module is equal to or below a prefix."""
    return module == prefix or module.startswith(prefix + ".")


def _is_forbidden_call(call: ast.Call) -> bool:
    """Return True for forbidden filesystem mutation calls."""
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr == "unlink":
        return True
    if func.attr == "remove" and isinstance(func.value, ast.Name):
        return func.value.id == "os"
    if func.attr == "rmtree" and isinstance(func.value, ast.Name):
        return func.value.id == "shutil"
    return False


def _contains_forbidden_text(text: str) -> bool:
    """Return True when source text contains forbidden deletion fragments."""
    return "rmtree" in text or "unlink(" in text


def _public_name_is_forbidden(name: str) -> bool:
    """Return True for public names that expose deletion-like API."""
    if name.startswith("_"):
        return False
    lowered = name.lower()
    return any(part in lowered for part in BAD_PUBLIC_PARTS)


def _target_names(target: ast.AST) -> list[str]:
    """Collect assignment target names from simple and tuple targets."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return []


def _base_name(base: ast.expr) -> str | None:
    """Return a readable base class name for common AST forms."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def test_invariant_1_layering() -> None:
    """Core must not import GUI, nodes, or Qt bindings."""
    violations: list[str] = []
    for path in _python_files(CORE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, _names in _imported_modules(tree):
            for forbidden in FORBIDDEN_CORE_IMPORTS:
                if _module_matches(module, forbidden):
                    relative = path.relative_to(ROOT)
                    violations.append(f"{relative} imports {module}")
    assert not violations, "Core layering violations:\n" + "\n".join(violations)


def test_invariant_2_gui_isolation() -> None:
    """GUI modules must not import safe_fs directly."""
    violations: list[str] = []
    for path in _python_files(GUI):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, names in _imported_modules(tree):
            for forbidden in FORBIDDEN_GUI_SAFE_FS:
                if _module_matches(module, forbidden):
                    relative = path.relative_to(ROOT)
                    violations.append(f"{relative} imports {module}")
            if module in ("lokdocsqr.core", "core") and "safe_fs" in names:
                relative = path.relative_to(ROOT)
                violations.append(f"{relative} imports core.safe_fs")
    assert not violations, "GUI safe_fs violations:\n" + "\n".join(violations)


def test_invariant_3_delete_ban() -> None:
    """Forbidden deletion calls must not appear outside safe_fs."""
    violations: list[str] = []
    for path in _python_files(SRC):
        if path == SAFE_FS:
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if _contains_forbidden_text(text):
            violations.append(f"{relative} contains forbidden deletion text")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_forbidden_call(node):
                violations.append(f"{relative} contains a forbidden deletion call")
    assert not violations, "Delete-ban violations:\n" + "\n".join(violations)


def test_invariant_4_no_delete_api() -> None:
    """safe_fs must not expose public deletion-like API."""
    if not SAFE_FS.exists():
        return
    tree = ast.parse(SAFE_FS.read_text(encoding="utf-8"), filename=str(SAFE_FS))
    violations: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if _public_name_is_forbidden(node.name):
                violations.append(f"public definition {node.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _target_names(target):
                    if _public_name_is_forbidden(name):
                        violations.append(f"public assignment {name}")
        elif isinstance(node, ast.AnnAssign):
            for name in _target_names(node.target):
                if _public_name_is_forbidden(name):
                    violations.append(f"public assignment {name}")
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _public_name_is_forbidden(item.name):
                    violations.append(f"public method {node.name}.{item.name}")
    assert not violations, "No-delete API violations:\n" + "\n".join(violations)


def test_invariant_5_node_contract() -> None:
    """Concrete Node subclasses must implement run and dry_run."""
    node_classes: list[tuple[Path, ast.ClassDef]] = []
    for path in _python_files(NODES):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = _base_name(base)
                if name is not None and (name == "Node" or name.endswith("Node")):
                    node_classes.append((path, node))
                    break
    if not node_classes:
        return
    violations: list[str] = []
    for path, node in node_classes:
        methods = {
            item.name
            for item in node.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for required in ("run", "dry_run"):
            if required not in methods:
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}:{node.name} lacks {required}")
    assert not violations, "Node contract violations:\n" + "\n".join(violations)


@pytest.mark.skip(reason="verified in phase 5")
def test_invariant_6_dry_run_purity() -> None:
    """Dry-run purity is verified with filesystem tests in Phase 5."""


@pytest.mark.skip(reason="verified in phase 5")
def test_invariant_7_scope_first() -> None:
    """Scope-first ordering is verified with node tests in Phase 5."""


@pytest.mark.skipif(
    not ENGINE.exists(),
    reason="executor engine is introduced in phase 4",
)
def test_invariant_8_executor_isolation() -> None:
    """Executor must not import concrete nodes."""
    violations: list[str] = []
    executor_dir = CORE / "executor"
    for path in _python_files(executor_dir):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module, _names in _imported_modules(tree):
            if _module_matches(module, "lokdocsqr.nodes") or module == "nodes":
                relative = path.relative_to(ROOT)
                violations.append(f"{relative} imports {module}")
    assert not violations, "Executor isolation violations:\n" + "\n".join(violations)

# === Phase 4 extra arch invariant: no delete calls in audit/executor ===
def test_no_delete_calls_in_audit_and_executor() -> None:
    """Invariant 3 must also hold for core/audit.py and core/executor/*.py."""
    targets = [CORE / "audit.py", *_python_files(CORE / "executor")]
    violations: list[str] = []
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        if _contains_forbidden_text(text):
            violations.append(f"{relative} contains forbidden deletion text")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_forbidden_call(node):
                violations.append(f"{relative} contains a forbidden deletion call")
    assert not violations, "Phase 4 delete-ban violations:\n" + "\n".join(violations)
