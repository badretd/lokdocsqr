# Packaging LokDocSQR with PyInstaller

Phase 11 prepares a reproducible **one-dir** PyInstaller build for the CLI.
The repository intentionally ships the spec and entry script; it does not ship
a pre-built binary.

Two binaries are planned:

| Target | Entry point | Spec | Status |
|---|---|---|---|
| CLI (`lokdocsqr`) | `lokdocsqr.cli.main:main` | `packaging/lokdocsqr-cli.spec` | ready to build |
| GUI (`lokdocsqr-gui`) | `lokdocsqr.gui.app:main` | not yet written (Phase 12) | stub |

---

## 1. Prerequisites

* Python 3.10+ (the project targets 3.10).
* The project development environment, including PyInstaller:

  ```bash
  uv sync --group dev
  ```

  `pyinstaller` is already declared in the `dev` dependency group.

* Build on the **same OS/architecture** you intend to distribute to.
  PyInstaller is not a cross-compiler: build Windows binaries on Windows,
  Linux binaries on Linux, and macOS binaries on macOS.

---

## 2. Build the CLI

From the repository root:

```bash
uv run pyinstaller packaging/lokdocsqr-cli.spec --noconfirm
```

Output:

```text
dist/
└── lokdocsqr/
    ├── lokdocsqr          # lokdocsqr.exe on Windows
    └── _internal/         # Python runtime, packages, data files
```

The spec uses **one-dir** mode, matching the decision recorded in
`docs/devstart.md`: faster startup, measurable size, and no per-launch
unpacking. Do not add `--onefile` unless you deliberately want a different
distribution model.

### What the spec bundles

* `packaging/pyinstaller_cli.py` — the frozen equivalent of the
  `lokdocsqr = "lokdocsqr.cli.main:main"` console script.
* Every `lokdocsqr.*` submodule, including all built-in node families, via
  `collect_submodules("lokdocsqr")`.
* `LICENSE`, `NOTICE`, `THIRD-PARTY-NOTICES`, and `README.md`.
* Declared third-party dependencies that the CLI actually imports
  (`pypdfium2`, `pytesseract`, `python-docx`, `ollama`, etc. are pulled in
  lazily by their node modules).

The spec **excludes** `PySide6` / `PyQt5` / `PyQt6` and `tkinter`. The CLI never
imports `lokdocsqr.gui`, so the desktop stack is not part of this binary.

### Optional dependencies

The OCR/PDF/DOCX/LLM nodes import their optional libraries lazily. If a library
is installed in the build environment, PyInstaller includes it; if it is
missing, the node reports a clear runtime error instead of breaking startup.
To keep the CLI small, install only the extras you need before building, for
example:

```bash
uv sync --group dev --extra xattr
```

---

## 3. Smoke-test the build

Run these commands against the built executable (`.exe` omitted on POSIX):

```bash
./dist/lokdocsqr/lokdocsqr --version
./dist/lokdocsqr/lokdocsqr list-nodes --json
./dist/lokdocsqr/lokdocsqr compile examples/vertical_slice.ldsq
./dist/lokdocsqr/lokdocsqr validate examples/vertical_slice.json
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json --dry-run
# CLI daemon smoke: starts, runs once, then serves declared triggers.
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json --dry-run --daemon
```

Expected:

* `--version` prints `lokdocsqr 0.1.0` (or the current package version).
* `list-nodes` shows `fs.make_dir`, `fs.write_file`, `fs.move`, etc.
* `compile` prints the canonical automation JSON schema (DSL → JSON).
* `validate` prints `OK: vertical-slice (3 nodes, 0 edges)`.
* `run --dry-run` exits `0`, writes `dry_run` audit entries, and creates no
  automation output files.

A real run needs an explicit opt-in because the CLI defaults to the fail-closed
`PolicyEngine`:

```bash
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json \
  --base-dir /tmp/lokdocsqr-demo --no-dry-run
```

Then inspect and reverse the audit log:

```bash
./dist/lokdocsqr/lokdocsqr audit show <run-id> --base-dir /tmp/lokdocsqr-demo
./dist/lokdocsqr/lokdocsqr audit rollback <run-id> --base-dir /tmp/lokdocsqr-demo
```

### About data files in one-dir builds

PyInstaller 6 places bundled data files inside `dist/lokdocsqr/_internal/` by
default. The license notices are therefore at:

```text
dist/lokdocsqr/_internal/LICENSE
dist/lokdocsqr/_internal/NOTICE
dist/lokdocsqr/_internal/THIRD-PARTY-NOTICES
```

If you need them at the distribution root for a particular installer format,
post-process the `dist/lokdocsqr` tree or add a platform-specific installer
step. Do not edit the generated `_internal` layout by hand.

---

## 4. Where the CLI reads and writes

The CLI is a thin shell around the core:

| Thing | Location |
|---|---|
| Workspace base directory | `--base-dir`, else config `base_dir`, else current directory |
| Scope roots | `--scope` (repeatable), else workflow settings, else config `scope_roots`, else `base_dir` |
| Trash | config `trash_dir`, else `<base_dir>/.trash` |
| Audit log | config `logs_dir`, else `<base_dir>/.logs`, file `audit.jsonl` |
| Runtime lock (`serve`) | `<base_dir>/.lokdocsqr-runtime.lock` |

The CLI never writes files directly. All mutations go through
`core.safe_fs` via the executor/nodes, and rollback goes through
`AuditLog.apply_rollback`.

---

## 5. Build troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Unknown node type` in the frozen binary | a node family was not collected | confirm `collect_submodules("lokdocsqr")` is present in the spec |
| `ModuleNotFoundError: PySide6` at build time | an accidental GUI import from the CLI path | keep `lokdocsqr.gui` out of `cli/main.py`; verify with `tests/test_architecture.py` |
| optional node reports `... not installed` | optional library absent at build time | install the extra before building, or accept the runtime error |
| `PermissionError` writing the audit log | base directory is read-only | pass `--base-dir`/`--config` pointing at a writable workspace |
| antivirus flags a fresh Windows build | unsigned PyInstaller bootloader | sign the executable with your normal release process |

---

## 6. Reproducibility notes

* Build from a clean checkout with the committed `uv.lock`.
* Record the PyInstaller version (`pyinstaller --version`) with each release.
* Keep the spec under version control; do not pass ad-hoc `--hidden-import`
  flags that are missing from the spec.
* `docs/devstart.md` describes the size budget. Measure the result with:

  ```bash
  du -sh dist/lokdocsqr
  ```

  The historical 260 MB figure is not a gate; the reproducible spec above is.
