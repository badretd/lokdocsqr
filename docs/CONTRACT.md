# LokDocSQR Architecture Contract

Enforced by `tests/test_architecture.py` at every phase gate. Invariants 6 and 7
are exercised by the Phase-5 node-level tests (`tests/test_dry_run.py`,
`tests/test_scope_first.py`) and are not duplicated in the architecture suite.

| ID | Rule | Enforcement |
|----|------|-------------|
| 1 | **Layering.** No module under `src/lokdocsqr/core/` may import `lokdocsqr.gui`, `lokdocsqr.nodes`, `PySide6`, `PyQt5`, or `PyQt6`. | `test_core_does_not_import_gui_nodes_or_qt` — AST import scan. |
| 2 | **GUI isolation.** No module under `src/lokdocsqr/gui/` may import `lokdocsqr.core.safe_fs`. Any other `core` import is allowed. | `test_gui_does_not_import_safe_fs` — AST import scan. |
| 3 | **Delete ban.** Outside `src/lokdocsqr/core/safe_fs.py`, no module under `src/lokdocsqr/` may call `os.remove`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, or `.unlink()`, and may not do `from os import remove|unlink|rmdir` / `from shutil import rmtree`. | `test_no_delete_calls_outside_safe_fs` — AST call scan. |
| 4 | **No-delete API.** `core/safe_fs.py` must not expose public `delete`, `remove`, `rmtree`, or `unlink` (no module-level `def`/`class`/assignment and no entry in `__all__`). | `test_safe_fs_has_no_public_delete_api` — AST public-name scan. |
| 5 | **Node contract.** Every concrete class in `src/lokdocsqr/nodes/` whose base name ends in `Node` must define both `run` and `dry_run`. Classes that inherit `ABC`/`ABCMeta` or mark either method with `@abstractmethod` are exempt. | `test_every_concrete_node_implements_run_and_dry_run` — AST class scan. |
| 6 | **Dry-run purity.** `dry_run()` must not mutate the filesystem. | Phase-5 filesystem tests (`tests/test_dry_run.py`). |
| 7 | **Scope-first.** `fs_ops` nodes must call `scope.check()` before any `safe_fs.*` call. | Phase-5 node tests (`tests/test_scope_first.py`). |
| 8 | **Executor isolation.** No module under `src/lokdocsqr/core/executor/` may import `lokdocsqr.nodes.*` (concrete nodes). | `test_executor_imports_no_concrete_nodes` — AST import scan. |

## Notes

- **Relative imports are resolved.** `test_architecture.py` normalises relative
  import syntax against the containing package, so `from ..gui import x` is
  treated as `lokdocsqr.gui` — the layering rules cannot be bypassed with
  relative syntax.
- **Name re-exports are not followed.** The checks match the resolved *module
  path*, not the local alias. `import lokdocsqr.gui as g` is caught;
  `from lokdocsqr import gui` (which imports the name, not the module) is not.
  Prefer the explicit `from lokdocsqr.gui import ...` form when writing code
  under `core/`.
- **Invariant 3 is conservative.** Any `.unlink()` call outside
  `safe_fs.py` is flagged, even if it is a custom method — this matches the
  intent of the rule. `os.remove` / `os.unlink` / `os.rmdir` /
  `shutil.rmtree` and their `from ... import ...` sources are flagged as well.
- **Invariant 5 is per-class.** A subclass that relies on `Node.run` without
  overriding it will be flagged. This is deliberate in early phases — every
  concrete node should declare its own `run` / `dry_run` so the contract is
  visible at the definition site.
- **Gate activity.** Invariants 1–5 and 8 are checked from Phase 0 onward and
  pass vacuously when the scanned tree does not yet contain matching modules.