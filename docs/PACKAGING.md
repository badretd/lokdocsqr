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
* `LICENSE`, `NOTICE`, `THIRD-PARTY-NOTICES`, `licenses/`, and `README.md`.
* The CLI's node dependencies (`deskew`, `ollama`, `openpyxl`, `pandas`,
  `pillow`, `pypdfium2`, `pytesseract`, `python-docx`). The optional `xattr`
  extra adds extended attribute support.

The spec **excludes** `PySide6` / `PyQt5` / `PyQt6` and `tkinter`. The CLI never
imports `lokdocsqr.gui`, and PySide6 is only installed through the optional
`gui` extra, so the desktop stack is not part of this binary.

### Node dependencies and optional extras

The OCR/PDF/DOCX/LLM/table nodes import their libraries lazily, so a missing or
broken installation reports a clear runtime error instead of breaking startup.
Those libraries are part of the CLI package. `uv sync --group dev` installs the
full CLI dependency set and the test tooling; add `--extra gui` only when you
are building the GUI, and `--extra xattr` for extended attribute support.

---

## 3. Smoke-test the build

Run these commands against the built executable (`.exe` omitted on POSIX):

```bash
./dist/lokdocsqr/lokdocsqr --version
./dist/lokdocsqr/lokdocsqr list-nodes --json
./dist/lokdocsqr/lokdocsqr compile examples/vertical_slice.ldsq
./dist/lokdocsqr/lokdocsqr validate examples/vertical_slice.json
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json --dry-run
# Trigger dispatch is local-only (no socket): a webhook payload can be
# supplied inline, from a file (@FILE) or stdin (-).
./dist/lokdocsqr/lokdocsqr trigger <automation> --type webhook \
  --payload '{"event": "smoke"}' --dry-run
# Daemon smoke. vertical_slice declares no triggers, so --daemon runs once
# and exits with guidance; use an automation with a `trigger` block to host
# watch/cron loops.
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json --dry-run --daemon
```

Expected:

* `--version` prints `lokdocsqr 0.1.0` (or the current package version).
* `list-nodes` shows `fs.make_dir`, `fs.write_file`, `fs.move`, etc.
* `compile` prints the canonical automation JSON schema (DSL → JSON).
* `validate` prints `OK: vertical-slice (3 nodes, 0 edges)`.
* `run --dry-run` exits `0`, writes `dry_run` audit entries, and creates no
  automation output files.
* `trigger --dry-run` dispatches the local trigger event without touching disk.
* `run --daemon` on a trigger-less automation runs once and exits instead of
  idling.

A real run needs an explicit opt-in because the CLI defaults to the fail-closed
`PolicyEngine`:

```bash
./dist/lokdocsqr/lokdocsqr run examples/vertical_slice.json \
  --base-dir /tmp/lokdocsqr-demo --no-dry-run
```

Then inspect and reverse the audit log:

```bash
./dist/lokdocsqr/lokdocsqr audit runs --base-dir /tmp/lokdocsqr-demo
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
dist/lokdocsqr/_internal/licenses/gpl-3.0.txt
dist/lokdocsqr/_internal/licenses/lgpl-3.0.txt
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
| a node reports `... not installed` | library absent at build time | install the library before building, or accept the runtime error |
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

---

## 7. Cross-platform builds

PyInstaller cannot cross-compile, so build and smoke-test the CLI on each
target OS you ship. The build steps are the same as §2.

### 7.1 Platform notes

* **Linux** builds link against the glibc of the build machine. If you must
  support older distributions, build in a container with the oldest glibc you
  target.
* **macOS** builds are architecture-specific. Build on the architecture you
  ship; for universal binaries you need an explicit `target_arch` / `lipo`
  step, which is outside this spec.
* **Windows** builds produce an `.exe` plus `_internal/`. Sign the executable
  in your release process if you distribute widely (see §5).
* **Lean CLI builds:** `PySide6` is an optional `gui` extra and is not part of
  the `dev` group. The CLI never uses it (the GUI package is a stub and the
  spec filters `lokdocsqr.gui` out of the hidden imports), so
  `uv sync --group dev` builds a CLI without the GUI stack.
* **No network at runtime.** The CLI never opens a listener; the build only
  uses the network to fetch dependencies.

### 7.2 Artifacts and checksums

Archive names encode the platform. Add a checksum step if you publish
downloads:

```bash
sha256sum dist/lokdocsqr.tar.gz > dist/lokdocsqr.tar.gz.sha256   # POSIX
```

Record the PyInstaller and Python versions in the release notes for
reproducibility.

---

## 8. Release checklist

Before tagging a release:

- [ ] `uv run pytest -q` is green on the OS you are releasing for.
- [ ] `uv run python tools/verify_dsl_docs.py` passes.
- [ ] `uv run ruff check src tests` and `uv run mypy src/lokdocsqr` pass.
- [ ] `uv run pyinstaller packaging/lokdocsqr-cli.spec --noconfirm` builds.
- [ ] The smoke tests in §3 pass against the frozen binary.
- [ ] `LICENSE`, `NOTICE`, `THIRD-PARTY-NOTICES`, and `licenses/` are present in `_internal/`.
- [ ] The version in `pyproject.toml` / `lokdocsqr.__version__` is bumped.
- [ ] Release notes list PyInstaller and Python versions.

