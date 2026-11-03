# LokDocSQR — Automation Schema (v1.0)

This document describes the **JSON automation schema** that LokDocSQR consumes,
and the **DSL** (Phase 10.5) that compiles into it.

Every automation — whether authored as JSON, as DSL, or built in the GUI —
becomes a `WorkflowDocument` through the same pipeline:

```
JSON dict / DSL text
        │  core.graph.parser.parse_graph   (structural parse, spec_version check)
        ▼
WorkflowDocument
        │  core.graph.validator.validate_graph  (semantic validation)
        │  core.graph.resolver.resolve_states   (disabled-cascade rule)
        │  core.graph.builder.build_graph       (topological order + handler lookup)
        ▼
ResolvedGraph  ──▶  core.executor.engine.Executor.execute  ──▶  RunResult
```

---

## 1. Top-level structure

The root of an automation is a JSON object with the following keys:

| Key | Type | Required | Default | Meaning |
|---|---|---|---|---|
| `spec_version` | string | yes | — | Must be `"1.0"`. |
| `metadata` | object | yes | — | Identity and provenance. |
| `settings` | object | no | defaults | Dry-run, scope, retry, concurrency. |
| `variables` | object | no | `{}` | Declared workflow variables. |
| `triggers` | list | no | `[]` | Runtime trigger declarations. |
| `nodes` | list | yes | — | The graph nodes, in any order. |
| `edges` | list | yes | — | Directed edges between nodes. |
| `extra_meta` | object | no | `{}` | Arbitrary extra data. Unknown top-level keys are folded in here by the parser. |

`parse_graph` rejects any document whose `spec_version` is not `"1.0"`, and
requires `metadata` and `metadata.id`.

### 1.1 `metadata`

```json
{
  "id": "invoice-sorter",
  "name": "Invoice sorter",
  "description": "Sort invoices into folders",
  "version": "1.0",
  "author": "alice",
  "created_at": "2026-01-01T00:00:00+00:00",
  "updated_at": null,
  "tags": ["invoices", "pdf"]
}
```

| Field | Type | Default |
|---|---|---|
| `id` | string | required |
| `name` | string | `""` |
| `description` | string | `""` |
| `version` | string | `"1.0"` |
| `author` | string | `""` |
| `created_at` | string \| null | `null` |
| `updated_at` | string \| null | `null` |
| `tags` | list of string | `[]` |

### 1.2 `settings`

```json
{
  "default_dry_run": true,
  "max_concurrency": 1,
  "scope": {
    "roots": ["~/inbox", "~/archive"],
    "writable": ["~/archive"],
    "allow_symlinks": false,
    "policy_ref": null
  },
  "error_handling": {
    "strategy": "fail",
    "max_retries": 0,
    "retry_delay_seconds": 0.0,
    "backoff": "none"
  }
}
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `default_dry_run` | bool | `true` | Whether runs default to dry-run. |
| `max_concurrency` | int | `1` | Bound on parallel runs (`>= 1`). |
| `scope.roots` | list of string | `[]` | Readable filesystem roots. |
| `scope.writable` | list of string | `[]` | Writable roots. |
| `scope.allow_symlinks` | bool | `false` | Whether symlinks may escape roots. |
| `scope.policy_ref` | string \| null | `null` | External policy reference. |
| `error_handling.strategy` | string | `"fail"` | `fail` \| `retry` \| `continue`. |
| `error_handling.max_retries` | int | `0` | Retries under the `retry` strategy. |
| `error_handling.retry_delay_seconds` | number | `0.0` | Base delay between retries. |
| `error_handling.backoff` | string | `"none"` | `none` \| `linear` \| `exponential`. |

Relative paths in scope settings and node params resolve against
`config.base_dir`, not the process CWD.

### 1.3 `variables`

```json
{
  "inbox": {"type": "string", "value": "inbox", "description": "watch root"}
}
```

A map of variable name to its declaration. Each value is an object with
`type` (default `"string"`), `value` (any JSON value, default `null`), and
`description` (default `""`). Non-null values are seeded into
`ctx.variables` at run start.

### 1.4 `triggers`

```json
[
  {"id": "watch-inbox", "type": "triggers.watch_folder", "title": "Watch inbox",
   "params": {"path": "inbox", "patterns": ["*.pdf"], "recursive": true}}
]
```

| Field | Type | Default |
|---|---|---|
| `id` | string | required |
| `type` | string | required |
| `title` | string | `""` |
| `params` | object | `{}` |

Trigger types recognized by `core.runtime` (the `triggers.` prefix is
optional in `type`):

| `type` | Bound to | Relevant `params` |
|---|---|---|
| `watch_folder` (`watch`, `watcher`) | folder watcher | `path` (required), `patterns`, `recursive`, `interval_seconds`, `debounce_seconds`, `events` |
| `scheduled_scan` (`scheduled`, `schedule`, `cron`) | cron / interval schedule | `cron` / `expression` / `schedule`, or `interval_seconds` / `every_seconds` |
| `manual_run` (`manual`) | caller (`Runtime.trigger`) | — |
| `webhook` | caller (`Runtime.trigger_webhook`) | — |

A trigger may also carry `params.dry_run` (bool) to force that binding's mode.

### 1.5 `nodes`

```json
{
  "id": "mk",
  "type": "fs.make_dir",
  "title": "Create work dir",
  "state": "enabled",
  "params": {"path_template": "work"},
  "inputs": ["path_template"],
  "outputs": [{"name": "path", "type": "string"}],
  "position": {"x": 10.0, "y": 20.0},
  "description": "make the working directory"
}
```

| Field | Type | Default |
|---|---|---|
| `id` | string | required (unique) |
| `type` | string | required — the registered node type id |
| `title` | string | `""` |
| `state` | string | `"enabled"` (`enabled` \| `disabled` \| `skipped` \| `failed`) |
| `params` | object | `{}` — the node's static payload |
| `inputs` | ports | `[]` |
| `outputs` | ports | `[]` |
| `position` | object of numbers | `{}` — GUI layout hint |
| `description` | string | `""` |

`type` must name a node registered in the `NodeRegistry` (for example
`fs.make_dir`, `fs.move`, `access.list_files`, `llm.local_llm`).

#### Ports

A port list accepts three shapes, which the parser normalizes into
`PortSpec(name, type="any", required=true, description="", default=None)`:

* a bare string — `"file"` (just the name);
* a single object — `{"name": "file", "type": "FileRef[]", "required": false}`;
* a list of the above.

### 1.6 `edges`

```json
{"id": "e1", "source": "mk", "target": "wr",
 "source_port": "out", "target_port": "in",
 "condition": null, "label": ""}
```

| Field | Type | Default |
|---|---|---|
| `id` | string | `"{source}->{target}"` |
| `source` | string | required — source node id |
| `target` | string | required — target node id |
| `source_port` | string | `"out"` |
| `target_port` | string | `"in"` |
| `condition` | string \| null | `null` |
| `label` | string | `""` |

Edges drive payload resolution (see §2) and the disabled cascade.

---

## 2. Payload resolution

A node's input payload is built once, by `core.payload.build_node_payload`,
with fixed precedence (lowest to highest):

1. `NodeSpec.params` — static configuration;
2. inbound edge values — an edge value overrides a same-named param;
3. port defaults — fill only keys neither params nor edges set;
4. a required input port with no value, edge, or default fails closed.

Multiple inbound edges to one target port become a list. Both the planner and
the executor call the same resolver.

---

## 3. Node type reference

| Node id | Family | Description |
|---|---|---|
| `fs.make_dir` | fs_ops | Create a folder (idempotent). |
| `fs.write_file` | fs_ops | Write / append file content. |
| `fs.move` | fs_ops | Move file(s) with a template + conflict policy. |
| `fs.rename` | fs_ops | Rename file(s). |
| `fs.copy` | fs_ops | Copy file(s), free-space check. |
| `fs.trash` | fs_ops | Move to trash / quarantine (never deletes). |
| `fs.archive` | fs_ops | Archive / extract (zip). |
| `fs.convert` | fs_ops | Convert file format. |
| `fs.tag` | fs_ops | Set tags / metadata / xattr. |
| `access.list_files` | access | List / glob files. |
| `access.read_metadata` | access | Read size, mime, timestamps, hash. |
| `access.read_content` | access | Read text / binary content. |
| `access.extract_text` | access | Extract text (PDF / DOCX / HTML). |
| `access.ocr` | access | OCR images and scanned PDFs. |
| `access.hash_dedupe` | access | Hash and group duplicates. |
| `access.index_search` | access | Index / search by name, metadata, text. |
| `llm.local_llm` | llm | Classify / extract / summarize / plan (read-only). |
| `triggers.watch_folder` | triggers | Report files from a folder watcher. |
| `triggers.scheduled_scan` | triggers | Scan a folder on a schedule. |
| `triggers.manual_run` | triggers | Normalize a manual invocation. |
| `triggers.webhook` | triggers | Normalize an external webhook payload. |
| `flow.if_branch` | flow | Branch on a condition. |
| `flow.filter` | flow | Filter `FileRef[]`. |
| `flow.loop_batch` | flow | Loop / batch over items. |
| `flow.merge` | flow | Merge / join inputs. |
| `flow.wait` | flow | Pause / debounce. |
| `flow.retry` | flow | Retry / error-handler marker. |
| `flow.subflow` | flow | Invoke a nested automation. |
| `utils.set_variables` | utils | Set run variables. |
| `utils.path_template` | utils | Assemble a safe path template. |
| `utils.json_validate` | utils | Validate JSON against a schema. |
| `utils.notify` | utils | Send a notification. |

Mutating `fs_ops` nodes emit an `ActionLog` (audit entry); the executor is the
single owner of appending those entries and finalizing their status
(`planned → dry_run / applied / failed`).

---

## 4. The DSL (Phase 10.5)

The DSL (`core.dsl`) compiles into the JSON schema above. It is a
brace-delimited configuration language: indentation is cosmetic, blocks are
delimited by `{`/`}`, statements are separated by newlines, and `#` starts a
comment.

Public API:

```python
from lokdocsqr.core.dsl import compile_dsl, parse_dsl, emit_dsl, document_to_dict

dict = compile_dsl(text)       # DSL text -> JSON schema dict
doc  = parse_dsl(text)         # DSL text -> WorkflowDocument
text = emit_dsl(doc)           # WorkflowDocument -> canonical DSL text
dict = document_to_dict(doc)   # WorkflowDocument -> JSON schema dict
```

Round-trip guarantees:

* `parse_graph(document_to_dict(doc)) == doc` — the document serializer is the
  exact inverse of the JSON parser.
* `emit_dsl(parse_dsl(text)) == text` — emitting is a fixed point on canonical
  text (so DSL → document → DSL is stable).

### 4.1 Example

```
automation "invoice-sorter"

name = "Invoice sorter"
tags = ["invoices", "pdf"]

default_dry_run = false

scope {
  roots = ["inbox"]
  writable = ["archive"]
}

trigger "watch-inbox" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = ["*.pdf"]
  }
}

node mk {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
}

node wr {
  type = "fs.write_file"
  params {
    path_template = "work/report.txt"
    content = "from-dsl"
  }
}

edge e1 {
  source = "mk"
  target = "wr"
}
```

### 4.2 Values

* **strings** — `"quoted"` (JSON escapes); a bare word (`fs.make_dir`,
  `enabled`) is also a string, except `true` / `false` / `null`.
* **numbers** — `42`, `-3`, `1.5`, `1e3` (int vs float preserved).
* **booleans / null** — `true`, `false`, `null`.
* **lists** — `["a", "b"]`.
* **maps** — inline `{ k = v, k = v }` or a block
  `{ k = v ... }` (newline-separated).

### 4.3 Convenience shorthands

The serializer always emits the canonical (long) form; the parser additionally
accepts these shorthands:

* Node params may be written directly in the node block — any key other than
  `type` / `title` / `state` / `description` / `params` / `inputs` / `outputs`
  / `position` is a param:
  ```
  node mk {
    type = fs.make_dir
    path_template = work      # -> params.path_template
  }
  ```
* A variable may be written as a plain assignment:
  `variable "inbox" = "inbox"`.

### 4.4 Mapping to JSON

| DSL construct | JSON schema |
|---|---|
| `automation "<id>"` | `metadata.id` |
| `name`, `description`, `version`, `author`, `tags`, `created_at`, `updated_at` | `metadata.*` |
| `default_dry_run`, `max_concurrency` | `settings.*` |
| `scope { ... }` | `settings.scope` |
| `error_handling { ... }` | `settings.error_handling` |
| `variable "<name>" { ... }` / `= value` | `variables.<name>` |
| `trigger "<id>" { ... }` | `triggers[]` entry |
| `node "<id>" { ... }` | `nodes[]` entry |
| `edge "<id>" { ... }` | `edges[]` entry |
| `extra_meta { ... }` | `extra_meta` |

`spec_version` is implied (`"1.0"`); write `spec_version = "..."` to override.
`nodes` and `edges` are represented as repeated `node`/`edge` statements rather
than explicit lists.
