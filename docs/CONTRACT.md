# LokDocSQR Architecture Contract

Enforced by `tests/test_architecture.py` at every phase gate.

| ID | Rule | Enforcement |
|----|------|-------------|
| 1 | **Layering.** No module under `src/lokdocsqr/core/` may import `lokdocsqr.gui`, `lokdocsqr.nodes`, `PySide6`, `PyQt5`, or `PyQt6`. | `test_invariant_1_layering` — AST import scan. |
| 2 | **GUI isolation.** No module under `src/lokdocsqr/gui/` may import `lokdocsqr.core.safe_fs` — neither `from lokdocsqr.core.safe_fs import ...` nor `from lokdocsqr.core import safe_fs`. Any other `core` import is allowed. | `test_invariant_2_gui_isolation` — AST import scan. |
| 3 | **Delete ban.** Outside `src/lokdocsqr/core/safe_fs.py`, no module under `src/lokdocsqr/` may call `os.remove`, `os.unlink`, `os.rmdir`, `shutil.rmtree`, or `.unlink()`, and may not do `from os import remove\|unlink\|rmdir` / `from shutil import rmtree`. | `test_invariant_3_delete_ban` — AST call scan; re-checked for `core/audit.py` and `core/executor/*` by `test_no_delete_calls_in_audit_and_executor`. |
| 4 | **No-delete API.** `core/safe_fs.py` must not expose public `delete`, `remove`, `rmtree`, or `unlink` — no module-level `def`/`class`/assignment, no public method, no entry in `__all__`. | `test_invariant_4_no_delete_api` — AST public-name scan. |
| 5 | **Node contract.** Every concrete class in `src/lokdocsqr/nodes/` whose base name is `Node` or ends in `Node` must define both `run` and `dry_run`. Classes that inherit `ABC`/`ABCMeta` or mark either method with `@abstractmethod` are exempt. | `test_invariant_5_node_contract` — AST class scan. |
| 6 | **Dry-run purity.** `dry_run()` must not mutate the filesystem. | `tests/test_dry_run.py::check_invariant_6` (called from `test_invariant_6_dry_run_purity`). |
| 7 | **Scope-first.** `fs_ops` nodes must call `scope.check()` before any `safe_fs.*` call. | `tests/test_scope_first.py::check_invariant_7` (called from `test_invariant_7_scope_first`). |
| 8 | **Executor isolation.** No module under `src/lokdocsqr/core/executor/` may import `lokdocsqr.nodes.*` (concrete nodes). | `test_invariant_8_executor_isolation` — AST import scan. |
| 9 | **Mutation ban.** Outside `src/lokdocsqr/core/safe_fs.py` and the single documented exemption `src/lokdocsqr/core/logging.py`, no module under `src/lokdocsqr/` may call a filesystem-mutating API — see the enumerated list in the "Filesystem access policy" section below. Read-only access is not restricted. | `test_invariant_9_mutation_ban` — AST call scan against `READONLY_FS_ALLOWED` / `MUTATION_FS_ALLOWED`. |

## Filesystem access policy

`src/lokdocsqr/core/safe_fs.py` is the single module that performs filesystem
**mutations** — writes, atomic replace, move, rename, quarantine/trash, and the
internal source cleanup that completes a cross-device move. Its module docstring
lists the guarantees it provides (atomic writes with fsync, Windows-aware
transient-lock retry, no public deletion API) and the list of documented
read-only callers.

**Read-only** access is permitted in other production modules and is not
restricted by invariant 9. The currently known read-only callers are:

| Module | Why it is allowed |
|--------|-------------------|
| `core/config.py` | Reads the JSON config via `Path.read_text`; writes go through `safe_fs.atomic_write`. |
| `core/logging.py` | **Documented mutation exemption.** Creates `logs_dir` and opens the rotating log file directly. Logging bootstraps before `safe_fs` is usable, and a failure inside `safe_fs` must not be able to silence log output. Scoped to this file only. |
| `core/paths.py` | Normalises and validates path strings (`os.path.abspath`, `Path.is_absolute`). No I/O. |
| `core/scope.py` | Resolves paths and inspects symlinks as part of the scope gate that runs *before* any mutation is attempted. |
| `core/audit.py` | Reads via `safe_fs.read_bytes`; the empty-file probe uses `path.exists()` to avoid an exception-driven read on the common empty case. Appends go through `safe_fs.atomic_append`. |
| `core/graph/validator.py` | Inspects path-shaped node parameters as strings via `render_template`; never opens the filesystem. |
| `core/executor/context.py` | Delegates every mutation to `safe_fs`; performs no direct I/O itself. |

Anything under `tests/` is exempt from invariant 9; test code uses
`Path.write_text` and friends freely.

### Enumerated mutating APIs (invariant 9)

The following calls are flagged outside `safe_fs.py` and the logging exemption:

- `pathlib.Path`: `write_text`, `write_bytes`, `rename`, `replace`, `touch`,
  `mkdir`, `rmdir`, `unlink`, `chmod`, `symlink_to`, `hardlink_to`.
- `os`: `remove`, `unlink`, `rename`, `replace`, `rmdir`, `mkdir`, `makedirs`,
  `chmod`, `link`, `symlink`.
- `shutil`: `copy`, `copy2`, `copyfile`, `copytree`, `move`, `rmtree`.

`open(..., "w" / "a" / "x")` is **not** covered by the AST scan — it is caught
by code review only. Prefer `safe_fs.atomic_write` / `safe_fs.atomic_append`;
if a legitimate need for direct `open(..., "w")` ever appears, add it to
invariant 9's detection set rather than letting the call pass silently.

### Adding a new exemption

Any new module that needs direct filesystem access must be added to **both**:

1. the corresponding list in `tests/test_architecture.py` —
   `READONLY_FS_ALLOWED` for read-only access, or `MUTATION_FS_ALLOWED` for the
   rare, individually justified exception; and
2. the "Known read-only callers" block in the `core/safe_fs.py` docstring.

The two lists are the source of truth; a divergence between them is a doc bug,
not a code bug.

## Notes

- **Invariants 6 and 7 — call-time enforcement.** Both invariants are
  now enforced by live tests. `tests/test_dry_run.py::check_invariant_6`
  snapshots the workspace before and after every fs_ops node's
  `dry_run()` and fails on any mutation. `tests/test_scope_first.py::
  check_invariant_7` installs a `ScopeFirstTracker` that wraps both
  `ctx.scope.check` and every mutating `safe_fs.*` method; a mutating
  call whose path was not previously passed through `scope.check` raises
  `RuntimeError("scope.check not called before safe_fs.<name>(<path>)")`
  *at the call site*, not after the mutation lands. Both checkers are
  invoked from `tests/test_architecture.py` so the phase gate exercises
  them.

- **Invariant 5 and underscore helper modules.** Files matching
  `src/lokdocsqr/nodes/**/_*.py` remain exempt from invariant 5 as
  internal helper modules (`_base.py`, `_common.py`). The exemption is
  guarded by `test_invariant_5_no_registered_nodes_in_underscore_modules`,
  which fails if any `@register_node`-decorated class appears in an
  underscore module — so the exemption cannot hide a real node from the
  contract.

- **ZipFile write-pattern AST invariant.** `archive.py` never opens a
  filesystem path with `zipfile.ZipFile(..., mode="w"/"a"/"x")`;
  archives for writing are built in `io.BytesIO` and committed via
  `safe_fs.atomic_write`. Enforced by
  `test_zipfile_never_opens_path_for_write`, which also rejects any
  `.extract(...)` / `.extractall(...)` call in `archive.py`.

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
  intent of the rule. `os.remove` / `os.unlink` / `os.rmdir` / `shutil.rmtree`
  and their `from ... import ...` sources are flagged as well.
- **Invariant 5 and internal base classes.** Files matching
  `src/lokdocsqr/nodes/**/_*.py` are exempt from invariant 5: they exist to
  hold shared scaffolding (`_base.py`) and helpers (`_common.py`), not concrete
  nodes. **Guard:** `test_invariant_5_no_registered_nodes_in_underscore_modules`
  ensures no `@register_node`-decorated class appears in an underscore module —
  otherwise the exemption could hide a real node from the contract.
- **Invariant 9 is call-based.** Only calls to the enumerated APIs are
  flagged. Reading a `Path` attribute, constructing a `Path`, or calling
  `open(..., "r")` is not. `open(..., "w" / "a" / "x")` is a known blind spot
  handled by review; see the note above.
- **Gate activity.** Invariants 1–5 and 8–9 are checked from Phase 0 onward and
  pass vacuously when the scanned tree does not yet contain matching modules.