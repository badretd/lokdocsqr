# LokDocSQR examples

Runnable automations written in the LokDocSQR DSL (`.ldsq`). Each file is
self-contained and safe by default: every example sets
`default_dry_run = true`, so a plain `run` only **simulates** the work. Add
`--no-dry-run` (or `--yes`) to execute for real.

LokDocSQR is local-only: no example opens a network listener or uploads data.

---

## Installation

From the repository root:

```bash
uv sync --group dev
uv run lokdocsqr --version
```

Or install into an existing environment:

```bash
uv pip install -e .
lokdocsqr --version
```

`python -m lokdocsqr …` is equivalent to `lokdocsqr …`.

---

## Compile and validate

Compile a DSL automation to the canonical JSON schema:

```bash
uv run lokdocsqr compile examples/01_inbox_to_archive_by_date.ldsq
uv run lokdocsqr compile examples/01_inbox_to_archive_by_date.ldsq --output /tmp/archive.json
```

Validate without executing anything:

```bash
uv run lokdocsqr validate examples/01_inbox_to_archive_by_date.ldsq
```

---

## Try an example safely

The repository ships a small `sample_data/` tree (`inbox/`, `docs/`). Copy it
first, because real runs move, rename and trash files:

```bash
cp -r examples/sample_data /tmp/lokdocsqr-examples

# Simulate (default: default_dry_run = true)
uv run lokdocsqr run examples/01_inbox_to_archive_by_date.ldsq \
  --base-dir /tmp/lokdocsqr-examples --dry-run --explain

# Execute for real (explicit approval)
uv run lokdocsqr run examples/01_inbox_to_archive_by_date.ldsq \
  --base-dir /tmp/lokdocsqr-examples --no-dry-run
```

`--base-dir` is the workspace the automation's relative paths resolve against.
Audit entries are written under `<base-dir>/.logs/audit.jsonl`; inspect and
roll back with:

```bash
uv run lokdocsqr audit show <run-id>     --base-dir /tmp/lokdocsqr-examples
uv run lokdocsqr audit rollback <run-id> --base-dir /tmp/lokdocsqr-examples
```

---

## What each automation does

| File | What it does | Demonstrates |
|---|---|---|
| `01_inbox_to_archive_by_date.ldsq` | Moves every file in `inbox/` to `archive/<file-date>/`. | `list_files → loop_batch → path_template → fs.move`; `{date}`/`{name}`/`{ext}`; `target_dir` |
| `02_batch_rename_invoices.ldsq` | Renames `inbox/*.pdf` to `acme-<name>.pdf`. | `list_files` pattern filter; `fs.rename`; conflict policy |
| `03_filter_and_copy_for_review.ldsq` | Copies non-empty `inbox/` files to `review/`, keeping originals. | `flow.filter` (`size > 0`); `fs.copy` |
| `04_watch_inbox_to_processed.ldsq` | Watches `inbox/` and moves new PDFs to `processed/`. | `watch_folder` trigger + trigger node; long-running mode |
| `05_scheduled_inbox_backup.ldsq` | Every night, copies `inbox/` files to `backup/<date>/`. | `scheduled_scan` trigger; cron + `fs.copy` |
| `06_find_todos.ldsq` | Read-only search for `TODO` across `docs/`. | `access.index_search`; read-only automation |
| `07_quarantine_large_files.ldsq` | Moves files larger than 1 KiB from `inbox/` to the workspace trash. | `flow.filter` (size); `fs.trash` (no deletion API) |
| `08_convert_csv_to_json.ldsq` | Converts `inbox/rows.csv` to `archive/rows.json`. | `fs.convert` (CSV ↔ JSON) |
| `vertical_slice.ldsq` | Minimal end-to-end slice: create folder, write file, move it. | `fs.make_dir`, `fs.write_file`, `fs.move` |

`vertical_slice.json` is the canonical-JSON form of `vertical_slice.ldsq`, kept
for the README/docs examples.

---

## Trigger-driven examples

`04_watch_inbox_to_processed.ldsq` and `05_scheduled_inbox_backup.ldsq` declare
triggers, so `serve` / `daemon` keep them running:

```bash
# Run each once, then host their watch/cron triggers until Ctrl+C.
uv run lokdocsqr serve \
  examples/04_watch_inbox_to_processed.ldsq \
  examples/05_scheduled_inbox_backup.ldsq \
  --base-dir /tmp/lokdocsqr-examples --dry-run

# Run one automation once, then host its triggers.
uv run lokdocsqr run examples/04_watch_inbox_to_processed.ldsq \
  --base-dir /tmp/lokdocsqr-examples --daemon --dry-run
```

`serve` with an automation that declares no `watch_folder`/`scheduled_scan`
trigger exits with guidance — use `run` for one-shot work, or `trigger` for
manual/webhook dispatch:

```bash
uv run lokdocsqr trigger examples/vertical_slice.ldsq \
  --base-dir /tmp/lokdocsqr-examples --type manual_run --dry-run
```

---

## Notes

- **Dry-run first.** All examples default to `default_dry_run = true`. Inspect
  the plan with `--explain` or `--json` before adding `--no-dry-run`.
- **Use a scratch workspace.** Real runs mutate the base directory. Copy
  `sample_data/` (or point `--base-dir` at your own data) rather than running
  against the repository tree.
- **Scope.** Each example declares its own `scope.roots`/`scope.writable`.
  Paths are relative to `--base-dir`; nothing outside those roots is touched.
- **Optional formats.** The examples use plain text/CSV/PDF placeholders. Nodes
  for real PDF/DOCX/OCR extraction and local LLM calls need their optional
  dependencies and are documented in `docs/DSL.md` §9.
- **Audit and rollback.** `fs.move`, `fs.rename` and `fs.trash` are
  rollback-eligible; `audit rollback <run-id>` reverses them.
- **Full reference.** See [`docs/DSL.md`](../docs/DSL.md) for every node and
  [`docs/PACKAGING.md`](../docs/PACKAGING.md) for building the CLI.
