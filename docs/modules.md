# LokDocSQR — Modules

This document lists every module to be implemented during development.
It is the authoritative roadmap: file paths, responsibilities, dependency rules.

---

## 1. Architectural Rules (non-negotiable)

1. **Two build targets, two entry points**
   - `cli/main.py` → headless binary (`core` + `nodes`).
   - `gui/app.py` → desktop binary (`core` + `nodes` + `gui`).
2. **Dependency direction is one-way:**
   ```
   nodes  ──▶  core.node_api  ──▶  core
   gui    ──▶  core (+ nodes for editor)
   cli    ──▶  core, nodes
   core   ✖  never imports gui, nodes, or PySide
   ```
   The executor never imports concrete nodes; it resolves them at runtime via `NodeRegistry`.
3. **Only `core.safe_fs` touches the filesystem.**
   No `os.remove`, `shutil.rmtree`, `Path.unlink` anywhere else — deletions are moves into a trash/quarantine area.
4. **All file-affecting operations support dry-run, audit log, and rollback/quarantine.**
5. **All filesystem work is confined to allowed roots declared by `WorkspaceScope`.**

---

## 2. Directory Tree

```
lokdocsqr/
├── core/                       # headless, no GUI imports
│   ├── __init__.py
│   ├── models.py
│   ├── errors.py
│   ├── config.py
│   ├── logging.py
│   ├── events.py
│   ├── paths.py
│   ├── scope.py
│   ├── policy.py
│   ├── audit.py
│   ├── plugins.py
│   ├── runtime.py
│   ├── safe_fs.py
│   ├── graph/
│   │   ├── parser.py
│   │   ├── validator.py
│   │   ├── resolver.py
│   │   └── builder.py
│   ├── executor/
│   │   ├── engine.py
│   │   └── context.py
│   └── node_api/
│       ├── base.py
│       ├── registry.py
│       └── decorators.py
│
├── nodes/                      # depends only on core.node_api
│   ├── __init__.py
│   ├── triggers/
│   ├── access/
│   ├── fs_ops/
│   ├── planning/
│   ├── flow/
│   ├── llm/
│   └── utils/
│
├── gui/
│   ├── app.py
│   ├── main_window.py
│   ├── bridge.py
│   ├── views/
│   ├── viewmodels/
│   └── widgets/
│
└── cli/
    └── main.py
```

---

## 3. Core Modules

### 3.1 `core/models.py`
Domain dataclasses shared across the whole system.
- `FileRef` — `id, path, hash, mime, size, timestamps, metadata`.
- `ActionProposal` — `type (move|rename|copy|trash|tag|…), source, target, reason, confidence, risk`.
- `ActionLog` — what actually happened, confirmed by whom/what, rollback eligibility.
- `NodeSpec`, `Edge`, `Automation`, `RawGraph`, `ResolvedGraph`.
- `NodeResult`, `NodeState {ENABLED, DISABLED, SKIPPED, FAILED}`.
- `Risk {LOW, MEDIUM, HIGH}`.
- `WorkspaceScope` — resolved roots + policy reference.

### 3.2 `core/errors.py`
Exception hierarchy: `LokError` → `ConfigError`, `GraphParseError`, `GraphValidationError`, `ScopeViolation`, `PolicyViolation`, `NodeExecutionError`, `SafeFsError`, `DryRunInterrupt`.

### 3.3 `core/config.py`
Load/save application configuration (base_dir, trash_dir, scope roots, log paths, default dry-run flag). No I/O outside `safe_fs` for writes.

### 3.4 `core/logging.py`
Structured logger factory (`RotatingFileHandler` + stdout), per-run correlation IDs.

### 3.5 `core/events.py`
Minimal, framework-agnostic observer (`emit`, `subscribe`). Every execution milestone, node event and audit event is emitted here. The GUI bridge subscribes to it — nothing in `core` ever knows about Qt.

### 3.6 `core/paths.py`
Safe path resolution and template rendering:
- resolve relative paths against `base_dir` / scope root;
- reject `..` escapes;
- provide `render_template(tpl, ctx)` used by `Path Template` node and `Move/Rename` nodes.

### 3.7 `core/scope.py`
`WorkspaceScope` implementation: allowed roots, read-only vs writable, symlink policy. Every node that resolves a path must go through `scope.check(path)`.

### 3.8 `core/policy.py`
Policy engine used by `Policy Guard` and `Verify`:
- rule definitions per action type + risk level;
- `auto_approve`, `require_verify`, `block` verdicts;
- explicit configuration override (e.g. "moving inside a scope = auto, trashing = always verify").

### 3.9 `core/audit.py`
Audit log persistence and replay:
- append `ActionLog` records (via `safe_fs` atomic append);
- lookup by run id / automation id / file id;
- rollback plan generation (for trashed and moved files where possible).

### 3.10 `core/plugins.py`
Node discovery mechanism:
- built-in discovery via `import nodes`;
- third-party discovery via `importlib.metadata.entry_points(group="lokdocsqr.nodes")`;
- returns list of registered node ids, never imports anything at module load.

### 3.11 `core/runtime.py`
Headless service host for long-running triggers (watchers, cron):
- starts `Watch Folder` and `Scheduled Scan` backends;
- dispatches automation runs through `executor.engine`;
- graceful shutdown, single-instance lock, error isolation per run.

### 3.12 `core/safe_fs.py`
The single module interacting with the filesystem. Must be the most carefully written module in the project.

- `atomic_write(path, data)` — temp file in target dir → `fsync` → `os.replace`.
- `atomic_append(path, data)`.
- `safe_move(src, dst)` — `os.replace` within a volume, cross-volume `copy → fsync → replace`.
- `safe_rename(path, new_name)`.
- `copy_file(src, dst)`.
- `make_dir(path)`.
- `quarantine(path)` / `trash(path)` — move into `config.trash_dir` with timestamp + sidecar metadata; never delete.
- `hash_file(path)`, `stat(path)`, `read_bytes(path, limit)`, `list_dir(path)`.
- **No deletion API exists on purpose.**

### 3.13 `core/graph/parser.py`
Raw JSON → `RawGraph`. Format errors only; no semantics.

### 3.14 `core/graph/validator.py`
Schema + semantic validation: port types, dangling edges, missing in/out nodes, cycles, duplicate ids, invalid path templates.

### 3.15 `core/graph/resolver.py`
**Disabled cascade rule:** if a node is `DISABLED`, every node reachable downstream from it becomes `SKIPPED`. For joins, a node is `SKIPPED` if *any* inbound edge originates from a `DISABLED`/`SKIPPED` node (this rule is fixed and documented).

### 3.16 `core/graph/builder.py`
`RawGraph` + resolver output → `ResolvedGraph`: topological order, node → handler map (via registry), resolved scopes and templates.

### 3.17 `core/executor/engine.py`
Executes `ResolvedGraph` node by node: builds payloads, calls `node.run(ctx, payload)`, emits events, records `ActionLog`, applies `Retry / Error Handler` semantics, honors dry-run mode globally.

### 3.18 `core/executor/context.py`
`ExecutionContext`: `scope`, `config`, `logger`, `events`, `audit`, `policy`, `dry_run`, per-run variables, resolved path cache. Passed to every node.

### 3.19 `core/node_api/base.py`
Node contract (`ABC`): `id`, `title`, `inputs`, `outputs`, `run(ctx, payload)`, `dry_run(ctx, payload)`, `describe()` for the GUI editor.

### 3.20 `core/node_api/registry.py`
`NodeRegistry` (supersedes the earlier `Registrify` idea): `register`, `get`, `list`, `clear`. Populated by `@register_node` at import time.

### 3.21 `core/node_api/decorators.py`
`@register_node` decorator plus helper decorators for typed ports.

---

## 4. Nodes Package

Every node is a class deriving from `core.node_api.base.Node` and decorated with `@register_node`. Nodes never import `gui`. Nodes that touch the disk go through `core.safe_fs` and `core.scope`.

### 4.1 `nodes/triggers/`
| File | Node |
|---|---|
| `watch_folder.py` | **Watch Folder** — create/modify/delete/rename events, glob/size/mime filters, debounce. |
| `scheduled_scan.py` | **Scheduled Scan** — cron-based filesystem scanning. |
| `manual_run.py` | **Manual Run** — launch with user-selected files/folders. |
| `webhook.py` | **Webhook / External Trigger** — optional external entry point. |

### 4.2 `nodes/access/`
| File | Node |
|---|---|
| `list_files.py` | **List Files / Glob** — recursive match with date/size/type filters. |
| `read_metadata.py` | **Read Metadata** — size, mime, timestamps, EXIF, xattr, hash. |
| `read_content.py` | **Read Content** — text/binary with size limits. |
| `extract_text.py` | **Extract Text** — PDF / DOCX / HTML / EML. |
| `ocr.py` | **OCR** — images and scanned PDFs. |
| `hash_dedupe.py` | **Hash & Dedupe** — hashing and duplicate grouping. |
| `index_search.py` | **Index / Search** — local index, name/metadata/text search, optional embeddings. |

### 4.3 `nodes/fs_ops/`
All `fs_ops` nodes implement `dry_run()` and always emit `ActionLog` entries.

| File | Node |
|---|---|
| `move.py` | **Move File(s)** — template paths, conflict policy `skip / rename / overwrite / ask`. |
| `rename.py` | **Rename File(s)** — templates with date/author/tags/LLM output. |
| `copy.py` | **Copy File(s)** — free-space check, conflict policy. |
| `trash.py` | **Delete to Trash / Quarantine** — never deletes; TTL and rollback. |
| `make_dir.py` | **Create Folder** — templated. |
| `write_file.py` | **Write / Append File** — reports, sidecars, logs. |
| `archive.py` | **Archive / Extract** — zip / 7z. |
| `convert.py` | **Convert File** — format conversion. |
| `tag.py` | **Tag / Set Metadata** — tags, xattr, sidecar JSON. |

### 4.4 `nodes/planning/`
| File | Node |
|---|---|
| `build_action_plan.py` | **Build Action Plan** — LLM/rules output → validated `ActionProposal[]` (schema, scope, conflicts, forbidden ops). |
| `policy_guard.py` | **Policy Guard** — consults `core.policy`; auto-approves low risk, blocks high risk, routes the rest to Verify. |
| `verify.py` | **Verify / Human-in-the-Loop** — diff preview (before/after), approve / reject / edit, batch approve, timeout. |
| `preview_changes.py` | **Preview Changes** — “before/after” tree and path diff (also embedded in Verify). |
| `audit_log.py` | **Audit Log** — explicit audit entry point; supports rollback lookup. |

### 4.5 `nodes/flow/`
| File | Node |
|---|---|
| `if_branch.py` | **If / Branch** — on metadata, LLM result, confidence, risk. |
| `filter.py` | **Filter** — filter `FileRef[]`. |
| `loop_batch.py` | **Loop / Batch** — one-by-one or chunked processing. |
| `merge.py` | **Merge / Join**. |
| `wait.py` | **Wait / Delay** — pause, debounce, wait for confirmation. |
| `retry.py` | **Retry / Error Handler**. |
| `subflow.py` | **Subflow** — invoke a nested automation. |

### 4.6 `nodes/llm/`
| File | Node |
|---|---|
| `local_llm.py` | **Local LLM** — classification, field extraction, summarization, plan generation. Returns structured JSON per schema + confidence + usage. **Never touches files.** |

### 4.7 `nodes/utils/`
| File | Node |
|---|---|
| `set_variables.py` | **Set Variables**. |
| `path_template.py` | **Path Template** — safe `{year}/{category}/{name}` assembly via `core.paths`. |
| `json_validate.py` | **JSON Parse / Validate** — schema validation of LLM structured output. |
| `notify.py` | **Notify** — desktop / email / messenger notification. |

---

## 5. GUI Modules (`gui/`)

- `gui/app.py` — bootstrap: config → logging → plugin discovery → QApplication → MainWindow.
- `gui/main_window.py` — top-level window, menu, docking layout, run controls.
- `gui/bridge.py` — **the only place** where `core.events` is mapped to Qt signals; passes state between executor thread and UI thread.
- `gui/viewmodels/` — MVVM layer: automation list, run state, node palette, audit browser; no direct Qt widget imports here.
- `gui/views/` — graph editor view, run monitor, verify dialog, audit log view, settings view.
- `gui/widgets/` — reusable widgets: node card, port, connection line, diff viewer (used by Verify and Preview Changes).

---

## 6. CLI (`cli/`)

- `cli/main.py` — argument parsing, subcommands:
  - `run <automation.json> [--dry-run] [--scope …]`
  - `validate <automation.json>`
  - `list-nodes`
  - `serve` — headless long-running mode using `core.runtime` (watchers + scheduler).
  - `audit show <run-id>`, `audit rollback <run-id>`.

---

## 7. Cross-cutting Implementation Notes

- **Dry-run** is a first-class concept: `ExecutionContext.dry_run` is honored by every `fs_ops` node through its `dry_run()` implementation, which returns a synthetic `ActionLog` without touching disk.
- **Verify is mandatory** before any irreversible or high-risk operation; the only bypass is an explicit policy rule (`Policy Guard`), which must be visible in the automation JSON.
- **Every filesystem-touching node** must pass paths through `scope.check()` before calling `safe_fs`.
- **Audit is append-only** and written through `safe_fs.atomic_append`.
- **Nodes must be independently testable** — each node ships with unit tests using a tmp workspace scope.
- **GUI must never import `core.safe_fs` directly** — all disk operations go through the executor.

---

## 8. Suggested Implementation Order

1. `core/safe_fs.py`, `core/errors.py`, `core/models.py`, `core/scope.py`.
2. `core/node_api/*`, `core/plugins.py`, `core/events.py`, `core/logging.py`, `core/config.py`.
3. `core/graph/*` (parser → validator → resolver → builder).
4. `core/executor/*`, `core/policy.py`, `core/audit.py`.
5. `nodes/fs_ops/*`, `nodes/access/*` — the minimal file automation loop.
6. `nodes/planning/*` (Build Action Plan → Policy Guard → Verify → Audit).
7. `nodes/flow/*`, `nodes/utils/*`.
8. `nodes/llm/local_llm.py`.
9. `nodes/triggers/*` + `core/runtime.py`.
10. `cli/main.py`.
11. `gui/*` (bridge → viewmodels → views → widgets).