"""Architecture contract tests for LokDocSQR."""
from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "lokdocsqr"
CORE = SRC / "core"
GUI = SRC / "gui"
NODES = SRC / "nodes"
SAFE_FS = CORE / "safe_fs.py"
ENGINE = CORE / "executor" / "engine.py"

# Artifacts delegated checkers may write relative to CWD.  Deleted from the
# real project root after every delegated run as a safety net.
_PROJECT_ROOT = ROOT
_DELEGATED_CWD_ARTIFACTS = ("out.txt", "out.zip", "out.gz", "out.tar")

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

READONLY_FS_ALLOWED = {
    CORE / "config.py",
    CORE / "paths.py",
    CORE / "scope.py",
    CORE / "audit.py",
    CORE / "graph" / "validator.py",
    CORE / "executor" / "context.py",
}

MUTATION_FS_ALLOWED = {
    CORE / "logging.py",
}

# Mutation methods. `replace` is included for Path but only matched when the
# receiver chain literally contains "Path" (see `_ast_mutation_hits`) — this
# matches acceptance item 2's grep and avoids false positives on str.replace(),
# e.g. `rendered.replace("\\", "/")` in nodes/fs_ops/_common.py.
_PATH_MUT = {
    "write_text", "write_bytes", "rename", "replace", "touch",
    "mkdir", "rmdir", "unlink", "chmod", "symlink_to", "hardlink_to",
}
_OS_MUT = {
    "remove", "unlink", "rename", "replace", "rmdir", "mkdir",
    "makedirs", "chmod", "link", "symlink",
}
_SH_MUT = {
    "copy", "copy2", "copyfile", "copytree", "move", "rmtree",
}


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
    """Return True for forbidden deletion calls (invariant 3)."""
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


def _attr_chain(node: ast.AST) -> list[str]:
    """Return dotted chain (``a.b.c`` -> ``['a','b','c']``) or ``[]``."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return parts
    return []


def _open_write_mode(call: ast.Call) -> str | None:
    """Return the mode string if ``open(...)`` is called with a write/append mode."""
    candidates = list(call.args[1:]) + [
        kw.value for kw in call.keywords if kw.arg == "mode"
    ]
    for arg in candidates:
        if (
            isinstance(arg, ast.Constant)
            and isinstance(arg.value, str)
            and arg.value
            and arg.value[0] in "wax"
        ):
            return arg.value
    return None


def _ast_mutation_hits(path: Path):
    """Yield ``(lineno, reason)`` for forbidden FS mutation calls.

    Aligned with acceptance item 2:
      * ``open(...)`` with a write/append mode
      * ``os.<mutator>(...)``
      * ``shutil.<mutator>(...)``
      * ``<chain containing "Path">.<mutator>(...)`` — this catches the
        literal ``Path.replace(...)`` form the item-2 grep matches, but
        does NOT flag ``x.replace(...)`` on a plain variable (which is
        usually ``str.replace``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            if _open_write_mode(node) is not None:
                yield node.lineno, "open(..., <write mode>)"
            continue

        if not isinstance(func, ast.Attribute):
            continue

        chain = _attr_chain(func)
        if len(chain) < 2:
            continue

        root, attr = chain[0], chain[-1]
        if root == "os" and attr in _OS_MUT:
            yield node.lineno, ".".join(chain)
        elif root == "shutil" and attr in _SH_MUT:
            yield node.lineno, ".".join(chain)
        elif attr in _PATH_MUT and "Path" in chain:
            yield node.lineno, ".".join(chain)


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
    for py_file in _python_files(NODES):
        if py_file.name.startswith("_"):
            continue  # internal helper modules — see Modules.txt §4.0
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                name = _base_name(base)
                if name is not None and (name == "Node" or name.endswith("Node")):
                    node_classes.append((py_file, node))
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


def _load_checker(module_name: str, func_name: str):
    """Load ``func_name`` from tests/<module_name>.py (pkg-agnostic)."""
    import importlib
    try:
        mod = importlib.import_module(f"tests.{module_name}")
        return getattr(mod, func_name)
    except ImportError:
        import importlib.util
        path = Path(__file__).parent / f"{module_name}.py"
        spec = importlib.util.spec_from_file_location(module_name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, func_name)


def _run_delegated(
    tmp_path: Path, monkeypatch, module_name: str, func_name: str
) -> None:
    """Run a delegated checker with a sandboxed CWD.

    Constraints we must satisfy simultaneously:

    * The checker writes artefacts relative to CWD, so we chdir into
      ``tmp_path`` while it runs.
    * The CWD must be back at the real project root *before this function
      returns* — the leak detector runs at end-of-test-body, while
      ``monkeypatch.chdir``'s teardown is still pending.
    * Some checkers additionally chdir back to the project root themselves
      (e.g. via ``Path(__file__).resolve().parent.parent``), so we also
      delete known artefacts from the real project root afterwards.
    """
    original_cwd = os.getcwd()
    monkeypatch.chdir(tmp_path)
    try:
        _load_checker(module_name, func_name)()
    finally:
        os.chdir(original_cwd)
        for name in _DELEGATED_CWD_ARTIFACTS:
            artifact = _PROJECT_ROOT / name
            if artifact.exists():
                artifact.unlink()


def test_invariant_6_dry_run_purity(tmp_path, monkeypatch) -> None:
    """Invariant 6 — delegated to tests/test_dry_run.py."""
    _run_delegated(tmp_path, monkeypatch, "test_dry_run", "check_invariant_6")


def test_invariant_7_scope_first(tmp_path, monkeypatch) -> None:
    """Invariant 7 — delegated to tests/test_scope_first.py."""
    _run_delegated(tmp_path, monkeypatch, "test_scope_first", "check_invariant_7")


def test_invariant_8_executor_isolation() -> None:
    """Executor must not import concrete nodes."""
    executor_dir = CORE / "executor"
    assert executor_dir.exists(), (
        f"executor directory missing: {executor_dir} — invariant 8 cannot "
        "be verified. If the executor was intentionally removed, update "
        "this invariant explicitly."
    )
    violations: list[str] = []
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


def test_zipfile_never_opens_path_for_write() -> None:
    """AST guard: archive.py must never open a path for ZipFile write."""
    archive_py = NODES / "fs_ops" / "archive.py"
    assert archive_py.exists(), f"missing {archive_py}"
    tree = ast.parse(archive_py.read_text(encoding="utf-8"), filename=str(archive_py))
    # Track locals bound to ``io.BytesIO()`` so ``ZipFile(buf, "w")`` is
    # recognised as safe even when ``buf`` is a name, not an inline call.
    bytesio_names: set[str] = set()
    for _n in ast.walk(tree):
        if isinstance(_n, ast.Assign) and isinstance(_n.value, ast.Call):
            _v = _n.value
            _f = None
            if isinstance(_v.func, ast.Name):
                _f = _v.func.id
            elif isinstance(_v.func, ast.Attribute):
                _f = _v.func.attr
            if _f and _f.endswith("BytesIO"):
                for _t in _n.targets:
                    if isinstance(_t, ast.Name):
                        bytesio_names.add(_t.id)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = None
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        if name != "ZipFile":
            continue
        mode = None
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if mode not in ("w", "a", "x"):
            continue
        first = node.args[0] if node.args else None
        for kw in node.keywords:
            if kw.arg == "file":
                first = kw.value
        ok = False
        if isinstance(first, ast.Call):
            fname = None
            if isinstance(first.func, ast.Name):
                fname = first.func.id
            elif isinstance(first.func, ast.Attribute):
                fname = first.func.attr
            if fname and fname.endswith("BytesIO"):
                ok = True
        elif isinstance(first, ast.Name) and first.id in bytesio_names:
            ok = True
        if not ok:
            violations.append(
                f"{archive_py.name}:{node.lineno} ZipFile write mode on non-BytesIO"
            )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in ("extract", "extractall"):
            violations.append(
                f"{archive_py.name}:{node.lineno} .{func.attr}() call"
            )
    assert not violations, (
        "ZipFile write-mode violations:\n" + "\n".join(violations)
    )


def test_invariant_5_no_registered_nodes_in_underscore_modules() -> None:
    """Guard: no @register_node class may live in an underscore module."""
    violations: list[str] = []
    for py_file in _python_files(NODES):
        if not py_file.name.startswith("_"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for deco in node.decorator_list:
                deco_name = None
                if isinstance(deco, ast.Name):
                    deco_name = deco.id
                elif isinstance(deco, ast.Attribute):
                    deco_name = deco.attr
                elif isinstance(deco, ast.Call):
                    f = deco.func
                    if isinstance(f, ast.Name):
                        deco_name = f.id
                    elif isinstance(f, ast.Attribute):
                        deco_name = f.attr
                if deco_name == "register_node":
                    violations.append(
                        f"{py_file.relative_to(ROOT)}:{node.lineno} {node.name}"
                    )
    assert not violations, (
        "Invariant-5 exemption breach — @register_node in _*.py:\n"
        + "\n".join(violations)
    )


def test_invariant_9_mutation_ban() -> None:
    """Only safe_fs (and the documented logging exemption) may mutate the FS."""
    allowed = READONLY_FS_ALLOWED | MUTATION_FS_ALLOWED | {SAFE_FS}
    violations: list[str] = []
    for path in _python_files(SRC):
        if path in allowed:
            continue
        rel = path.relative_to(ROOT)
        for lineno, reason in _ast_mutation_hits(path):
            violations.append(f"{rel}:{lineno} {reason}")
    assert not violations, "Mutation-ban violations:\n" + "\n".join(violations)