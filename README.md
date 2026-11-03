# LokDocSQR

> **Status: pre-1.0.** The headless CLI (Phase 11) is complete and usable.
> The desktop GUI (Phase 12) is not implemented yet. Treat the project as
> pre-1.0 and test against a copy of your data.

A **local-first** automation engine for files: graph-based workflows with
dry-run, append-only audit and rollback, scope-enforced filesystem operations,
and optional local LLM support.

LokDocSQR runs locally by default. It opens no network listener, and every
automation, trigger and audit record stays on your machine. The optional LLM
node talks to the Ollama endpoint you configure (localhost by default).

---

## What This Is

LokDocSQR builds automations over local files, for example: *"when a PDF
appears in this folder, move it to `{year}/{category}/` and record the decision
in the audit log."*

Workflows are described as a graph of nodes. Every filesystem operation goes
through `core.safe_fs`, respects `WorkspaceScope`, supports dry-run, writes an
`ActionLog`, and can be rolled back.

Two binaries are planned from one codebase:

- `cli/` — headless mode (batch runs, watchers, scheduler, audit). **This is
  the release target.**
- `gui/` — desktop graph editor (Phase 12, not implemented).

---

## Requirements

- Python 3.10+ (the project targets 3.10).
- A writable workspace directory for the files you automate.
- Optional: the GUI extra (PySide6, LGPLv3) and `xattr` support. The CLI
  includes the OCR, PDF, DOCX, table and local LLM libraries.

---

## Installation

From a checkout, using [uv](https://docs.astral.sh/uv/):

```bash
uv sync --group dev
uv run lokdocsqr --version
```

The core install is the full CLI:

```bash
uv pip install -e .
lokdocsqr --version
```

Optional additions:

```bash
uv pip install -e '.[gui]'    # PySide6 desktop GUI (LGPLv3)
uv pip install -e '.[xattr]'  # extended attribute support for fs.tag
```

`python -m lokdocsqr` is equivalent to the installed `lokdocsqr` entry point.

---

## Quick Start

Validate and dry-run the bundled vertical slice:

```bash
lokdocsqr validate examples/vertical_slice.json
lokdocsqr run examples/vertical_slice.json --dry-run
lokdocsqr list-nodes
```

Browse the runnable DSL automations — inbox archiving, batch rename, filtering,
watch/scheduled triggers, search, trash and conversion:

```bash
cat examples/README.md
```

Compile the DSL example to canonical automation JSON:

```bash
lokdocsqr compile examples/vertical_slice.ldsq
lokdocsqr compile examples/vertical_slice.ldsq --output /tmp/vertical_slice.json
```

A real run against a scratch workspace:

```bash
lokdocsqr run examples/vertical_slice.json \
  --base-dir /tmp/lokdocsqr-demo --no-dry-run

lokdocsqr audit show <run-id> --base-dir /tmp/lokdocsqr-demo
lokdocsqr audit rollback <run-id> --base-dir /tmp/lokdocsqr-demo
```

---

## Dry-Run, Real Runs and Approval

Every mutation has two phases:

| Flag | Behaviour |
|---|---|
| `--dry-run` | Simulate; write `dry_run` audit entries; touch nothing. |
| `--no-dry-run` | Execute for real **and** approve required verification. |
| `--yes` | Approve verification for real runs (unattended). |

The default `PolicyEngine` is **fail-closed**: `write`, `move`, `copy` and
`mkdir` require verification. A document that sets `default_dry_run = false`
requests real execution, but the operator must still opt in on the command line
with `--no-dry-run` or `--yes`. If a real-run document is invoked without
approval, the CLI refuses **before any node runs** and prints the exact flags
to add.

A `--policy FILE` overrides the built-in policy. A `--config FILE` sets
`base_dir`, `trash_dir`, `logs_dir` and scope roots; `--scope PATH`
(repeatable) overrides the writable workspace. Automations must use `.json` or
`.ldsq`; other extensions are rejected.

---

## Triggers and Long-Running Mode

An automation can declare triggers (`watch_folder`, `scheduled_scan`) that the
runtime hosts. See [`docs/DSL.md`](./docs/DSL.md) §10 for the full reference.

```bash
# Host watch/cron triggers until Ctrl+C or SIGTERM.
lokdocsqr serve <automation-or-directory...>

# Run each automation once first, then host its triggers.
lokdocsqr serve --daemon <automation...>
lokdocsqr daemon <automation...>          # alias for serve --daemon

# Run one automation once, then host its triggers.
lokdocsqr run <automation> --daemon
```

`--daemon` / `serve` are only meaningful for automations that declare
`watch_folder` or `scheduled_scan` triggers. With no such triggers, `serve`
exits with guidance and `run --daemon` performs its single run and exits
instead of idling.

An explicit `--dry-run` / `--no-dry-run` applies to the **whole** session: it is
propagated to every trigger binding, so trigger-driven runs use the same mode
as the initial run. Without a flag, each document's `default_dry_run` governs.

`manual_run` and `webhook` triggers are **not** served continuously. Fire them
locally, with no socket and no network listener:

```bash
lokdocsqr trigger <automation> --type manual_run --files inbox/a.pdf
lokdocsqr trigger <automation> --type webhook --payload '{"event": "pushed"}'
lokdocsqr trigger <automation> --type webhook --payload @payload.json
cat payload.json | lokdocsqr trigger <automation> --type webhook --payload -
```

---

## Audit and Rollback

The audit log is append-only: every run appends its entries to
`<base-dir>/.logs/audit.jsonl`, so the full history across runs is preserved.
Each run — including every watch or schedule cycle — gets its own run id.

```bash
# List runs: run id, automation, entry/applied/rollback counts, time range.
lokdocsqr audit runs --base-dir <workspace>
lokdocsqr audit runs --automation <id> --last 20 --json --base-dir <workspace>

# Show one run, the whole log, or one file's history across runs.
lokdocsqr audit show <run-id> --base-dir <workspace>
lokdocsqr audit show --base-dir <workspace>                    # all entries
lokdocsqr audit show --path inbox/report.pdf --base-dir <workspace>

# Review and reverse a run's applied, rollback-eligible actions.
lokdocsqr audit rollback <run-id> --base-dir <workspace> --dry-run
lokdocsqr audit rollback <run-id> --base-dir <workspace>
```

`audit show <run-id>` is scoped to that run. Because each daemon/watch cycle is
a separate run, use `audit runs` to find the id, or `audit show --path` to
follow one file across runs. `audit rollback` is likewise per run: to undo a
sequence, roll back the run ids in reverse chronological order (each rollback
is itself appended to the audit log).

### Retention and cleanup

Rollback data has two parts, and **neither is cleaned up automatically** —
deleting it is irreversible, so cleanup is an explicit, previewable operator
action:

| Data | What it is | Command |
|---|---|---|
| Audit entries | the rollback **plan** (what moved where) | `audit prune` |
| Trash contents + sidecars | the rollback **payload** for `fs.trash` | `trash sweep` |

```bash
# Trash TTL: each fs.trash sidecar records ttl_expires_at (default 30 days).
lokdocsqr trash sweep --base-dir <workspace> --dry-run
lokdocsqr trash sweep --base-dir <workspace>

# Audit retention: keep the last 90 days and/or the newest N runs.
lokdocsqr audit prune --older-than 90 --base-dir <workspace> --dry-run
lokdocsqr audit prune --keep-runs 500 --base-dir <workspace>
```

- `trash sweep` removes only regular files inside the trash directory whose
  sidecar TTL has passed, plus their sidecars. Unknown files, symlinks, and
  entries without a TTL sidecar are left untouched.
- `audit prune` drops **whole runs** (originals and their rollback entries
  together) so retained runs still produce a correct rollback plan. It reports
  how many applied, rollback-eligible actions become irreversible.
- Schedule either command (cron, systemd timer, Task Scheduler) or call
  `Runtime.sweep_trash()` from an embedder.
- Keep audit retention **at least as long as** the trash TTL, so
  `audit rollback` can still restore a trashed file within its TTL. A pruned
  audit run cannot be rolled back automatically.

Locations (defaults):

| Thing | Location |
|---|---|
| Workspace base | `--base-dir`, else config `base_dir`, else current directory |
| Scope roots | `--scope`, else workflow settings, else config, else `base_dir` |
| Trash | config `trash_dir`, else `<base_dir>/.trash` |
| Audit log | config `logs_dir`, else `<base_dir>/.logs/audit.jsonl` |
| Runtime lock | `<base_dir>/.lokdocsqr-runtime.lock` |

The CLI never writes files directly. All mutations go through
`core.safe_fs` via the executor/nodes, and rollback goes through
`AuditLog.apply_rollback`.

---

## Project Principles (Non-Negotiable)

1. **One-way dependencies.** `nodes → core.node_api → core`.
   `core` never imports `gui`, `nodes`, or PySide. `gui` never imports
   `core.safe_fs` directly.
2. **Only `core.safe_fs` touches the filesystem.** There is intentionally no
   deletion API — only `quarantine()` / `trash()` with metadata and TTL.
3. **All filesystem work goes through `WorkspaceScope`.** No escapes outside
   declared roots, no `..` escapes, no symlink policy bypasses.
4. **Dry-run is first-class.** Every `fs_ops` node implements `dry_run()` and
   returns a synthetic `ActionLog` without touching disk.
5. **Audit is append-only.** Every file change leaves a record, and every
   record is suitable for rollback planning.
6. **Verify is mandatory** before irreversible or high-risk operations. The
   only bypass is an explicit rule in the automation JSON.

---

## Development Status

Following the plan in [`docs/modules.md`](./docs/modules.md), section 8.

- [x] `core/safe_fs.py`, `core/errors.py`, `core/models.py`, `core/scope.py`
- [x] `core/node_api/*`, `core/plugins.py`, `core/events.py`,
      `core/logging.py`, `core/config.py`
- [x] `core/graph/*` (parser → validator → resolver → builder)
- [x] `core/executor/*`, `core/policy.py`, `core/audit.py`
- [x] `nodes/fs_ops/*`, `nodes/access/*`
- [x] `nodes/planning/*` (Build Action Plan → Policy Guard → Verify → Audit)
- [x] `nodes/flow/*`, `nodes/utils/*`
- [x] `nodes/llm/local_llm.py`
- [x] `nodes/triggers/*` + `core/runtime.py`
- [x] `cli/main.py` (run, compile, validate, list-nodes, trigger, serve,
      daemon, audit)
- [ ] `gui/*`

See [`docs/PHASE_STATE.md`](./docs/PHASE_STATE.md) for the current phase status
and the implemented / tested / integrated breakdown.

---

## Testing

The repository ships an extensive test suite (1500+ tests) covering the core
invariants, the executor, every node family, the DSL round-trip, the runtime
triggers and the CLI:

```bash
uv run pytest -q
```

Documentation examples are executable and can be checked locally:

```bash
uv run python tools/verify_dsl_docs.py   # docs/DSL.md examples compile/round-trip
uv run pytest tests/test_dsl_docs.py tests/test_contract_docs.py
```

Static checks:

```bash
uv run ruff check src tests
uv run mypy src/lokdocsqr
```

---

## Building Release Binaries

The CLI is packaged with PyInstaller (one-dir mode). See
[`docs/PACKAGING.md`](./docs/PACKAGING.md) for the full build guide, smoke
tests, data-file layout and troubleshooting.

Build locally (on the target OS — PyInstaller is not a cross-compiler):

```bash
uv sync --group dev
uv run pyinstaller packaging/lokdocsqr-cli.spec --noconfirm
./dist/lokdocsqr/lokdocsqr --version
```

Cross-platform builds (Linux, macOS, Windows) must be produced on each target
OS; PyInstaller is not a cross-compiler. See
[`docs/PACKAGING.md`](./docs/PACKAGING.md#7-cross-platform-builds) for the
platform-specific build notes.

---

## Documentation

- [`examples/README.md`](./examples/README.md) — runnable DSL automations and a feature tour.
- [`docs/modules.md`](./docs/modules.md) — the authoritative module map.
- [`docs/DSL.md`](./docs/DSL.md) — the automation DSL and full node reference.
- [`docs/SCHEMA.md`](./docs/SCHEMA.md) — the JSON automation schema.
- [`docs/PACKAGING.md`](./docs/PACKAGING.md) — building and shipping the CLI.
- [`docs/PHASE_STATE.md`](./docs/PHASE_STATE.md) — current phase status.

---

## Contributing

The project is pre-1.0. Issues and architecture discussions are welcome.
Please open an issue before a substantial pull request.

---

## License

Licensed under the **Apache License, Version 2.0**.

See the [`LICENSE`](./LICENSE) file for the full license text.

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an **“AS IS” BASIS, WITHOUT
WARRANTIES OR CONDITIONS OF ANY KIND**, either express or implied. See the
License for the specific language governing permissions and limitations under
the License.

### Attribution and Third-Party Components

- [`NOTICE`](./NOTICE) — attribution notices for this project, as described
  in Section 4(d) of the Apache License 2.0. Downstream distributions that
  include this project must preserve the contents of this file.
- [`THIRD-PARTY-NOTICES`](./THIRD-PARTY-NOTICES) — license and attribution
  information for third-party dependencies bundled with, vendored into, or
  otherwise redistributed as part of LokDocSQR. This file is informational
  for users and contributors; it does not modify the terms of the Apache
  License 2.0 that governs this project’s own source code.

If you redistribute LokDocSQR (in source or binary form), you are responsible
for complying with the licenses of all third-party components listed in
`THIRD-PARTY-NOTICES`, in addition to the terms of the Apache License 2.0.
