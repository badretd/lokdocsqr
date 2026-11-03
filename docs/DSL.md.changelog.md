# docs/DSL.md — Phase 10.5 fix changelog

This pass supersedes the earlier documentation-only pass. It fixes four
behavioral divergences (D1, D5, D10, D23), updates the reference, and adds
automated documentation verification.

Repository HEAD during verification: `de376f49a2db5221ecffc6c01a3b27d33567ccd1`
(`git rev-parse HEAD`), with these working-tree changes applied.

## Decisions

### D1 — `edge.condition`: remove the field

**Decision: remove.** `EdgeSpec.condition` was parsed, stored, serialized, and
never read. No test or fixture relied on gating behavior; the only existing
test that passed a condition through JSON (`tests/test_phase_10_5.py`) asserts
document round-tripping, which continues to pass because the unknown JSON key
is dropped.

Changes:

* `EdgeSpec` no longer has a `condition` field.
* `graph/parser.py:_parse_edge` no longer reads `condition` (JSON unknown keys
  are dropped as before).
* The DSL edge parser now rejects `condition` with a `GraphParseError` instead
  of silently accepting an inert key.
* `dsl/serializer.py` emits no `condition` key or line.
* `docs/DSL.md` §5.10, §7, §8, §11, §14.2, §17 and the example set now describe
  edges as unconditional dependencies.

This avoids a half-implemented feature and keeps the public schema honest.
Conditional logic remains available through `flow.if_branch` and `flow.filter`.

### D5 — fs_ops output ports: populate them

**Decision: populate, do not remove the ports.** Every affected node already
computes the value needed by its declared port.

* `fs.copy`, `fs.move`, `fs.rename` return `{"action_log": log, "file": <resolved destination>}`.
* `fs.archive` returns `{"action_log": log_or_logs, "file": <archive path create / target_dir extract>}`.
* `fs.convert` returns `{"action_log": log, "file": <target path>}`.
* `fs.trash` returns `{"action_log": log, "files": [<trashed path>]}`.

`core/executor/engine.py:_logs_from_return` already supports this shape, so the
executor now exposes the remaining keys as `NodeResult.outputs`. A small
`ActionResult(dict)` helper in `nodes/fs_ops/_common.py` preserves attribute
access (`.reason`, `.target`, `.dry_run`, ...) for existing direct callers.

## Behavioral changes

| Area | Before | After |
|---|---|---|
| Edge conditions | JSON/DSL stored a `condition` and never evaluated it | Field removed; JSON drops the unknown key; DSL raises a parse error |
| `fs.copy` / `fs.move` / `fs.rename` | `run` returned `ActionLog`; `file` output empty | Returns a dict with `action_log` and the destination `file` |
| `fs.archive` | Returned log(s); `file` output empty | Returns `action_log` plus archive path (create) or extraction directory (extract) |
| `fs.convert` | Returned log; `file` output empty | Returns `action_log` plus target path |
| `fs.trash` | Returned log; `files` output empty | Returns `action_log` plus a one-element `files` list |
| `flow.filter` non-list input | Silent empty aggregate (`errors=0`) | Fail-soft error: `results=["error"]`, `errors=1`, `total=0` |
| `validate_graph` indexed templates | `TypeError` during empty-context probe | Probe defers unresolvable placeholders; static absolute/`..` checks remain |
| `emit_dsl`/`document_to_dict` edge output | Included `condition` | No condition key/line |

## Migration notes

* **Edge conditions:** remove `condition` from DSL edges. Documents that use it
  now fail to parse. JSON documents are more forgiving: the key is ignored, but
  it still does not gate anything, so remove it to avoid confusion. Replace
  intended gating with explicit edge topology plus `flow.if_branch` /
  `flow.filter`; neither node rewires execution by itself.
* **fs output wiring:** nodes that previously had to work around empty
  `file`/`files` outputs can now wire the declared port directly. If a
  downstream node receives `None`, check the port name and document-declared
  edge, not the old return shape.
* **`flow.filter`:** callers that passed a scalar or a dict without `items`,
  `files`, `file_refs`, `input`, or `data` now get `errors=1` instead of a
  silent empty result. Empty lists are unchanged.
* **Indexed templates:** `utils.path_template` with `{item[path]}` (and similar
  subscript placeholders) now registers successfully. Literal absolute paths
  and literal `..` segments are still rejected at validation time.
* **No `spec_version` bump.** `spec_version` remains `"1.0"`. The edge schema
  change is handled through these migration notes rather than versioning.

## Files touched

Source:

* `src/lokdocsqr/core/models.py`
* `src/lokdocsqr/core/graph/parser.py`
* `src/lokdocsqr/core/graph/validator.py`
* `src/lokdocsqr/core/dsl/parser.py`
* `src/lokdocsqr/core/dsl/serializer.py`
* `src/lokdocsqr/nodes/flow/filter.py`
* `src/lokdocsqr/nodes/fs_ops/_base.py`
* `src/lokdocsqr/nodes/fs_ops/_common.py`
* `src/lokdocsqr/nodes/fs_ops/archive.py`
* `src/lokdocsqr/nodes/fs_ops/convert.py`
* `src/lokdocsqr/nodes/fs_ops/trash.py`

Tests:

* `tests/test_phase_10_5_fixes.py` (new)
* `tests/test_dsl_docs.py` (new)
* `tests/test_phase_8a.py` (D10 xfail removed)
* `tests/test_models.py` (D1 attribute removed)
* `tests/test_archive_conflict.py`, `tests/test_archive_dry_run.py`
  (unpack the new archive result)

Tooling and docs:

* `tools/verify_dsl_docs.py` (new)
* `docs/DSL.md`
* `docs/SCHEMA.md`
* `docs/DSL.md.changelog.md` (this file)

## Verification log

Commands:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python tools/verify_dsl_docs.py
```

Results:

* Full suite: `1451 passed, 6 skipped, 2 xfailed`. The two xfails are
  pre-existing and unrelated to D1/D5/D10/D23. The D10 xfail was removed and
  the test passes normally. No new `xfail` or `skip` markers were added.
* Docs verifier: `OK: 56 ldsq blocks, 9 json blocks, 7 DSL/JSON pairs, 32 registered node ids`.
* `tests/test_dsl_docs.py::test_docs_examples` passes.
* D1/D5/D10/D23 regression tests in `tests/test_phase_10_5_fixes.py` all pass
  (15 tests).

Section 16 examples, end to end:

| Example | Compile/parse/emit/inverse | Runtime smoke |
|---|---|---|
| 16.1 (a) watch → make_dir → write_file | pass | dry-run success, no errors |
| 16.2 (b) if_branch evaluation | pass | dry-run success, no errors |
| 16.3 (c) fs.move conflict policy | pass | dry-run success, no errors |
| 16.4 (d) child `copy-child` | pass | registered; dry-run success |
| 16.5 (e) list → filter → loop_batch → copy | pass | real run success; produced `copied-seed.txt` and `copied-seed (1).txt` |
| 16.6 (f) llm.local_llm classification | pass | dry-run success, no errors |

Runtime smoke used a temporary `AppConfig(base_dir=...)`, `policy=None`,
`single_instance=False`, and `Runtime.run_once`. Example 16.5 registered the
child and parent so `Runtime._catalog()` could resolve `graph_id="copy-child"`.

Remaining limitations are exactly the entries under `docs/DSL.md` →
"Known divergences".
