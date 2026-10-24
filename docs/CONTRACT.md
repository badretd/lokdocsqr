# LokDocSQR Architecture Contract

This contract is enforced by `tests/test_architecture.py` at every gate.
Invariants 6 and 7 are verified in Phase 5; invariant 8 becomes active in Phase 4.

| ID | Rule | Verification |
|---|---|---|
| 1 | **Layering:** `core/**` must not import `gui`, `nodes`, `PySide6`, `PyQt5`, or `PyQt6`. | AST import scan over `src/lokdocsqr/core`. |
| 2 | **GUI isolation:** `gui/**` must not import `core.safe_fs` directly. Other `core` imports are allowed. | AST import scan over `src/lokdocsqr/gui`. |
| 3 | **Delete ban:** outside `core/safe_fs.py`, there must be no `os.remove`, `shutil.rmtree`, or `Path.unlink` calls. | AST call scan plus text grep. |
| 4 | **No-delete API:** `core/safe_fs.py` must not export public `delete`, `remove`, `rmtree`, or `unlink` names. | AST public-name scan of `core/safe_fs.py`. |
| 5 | **Node contract:** every concrete `Node` subclass implements `run` and `dry_run`. | AST class scan over `src/lokdocsqr/nodes`. |
| 6 | **Dry-run purity:** `dry_run()` must not mutate the filesystem. | Verified with filesystem tests in Phase 5. |
| 7 | **Scope-first:** `fs_ops` nodes call `scope.check()` before `safe_fs.*`. | Verified with node tests in Phase 5. |
| 8 | **Executor isolation:** `core/executor/**` imports no concrete nodes, only `NodeRegistry`. | AST import scan over `core/executor` from Phase 4. |

Invariants 1-5 and 8 are enforced by architecture tests on each gate where their targets exist.
