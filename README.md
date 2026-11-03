# LokDocSQR

> **Status: pre-alpha. Active development. Nothing is stable; nothing works end-to-end yet. Do not use in production.**

A local automation builder for files: graph-based workflows, dry-run, audit and rollback, safe filesystem operations, and optional local LLM support.

---

## What This Is

LokDocSQR is a desktop and headless tool for building automations over local files: “if a PDF from a certain author appears in this folder, move it to `{year}/{category}/` and record the decision in the audit log.”

Workflows are described as a graph of nodes. Every filesystem operation goes through `safe_fs`, respects `WorkspaceScope`, supports dry-run, writes an `ActionLog`, and can be rolled back.

Two binaries from one codebase:

- `cli/` — headless mode (watchers, scheduler, batch runs).
- `gui/` — desktop graph editor and run monitor.

---

## Tests, tests, tests...
In terms of tests, this is the worst I’ve ever seen. Right now, we have 500+ completely useless tests that verify themselves, trigger other tests, check documentation, and—God knows what else. I honestly tried to fix this using AI, but neither my sanity nor the AI’s could handle it.

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

Following the plan in [`modules.md`](./docs/modules.md), section 8. The order is:

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
- [x] `cli/main.py` (run, validate, list-nodes, serve, audit)
- [ ] `gui/*`

The checklist is updated as work progresses. See
[`docs/PHASE_STATE.md`](./docs/PHASE_STATE.md) for the current phase status and
the implemented / tested / integrated breakdown.

---

## Why This May Be Interesting

- Filesystem operations with guaranteed dry-run, audit, and rollback are rare
  in desktop automation tools.
- Strict `core` / `nodes` / `gui` separation means nodes can be tested in
  isolation against a temporary workspace without pulling in Qt.
- The only module that touches disk is `core/safe_fs.py`. This is not “best
  effort”; it is an architectural invariant enforced by tests and review.

---

## How to Run

Install the project in editable mode (or use `uv run`):

```bash
uv pip install -e .
lokdocsqr --version
```

Validate and dry-run the bundled vertical slice:

```bash
lokdocsqr validate examples/vertical_slice.json
lokdocsqr run examples/vertical_slice.json --dry-run
lokdocsqr list-nodes
```

Compile the DSL example to canonical automation JSON:

```bash
lokdocsqr compile examples/vertical_slice.ldsq
lokdocsqr compile examples/vertical_slice.ldsq --output /tmp/vertical_slice.json
```

A real run is an explicit opt-in. `--no-dry-run` (or `--yes`) supplies the
human approval required by the default fail-closed policy engine:

```bash
lokdocsqr run examples/vertical_slice.json \
  --base-dir /tmp/lokdocsqr-demo --no-dry-run

lokdocsqr audit show <run-id> --base-dir /tmp/lokdocsqr-demo
lokdocsqr audit rollback <run-id> --base-dir /tmp/lokdocsqr-demo
```

Use `--config FILE` to set `base_dir`, `trash_dir`, `logs_dir` and scope roots,
and `--scope PATH` (repeatable) to override the writable workspace. A
`--policy FILE` overrides the built-in fail-closed policy. Automations must use
`.json` or `.ldsq`; other extensions are rejected.

`lokdocsqr serve <automation...>` hosts watch/cron triggers until Ctrl+C.
See [`docs/PACKAGING.md`](./docs/PACKAGING.md) for the PyInstaller CLI build.

---

## Documentation

- [`modules.md`](./docs/modules.md) — the authoritative module map: paths,
  responsibilities, and dependency rules. This is the source of truth for
  architecture.
- Everything else will be added as it appears. For now, the README and
  `modules.md` cover what you need to know at the start.

---

## Contributing

The project is in early development. Issues and architecture discussions are
welcome. Pull requests should be preceded by an issue so time is not wasted.
A contributing guide will appear when there is something to contribute to.

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