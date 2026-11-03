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

---

# docs/DSL.md — Phase 10.6 ergonomics & diagnostics changelog

This pass is behavior-changing but stays on `spec_version = "1.0"`. Existing
documents keep parsing, `compile_dsl` still returns the raw parser dict, and the
round-trip guarantees are unchanged:

```text
parse_graph(document_to_dict(doc)) == doc
emit_dsl(parse_dsl(text)) == text          # canonical text
```

## Decisions and behavior changes

| # | Area | Before | After |
|---|---|---|---|
| 1 | Nested child failures | `loop_batch`/`subflow` swallowed failures; `RunResult.success` stayed true | `on_child_error = "continue"|"fail"|"warn"` (default `warn`); `warn` records `RunResult.warnings` and fails the producing node, `continue` keeps success, `fail` re-raises the first child `LokError`. `RunResult`/`NodeResult` gain `warnings` |
| 2 | Validation output | CLI surfaced errors one at a time in practice | `validate_graph` returns every error; CLI prints all; `--first-error` restores one-at-a-time output |
| 3 | Run introspection | Only node state was printed | `run --explain` prints outputs (types + 200-char values), action logs, errors; `--format=text|json`; `--explain` implies text |
| 4 | Empty scans | `ok` with no indication whether a folder was empty | `access.list_files` and `triggers.watch_folder` always expose `scanned`; `--explain` prints `<node>: scanned N file(s)` |
| 5 | Undeclared registry ports | Unknown port was an error even when the class declared it | `validate_graph` emits a warning via `warnings=`; CLI prints warnings to stderr; registry ports are never injected into the document |
| 6 | Ordering dependencies | Required a fake data port | `edge.kind = "order"` constrains topology only, needs no `target_port`, is skipped by payload resolution and port validation |
| 7 | Long lists | Lists had to be one physical line; trailing comma failed | Newlines inside `[...]` are whitespace and a trailing comma is accepted; emitter still writes inline lists |
| 8 | Path templates | Only `{key}` placeholders | Added `basename`, `dirname`, `stem`, `suffix`, `join` (quoted literals or context keys, no nesting); absolute/`..` checks still run after substitution |
| 9 | Parent traversal | Any `..` rejected before scope resolution | Opt-in `scope.allow_parent_traversal`/`AppConfig.allow_parent_traversal`; final writable-root containment still rejects escapes; default remains closed |
| 10 | Move/copy destination base | `template` always relative to `source.parent` | `fs.move`/`fs.copy` accept `target_dir`, rendered against `config.base_dir`; `template` then resolves against it |
| 11 | Two-file catalogs in CLI | `graph_id not found` under `run` | `--include` registers files/directories before execution; `--catalog-fallback` opt-in loads `<base_dir>/<graph_id>.ldsq|json` with a warning |
| 12 | CLI dry-run + nested graphs | Failed closed on `loop_batch`/`subflow` | CLI `run --dry-run` sets `recursive_preview=true` and wires the existing planner callback; library remains strict |
| 13 | `retry_policy` on non-retry-safe nodes | Failed before `run` | Warns (`RunResult.warnings`) and applies the policy; `error_handling.strict_retry_safe = true` restores rejection |
| 14 | `retry_on` matching | Only the wrapper type matched | Matches `type(exc).__name__` across the `__cause__`/`__context__` chain |
| 15 | Document dry-run default | Only `false` won over config; explicit `true` could be overridden | Explicit document setting is absolute; precedence is explicit argument > document > Runtime > config |
| 16 | `serve` | Worked but was under-documented | Documented §10.7; `--include` added; file-or-directory, `--base-dir`, `--dry-run`/`--no-dry-run` stabilized |
| 17 | `edge.condition` | Removed in Phase 10.5 as inert | Reinstated as a real gate: all inbound data edges falsy ⇒ target skipped and descendants cascade; uses §11 language |
| 18 | Shorthand variables | Always `type = "string"` | Infers `int`, `float`, `bool`, `None`, else `string`; block form unchanged |
| 19 | Conditions | `item.path` was a parse error | Only `item.<key>` is supported when `item` is a dict; no general attribute access |
| 20 | CLI formats | `--json` only on some commands | Unified `--format=text|json` on `compile`, `validate`, `run`; `--json` remains an alias |

## Compatibility and migration notes

* **No `spec_version` bump.** New edge/variable/scope fields are optional with
  backward-compatible defaults.
* **Nested error default:** documents that relied on child failures being
  silent now see `RunResult.warnings` and a failed run under the default
  `on_child_error = "warn"`. Use `on_child_error = "continue"` for the old
  success semantics.
* **Retry safety:** documents with `retry_policy` on `retry_safe=False` nodes
  now run with a warning by default. Set
  `error_handling.strict_retry_safe = true` to opt back into rejection.
* **Edge conditions:** `edge.condition` gates execution again. A node is
  skipped only when every inbound data edge has a condition and all are falsy;
  an ungated or truthy edge keeps it runnable. Order edges never gate.
* **Variable inference:** shorthand variables no longer default to `string`.
  Use the block form if an exact type spelling (for example `integer`) matters.
* **Parent traversal:** still rejected by default. Enable
  `scope.allow_parent_traversal` only when a writable root covers the resolved
  sibling path.

## Files touched (summary)

Source: core models/parser/validator/paths/scope/config/runtime/executor/
payload/conditions; CLI; `nodes/flow/{loop_batch,subflow,_common}`;
`nodes/fs_ops/{_base,copy,move,_common}`; trigger helpers and watch node;
`nodes/triggers/_base.py`.

Tests: `tests/test_phase_10_6_diagnostics.py` (new), plus targeted updates to
`tests/test_phase_8f.py`, `tests/test_phase_8pre_retry_policy.py`, and
`tests/test_phase_10_5_fixes.py` where the specified behavior intentionally
changed.

Docs/tooling: `docs/DSL.md`, this changelog. `tools/verify_dsl_docs.py`
remains generic and automatically covers the new DSL blocks.

## Verification

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python tools/verify_dsl_docs.py
```

Results for this pass: full suite `1476 passed, 6 skipped, 2 xfailed` before
the P6 additions; the final run for items 17–20 is recorded in the delivery
message. No new `skip` or `xfail` markers were added.
