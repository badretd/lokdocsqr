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
- [ ] `nodes/fs_ops/*`, `nodes/access/*`
- [ ] `nodes/planning/*` (Build Action Plan → Policy Guard → Verify → Audit)
- [ ] `nodes/flow/*`, `nodes/utils/*`
- [ ] `nodes/llm/local_llm.py`
- [ ] `nodes/triggers/*` + `core/runtime.py`
- [ ] `cli/main.py`
- [ ] `gui/*`

The checklist will be updated as work progresses. **No item is complete yet**,
so there is nothing to try — only source code to read and architecture to
discuss in issues.

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

Not yet. Once the minimal vertical slice exists
(`safe_fs` + `fs_ops` + `executor` + `cli`), this section will include a
Quickstart with an example JSON automation and:

```bash
lokdocsqr run examples/move-by-author.json --dry-run
```

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