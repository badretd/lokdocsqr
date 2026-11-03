# LokDocSQR Automation DSL and Schema Reference

This document is the authoritative reference for authoring, parsing, emitting, and debugging LokDocSQR automations. It covers both the `.ldsq` DSL and the JSON `WorkflowDocument` schema it compiles into. Everything below is derived from the implementation under `src/lokdocsqr/`; where the prose specification in `docs/SCHEMA.md` differs from executable behavior, the code wins and the difference is recorded in [Known divergences](#known-divergences).

## Contents

- [1. Overview and pipeline](#1-overview-and-pipeline)
- [2. Quick start](#2-quick-start)
- [3. Public Python API](#3-public-python-api)
- [4. Lexical grammar](#4-lexical-grammar)
- [5. Statements](#5-statements)
- [6. Value grammar](#6-value-grammar)
- [7. Blocks, shorthands, and canonical form](#7-blocks-shorthands-and-canonical-form)
- [8. DSL → JSON mapping](#8-dsl--json-mapping)
- [9. Node reference](#9-node-reference)
- [10. Trigger reference (runtime)](#10-trigger-reference-runtime)
- [11. Condition expression language](#11-condition-expression-language)
- [12. Template and interpolation syntax](#12-template-and-interpolation-syntax)
- [13. Payload resolution](#13-payload-resolution)
- [14. State, disabled cascade, retries, and error handling](#14-state-disabled-cascade-retries-and-error-handling)
- [15. Subflows](#15-subflows)
- [16. End-to-end examples](#16-end-to-end-examples)
- [17. Common pitfalls](#17-common-pitfalls)
- [18. Versioning and compatibility](#18-versioning-and-compatibility)
- [19. Appendix](#19-appendix)
- [Known divergences](#known-divergences)
- [Verification log](#verification-log)

Canonical file extension: `.ldsq` (`src/lokdocsqr/core/dsl/__init__.py:DSL_EXTENSION`).

---

## 1. Overview and pipeline

Every automation — JSON, DSL, GUI-built, or programmatic — becomes a `WorkflowDocument`, then a `ResolvedGraph`, then a `RunResult`:

```text
JSON object (dict)                    DSL text (.ldsq)
        │                                     │
        │                                     │  core.dsl.parser.dsl_to_dict / compile_dsl
        │                                     ▼
        │                             raw JSON-schema dict
        │                                     │
        └──────────────┬──────────────────────┘
                       ▼
        core.graph.parser.parse_graph           structural parse, spec_version check
                       ▼
                WorkflowDocument
                       │
                       ├── core.graph.validator.validate_graph      semantic validation (list of errors)
                       ├── core.graph.resolver.resolve_states       disabled/skipped cascade
                       └── core.graph.builder.build_graph           topological order + handler lookup
                       ▼
                 ResolvedGraph
                       │
                       ▼
        core.executor.engine.Executor.execute   payload resolution, policy gate, retries, audit
                       ▼
                    RunResult
```

| Stage | Owner | Input → output |
|---|---|---|
| DSL lexing/parsing | `src/lokdocsqr/core/dsl/parser.py` | `str` → raw schema `dict` |
| DSL emitting | `src/lokdocsqr/core/dsl/serializer.py` | `WorkflowDocument`/raw `dict` → canonical `str` |
| Structural parse | `src/lokdocsqr/core/graph/parser.py:parse_graph` | raw `dict` → `WorkflowDocument` |
| Semantic validation | `src/lokdocsqr/core/graph/validator.py:validate_graph` | `WorkflowDocument` → `list[str]` errors |
| State resolution | `src/lokdocsqr/core/graph/resolver.py:resolve_states` | `WorkflowDocument` → `dict[str, NodeState]` |
| Graph building | `src/lokdocsqr/core/graph/builder.py:build_graph` | document + states + registry → `ResolvedGraph` |
| Payload resolution | `src/lokdocsqr/core/payload.py:build_node_payload` | `NodeSpec` + inbound edges + upstream outputs → `dict` |
| Execution | `src/lokdocsqr/core/executor/engine.py:Executor.execute` | `ResolvedGraph` + `ExecutionContext` → `RunResult` |
| Trigger hosting | `src/lokdocsqr/core/runtime.py:Runtime` | registered automations → watcher/cron bindings and runs |

Key modeling classes live in `src/lokdocsqr/core/models.py`: `WorkflowDocument`, `WorkflowMetadata`, `WorkflowSettings`, `WorkflowScopeSettings`, `WorkflowErrorHandling`, `VariableSpec`, `TriggerSpec`, `NodeSpec`, `PortSpec`, `EdgeSpec`, `NodeState`, `ActionLog`, `NodeResult`, `RunResult`, `ResolvedGraph`.

### 1.1 What validates where

| Check | Performed by | Raised/returned as |
|---|---|---|
| DSL syntax | `core.dsl.parser` | `GraphParseError` |
| `spec_version == "1.0"` | `core.graph.parser.parse_graph` | `GraphParseError` |
| Metadata `id` present | `parse_graph` | `GraphParseError` |
| Types of document fields, ports, state | `parse_graph` | `GraphParseError` |
| Duplicate ids, dangling edges, self-loops, port names, path policy | `validate_graph` | returned `list[str]` — every discovered error, not just the first |
| Unknown node type | `build_graph` (enabled nodes only) | `GraphValidationError` |
| Graph cycle | `build_graph` | `GraphValidationError` |
| Node param semantics | node's `run`/`dry_run` | `NodeExecutionError`, `ScopeViolation`, etc. |
| Policy/approval | executor policy gate | `PolicyBlockedError`, `PolicyTimeoutError`, `PolicyError` |

`parse_dsl()` performs **structural** parsing only. It does **not** check that `NodeSpec.type` is registered, that params are valid, that edges form a DAG, or that required ports are satisfiable. `parse_dsl(text)` is exactly `parse_graph(compile_dsl(text))` (`src/lokdocsqr/core/dsl/__init__.py:parse_dsl`).

---

## 2. Quick start

Minimal automation that creates a directory and writes a file:

```ldsq
automation "quick-start"

name = "Quick start"
default_dry_run = true

node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
}

node "wr" {
  type = "fs.write_file"
  params {
    path_template = "work/report.txt"
    content = "hello from DSL"
  }
}
```

Equivalent JSON (`compile_dsl` output; this is the raw compiled dict, not the normalized `document_to_dict` form):

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "quick-start",
    "name": "Quick start"
  },
  "nodes": [
    {
      "id": "mk",
      "type": "fs.make_dir",
      "params": {
        "path_template": "work"
      }
    },
    {
      "id": "wr",
      "type": "fs.write_file",
      "params": {
        "path_template": "work/report.txt",
        "content": "hello from DSL"
      }
    }
  ],
  "edges": [],
  "settings": {
    "default_dry_run": true
  }
}
```

CLI equivalents (`src/lokdocsqr/cli/main.py`):

```bash
lokdocsqr compile automation.ldsq
lokdocsqr validate automation.ldsq
lokdocsqr run automation.ldsq --dry-run
lokdocsqr run automation.ldsq --no-dry-run   # explicit real-run opt-in
lokdocsqr run parent.ldsq --include .         # register sibling child automations
lokdocsqr run parent.ldsq --catalog-fallback # opt-in <base_dir>/<graph_id>.ldsq lookup
```

The CLI accepts only `.json` and `.ldsq` (`src/lokdocsqr/cli/main.py:_read_automation`). `--include` is repeatable, accepts a file or a directory of automation files, and registers every document before the main run; the main document is skipped if a directory includes it. `--catalog-fallback` is an opt-in CLI convenience that loads `<base_dir>/<graph_id>.ldsq` then `.json` when a `graph_id` is missing from the catalog, printing a warning.

---

## 3. Public Python API

```python
from lokdocsqr.core.dsl import (
    DSL_EXTENSION,
    compile_dsl,
    parse_dsl,
    emit_dsl,
    document_to_dict,
    dsl_to_dict,
    dsl_from_dict,
)
```

Exact signatures (`src/lokdocsqr/core/dsl/__init__.py`; `src/lokdocsqr/core/dsl/serializer.py`):

```python
DSL_EXTENSION: str = ".ldsq"

def compile_dsl(text: str) -> dict[str, Any]: ...
def parse_dsl(text: str) -> WorkflowDocument: ...
def emit_dsl(document: WorkflowDocument) -> str: ...
def document_to_dict(document: WorkflowDocument) -> dict[str, Any]: ...
def dsl_to_dict(text: str) -> dict[str, Any]: ...
def dsl_from_dict(raw: dict[str, Any]) -> str: ...
```

| Function | Semantics | Returns | Raises |
|---|---|---|---|
| `compile_dsl(text)` | Lex + parse DSL into the raw schema dict. No semantic validation. | `dict[str, Any]` | `GraphParseError` |
| `dsl_to_dict(text)` | Alias-level implementation of `compile_dsl`. | `dict[str, Any]` | `GraphParseError` |
| `parse_dsl(text)` | `parse_graph(compile_dsl(text))`. | `WorkflowDocument` | `GraphParseError` |
| `document_to_dict(doc)` | Inverse of `parse_graph`; omits default-valued fields. Values are copied, not serialized. | `dict[str, Any]` | — (serialization errors surface in `emit_dsl`) |
| `emit_dsl(doc)` | `dsl_from_dict(document_to_dict(doc))`. | canonical DSL `str` | `ValueError` for non-finite floats, `TypeError` for unsupported values |
| `dsl_from_dict(raw)` | Render a raw schema dict as canonical DSL. | `str` | same as above |

Related graph API (`src/lokdocsqr/core/graph/__init__.py`):

```python
parse_graph(data: dict[str, Any]) -> WorkflowDocument
validate_graph(document: WorkflowDocument) -> list[str]
resolve_states(document: WorkflowDocument, registry: Any | None = None) -> dict[str, NodeState]
build_graph(document: WorkflowDocument, states: dict[str, NodeState], registry: NodeRegistry) -> ResolvedGraph
```

Exceptions (`src/lokdocsqr/core/errors.py`):

| Exception | Parent | Used for |
|---|---|---|
| `GraphParseError` | `LokError` | malformed JSON/DSL/schema structure |
| `GraphValidationError` | `LokError` | graph cannot be built (cycle, unknown enabled node type) |
| `NodeExecutionError` | `LokError` | a node failed |
| `ScopeViolation` | `LokError` | path escapes scope or template policy |
| `SafeFsError` | `LokError` | safe filesystem operation failed |
| `ConfigError` | `LokError` | configuration problem (e.g. missing `trash_dir`) |
| `PolicyError` / `PolicyBlockedError` / `PolicyTimeoutError` | `LokError` | policy gate failures |

`compile_dsl` is not the same as `document_to_dict(parse_dsl(text))`: `compile_dsl` preserves the raw shape produced by the DSL (including `settings: {}` if an empty `scope {}` was written), while `document_to_dict` normalizes and omits defaults.

### 3.1 CLI output modes

`lokdocsqr run` supports three output modes:

| Flags | Output |
|---|---|
| none | Compact human summary: run id/success, one line per node state, audit log lines, errors, warnings on stderr |
| `--json` or `--format=json` | Full `RunResult.to_dict()` JSON on stdout (`default=str` for non-JSON values) |
| `--explain` | Per-node detail: state, every output key with its Python type and value truncated to 200 characters, each `ActionLog` (type, status, reason, target), and the node error |

`--explain` implies `--format=text` unless an explicit `--format` is supplied. `--json` remains supported as an alias for `--format=json`. Warnings are always printed to stderr in text mode, and are present under `warnings` in JSON mode.

---

## 4. Lexical grammar

Source: `src/lokdocsqr/core/dsl/parser.py:_tokenize`.

### 4.1 Tokens

| Token | Produced from |
|---|---|
| `NEWLINE` | a single LF (`\n`) |
| `LBRACE` / `RBRACE` | `{` / `}` |
| `LBRACK` / `RBRACK` | `[` / `]` |
| `EQ` | `=` |
| `COMMA` | `,` |
| `STRING` | a double-quoted JSON string |
| `NUMBER` | an integer or floating-point literal |
| `WORD` | a bare identifier |
| `EOF` | end of input |

There are no tokens for single quotes, semicolons, colons, or operators. A `;` is an “unexpected character” parse error.

### 4.2 Whitespace, comments, line structure

* Space (` `), tab (`\t`), and carriage return (`\r`) are skipped.
* LF produces a `NEWLINE` token. Newlines separate entries inside blocks and top-level statements. They are required inside `{ ... }` block maps after each entry (unless the block ends).
* `#` starts a comment that runs to the next LF. The LF still produces `NEWLINE`.
* Blank lines are permitted anywhere newlines are skipped.
* Indentation is cosmetic; braces alone determine nesting.

```ldsq
# top-level comment
automation "commented"   # inline comment
node "mk" {              # comment after brace
  type = "fs.make_dir"   # comment after entry
}
```

### 4.3 Strings

Only double-quoted strings are accepted. The contents are decoded with Python's `json.loads` (`parser.py:_tokenize`), so JSON escapes apply:

| Escape | Decoded value |
|---|---|
| `\"` | `"` |
| `\\` | `\` |
| `\/` | `/` |
| `\b` | backspace |
| `\f` | form feed |
| `\n` | LF |
| `\r` | CR |
| `\t` | TAB |
| `\uXXXX` | Unicode code point |

A literal LF inside a string is rejected as “unterminated”; multi-line strings are not supported. Invalid JSON escapes or control characters raise `GraphParseError`.

Examples:

```ldsq
automation "strings"
extra_meta {
  name = "Line one\nLine two"
  path_template = "work/report.txt"
  description = "quote: \" and backslash: \\"
}
```

Single-quoted strings are **not** DSL syntax (they are only accepted inside condition expressions; see §11).

### 4.4 Words and keywords

A word starts with an ASCII letter or `_` and continues with ASCII letters, digits, `_`, `.`, or `-` (`parser.py:_WORD_START`, `_WORD_CHARS`).

```text
word-start  := [A-Za-z_]
word-cont   := [A-Za-z0-9_.-]
```

Consequences:

* `fs.make_dir`, `triggers.watch_folder`, `application/json`, `enabled`, `a-b` are single bare words.
* `true`, `false`, `null` are bare words that become JSON `true`, `false`, `null` **only in value position** (`parser.py:_parse_value`). As keys they remain strings.
* Words cannot start with a digit, `.`, or `-`.
* `1abc` tokenizes as `NUMBER(1)` then `WORD(abc)`, which is a syntax error in most positions.
* A bare word that looks like a number is not a word because digit start is handled by the number lexer.

### 4.5 Numbers

```text
number := "-"? digit+ ("." digit*)? ([eE] [+-]? digit*)?
```

The implementation sets `is_float = True` if a `.` or exponent marker is present; otherwise it calls `int()` (`parser.py:_tokenize`). Therefore:

| Literal | Python value |
|---|---|
| `42` | `int` 42 |
| `-3` | `int` -3 |
| `1.0` | `float` 1.0 |
| `1.` | `float` 1.0 |
| `1e3` | `float` 1000.0 |
| `1E+3` | `float` 1000.0 |
| `2.5e-2` | `float` 0.025 |
| `-0` | `int` 0 |

Int versus float is preserved through `compile_dsl`, `document_to_dict`, and `emit_dsl` (floats use Python `repr`). `+1`, `.5`, `1e`, and `nan`/`inf` are not accepted.

### 4.6 Bare strings

Any non-keyword `WORD` in value position is a string:

```ldsq
automation "bare-words"
extra_meta {
  type = fs.make_dir          # string "fs.make_dir"
  state = disabled            # string "disabled"
  policy = text-csv           # string "text-csv"
}
```

The emitter never emits bare strings; canonical output quotes every string value. Bare words are a parser convenience only.

---

## 5. Statements

### 5.1 Document header

```text
automation <label>
```

`<label>` is either a word or a quoted string and must be non-empty (`parser.py:_parse_label`). The `automation` statement is mandatory, must be the first non-newline token, and may appear exactly once.

```ldsq
automation "invoice-sorter"
```

`metadata.id` is taken from this label. A second `automation` statement raises `GraphParseError` (“may only appear once, at the top”).

### 5.2 Metadata assignments

```text
name = <value>
description = <value>
version = <value>
author = <value>
tags = <value>
created_at = <value>
updated_at = <value>
```

These map directly to `metadata.<key>` (`parser.py:_parse_top_level`). The parser does not type-check them; `parse_graph` does:

| Key | Accepted JSON type | Default in `WorkflowMetadata` |
|---|---|---|
| `name` | string | `""` |
| `description` | string | `""` |
| `version` | string | `"1.0"` |
| `author` | string | `""` |
| `tags` | list of strings | `[]` |
| `created_at` | string or null | `null` |
| `updated_at` | string or null | `null` |

Wrong types raise `GraphParseError` from `parse_graph`.

### 5.3 `spec_version`

```text
spec_version = <value>
```

The DSL raw document starts with `spec_version: "1.0"` (`parser.py:parse`). The statement overrides it. `parse_dsl` then requires exactly `"1.0"`; any other value raises `GraphParseError` (`graph/parser.py:parse_graph`).

```ldsq
spec_version = "1.0"   # explicit, optional
```

### 5.4 Settings assignments

```text
default_dry_run = <bool>
max_concurrency = <int>
```

Both write into `settings.*` (`parser.py:_parse_top_level`). `parse_graph` validates `default_dry_run` as a boolean and `max_concurrency` as an integer; `validate_graph` requires `max_concurrency >= 1`.

### 5.5 `scope` block

```text
scope {
  roots = <list[str]>
  writable = <list[str]>
  allow_symlinks = <bool>
  policy_ref = <string|null>
  allow_parent_traversal = <bool>
}
```

Maps to `settings.scope` (`parser.py:_parse_top_level`). Empty `scope {}` is accepted and parses to an empty settings scope; it is omitted by the canonical emitter. Unknown keys inside `scope` are preserved in the raw block but ignored by `parse_graph` (only `roots`, `writable`, `allow_symlinks`, `policy_ref`, `allow_parent_traversal` are read). `allow_parent_traversal` defaults to `false`; when `true`, `..` is allowed in rendered templates and paths as long as the resolved path stays inside a writable root (see §12.4).

### 5.6 `error_handling` block

```text
error_handling {
  strategy = <"fail"|"retry"|"continue">
  max_retries = <int>
  retry_delay_seconds = <number>
  backoff = <"none"|"linear"|"exponential">
  strict_retry_safe = <bool>
}
```

Maps to `settings.error_handling` (and `strict_retry_safe` to `settings.strict_retry_safe`). `parse_graph` accepts any string for `strategy` and `backoff`; the executor clamps unknown strategies to `fail` (`executor/engine.py:Executor.execute`), and `_retry_delay` treats unknown backoff as `none`. `strict_retry_safe` defaults to `false`: a `retry_policy` on a non-retry-safe node warns and runs; setting it to `true` restores the pre-10.6 fail-before-run behavior (§14.5).

### 5.7 Variables

Block form:

```text
variable "<name>" {
  type = <string>
  value = <value>
  description = <string>
}
```

Shorthand form:

```text
variable "<name>" = <value>
```

The block form is parsed as a raw map and handed to `parse_graph`, which reads only `type` (default `"string"`), `value` (default `null`), and `description` (default `""`). Unknown keys are silently ignored by `parse_graph`.

The shorthand always emits `{"type": "string", "value": <value>}` (`parser.py:_parse_variable`). It does **not** infer the type from the value. `variable "n" = 5` produces a string-typed variable whose value is the integer 5.

Canonical emitter details (`serializer.py:dsl_from_dict`):

* Variables are emitted sorted by name.
* `variable "x" {}` is emitted for a declaration with no explicit `value`, no `description`, and default `type`.
* `value` is omitted when `None`; an explicit null value therefore cannot be distinguished from an absent value.
* `type` is omitted when `"string"`; `description` is omitted when empty.

### 5.8 Triggers

```text
trigger "<id>" {
  type = <string>
  title = <string>
  params {
    <key> = <value>
    ...
  }
}
```

`type` is required in practice: the parser defaults it to `""`, and `parse_graph` rejects an empty trigger type. `title` defaults to `""`; `params` defaults to `{}` (`parser.py:_parse_trigger`, `graph/parser.py:_parse_trigger`).

Unknown keys at trigger-block level are **silently dropped** by the DSL parser (`_parse_trigger` reads only `type`, `title`, `params`). Put custom data under `params`.

Trigger params are runtime-binding configuration, not automatically the params of a trigger node. See §10 for the runtime trigger reference.

### 5.9 Nodes

Full form:

```text
node "<id>" {
  type = <string>
  title = <string>
  state = <"enabled"|"disabled"|"skipped"|"failed">
  description = <string>
  params {
    <key> = <value>
    ...
  }
  inputs = <port-or-list>
  outputs = <port-or-list>
  position = { x = <number>, y = <number> }
}
```

Reserved node keys, exactly (`parser.py:_parse_node_body`):

```text
type, title, state, description, params, inputs, outputs, position
```

Any other key in a node block is routed into `params`:

```ldsq
node "mk" {
  type = "fs.make_dir"
  path_template = "work"      # -> params.path_template
}
```

This inline-params shorthand is **parser-only**; the emitter always writes a `params { ... }` block.

`type` defaults to `""` and is rejected by `parse_graph` if empty. `state` accepts `enabled`, `disabled`, `skipped`, `failed`, case-insensitively (`_coerce_state`). `position` values must be numbers; they are converted to `float`. `inputs`/`outputs` accept a single string, a single port object, or a list of either (§9.0).

A node may contain both `params { ... }` and inline params. They merge, but a later explicit `params { ... }` assignment replaces earlier inline keys because it is the same key in the block map. Canonical output uses only the explicit `params` block.

### 5.10 Edges

```text
edge "<id>" {
  source = <string>
  target = <string>
  source_port = <string>
  target_port = <string>
  label = <string>
  kind = <"data"|"order">
}
```

`source` and `target` are required by `parse_graph`; empty values fail. `source_port` defaults to `"out"`, `target_port` defaults to `"in"`, `label` defaults to `""`, and `kind` defaults to `"data"` (`graph/parser.py:_parse_edge`). Unknown keys in a JSON edge object are silently dropped by `parser.py:_parse_edge`.

`kind = "order"` makes the edge an ordering-only dependency: it constrains topological order but contributes nothing to the payload and needs no `target_port`. `kind = "data"` (the default) behaves exactly as before.

**DSL edge ids:** the DSL grammar requires a label after `edge`; `edge { ... }` is a parse error. There is no implicit-id shorthand in the DSL. The JSON parser, by contrast, derives `"{source}->{target}"` when `id` is absent (`graph/parser.py:_parse_edge`).

**Edges have no condition.** `EdgeSpec` no longer has a `condition` field. The JSON parser ignores an unknown `condition` key (unknown edge keys are dropped); the DSL edge parser rejects it with a parse error. Use `flow.if_branch` or `flow.filter` to compute decisions inside the graph (§11).

### 5.11 `extra_meta`

```text
extra_meta {
  <key> = <value>
  ...
}
```

Maps to the top-level `extra_meta` object. In JSON, any unknown **top-level** key is folded into `extra_meta` by `parse_graph` (`graph/parser.py:parse_graph`). In DSL, an unknown top-level statement is a parse error, so `extra_meta` is the only place for forward-compatible custom data.

Canonical output:

```ldsq
extra_meta {
  custom-key = [1, {nested = true}]
}
```

---

## 6. Value grammar

Source: `parser.py:_parse_value`, `_parse_list`, `_parse_inline_map`, `_parse_block`, `_parse_entry_value`.

```text
value       := STRING | NUMBER | WORD | list | inline-map
list        := "[" [ value { "," value } ] "]"
inline-map  := "{" [ key "=" value { "," key "=" value } ] "}"
key         := WORD | STRING
```

| Form | Example | Result |
|---|---|---|
| String | `"a\nb"` | `str` |
| Bare string | `work` | `str` `"work"` |
| Integer | `42`, `-3` | `int` |
| Float | `1.5`, `1e3` | `float` |
| Boolean | `true`, `false` | `bool` |
| Null | `null` | `None` |
| List | `["a", "b", 1]` | `list` |
| Inline map | `{x = 1, y = 2}` | `dict` |
| Block map | `position { x = 1 ... }` | `dict` |

Rules and restrictions:

* Lists are comma-separated and may span multiple lines. Newlines inside `[...]` are treated as whitespace, and a trailing comma before `]` is accepted. The emitter still writes lists inline.
* Inline maps are comma-separated `key = value` entries on one logical line (newlines are not skipped inside the braces). Trailing commas fail in inline maps.
* Block maps are newline-separated and accept either `key = value` or `key { ... }` entries (`_parse_entry_value`). This is how nested maps such as `params { ... }`, `scope { ... }`, and `schema { ... }` are written.
* A key may be a bare word or a quoted string. Quoted keys allow spaces and punctuation.
* Duplicate keys in a block or inline map: the last assignment wins.
* Empty list `[]` and empty map `{}` are valid.
* Bare-word values become booleans/null only for exact lowercase `true`, `false`, `null`. `True`, `FALSE`, and `none` are strings in DSL value position.
* Int versus float is preserved. Floats are emitted with `repr`, e.g. `1.0`, `1000.0`, `0.025`.
* Non-finite floats (`nan`, `inf`) cannot be produced by the lexer and cannot be emitted (`serializer.py:_fmt_value` raises `ValueError`).

Block-form list (identical to `items = [1, 2, 3]`):

```ldsq
node "loop" {
  type = "flow.loop_batch"
  params {
    items = [
      1,
      2,
      3,
    ]
    graph_id = "child"
  }
}
```

---

## 7. Blocks, shorthands, and canonical form

### 7.1 Parser-only shorthands

The parser accepts more forms than the emitter produces. These are **not canonical**:

| Shorthand | Accepted by parser | Canonical emitter output |
|---|---|---|
| Node params written directly in the node block | yes (`path_template = "work"`) | always `params { ... }` |
| `variable "x" = value` | yes, forces `type = "string"` | `variable "x" { type = ... value = ... }` |
| Bare string values | yes (`type = fs.make_dir`) | quoted strings (`type = "fs.make_dir"`) |
| Bare string labels | yes (`node mk {`) | quoted labels (`node "mk" {`) |
| Non-empty maps written as nested blocks inside any block entry | yes (`schema { ... }`) | maps in value position are inlined (`schema = {type = "object", ...}`) |
| Long lists split across lines, with an optional trailing comma | yes (`items = [\n  1,\n  2,\n]`) | lists are inline (`items = [1, 2]`) |
| `edge` without `label` | yes | omitted if empty |
| Ports as bare strings | yes (`inputs = ["file"]`) | bare string retained if it is a default `PortSpec` |
| `position` omitted | yes | omitted |
| Empty `scope {}`, `error_handling {}`, `extra_meta {}` | yes | omitted |

### 7.2 Canonical formatting rules

`emit_dsl` is deterministic (`serializer.py:dsl_from_dict`):

* One statement per line; no blank lines.
* Two spaces per block nesting level.
* Closing brace on its own line at the parent indent.
* Final newline is always present.
* Every string value is double-quoted with JSON escaping, `ensure_ascii=False`.
* Bare keys are used only when the key matches `^[A-Za-z_][A-Za-z0-9_.\-]*$`; otherwise the key is JSON-quoted (`_fmt_key`).
* Lists are inline: `[a, b]`.
* Maps in value position are inline: `{k = v, k = v}`.
* Empty maps at block positions are `key {}`.
* Defaults are omitted (see below).

### 7.3 Canonical key order

Top level:

1. `automation "<id>"`
2. `spec_version = "..."` (only if not `"1.0"`)
3. Metadata, in this exact order:
   `name`, `description`, `author`, `created_at`, `updated_at`, then `version` (only if not `"1.0"`), then `tags`.
4. Settings:
   `default_dry_run` (only if `false`), `max_concurrency` (only if not `1`), `scope` (if non-empty), `error_handling` (if non-empty).
5. Variables, sorted by name.
6. Triggers in document order.
7. Nodes in document order.
8. Edges in document order.
9. `extra_meta` (if non-empty).

Node order inside the block: `type`, `title`, `state`, `description`, `params`, `inputs`, `outputs`, `position`.

Trigger order: `type`, `title`, `params`.

Edge order: `source`, `target`, `source_port` (if not `"out"`), `target_port` (if not `"in"`), `kind` (if not `"data"`), `label` (if non-empty).

Metadata omission rules (`serializer.py:_metadata_to_dict`):

* `name`, `description`, `author`: omitted when empty.
* `version`: omitted when `"1.0"`.
* `created_at`, `updated_at`: omitted when `None`.
* `tags`: omitted when empty.

Settings omission rules (`serializer.py:_settings_to_dict`):

* `default_dry_run`: emitted only when `false`.
* `max_concurrency`: emitted only when not `1`.
* `scope`: emitted only when at least one field is non-default.
* `error_handling`: emitted only when at least one field is non-default (`strategy != "fail"`, `max_retries != 0`, `retry_delay_seconds != 0`, `backoff != "none"`).

### 7.4 Round-trip guarantees

Pinned by `tests/test_phase_10_5.py`:

```text
parse_graph(document_to_dict(doc)) == doc          # for any WorkflowDocument
emit_dsl(parse_dsl(text)) == text                  # only for canonical text
emit_dsl(parse_dsl(emit_dsl(doc))) == emit_dsl(doc) # fixed point for any doc
```

The second equality is **not** a promise that arbitrary input is preserved byte-for-byte. For example, a parser shorthand is normalized:

```ldsq
# input
node mk {
  type = fs.make_dir
  path_template = work
}
```

```ldsq
# emit_dsl(parse_dsl(input))
node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
}
```

Other normalizations: variables sorted by name; default-valued metadata/settings omitted; `variable "x" = 1` becomes a block with `type = "string"`; maps in value position are inlined; edges lose default ports.

`compile_dsl(text)` returns the raw dict without applying document defaults. `document_to_dict(parse_dsl(text))` is the normalized dict.

### 7.5 Canonical example

```ldsq
automation "canonical"
name = "Canonical example"
tags = ["demo"]
default_dry_run = false
max_concurrency = 2
scope {
  roots = ["inbox"]
  writable = ["inbox"]
}
variable "count" {
  type = "integer"
  value = 3
}
trigger "watch" {
  type = "triggers.watch_folder"
  title = "Watch"
  params {
    path = "inbox"
    patterns = ["*.txt"]
  }
}
node "mk" {
  type = "fs.make_dir"
  title = "Make work"
  state = "disabled"
  params {
    path_template = "work"
  }
  position = {x = 10.0, y = 20.0}
}
edge "e" {
  source = "mk"
  target = "wr"
  source_port = "out"
  target_port = "in"
  label = "always"
}
```

The emitter would drop `source_port = "out"` and `target_port = "in"` (they are defaults) and would keep `label = "always"`.

---

## 8. DSL → JSON mapping

| DSL construct | JSON schema location | Notes |
|---|---|---|
| `automation "<id>"` | `metadata.id` | mandatory, exactly once |
| `spec_version = v` | `spec_version` | DSL defaults to `"1.0"`; `parse_graph` accepts only `"1.0"` |
| `name = v` | `metadata.name` | string default `""` |
| `description = v` | `metadata.description` | string default `""` |
| `version = v` | `metadata.version` | string default `"1.0"` |
| `author = v` | `metadata.author` | string default `""` |
| `created_at = v` | `metadata.created_at` | string or null default `null` |
| `updated_at = v` | `metadata.updated_at` | string or null default `null` |
| `tags = v` | `metadata.tags` | list of strings default `[]` |
| `default_dry_run = v` | `settings.default_dry_run` | bool default `true` |
| `max_concurrency = v` | `settings.max_concurrency` | int default `1`, must be `>= 1` |
| `scope { ... }` | `settings.scope` | `roots`, `writable`, `allow_symlinks`, `policy_ref` |
| `error_handling { ... }` | `settings.error_handling` | `strategy`, `max_retries`, `retry_delay_seconds`, `backoff`; `strict_retry_safe` maps to `settings.strict_retry_safe` |
| `variable "<name>" { ... }` | `variables.<name>` | object `{type, value, description}` |
| `variable "<name>" = value` | `variables.<name>` | shorthand forces `type = "string"` |
| `trigger "<id>" { ... }` | `triggers[]` | entry `{id, type, title?, params?}` |
| `node "<id>" { ... }` | `nodes[]` | entry `{id, type, title?, state?, description?, params?, inputs?, outputs?, position?}` |
| Node inline `key = value` | `nodes[].params.<key>` | unless `key` is a reserved node key |
| `node ... inputs = ...` | `nodes[].inputs` | string, port object, or list |
| `node ... outputs = ...` | `nodes[].outputs` | string, port object, or list |
| `node ... position = {...}` | `nodes[].position` | numbers converted to float |
| `edge "<id>" { ... }` | `edges[]` | entry `{id, source, target, source_port?, target_port?, label?, kind?}`; `kind` defaults to `"data"`; `condition` is not part of the schema |
| `extra_meta { ... }` | `extra_meta` | arbitrary custom data |
| JSON-only: missing `edges[].id` | `edges[].id = "{source}->{target}"` | no DSL equivalent |
| JSON-only: unknown top-level key | `extra_meta.<key>` | folded by `parse_graph`; no DSL equivalent |

Ports accept three shapes, normalized by `graph/parser.py:_parse_ports` and `_parse_port`:

```text
"inputs": "file"
"inputs": {"name": "file", "type": "FileRef[]", "required": false, "default": null}
"inputs": [{"name": "file", "type": "FileRef[]"}]
```

A bare string becomes `PortSpec(name=..., type="any", required=true, description="", default=None)`.

---

## 9. Node reference

### 9.0 Registry, ports, and audit conventions

Nodes are registered by the `@register_node` decorator into the global `NodeRegistry` (`src/lokdocsqr/core/node_api/registry.py`, `decorators.py`). Importing `lokdocsqr.nodes` imports every built-in family and fires the decorators (`src/lokdocsqr/nodes/__init__.py`). Third-party nodes can be registered through the `lokdocsqr.nodes` entry-point group (`core/plugins.py`).

There are **32** built-in registered node types:

| Type id | Family | Title | `retry_safe` | Emits `ActionLog` |
|---|---|---|---|---|
| `fs.make_dir` | fs_ops | Create Folder | false | yes |
| `fs.write_file` | fs_ops | Write / Append File | false | yes |
| `fs.copy` | fs_ops | Copy File(s) | false | yes |
| `fs.move` | fs_ops | Move File(s) | false | yes |
| `fs.rename` | fs_ops | Rename File(s) | false | yes |
| `fs.tag` | fs_ops | Tag / Set Metadata | false | yes |
| `fs.trash` | fs_ops | Delete to Trash / Quarantine | false | yes |
| `fs.archive` | fs_ops | Archive / Extract | false | yes |
| `fs.convert` | fs_ops | Convert File | false | yes |
| `access.list_files` | access | List Files / Glob | false | no |
| `access.read_metadata` | access | Read Metadata | false | no |
| `access.read_content` | access | Read Content | false | no |
| `access.extract_text` | access | Extract Text | false | no |
| `access.ocr` | access | OCR | false | no |
| `access.hash_dedupe` | access | Hash & Dedupe | false | no |
| `access.index_search` | access | Index / Search | false | no |
| `flow.if_branch` | flow | If / Branch | true | no |
| `flow.filter` | flow | Filter | true | no |
| `flow.loop_batch` | flow | Loop / Batch | false | no |
| `flow.merge` | flow | Merge / Join | true | no |
| `flow.wait` | flow | Wait / Delay | true | no |
| `flow.retry` | flow | Retry | false | no |
| `flow.subflow` | flow | Subflow | false | no |
| `utils.set_variables` | utils | Set Variables | true | no |
| `utils.path_template` | utils | Path Template | true | no |
| `utils.json_validate` | utils | JSON Parse / Validate | true | no |
| `utils.notify` | utils | Notify | false | no |
| `llm.local_llm` | llm | Local LLM | true | no |
| `triggers.watch_folder` | triggers | Watch Folder | true | no |
| `triggers.scheduled_scan` | triggers | Scheduled Scan | true | no |
| `triggers.manual_run` | triggers | Manual Run | true | no |
| `triggers.webhook` | triggers | Webhook / External Trigger | true | no |

`retry_safe` is the class flag checked by the executor before allowing `params.retry_policy` (§14.4).

**Two different port concepts.** A node class exposes metadata through `.inputs` / `.outputs` for the GUI and documentation, but `parse_graph` does **not** copy that metadata into `NodeSpec`. Required-port enforcement and edge-port validation use only the `inputs`/`outputs` written in the document. If a document omits `inputs`, no input port is required even if the class declares one. This is why many examples work with only `params`.

**Registry-port warning.** When an edge names a port that is not declared in the document but *is* declared on the registered node class, `validate_graph` emits a warning instead of an error. The CLI prints it to stderr and does not fail. The warning recommends adding the declaration explicitly (for example ``outputs = [{name = "path"}]``). Registry ports are never injected into `NodeSpec`, so round-tripping is unaffected. Callers collect warnings through the keyword out-parameter: ``validate_graph(document, warnings=warnings)``.

**Ordering-only edges.** An edge with `kind = "order"` is a dependency without data. Ports are neither required nor validated, and payload resolution skips it. Use it to sequence two nodes that have no meaningful value to pass.

**Audit convention.** Nodes construct `ActionLog` objects; the executor owns appending and finalizing (`core/executor/engine.py:_logs_from_return`, `Executor.execute`). Accepted return shapes are `ActionLog`, a non-empty list/tuple of `ActionLog`, or a dict with `"action_log"` containing one of those. In the dict case the `action_log` key is removed from `NodeResult.outputs`; in the direct case outputs are empty. `status == "planned"` is finalized to `dry_run` in dry-run, `applied` for an enabled result, or `failed` for a failed result. Run/automation ids are filled if missing.

### 9.1 `fs_ops` family

All `fs_ops` nodes mutate through `core.safe_fs` and call `scope.check` before mutation. Their params are consumed from the node payload (static `params` plus inbound edge values), not from a separate typed schema. Path-like strings are validated by `validate_graph` for `..`, absolute paths, and template policy (`graph/validator.py`). In particular, `validate_graph` rejects an absolute path-like param (`path`, `source`, `target`, `template`, `path_template`, and keys ending `_path`/`_template`/`_dir`/`_folder`/`_file`), even though a node called directly can resolve an absolute source.

#### `fs.make_dir` — Create Folder

Creates a directory at a rendered `path_template`; idempotent. Source: `src/lokdocsqr/nodes/fs_ops/make_dir.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path_template` | string | — (required) | relative template | Target directory; rendered with `render_template` against `variables` only |
| `variables` | object | `{}` | any mapping | Template variables; no `name`/`ext`/`date` defaults for this node |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path_template` | string | true | none |
| `variables` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `action_log` | ActionLog |
| `path` | string |

**Side effects / audit.** Calls `safe_fs.make_dir`; emits `ActionLog(action_type="make_dir")`, risk `LOW`; dry-run performs no mutation and records `would_create` in `extra`. Returns `{"action_log": ActionLog, "path": str}`; the executor strips `action_log` from outputs.

```ldsq
node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
}
```

#### `fs.write_file` — Write / Append File

Writes or appends UTF-8 content atomically. Source: `src/lokdocsqr/nodes/fs_ops/write_file.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path_template` | string | — (required) | relative template | Target file |
| `content` | string or bytes | — (required) | any string/bytes | Content to write; JSON/DSL supplies strings |
| `mode` | string | `"write"` | `"write"`, `"append"` | Truncate/replace or append atomically |
| `create_parents` | bool | `true` | true/false | Create parent directory via `safe_fs.make_dir` |
| `variables` | object | `{}` | any mapping | Template variables |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path_template` | string | true | none |
| `content` | string | true | none |
| `mode` | string | false | `"write"` |
| `create_parents` | bool | false | `true` |
| `variables` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `action_log` | ActionLog |
| `path` | string |
| `bytes_written` | integer |

**Side effects / audit.** Calls `safe_fs.atomic_write` or `safe_fs.atomic_append`; can create parents. `ActionLog.action_type` is `write_file:write` or `write_file:append`, risk `MEDIUM`. Dry-run records `would_overwrite` in `extra` and does not write. Non-string/bytes `content` raises `NodeExecutionError`.

```ldsq
node "wr" {
  type = "fs.write_file"
  params {
    path_template = "work/report.txt"
    content = "hello"
    mode = "append"
    create_parents = true
  }
}
```

#### `fs.copy` — Copy File(s)

Copies one source file to a rendered destination with conflict resolution and an optional free-space precheck. Source: `src/lokdocsqr/nodes/fs_ops/copy.py`, `_base.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `source` | string | — (required) | path | Source file; resolved against `config.base_dir` and read-scope-checked |
| `template` | string | `"{name}{ext}"` | template | Destination; relative results resolve against `target_dir` when set, otherwise `source.parent` |
| `target_dir` | string or null | `null` | relative template | Optional base directory rendered against `config.base_dir`; `template` then resolves against it |
| `conflict_policy` | string | `"skip"` | `"skip"`, `"rename"`, `"overwrite"`, `"ask"` | What to do if destination exists |
| `check_free_space` | bool | `true` | true/false | Probe target volume free space before copy |
| `variables` | object | `{}` | any mapping | Extra template variables; override `name`/`ext`/`date`/`author`/`tags` |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `file` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `file` | string | Resolved destination path |

**`target_dir`.** When set, `target_dir` is rendered against `config.base_dir` and scope-checked first; the `template` result is then resolved against that directory instead of `source.parent`. When `null` (default), behaviour is unchanged.

**Side effects / audit.** Calls `safe_fs.copy_file`; `ActionLog(action_type="copy")`; risk `LOW` with no conflict, `MEDIUM` for skip/rename/overwrite, `HIGH` for pending verify. `ask` currently behaves as `skip` with reason `conflict_pending_verify`. Dry-run also performs the free-space probe and may fail with `SafeFsError("insufficient_space")`. `run` and `dry_run` return `{"action_log": log, "file": <destination>}`, so a downstream node wired to `file` receives the destination path.

```ldsq
node "cp" {
  type = "fs.copy"
  params {
    source = "inbox/a.txt"
    template = "copied-{name}{ext}"
    conflict_policy = "rename"
    check_free_space = true
  }
}
```

#### `fs.move` — Move File(s)

Moves one source file to a rendered destination. Cross-volume handling is inside `safe_fs.safe_move`. Source: `src/lokdocsqr/nodes/fs_ops/move.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `source` | string | — (required) | path | Source file; write-scope-checked |
| `template` | string | `"{name}{ext}"` | template | Destination; relative to `target_dir` when set, otherwise `source.parent` |
| `target_dir` | string or null | `null` | relative template | Optional base directory rendered against `config.base_dir`; `template` then resolves against it |
| `conflict_policy` | string | `"skip"` | `"skip"`, `"rename"`, `"overwrite"`, `"ask"` | Conflict handling |
| `cross_volume` | bool | `true` | true/false | Reserved; `safe_fs.safe_move` already handles EXDEV |
| `variables` | object | `{}` | any mapping | Template variables |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `file` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `file` | string | Resolved destination path |

**`target_dir`.** When set, `target_dir` is rendered against `config.base_dir` and scope-checked first; the `template` result is then resolved against that directory instead of `source.parent`. When `null` (default), behaviour is unchanged.

**Side effects / audit.** Calls `safe_fs.safe_move` when the conflict decision is `proceed`. `ActionLog(action_type="move")`; rollback-eligible only when applied. Risk `LOW` for no conflict/no change, `MEDIUM` for policy-resolved conflicts, `HIGH` for `ask`. `run` and `dry_run` return `{"action_log": log, "file": <destination>}`, so the declared `file` port is populated.

```ldsq
node "mv" {
  type = "fs.move"
  params {
    source = "inbox/a.txt"
    template = "archive/{name}{ext}"
    conflict_policy = "overwrite"
  }
}
```

#### `fs.rename` — Rename File(s)

Rewrites a file name via a template. Source: `src/lokdocsqr/nodes/fs_ops/rename.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `source` | string | — (required) | path | Source file |
| `template` | string | `"{name}{ext}"` | template | New name or relative path |
| `conflict_policy` | string | `"rename"` | `"skip"`, `"rename"`, `"overwrite"`, `"ask"` | Conflict handling |
| `variables` | object | `{}` | any mapping | Template variables |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `file` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `file` | string | Resolved destination path |

**Side effects / audit.** Uses `safe_fs.safe_rename` when the destination parent equals the source parent, otherwise `safe_fs.safe_move`. `ActionLog(action_type="rename")`; rollback-eligible when applied. `run` and `dry_run` return `{"action_log": log, "file": <destination>}`.

```ldsq
node "rn" {
  type = "fs.rename"
  params {
    source = "inbox/a.txt"
    template = "renamed-{name}{ext}"
    conflict_policy = "rename"
  }
}
```

#### `fs.tag` — Tag / Set Metadata

Sets, adds, or removes tags, persisting them to a sidecar JSON and (best effort) an extended attribute. Source: `src/lokdocsqr/nodes/fs_ops/tag.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path` | string | — (required) | path | Existing target file |
| `tags` | list[string] | — (required) | list of strings | Tags to set/add/remove |
| `mode` | string | `"set"` | `"set"`, `"add"`, `"remove"` | Replace, union, or subtract |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path` | string | true | none |
| `tags` | list[string] | true | none |
| `mode` | string | false | `"set"` |

**Outputs**

| Name | Type |
|---|---|
| `action_log` | ActionLog |
| `tags` | list[string] |

**Side effects / audit.** Writes `<file>.tags.json` via `safe_fs.atomic_write`; calls `safe_fs.set_xattr("user.lokdocsqr.tags", ...)` when available. Does not modify file content. `ActionLog(action_type="tag")`, risk `LOW`; `extra` records `mode`, `added`, `removed`, final `tags`. Dry-run computes the final tag list but writes neither sidecar nor xattr. Missing target raises `NodeExecutionError`.

```ldsq
node "tag" {
  type = "fs.tag"
  params {
    path = "inbox/a.txt"
    tags = ["invoice", "acme"]
    mode = "add"
  }
}
```

#### `fs.trash` — Delete to Trash / Quarantine

Moves a file into the configured trash directory and writes a sidecar; never deletes. Source: `src/lokdocsqr/nodes/fs_ops/trash.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path` | string | — (required) | path | File to trash; write-scope-checked |
| `ttl_seconds` | integer or null | config `trash_default_ttl`, else 30 days | any integer (not range-checked) | Recorded in the sidecar for the runtime expiry sweep |
| `reason` | string or null | `"trash"` | any string | Recorded in `ActionLog.reason`; no behavioral effect |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `files` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `files` | list[string] | Trashed path (one-element list) |

**Side effects / audit.** Requires `ctx.config.trash_dir` (missing raises `ConfigError`); verifies it is inside write scope. Moves via `safe_fs.trash`, writes `<trash-name>.sidecar.json` with `hash_before`, paths, size, timestamps, TTL, run/automation ids. `ActionLog(action_type="trash")`, risk `MEDIUM`, `rollback_eligible=True`. Dry-run plans the trash target and hashes the source but does not move it. `run` and `dry_run` return `{"action_log": log, "files": [<trash path>]}`.

```ldsq
node "tr" {
  type = "fs.trash"
  params {
    path = "inbox/a.txt"
    ttl_seconds = 86400
    reason = "duplicate"
  }
}
```

#### `fs.archive` — Archive / Extract

Creates a zip archive from files, or extracts a zip with zip-slip protection. Source: `src/lokdocsqr/nodes/fs_ops/archive.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `operation` | string | `"create"` | `"create"`, `"extract"` | Operation selector |
| `sources` | list[string] | `[]` | non-empty for create | Files to pack |
| `target` | string | `""` | template | Destination archive for create |
| `source` | string | `""` | path | Source archive for extract |
| `target_dir` | string | `""` | template | Extraction directory |
| `format` | string | `"zip"` | `"zip"` | Archive format (only zip is implemented) |
| `overwrite` | bool | `false` | true/false | Create: replace an existing target archive |
| `conflict_policy` | string | `"skip"` | `"skip"`, `"rename"`, `"overwrite"`, `"ask"` | Extract: per-file conflict handling |
| `variables` | object | `{}` | any mapping | Template variables for `target`/`target_dir` |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `file` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `file` | string | Create: archive path; extract: extraction directory |

**Side effects / audit.** `create` builds the zip in memory and commits with `safe_fs.atomic_write`; it refuses an existing target unless `overwrite=true`. `extract` validates every entry before writing (absolute paths, `..`, escapes outside `target_dir` raise `ScopeViolation`), creates directories with `safe_fs.make_dir`, and writes files with `safe_fs.atomic_write`. Logs: one `archive_create` log for create; one `archive_extract` log per extracted/skipped file for extract. The return value is `{"action_log": log_or_logs, "file": <archive path or target_dir>}`.

```ldsq
node "ar" {
  type = "fs.archive"
  params {
    operation = "create"
    sources = ["inbox/a.txt", "inbox/b.txt"]
    target = "archive/bundle.zip"
    format = "zip"
    overwrite = false
  }
}
```

#### `fs.convert` — Convert File

Converts between registered MIME pairs. Only `text/csv → application/json` and `application/json → text/csv` are implemented. Source: `src/lokdocsqr/nodes/fs_ops/convert.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `source` | string | — (required) | path | Source file |
| `target` | string | — (required) | template | Destination file |
| `from_mime` | string or null | null (guessed) | MIME string | Source MIME override |
| `to_mime` | string or null | null (guessed) | MIME string | Destination MIME override |
| `overwrite` | bool | `false` | true/false | Replace an existing target |
| `variables` | object | `{}` | any mapping | Template variables for `target` |
| `supported` | — | — | — | Informational only; appears in `describe()` as a value, is not read from the payload |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `file` | any | n/a (bare registry port) | none |

**Outputs**

| Name | Type | Meaning |
|---|---|---|
| `file` | string | Converted target path |

**Side effects / audit.** Reads with `safe_fs.read_bytes`, writes with `safe_fs.atomic_write`. Unsupported pair raises `NodeExecutionError("converter_not_available: ...")` with the supported list. `ActionLog(action_type="convert")`, risk `LOW`. Dry-run reads the source to validate but does not write. `run` and `dry_run` return `{"action_log": log, "file": <target>}`.

```ldsq
node "cv" {
  type = "fs.convert"
  params {
    source = "inbox/rows.csv"
    target = "archive/rows.json"
    from_mime = "text/csv"
    to_mime = "application/json"
    overwrite = false
  }
}
```

### 9.2 `access` family

All `access.*` nodes are read-only: no `ActionLog` is emitted, and every path is scope-checked with `write=False` before `stat`/read. Their params are payload keys read in `run`/`dry_run`; they have no separate typed param schema. Source base: `src/lokdocsqr/nodes/access/_base.py`.

#### `access.list_files` — List Files / Glob

Lists files/directories under scoped roots with glob and metadata filters. Source: `src/lokdocsqr/nodes/access/list_files.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path` | string or list[string] | — (required) | path(s) | Root(s) to scan; must be directories |
| `pattern` | string | `"*"` | fnmatch pattern | Name/glob filter |
| `recursive` | bool | `false` | true/false | Recurse into subdirectories |
| `extensions` | list[string] or null | null | e.g. `[".pdf", "docx"]` | Case-insensitive suffix filter; leading dot optional |
| `min_size` | integer or null | null | bytes | Minimum file size |
| `max_size` | integer or null | null | bytes | Maximum file size |
| `include_dirs` | bool | `false` | true/false | Include matching directories |
| `follow_symlinks` | bool | `false` | true/false | Include symlink entries |
| `limit` | integer | `10000` | `> 0` | Maximum collected entries |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `files` | FileRef[] |

**Runtime result keys.** `files` (list of `{path, size, is_dir}`), `truncated` (bool), `scanned` (int). `truncated` is true only when a matching entry exists beyond `limit`. `scanned` is always present, including `0` for an empty folder; `--explain` prints it as `<node_id>: scanned N file(s)`.

```ldsq
node "ls" {
  type = "access.list_files"
  params {
    path = "inbox"
    pattern = "*.pdf"
    recursive = true
    limit = 1000
  }
}
```

#### `access.read_metadata` — Read Metadata

Returns size/mtime/mode/type metadata, optionally hashing files. Source: `src/lokdocsqr/nodes/access/read_metadata.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Files/directories to inspect |
| `include_hash` | bool | `false` | true/false | Compute a digest for regular files |
| `hash_algo` | string | `"sha256"` | `hashlib` algorithm name | Hash algorithm |
| `max_bytes_for_hash` | integer | `52428800` (50 MiB) | `>= 0` | Skip hashing larger files |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `items` | Metadata[] |

**Runtime result keys.** `items` entries contain `path`, `size`, `mtime`, `mode`, `is_dir`, `is_symlink`, optional `hash`, and optional `error`/`hash_error`. Per-path failures are item errors; the node fails only if every path fails. Dry-run never hashes.

```ldsq
node "md" {
  type = "access.read_metadata"
  params {
    paths = ["inbox/a.pdf"]
    include_hash = true
    hash_algo = "sha256"
    max_bytes_for_hash = 52428800
  }
}
```

#### `access.read_content` — Read Content

Reads text or binary content with strict size and binary policies. Source: `src/lokdocsqr/nodes/access/read_content.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Files to read |
| `encoding` | string | `"utf-8"` | codec name | Text decoding |
| `errors` | string | `"strict"` | codec error handler | Decode error behavior |
| `max_bytes` | integer | `1048576` (1 MiB) | `>= 0` | Per-file read cap |
| `binary_policy` | string | `"error"` | `"error"`, `"skip"`, `"base64"` | Binary-content behavior |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `items` | Content[] |

**Runtime result keys.** Items contain `path`, `encoding`, `bytes`, `truncated`, `binary`, optional `text`, optional `error`; dry-run items also include `size` and `max_bytes`. `binary_policy="error"` raises `AccessBinaryError` for the first binary file; `"skip"` omits text; `"base64"` returns base64 text. Files larger than `max_bytes` at stat time are per-item errors.

```ldsq
node "rc" {
  type = "access.read_content"
  params {
    paths = ["inbox/a.txt"]
    encoding = "utf-8"
    max_bytes = 1048576
    binary_policy = "skip"
  }
}
```

#### `access.extract_text` — Extract Text

Extracts text/CSV from supported formats through the `HANDLERS` registry. Source: `src/lokdocsqr/nodes/access/extract_text.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Files to extract |
| `format` | string | `"auto"` | `auto`, `txt`, `html`, `htm`, `csv`, `png`, `jpg`, `jpeg`, `tif`, `tiff`, `bmp`, `pdf`, `docx`, `eml` | Explicit format or extension inference |
| `max_bytes` | integer | `1048576` (1 MiB) | `>= 0` | Per-file read cap |
| `csv_delimiter` | string or null | null | single character | If null, `csv.Sniffer` guesses; fallback `,` |
| `csv_encoding` | string | `"utf-8-sig"` | codec name | CSV decoding |
| `html_keep_whitespace` | bool | `false` | true/false | Preserve HTML whitespace instead of collapsing |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `items` | Text[] |

**Runtime result keys.** Items contain `path`, `format`, `text`, `csv`, `bytes`, `truncated`, optional `error`. `txt` uses UTF-8 with replacement; `html` uses stdlib parser, skips script/style, unescapes entities; `csv` returns a list of rows in `csv`. PDF/DOCX/EML and image OCR handlers are lazy and report missing optional dependencies as per-item errors. The OCR handler registered here is invoked with default `lang="eng"`, `psm=3`, `oem=3`; this node does not forward those options.

```ldsq
node "et" {
  type = "access.extract_text"
  params {
    paths = ["inbox/a.pdf"]
    format = "auto"
    max_bytes = 1048576
  }
}
```

#### `access.ocr` — OCR

Runs Tesseract OCR on images with per-item errors and lazy optional imports. Source: `src/lokdocsqr/nodes/access/ocr.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Image files |
| `lang` | string | `"eng"` | Tesseract language code | OCR language |
| `psm` | integer | `3` | Tesseract PSM | Page segmentation mode |
| `oem` | integer | `3` | Tesseract OEM | OCR engine mode |
| `max_bytes` | integer | `20971520` (20 MiB) | `>= 0` | Per-file size cap |
| `preprocessing` | string | `"none"` | `"none"`, `"grayscale"`, `"grayscale+threshold"` | Pillow preprocessing |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `items` | OcrItem[] |

**Runtime result keys.** Items contain `path`, `text`, `confidence`, `lang`, `bytes`, `error`. Supported extensions are `png`, `jpg`, `jpeg`, `tif`, `tiff`, `bmp`; PDF/DOCX are rejected per item. Dry-run only stats paths and reports structural errors; it never imports `pytesseract` or `PIL`.

```ldsq
node "ocr" {
  type = "access.ocr"
  params {
    paths = ["inbox/scan.png"]
    lang = "eng"
    psm = 3
    oem = 3
    preprocessing = "grayscale"
  }
}
```

#### `access.hash_dedupe` — Hash & Dedupe

Hashes files and groups identical `(digest, size)` content. Source: `src/lokdocsqr/nodes/access/hash_dedupe.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Files/directories to scan |
| `recursive` | bool | `true` | true/false | Recurse into directories |
| `algo` | string | `"sha256"` | `"sha256"`, `"md5"`, `"blake2b"` | Digest algorithm |
| `max_bytes_for_hash` | integer | `52428800` (50 MiB) | `>= 0` | Larger files go to `too_large` |
| `skip_symlinks` | bool | `true` | true/false | Skip symlink entries |
| `include_unique` | bool | `false` | true/false | Include single-file groups |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `groups` | dict[] |

**Runtime result keys.** `groups` entries `{hash, algo, size, paths}` sorted by `(size, hash)`; plus `too_large`, `skipped`, `scanned`, `unique_count`, `duplicate_count`. Dry-run hashes nothing: `groups` is empty and both counts are null.

```ldsq
node "hd" {
  type = "access.hash_dedupe"
  params {
    paths = ["inbox"]
    recursive = true
    algo = "sha256"
    max_bytes_for_hash = 52428800
    skip_symlinks = true
  }
}
```

#### `access.index_search` — Index / Search

Searches file text in memory using `extract_text.HANDLERS`. Source: `src/lokdocsqr/nodes/access/index_search.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `paths` | string or list[string] | — (required) | path(s) | Files/directories to search |
| `query` | string | — (required, non-empty) | any string | Search text or regex |
| `mode` | string | `"substring"` | `"substring"`, `"word"`, `"regex"` | Matching mode |
| `case_sensitive` | bool | `false` | true/false | Case sensitivity |
| `context_chars` | integer | `40` | `>= 0` | Context window characters on each side |
| `max_matches_per_file` | integer | `100` | `>= 0` | Stop a file after this many matches |
| `max_total_matches` | integer | `1000` | `>= 0` | Stop the walk after this many matches |
| `max_bytes_per_file` | integer | `1048576` (1 MiB) | `>= 0` | Skip larger files without opening |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `paths` | string or list[string] | true (at run time) | none |

**Outputs**

| Name | Type |
|---|---|
| `matches` | dict[] |

**Runtime result keys.** `matches` entries `{path, line, col, match, context}` sorted by `(path, line, col)`; plus `files_scanned`, `files_skipped`, `truncated`, `errors`, and in dry-run `files_would_scan`. Symlinks are not followed. Invalid regex raises `AccessLimitError` (a `LokError`) before traversal.

```ldsq
node "ix" {
  type = "access.index_search"
  params {
    paths = ["inbox"]
    query = "ACME"
    mode = "word"
    case_sensitive = false
    context_chars = 40
  }
}
```

### 9.3 `flow` family

Flow nodes are pure with respect to the filesystem (nested `loop_batch`/`subflow` execute child graphs as their documented side effect). They use the shared `_FlowBase` and condition evaluator in `src/lokdocsqr/nodes/flow/_common.py`.

#### `flow.if_branch` — If / Branch

Evaluates a safe condition against variables and payload fields. Source: `src/lokdocsqr/nodes/flow/if_branch.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `condition` | string | — (required) | condition expression (§11) | Expression to evaluate |
| `items` | any[] | absent | list/tuple | If present, evaluate once per item; otherwise evaluate the payload once |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `condition` | string | true | none |
| `items` | any[] | false | none |

**Outputs**

| Name | Type |
|---|---|
| `result` | any (bool, list[bool], or `"error"` sentinel) |
| `branch` | any (`"true"`, `"false"`, `"error"`, or lists of those) |
| `items` | any[] |

**Behavior.** A list/tuple payload, or a dict payload containing a list under `items`, `files`, `file_refs`, `input`, or `data`, is treated as a sequence: it returns one result/branch per input in `items`. Otherwise it evaluates once and returns `items: []`. A condition error becomes `"error"` rather than an exception. `flow.if_branch` does **not** gate downstream nodes; edges from `branch` are data dependencies only (see §11.1).

```ldsq
node "if" {
  type = "flow.if_branch"
  params {
    condition = "size > 0"
    items = [1, 2, 3]
  }
}
```

#### `flow.filter` — Filter

Keeps items for which a condition is true. Source: `src/lokdocsqr/nodes/flow/filter.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `items` | any[] | absent → `[]` | list/tuple | Items to test |
| `condition` | string | — (required) | condition expression | Per-item predicate |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `items` | any[] | false | none |
| `condition` | string | true | none |

**Outputs**

| Name | Type |
|---|---|
| `items` | any[] |
| `results` | any[] |
| `total` | int |
| `kept` | int |
| `filtered` | int |
| `errors` | int |

**Behavior.** Each item dict is merged over the base mapping; a non-dict item is exposed as `item`. Evaluation errors append the string `"error"` to `results`, increment `errors`, and do not add the item to `items`. The preview strips content-like keys (`content`, `text`, `body`, `data`, `raw`, `bytes`, `ocr`, `extracted_text`, `extracted`, `content_text`) before evaluating, so content-dependent predicates are approximate in preview. A scalar, or a dict without one of the recognized list keys (`items`, `files`, `file_refs`, `input`, `data`), is invalid input: the node stays fail-soft and returns `{"items": [], "results": ["error"], "total": 0, "kept": 0, "filtered": 0, "errors": 1}`. An empty list is valid and yields `errors=0`.

```ldsq
node "flt" {
  type = "flow.filter"
  params {
    condition = "size > 0"
    items = [{path = "a", size = 1}]
  }
}
```

#### `flow.loop_batch` — Loop / Batch

Executes a child graph once per item (`loop`) or once per chunk (`batch`). Source: `src/lokdocsqr/nodes/flow/loop_batch.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `items` | any[] | absent/null → `[]` | list or tuple | Input units |
| `mode` | string | `"loop"` | `"loop"`, `"batch"` | Per-item or per-chunk execution |
| `batch_size` | integer | `1` | `> 0` | Chunk size; validated only in `batch` mode |
| `graph_id` | string | — | catalog id | Exactly one of `graph_id` / `graph` |
| `graph` | object | — | raw Workflow v1.0 dict | Inline child graph |
| `max_depth` | integer | config `flow.max_subflow_depth`, else `4` | `> 0` | Maximum child depth |
| `recursive_preview` | bool | `false` | true/false | Allows dry-run preview through `ctx.planning_callback`; otherwise preview fails closed |
| `on_child_error` | string | `"warn"` | `"continue"`, `"fail"`, `"warn"` | How a failed child unit is reported (§14.6) |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `items` | any[] | false | none |
| `mode` | string | false | `"loop"` |
| `batch_size` | integer | false | `1` |

**Outputs**

| Name | Type |
|---|---|
| `results` | any[] |
| `errors` | any[] |
| `error_count` | integer |
| `total` | integer |
| `mode` | string |
| `batch_size` | integer |
| `graph_id` | string |
| `depth` | integer |

**Behavior.** The child graph is resolved even when `items` is empty (fail-closed). In `loop` mode each child context gets `item`, `index`, and `batch=[item]`; in `batch` mode `item=None` and `batch` is the chunk. Parent variables stay visible; `flow.run_id`, `flow.automation_id`, `flow.depth`, `flow.parent_run_id` are injected. Per-item failures (a raised `LokError` or a child `RunResult` with `success=false`) are recorded as `{index, error, error_type}` and `results` stays aligned (`None` for the failed unit); non-`LokError` exceptions propagate. `on_child_error` selects the reporting policy: `continue` records errors and leaves the node successful; `warn` (default) also records each failure in `RunResult.warnings` and marks the node/run failed while continuing the remaining items; `fail` re-raises the first child `LokError` immediately. `dry_run` fails closed unless `params.recursive_preview=true` and `ctx.planning_callback` is available. The node does not emit `ActionLog` itself; child nodes do.

```ldsq
node "loop" {
  type = "flow.loop_batch"
  params {
    items = [1, 2, 3]
    mode = "batch"
    batch_size = 2
    graph_id = "child"
    max_depth = 4
  }
}
```

#### `flow.merge` — Merge / Join

Merges multiple incoming values. Source: `src/lokdocsqr/nodes/flow/merge.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `items` | any[] | absent/null → `[]` | list/tuple or single value | Values to merge; a single value is wrapped in a list |
| `mode` | string | `"last_wins"` | `"last_wins"`, `"concat"`, `"dict_merge"` | Merge strategy |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `items` | any[] | false | none |
| `mode` | string | false | `"last_wins"` |

**Outputs**

| Name | Type |
|---|---|
| `merged` | any |
| `count` | int |
| `items` | any[] |
| `mode` | string |
| `errors` | any[] |
| `error_count` | int |

**Behavior.** `last_wins` ignores `None`; `concat` flattens one list level and appends scalars; `dict_merge` shallow-merges dicts later-wins and records non-dict entries in `errors`. Unknown modes return `merged=None` and one error entry; they do not raise.

```ldsq
node "mg" {
  type = "flow.merge"
  params {
    items = [[1, 2], [3]]
    mode = "concat"
  }
}
```

#### `flow.wait` — Wait / Delay

Sleeps for a deterministic duration. Source: `src/lokdocsqr/nodes/flow/wait.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `seconds` | number | `1.0` | `>= 0` | Duration passed to `ctx.clock.sleep` |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `seconds` | float | false | `1.0` |

**Outputs**

| Name | Type |
|---|---|
| `slept` | float |
| `seconds` | float |
| `dry_run` | bool |

**Behavior.** `run` sleeps on `ctx.clock`. `dry_run` never sleeps and is side-effect free; it returns `slept = 0.0`, `seconds`, and `would_sleep` with the duration it would have waited. Non-numeric or negative values raise `NodeExecutionError` in both modes.

```ldsq
node "wt" {
  type = "flow.wait"
  params {
    seconds = 0.5
  }
}
```

#### `flow.retry` — Retry

Retry/error-handler marker. Source: `src/lokdocsqr/nodes/flow/retry.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `retry_policy` | object | absent | any mapping | Echoed unchanged to the `retry_policy` output |
| `on_error` | bool | injected by executor | true/false | Not a user param; the executor sets it from upstream FAILED results |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `retry_policy` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `retry` | boolean |
| `pass` | boolean |
| `retry_policy` | object |
| `on_error` | boolean |

**Behavior.** `_inject_on_error=True` makes the executor (a) keep this node runnable when an upstream node FAILED and (b) set `payload["on_error"] = any(upstream result state == FAILED)`. The node returns `retry = on_error`, `pass = not on_error`. It does not itself retry anything; it is a routing/marker node.

```ldsq
node "rt" {
  type = "flow.retry"
  params {
    retry_policy = {attempts = 3, backoff = 1.0, retry_on = ["NodeExecutionError"]}
  }
}
```

#### `flow.subflow` — Subflow

Invokes a nested automation or an inline graph. Source: `src/lokdocsqr/nodes/flow/subflow.py`, shared guards in `flow/_common.py:_resolve_child_graph`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `graph_id` | string | — | catalog id | Exactly one of `graph_id` / `graph` |
| `graph` | object | — | raw Workflow v1.0 dict | Inline child graph |
| `max_depth` | integer | config `flow.max_subflow_depth`, else `4` | `> 0` | Maximum child depth (inclusive) |
| `recursive_preview` | bool | `false` | true/false | Allows dry-run preview through `ctx.planning_callback`; otherwise preview fails closed |
| `on_child_error` | string | `"warn"` | `"continue"`, `"fail"`, `"warn"` | How a failed child run is reported (§14.6) |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `graph_id` | string | false | none |
| `graph` | dict | false | none |
| `max_depth` | int | false | config/4 |

**Outputs**

| Name | Type |
|---|---|
| `result` | any (a `RunResult` in normal execution) |
| `graph_id` | string |
| `depth` | int |
| `errors` | any[] |
| `error_count` | int |

**Behavior.** Exactly one of `graph_id`/`graph` is required. `graph_id` must not contain `/`, `\`, or end with `.json`; it is looked up in `ctx.automations` (`AutomationCatalog`) and is not a filesystem path. Inline graphs run `parse_graph → validate_graph → resolve_states → build_graph`. The depth guard rejects when `ctx.flow_depth >= max_depth`; the child receives `flow_depth + 1`. A `graph_id` already in `ctx.subflow_stack` raises a cycle error. Child contexts share scope/config/audit/policy/clock and merge parent variables with `flow.*`; the child document's own `settings.scope` is not applied. A raised child `LokError` or a child `RunResult` with `success=false` is reported through `errors`; under the default `on_child_error=warn` it also adds a `RunResult.warnings` entry and marks the run failed, `continue` records it without failing, and `fail` re-raises the first `LokError`. Dry-run fails closed unless `recursive_preview=true` and a planning callback exists. See §15.

```ldsq
node "sf" {
  type = "flow.subflow"
  params {
    graph_id = "child"
    max_depth = 4
  }
}
```

### 9.4 `utils` family

#### `utils.set_variables` — Set Variables

Sets run variables through `ctx.set_variable`. Source: `src/lokdocsqr/nodes/utils/set_variables.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `variables` | object | — (required) | mapping of name → value | Variables to set |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `variables` | dict | true | none |

**Outputs**

| Name | Type |
|---|---|
| `variables` | dict |
| `count` | int |
| `errors` | any[] |

**Behavior.** Reserved names in `RESERVED_VARIABLES` (`run_id`, `automation_id`, `flow_depth`, `automations`, `scope`, `config`, `audit`, `logger`, `events`, `policy`, `verify_callback`, `clock`, `flow.run_id`, `flow.automation_id`, `flow.depth`, `flow.parent_run_id`) are reported per item and not raised. Non-string names are errors. Dry-run returns `would_apply` plus `preview`/`dry_run` and does not mutate. No filesystem access.

```ldsq
node "sv" {
  type = "utils.set_variables"
  params {
    variables = {category = "invoice", score = 7}
  }
}
```

#### `utils.path_template` — Path Template

Renders a safe path string from `ctx.variables` plus a payload-local overlay. Source: `src/lokdocsqr/nodes/utils/path_template.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `template` | string | — (required) | template | Path template |
| `variables` | object | `{}` | mapping | Overlay; payload values win over run variables |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `template` | string | true | none |
| `variables` | dict | false | none |

**Outputs**

| Name | Type |
|---|---|
| `path_template` | string |
| `path` | string |
| `template` | string |
| `variables` | dict |
| `errors` | any[] |
| `error_count` | int |

**Behavior.** Pure; no filesystem access and no scope check. Rendering uses `render_template` (rejects absolute results and `..`). A missing placeholder is left literal by `SafeDict`; because a downstream path with `{` is unusable, this node reports `unresolved placeholder in rendered path` as an error and returns null path fields. On any failure the path fields are null and `errors` is non-empty; the node does not raise. Preview equals execution.

```ldsq
node "pt" {
  type = "utils.path_template"
  params {
    template = "{year}/{name}"
    variables = {year = "2026", name = "a.txt"}
  }
}
```

#### `utils.json_validate` — JSON Parse / Validate

Validates JSON data against a stdlib draft-07 subset. Source: `src/lokdocsqr/nodes/utils/json_validate.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `data` | any | — (required) | JSON string, dict, list, scalar | Value to validate; strings are parsed as JSON |
| `schema` | object | — (required) | JSON Schema object | Supported keywords below |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `data` | any | true | none |
| `schema` | dict | true | none |

**Outputs**

| Name | Type |
|---|---|
| `valid` | bool |
| `data` | any |
| `errors` | any[] |
| `error_count` | int |

**Supported schema keywords:** `type` (string or list of `string`, `integer`, `number`, `boolean`, `array`, `object`, `null`), `required`, `properties`, `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`. Unknown keywords are rejected (reported as errors), not ignored. Malformed JSON input returns `valid=false` with a parse error. Preview adds `preview` and `dry_run`.

```ldsq
node "jv" {
  type = "utils.json_validate"
  params {
    data = {label = "invoice"}
    schema = {type = "object", required = ["label"], properties = {label = {type = "string"}}}
  }
}
```

#### `utils.notify` — Notify

Sends a notification via email or webhook. Source: `src/lokdocsqr/nodes/utils/notify.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `channel` | string | — (required) | `"email"`, `"webhook"` | Delivery channel; `"desktop"` is explicitly rejected |
| `title` | string | `""` | any string | Subject/title |
| `body` | string | `""` | any string | Body text |
| `target` | string | `""` | email address or http(s) URL | Destination |
| `config` | object | `{}` | channel-specific | Email: `smtp_host` (required), `smtp_port` (587), `from_addr`/`smtp_user`, `smtp_password`, `smtp_tls` (true), `timeout` (10). Webhook: `method` (POST), `headers`, `timeout` (10) |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `channel` | string | true | none |
| `title` | string | false | `""` |
| `body` | string | false | `""` |
| `target` | string | false | `""` |
| `config` | object | false | `{}` |

**Outputs**

| Name | Type |
|---|---|
| `sent` | boolean |
| `channel` | string |
| `error` | string |
| `error_count` | integer |
| `dry_run` | boolean |

**Behavior.** Delivery exceptions are fail-soft (`sent=false`, `error` set, `error_count=1`); invalid channel and missing channel configuration raise `NodeExecutionError`. Dry-run validates the channel and never delivers.

```ldsq
node "nt" {
  type = "utils.notify"
  params {
    channel = "webhook"
    title = "Done"
    body = "Workflow finished"
    target = "https://example.invalid/hook"
    config = {timeout = 5}
  }
}
```

### 9.5 `llm` family

#### `llm.local_llm` — Local LLM

Turns text into structured JSON through a local Ollama model. Source: `src/lokdocsqr/nodes/llm/local_llm.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `text` | string | — (required) | any string | Input text; aliases `input`, `content`, `document` are also accepted |
| `task` | string | `"summarize"` | `"classify"`, `"extract"`, `"summarize"`, `"plan"` | Prompt template / expected shape |
| `schema` | object or JSON string | null | JSON Schema object | Appended to the prompt; not enforced by this node |
| `model` | string | `"llama3.2"` | Ollama model name | Model override |
| `system_prompt` | string | built-in | any string | System message override |
| `prompt` | string | task instruction | any string | User instruction override |
| `options` | object | null | Ollama options | Forwarded as `options` |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `text` | string | true (at run time) | none |
| `task` | string | false | `"summarize"` |
| `schema` | dict | false | none |
| `model` | string | false | `"llama3.2"` |
| `system_prompt` | string | false | built-in |
| `prompt` | string | false | task instruction |
| `options` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `data` | any |
| `text` | string |
| `confidence` | number |
| `usage` | object |

**Behavior.** Calls `client.chat(model=..., messages=[...], format="json", options=...)`. Model output may be fenced JSON. Non-JSON output raises `NodeExecutionError`. `confidence` is taken from `data.confidence` or `data.score` when numeric. `usage` contains `model`, `prompt_tokens`, `completion_tokens`, optional `total_tokens`, optional `total_duration_ns`. The node never touches the filesystem and lazily imports `ollama` only in normal execution. Dry-run returns null data/text/confidence and `usage.dry_run=true` without constructing a client. Schema validation is deliberately delegated to `utils.json_validate`.

```ldsq
node "llm" {
  type = "llm.local_llm"
  params {
    text = "Invoice 42 from ACME"
    task = "classify"
    schema = {type = "object", required = ["label"], properties = {label = {type = "string"}}}
    model = "llama3.2"
  }
}
```

### 9.6 `triggers` family (trigger nodes)

These nodes are ordinary nodes that normalize runtime events. They are distinct from the *trigger declarations* in a document (`triggers[]`) that `Runtime` binds; see §10.

#### `triggers.watch_folder` — Watch Folder

Reports files observed by a folder watcher or a fresh scan. Source: `src/lokdocsqr/nodes/triggers/watch_folder.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path` | string | absent | path | Folder/file to scan if no runtime files are present |
| `patterns` | list[string] | `["*"]` | glob patterns | Scan filter; `pattern` (singular) is also accepted |
| `recursive` | bool | `true` | true/false | Recurse in scan fallback |
| `events` | list[string] | `["created", "modified", "deleted"]` | event kinds | Declared for GUI/documentation; the node itself does not read it — the runtime `FolderWatcher` filters changes by event kind |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path` | string | false | none |
| `patterns` | array | false | `["*"]` |
| `recursive` | bool | false | `true` |
| `events` | array | false | all three |

**Outputs**

| Name | Type |
|---|---|
| `files` | FileRef[] |
| `paths` | string[] |
| `changes` | object[] |
| `event` | object |
| `payload` | object |
| `scanned` | integer |

**Behavior.** Reads the injected event from `ctx.variables["trigger"]`; prefers event `files`/`paths`, then non-deleted changes, then a read-only scan of `path`. The result always includes `scanned` (number of files examined by the scan fallback, or the number of event files when no scan ran), so an empty folder is distinguishable from a broken graph. No audit entry. Event files are normalized without a scope check; downstream access/fs nodes check them.

```ldsq
node "w" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = ["*.pdf"]
    recursive = true
  }
}
```

#### `triggers.scheduled_scan` — Scheduled Scan

Performs a read-only scan for a scheduled run and exposes schedule metadata. Source: `src/lokdocsqr/nodes/triggers/scheduled_scan.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `path` | string | absent | path | Folder/file to scan |
| `patterns` | list[string] | `["*"]` | glob patterns | Scan filter |
| `recursive` | bool | `true` | true/false | Recurse |
| `cron` | string | absent | cron expression | Echo/metadata only; the runtime binding owns scheduling |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `path` | string | false | none |
| `patterns` | array | false | `["*"]` |
| `recursive` | bool | false | `true` |
| `cron` | string | false | none |

**Outputs**

| Name | Type |
|---|---|
| `files` | FileRef[] |
| `paths` | string[] |
| `event` | object |
| `payload` | object |
| `due_at` | number |

**Behavior.** `due_at` comes from event payload `scheduled_for` or node param `due_at`; otherwise null. No audit entry.

```ldsq
node "ss" {
  type = "triggers.scheduled_scan"
  params {
    path = "inbox"
    patterns = ["*.pdf"]
    recursive = true
    cron = "0 2 * * *"
  }
}
```

#### `triggers.manual_run` — Manual Run

Normalizes files and payload from a manual invocation. Source: `src/lokdocsqr/nodes/triggers/manual_run.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `files` | array | absent | path strings or FileRef-like maps | Selected files; `paths` alias accepted |
| `payload` | object | `{}` | any mapping | Event payload |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `files` | array | false | none |
| `payload` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `files` | FileRef[] |
| `paths` | string[] |
| `event` | object |
| `payload` | object |

**Behavior.** Falls back to event files when node `files` is empty. Builds an event `{type: "manual_run", automation_id, files, payload}`. No audit entry.

```ldsq
node "mr" {
  type = "triggers.manual_run"
  params {
    files = ["inbox/a.pdf"]
    payload = {source = "manual"}
  }
}
```

#### `triggers.webhook` — Webhook / External Trigger

Exposes an external JSON payload and headers. Source: `src/lokdocsqr/nodes/triggers/webhook.py`.

**Params**

| Name | Type | Default | Allowed values | Meaning |
|---|---|---|---|---|
| `payload` | object | `{}` | mapping | JSON body override |
| `headers` | object | `{}` | mapping | Headers override |

**Inputs**

| Name | Type | Required | Default |
|---|---|---|---|
| `payload` | object | false | none |
| `headers` | object | false | none |

**Outputs**

| Name | Type |
|---|---|
| `payload` | object |
| `headers` | object |
| `event` | object |

**Behavior.** Prefers node-supplied mappings, then the injected event payload, then the whole event. No audit entry.

```ldsq
node "wh" {
  type = "triggers.webhook"
  params {
    payload = {id = 1}
    headers = {content-type = "application/json"}
  }
}
```

---

## 10. Trigger reference (runtime)

Trigger declarations live in `triggers[]` on the document. `Runtime.register()` binds them immediately (`core/runtime.py:_bind_document_triggers`); `Runtime.run_once()` executes an automation without requiring a background trigger.

### 10.1 Type normalization and aliases

`_trigger_kind` lowercases the type and strips **one** leading `triggers.` or `trigger.` prefix (`core/runtime.py:_trigger_kind`). Matching then uses the normalized kind.

| Canonical kind | Accepted `type` values (after prefix/lowercase) | Bound mechanism |
|---|---|---|
| `watch_folder` | `watch_folder`, `watch`, `watcher` | `FolderWatcher` polling thread, bound by `Runtime.add_watch` |
| `scheduled_scan` | `scheduled_scan`, `scheduled`, `schedule`, `cron` | `CronSchedule`/`IntervalSchedule`, polled by the runtime scheduler, bound by `Runtime.add_cron` |
| `manual_run` | `manual_run`, `manual` | caller via `Runtime.trigger` / `Runtime.run_once`; no background binding |
| `webhook` | `webhook` | caller via `Runtime.trigger_webhook` / `Runtime.trigger`; no background binding |

Examples of accepted type strings: `triggers.watch_folder`, `trigger.watch`, `WATCH_FOLDER` (lowercased before matching), `scheduled_scan`, `cron`, `manual`, `webhook`. `triggers.triggers.watch` strips only one prefix and is unknown.

Unknown trigger kinds are logged and skipped; registration does not fail. A watch trigger without `path`, or a schedule trigger without a parseable schedule, is also logged and skipped.

### 10.2 `watch_folder`

```json
{
  "id": "watch-inbox",
  "type": "triggers.watch_folder",
  "title": "Watch inbox",
  "params": {
    "path": "inbox",
    "patterns": ["*.pdf"],
    "recursive": true,
    "interval_seconds": 1.0,
    "debounce_seconds": 0.25,
    "events": ["created", "modified"],
    "dry_run": false
  }
}
```

| Param | Type | Default | Meaning |
|---|---|---|---|
| `path` | string | — (required; missing trigger is skipped) | Watched path; relative paths resolve against `config.base_dir` |
| `patterns` | string or list[string] | `["*"]` | Glob filters; `pattern` alias accepted |
| `recursive` | bool | `true` | Recurse into subdirectories |
| `interval_seconds` | number | `0.5` (`DEFAULT_WATCH_INTERVAL`) | Poll interval; non-numeric falls back to default, numeric is clamped `>= 0.01` by the watcher |
| `debounce_seconds` | number | `0.0` (`DEFAULT_DEBOUNCE`) | Extra wait before emitting a merged change batch |
| `events` | string or list[string] | `["created","modified","deleted"]` | Change kinds to emit; values are lowercased |
| `dry_run` | bool | unset (`null`) | Forces the binding's run mode; non-bool values are ignored |

The watcher snapshots `(mtime_ns, size)`; kinds are `created`, `modified`, `deleted`. A trigger without `path` is skipped at registration with a warning.

```ldsq
trigger "watch-inbox" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = ["*.pdf"]
    recursive = true
    interval_seconds = 1.0
    debounce_seconds = 0.25
    events = ["created", "modified"]
  }
}
```

### 10.3 `scheduled_scan`

```json
{
  "id": "nightly",
  "type": "triggers.scheduled_scan",
  "params": {"cron": "0 2 * * *", "dry_run": true}
}
```

| Param | Type | Default | Meaning |
|---|---|---|---|
| `cron` | string | — | Cron expression; first non-empty of `cron`, `expression`, `schedule` wins |
| `expression` | string | — | Alias for `cron` |
| `schedule` | string | — | Alias for `cron` |
| `interval_seconds` | number | — | Fixed interval in seconds when no cron string is supplied |
| `every_seconds` | number | — | Alias for `interval_seconds` |
| `dry_run` | bool | unset (`null`) | Forces the binding's run mode; non-bool ignored |

If no string `cron`/`expression`/`schedule` and no numeric interval is provided, the trigger is skipped with a warning. A numeric string schedule (e.g. `"60"`) is also accepted as an interval by `Runtime.add_cron`.

**Cron grammar** (`core/runtime.py:CronSchedule`):

* Five fields: `minute hour day-of-month month day-of-week`.
* Six fields: leading `seconds` field — `second minute hour day-of-month month day-of-week`.
* Field syntax: `*`, number, name, range `a-b`, step `*/n` or `a-b/n`, comma lists.
* Month names: `jan`…`dec` (case-insensitive). Weekday names: `sun`…`sat`; `7` is accepted as Sunday.
* Aliases: `@yearly`, `@annually`, `@monthly`, `@weekly`, `@daily`, `@midnight`, `@hourly`.
* `@every <duration>` where duration accepts `ms`, `s`, `m`, `h`, `d`; must be positive.
* Times are interpreted in UTC by default. Matching day-of-month and day-of-week are OR-ed when both fields are restricted.

```ldsq
trigger "nightly" {
  type = "scheduled_scan"
  params {
    cron = "0 2 * * *"
    dry_run = true
  }
}

trigger "poll" {
  type = "cron"
  params {
    interval_seconds = 60
  }
}
```

### 10.4 `manual_run`

Manual triggers are not bound at registration. They are driven by `Runtime.trigger(automation_id, trigger_type="manual_run", payload=...)` or `Runtime.run_once(...)`. The trigger declaration's `params` are not consumed by the binding; the `triggers.manual_run` **node** consumes its own NodeSpec params (`files`, `payload`).

| Param | Type | Default | Meaning |
|---|---|---|---|
| `dry_run` | bool | unset | Read generically by `_bind_document_triggers`, then ignored for manual/webhook; pass `dry_run=` to `Runtime.trigger` instead |

```ldsq
trigger "manual" {
  type = "manual_run"
}

node "mr" {
  type = "triggers.manual_run"
  params {
    files = ["inbox/a.pdf"]
    payload = {source = "manual"}
  }
}
```

### 10.5 `webhook`

Also not bound at registration. Use `Runtime.trigger_webhook(automation_id, payload=...)` or `Runtime.trigger(..., trigger_type="webhook", payload=...)`. As with manual runs, `params.dry_run` has no effect on the binding.

| Param | Type | Default | Meaning |
|---|---|---|---|
| `dry_run` | bool | unset | Ignored for webhook bindings |

```ldsq
trigger "hook" {
  type = "webhook"
}

node "wh" {
  type = "triggers.webhook"
  params {
    payload = {id = 1}
    headers = {content-type = "application/json"}
  }
}
```

### 10.6 `params.dry_run` resolution

`Runtime._execute` resolves the run mode in this order (`core/runtime.py:_execute`):

1. explicit `dry_run` argument to `run_once`/`trigger`;
2. an explicitly set `document.settings.default_dry_run` (DSL/JSON key present, or a programmatic `false`);
3. `Runtime(default_dry_run=...)`;
4. `config.default_dry_run` (default `true`).

An explicit document setting is absolute: a document that says `default_dry_run = true` stays a dry-run even when `config.default_dry_run = false`. The explicit-argument path is still the way to force either mode. A watch/cron binding's `params.dry_run` is passed as the explicit argument for that binding. Manual/webhook declarations do not carry a binding; use the call argument.

### 10.7 `serve`

`lokdocsqr serve` hosts watch/cron triggers until interrupted:

```bash
lokdocsqr serve <file-or-dir> [<file-or-dir> ...] [--base-dir DIR] [--include PATH] [--dry-run|--no-dry-run]
```

* Each positional argument is a `.ldsq` or `.json` automation file, or a directory containing them.
* `--base-dir` sets the workspace used to resolve relative paths and the default scope.
* `--include PATH` (repeatable; file or directory) registers additional automations, which is useful when one serving document references another by `graph_id`.
* `--dry-run` / `--no-dry-run` become the Runtime default for bindings that do not set `params.dry_run`.
* Registration prints `registered <automation_id>` for every document, then `serving; press Ctrl+C to stop`. Ctrl+C stops the runtime cleanly.

The command performs no immediate run; trigger bindings execute through the same pipeline as `run`.

---

## 11. Condition expression language

Source: `src/lokdocsqr/nodes/flow/_common.py` (`_tokenize`, `_Parser`, `_compare`, `_lookup`, `_evaluate`, `_base_mapping`).

### 11.1 Where it is used

| Location | Evaluated? | Notes |
|---|---|---|
| `flow.if_branch` params `condition` | yes | scalar or per-item; errors become `"error"` |
| `flow.filter` params `condition` | yes | per item; errors increment `errors` |

Edges carry no condition. `EdgeSpec` has no `condition` field, the DSL edge parser rejects `condition`, and a JSON edge object with an unknown `condition` key has it dropped by `graph/parser.py:_parse_edge`. Use `flow.if_branch` to compute a decision and `flow.filter` to select items; neither rewires graph execution, so control flow remains explicit in the edge topology.

### 11.2 Grammar

```text
expr        := or_expr
or_expr     := and_expr { "or" and_expr }
and_expr    := not_expr { "and" not_expr }
not_expr    := "not" not_expr | comparison
comparison  := primary [ compare_op primary | "not" "in" primary ]
compare_op  := "==" | "!=" | "<" | "<=" | ">" | ">=" | "in"
primary     := "(" expr ")"
             | "(" expr "," expr { "," expr } ")"     # tuple
             | "[" [ expr { "," expr } ] "]"          # list
             | NUMBER
             | STRING
             | "True" | "False" | "None"
             | IDENT
```

There is exactly one optional comparison per `comparison`; chained comparisons (`a < b < c`) are a “trailing tokens” error.

### 11.3 Tokens

| Kind | Recognized |
|---|---|
| Whitespace | any `str.isspace()` |
| Strings | single or double quoted; escapes `\n`, `\r`, `\t`, `\\`, `\'`, `\"`; other escapes are taken literally |
| Numbers | optional `-`, digits, optional `.` and digits; no exponent form in conditions |
| Identifiers | ASCII letter or `_`, then ASCII letters/digits/`_`; dots are **not** part of identifiers |
| Constants | `True`, `False`, `None` (capitalized only) |
| Operators | `==`, `!=`, `<`, `<=`, `>`, `>=`, `and`, `or`, `not`, `in` |
| Punctuation | `(`, `)`, `[`, `]`, `,` |

Pre-tokenization guard: any expression containing `..`, `/`, or `\` is rejected as “path-like condition”, including inside string literals (`_tokenize`). This is why `path == "/tmp"` is an error.

### 11.4 Precedence and associativity

From lowest to highest:

1. `or` (left-associative; both operands are evaluated, then `bool`-coerced — there is no short-circuit evaluation)
2. `and` (left-associative; both operands are evaluated, then `bool`-coerced — there is no short-circuit evaluation)
3. `not` (unary, right-associative)
4. comparison (`==`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not in`)
5. primary (parentheses, lists, tuples, literals, identifiers)

Parentheses override precedence. `not` binds tighter than `and`/`or`: `not a and b` means `(not a) and b`.

### 11.5 Context

`_base_mapping(ctx, payload)` builds the name mapping:

1. all entries from `ctx.variables` (includes declared variables, runtime variables, and `flow.*` / `trigger` entries);
2. `payload["variables"]` if it is a mapping (overrides run variables);
3. every other payload key except `items`, `files`, `file_refs`, `input`, `data`, `condition`, and `variables` (overrides prior values).

For each item:

* if the item is a dict, its keys are merged over the base mapping;
* otherwise the item is exposed as `item`.

Upstream outputs are visible only when an edge delivers them into a payload key; there is no `$node.port` reference syntax. File references are ordinary dicts (`path`, `size`, `is_dir`) or strings once they arrive in the payload.

`flow.if_branch` in scalar mode uses the payload dict (minus `condition`) as the per-item mapping. `flow.filter` keeps `condition` out of the base mapping.

There is no nested field access in the language. A dotted expression such as `item.path` is not an identifier and is a parse error. Because item dicts are merged into the top-level mapping, use `path` rather than `item.path` for dict items. For non-dict items, `item` is the whole value.

`_lookup` rejects unknown names and unsafe values: modules, classes, and callables are rejected with a condition error. There is no `eval`, `exec`, `compile`, `ast`, `getattr`, or filesystem access in the evaluator.

### 11.6 Truthiness

The parser returns `bool(value)` at the top level. `and`/`or`/`not` also coerce with `bool`. Standard Python truthiness applies:

| Value | Truthy? |
|---|---|
| `None` | no |
| `False`, `0`, `0.0` | no |
| `""`, `[]`, `{}`, `()` | no |
| non-empty string/list/tuple/dict | yes |
| non-zero number | yes |

`==`/`!=` use Python equality. `1 == True` is true; `1 == "1"` is false. Ordering comparisons (`<`, `<=`, `>`, `>=`) use Python comparison; incompatible types raise a condition error.

`in` and `not in` require the right operand to be a `str`, `list`, `tuple`, `set`, `frozenset`, or `dict` (`_compare`). For a dict, membership tests keys. Any other right operand is a condition error.

### 11.7 Error behavior

A parse/evaluation failure raises the internal `_CondError`. Node behavior:

| Node | On condition error |
|---|---|
| `flow.if_branch` | returns `result="error"`, `branch="error"` (or per-item values) |
| `flow.filter` | appends `"error"` to `results`, increments `errors`, drops the item from `items` |

Neither node raises for a bad condition. Unknown identifiers, path-like expressions, malformed syntax, unsafe values, and invalid `in` containers all become per-item errors.

### 11.8 Worked examples

1. Numeric comparison:

```text
size > 0
```

2. Conjunction with a string literal:

```text
kind == 'invoice' and score >= 0.8
```

3. Membership in a list:

```text
tag in ['pdf', 'docx']
```

4. Negation:

```text
not deleted
```

5. Parentheses and mixed operators:

```text
(a == 1 or b == 2) and not c
```

6. `not in`:

```text
name not in ['draft', 'trash']
```

7. Constants and None:

```text
enabled == True and deleted_at == None
```

8. Path-like expression is rejected:

```text
path == "/tmp"
```

This raises a condition error because `/` is rejected before tokenization.

9. Lowercase booleans are identifiers, not constants:

```text
enabled == true
```

`true` is not a condition constant (`True` is). It is looked up as a variable; if absent, this is an “unknown name” condition error.

---

## 12. Template and interpolation syntax

Source: `src/lokdocsqr/core/paths.py:render_template`; `src/lokdocsqr/nodes/fs_ops/_base.py`, `_common.py`; `src/lokdocsqr/nodes/utils/path_template.py`.

### 12.1 Placeholder syntax

Templates use Python `str.format_map` syntax with a `SafeDict`:

```text
{key}
```

Examples:

```text
work/{name}{ext}
{year}/{category}/{name}
{date}/{author}-{name}{ext}
{item[path]}
```

* Missing keys are left as a literal `{key}` by `SafeDict.__missing__`.
* `{{` and `}}` escape literal braces.
* Format specs are technically supported by `str.format_map` (e.g. `{score:03d}`), but the stable contract uses simple `{key}` placeholders. `render_template` wraps `ValueError`/`KeyError` as `ScopeViolation`.
* `{item[path]}` selects a dict key; `{item.path}` would attempt attribute access and is not the intended form for dict items.
* A small whitelist of string functions is available: `basename(path)`, `dirname(path)`, `stem(path)`, `suffix(path)`, and `join(a, b, ...)`. Arguments are quoted string literals or bare context keys; nesting and arbitrary expressions are not supported. `join` uses `PurePosixPath` semantics (forward slashes) so results are portable. Unresolved function names or arguments are left literal, like missing keys.
* No nested or computed expressions beyond those five functions are supported.
* **Indexed placeholders and `validate_graph`:** the validator still runs its static checks (literal absolute path, literal `..`) on the raw template, then probes it with an empty context. When that probe cannot resolve an indexed/subscripted placeholder (`TypeError`, `KeyError`, `IndexError`, `AttributeError`), validation defers the template to runtime instead of failing registration. Rendering with the populated `item` then applies the normal absolute/`..`/scope rules.

### 12.2 Where templates are used

| Field | Node(s) | Context available |
|---|---|---|
| `path_template` | `fs.make_dir`, `fs.write_file` | `payload["variables"]` only |
| `template` | `fs.copy`, `fs.move`, `fs.rename` | `payload["variables"]` plus standard `name`, `ext`, `date`, `author`, `tags` derived from the source; caller values win |
| `target` (create) / `target_dir` (extract) | `fs.archive` | `payload["variables"]` |
| `target` | `fs.convert` | `payload["variables"]` |
| `template` | `utils.path_template` | all `ctx.variables` merged with `payload["variables"]`; payload wins |

Standard variables for source→target nodes (`FsNode._variables`):

| Variable | Value |
|---|---|
| `name` | source stem (`Path.stem`) |
| `ext` | source suffix including the dot (`Path.suffix`), e.g. `.pdf` |
| `date` | source mtime in UTC as `YYYY-MM-DD`, or `""` if stat fails |
| `author` | `""` unless supplied in `variables` |
| `tags` | `""` unless supplied in `variables` |

`fs.make_dir` and `fs.write_file` do **not** inject these standard variables; only explicit `variables` are available. This is a common source of unresolved `{name}` placeholders.

### 12.3 Relative resolution

| Node/field | Relative result resolves against |
|---|---|
| `fs.make_dir` `path_template` | `config.base_dir` (then scope-checked) |
| `fs.write_file` `path_template` | `config.base_dir` |
| `fs.archive` `target` / `target_dir` | `config.base_dir` |
| `fs.convert` `target` | `config.base_dir` |
| `fs.copy` / `fs.move` `target_dir` | `config.base_dir` (then scope-checked) |
| `fs.copy` / `fs.move` / `fs.rename` `template` | `target_dir` when set, otherwise `source.parent` |
| `source`/`path`/`paths` inputs | `config.base_dir` (then scope-checked) |

`config.base_dir` comes from `AppConfig` (`core/config.py`). The CLI sets it from `--base-dir`, a config file, or the first `--scope` path; otherwise it uses the process CWD (`cli/main.py:_config_from_args`). Node implementations never resolve relative paths against the process CWD.

### 12.4 Rendering safety rules

`render_template(template, context, *, allow_parent_traversal=False)` (`core/paths.py`):

1. substitute whitelisted path functions, then `SafeDict`;
2. reject a rendered absolute path (`Path(rendered).is_absolute()`);
3. when `allow_parent_traversal` is `false`, `os.path.normpath` the result and reject if any remaining part is `..`;
4. return the rendered string.

`render_and_check` (`nodes/fs_ops/_common.py`) additionally:

1. rejects `..` in the raw rendered path parts (before normalization) unless the workflow set `scope.allow_parent_traversal = true`;
2. resolves relative paths against `config.base_dir`;
3. calls `ctx.scope.check(path, write=True)` and raises `ScopeViolation` on failure — this is the final containment check when parent traversal is allowed.

`FsTemplateNode._render_target` for copy/move/rename:

* rejects an empty/whitespace-only render;
* rejects `..` in the rendered parts unless `scope.allow_parent_traversal = true`;
* if the rendered path has no suffix and the source has one, appends the source suffix;
* returns an absolute path unchanged (in practice `render_template` has already rejected absolute results);
* otherwise resolves against `source.parent` (or `target_dir`, §9.1);
* calls `scope.check(dst, write=True)`.

The setting is carried on the `ScopeManager` so node code reads one source of truth: `ctx.scope.allow_parent_traversal`. The validator also skips its static `..` rejection when the document enables the flag, but still rejects literal absolute paths.

`utils.path_template` additionally rejects a rendered string that still contains `{` (an unresolved placeholder) and returns an error result instead of raising.

**Unresolved placeholders are not universally fatal.** `utils.path_template` rejects them; `fs.make_dir`/`fs.write_file`/`fs.copy`/`fs.move`/`fs.rename` will pass a literal `{name}` through to path resolution, which may create a path containing braces or fail scope/path checks. Always ensure the variables are present.

### 12.5 Scope and symlink policy

`ScopeManager.check(path, write=...)` (`core/scope.py`):

* rejects any path whose raw parts contain `..` unless `allow_parent_traversal` is enabled;
* resolves the path (`Path.resolve(strict=False)`);
* if `allow_symlinks` is false, rejects a symlink **leaf** (`path.is_symlink()`); ancestor symlinks are resolved and vetted by the root containment check;
* selects readable roots (`roots`) or writable roots (`writable`) depending on `write`;
* requires at least one configured root;
* requires the resolved path to be relative to one of the selected roots.

Runtime scope construction (`core/runtime.py:_scope_for`):

1. `settings.scope.roots` if non-empty, each resolved against `config.base_dir`;
2. else `config.scope_roots`;
3. else `[config.base_dir]`.

`writable` = `settings.scope.writable` if non-empty, else the resolved roots. `allow_symlinks` and `policy_ref` are passed through. The CLI `--scope` option replaces both `roots` and `writable` with the supplied paths (`cli/main.py:_apply_scope_override`).

Consequences:

* A node writing under `settings.scope.roots` but not `writable` fails scope checks.
* `scope.allow_parent_traversal=true` permits `..` in a rendered path; the resolved path must still be relative to a writable root, so escaping the workspace remains a `ScopeViolation`.
* `scope.allow_symlinks=false` does not forbid symlinked ancestors, only a symlink leaf.
* Triggers bound with a path do not call `scope.check` at bind time; trigger nodes that scan do check discovered paths, but event-supplied files are normalized without a scope check and are checked later by downstream access/fs nodes.

### 12.6 Worked examples

```ldsq
node "by-date" {
  type = "fs.move"
  params {
    source = "inbox/report.pdf"
    template = "{date}/{name}{ext}"
    conflict_policy = "rename"
  }
}
```

With `source.mtime = 2026-11-03`, `source.parent = inbox`, this renders `inbox/2026-11-03/report.pdf`.

```ldsq
node "safe-name" {
  type = "utils.path_template"
  params {
    template = "work/{year}/{slug}.txt"
    variables = {year = "2026", slug = "invoice-42"}
  }
}
```

Returns `path_template = "work/2026/invoice-42.txt"`.

```ldsq
node "sibling-name" {
  type = "utils.path_template"
  params {
    template = "{join('archive', basename(source))}"
    variables = {source = "inbox/report.pdf"}
  }
}
```

Returns `path_template = "archive/report.pdf"`. `basename`, `dirname`, `stem`, `suffix`, and `join` are the only functions; they accept quoted literals or context keys.

```ldsq
node "loop-path" {
  type = "utils.path_template"
  params {
    template = "{item[path]}"
  }
}
```

Inside `flow.loop_batch`, `item` is the current dict; this selects its `path` key when rendering with a populated context. The document passes `validate_graph` and `build_graph`, and the node renders the current item's path at runtime (for example `a/b.txt` for `{path = "a/b.txt"}`).

A template `../archive/{name}{ext}` is rejected by `render_template`/`render_and_check` by default; relative destination templates cannot escape `source.parent` via `..`. To move to a sibling directory without enabling traversal, choose a source whose parent makes the relative destination correct (for example source `invoice.txt` at `base_dir` with template `archive/{name}{ext}`), or use `target_dir` (§9.1).

**Sibling folders with `target_dir`.** With a workspace `~/Documents` containing sibling folders `watch` and `tmp`, both listed as readable/writable roots, `target_dir` expresses the destination directly. Enable `allow_parent_traversal` only if the rendered path itself contains `..`; the resolved path must still be inside a writable root:

```ldsq
automation "watch-to-tmp"

scope {
  roots = ["watch", "tmp"]
  writable = ["watch", "tmp"]
  allow_parent_traversal = true
}

node "mv" {
  type = "fs.move"
  params {
    source = "watch/report.pdf"
    target_dir = "../tmp"
    template = "{name}{ext}"
    conflict_policy = "rename"
  }
}
```

The document renders `../tmp` against `config.base_dir` (`~/Documents`), then `report.pdf` against that directory, producing `~/Documents/tmp/report.pdf`. Without `target_dir`, the same destination would require a source at `~/Documents` or a template relative to `watch`.

---

## 13. Payload resolution

Source: `src/lokdocsqr/core/payload.py:build_node_payload`; callers `core/executor/engine.py:_build_payload` and `core/planning/builder.py:_node_payload`.

### 13.1 Precedence

Lowest to highest:

1. `NodeSpec.params` — static configuration.
2. Inbound edge values — an edge value overrides a same-named param.
3. Port defaults — fill only keys that neither params nor edges set.
4. Required input port with no value/default — fail closed.

| Step | Source | Overrides previous? |
|---|---|---|
| 1 | `node.params` | base |
| 2 | `outputs[edge.source][edge.source_port]` assigned to `payload[edge.target_port]` | yes |
| 3 | `node.inputs[i].default` for a port name not already in payload | fills gaps |
| 4 | required `node.inputs[i]` with no payload value and no default | raises |

The executor raises `GraphValidationError` for step 4; the planner keeps `ValueError` (`build_node_payload(required_port_error=...)`).

### 13.2 Edge value semantics

For each inbound edge of the target node:

* An edge with `kind = "order"` contributes nothing to the payload; it only constrains topological order.
* If the edge source has no output entry (the source did not run in the ENABLED state, or its result is absent), the edge contributes nothing.
* If the source exists and its output dict lacks `source_port`, the value appended is `None`.
* If the source output port exists, its value is appended.
* If a single edge targets a port, the payload value is the bare value.
* If multiple edges target the same port, the payload value is a list in document-edge order. The list is **not flattened** even if individual edge values are lists.

```text
source A output files = [a, b]
source B output files = [c]

A --files--> target.items
B --files--> target.items

payload["items"] = [[a, b], [c]]      # two edges -> list of two values
```

```text
A --files--> target.items (only edge)

payload["items"] = [a, b]             # one edge -> bare value (already a list)
```

This is why `flow.merge` accepts both a single list and a list of values: `items` may arrive in either shape.

### 13.3 Planner vs executor

The executor always resolves edges (`resolve_edges=True`). The planner resolves edges only when the graph contains at least one dynamic-payload node type: `utils.path_template`, `utils.set_variables`, or `llm.local_llm` (`core/planning/builder.py:_DYNAMIC_PAYLOAD_NODE_TYPES`). For other graphs, the planner returns static `params` only (M8 known limitation). Plans and real runs can therefore differ for graphs that rely on edge payloads without one of those node types.

The planner also threads a `planning_vars` mapping through the dry-run pass: any dry-run result dict with a `variables` dict contributes non-reserved keys to subsequent nodes. `ctx.variables` itself is isolated and not mutated.

### 13.4 Fail-closed cases

| Case | Result |
|---|---|
| Required input port declared, no param/edge/default | `GraphValidationError` (executor) / `ValueError` (planner) |
| Edge with `source_port=None` or `target_port=None` | `ValueError` from `build_node_payload` |
| Edge with non-string source/ports | silently skipped |
| Edge source has no outputs (skipped/disabled/failed) | edge contributes nothing |
| Missing source output port key | `None` is injected |

Port declarations are document-controlled: `node.inputs` must list the port for required/default logic to apply. The registry's `Node.inputs` metadata is not copied into `NodeSpec`.

---

## 14. State, disabled cascade, retries, and error handling

### 14.1 Node states

`NodeState` (`core/models.py`): `enabled`, `disabled`, `skipped`, `failed`. `parse_graph` accepts strings case-insensitively.

| State | Meaning |
|---|---|
| `enabled` | eligible for execution |
| `disabled` | declared off; not built; cascades `skipped` to enabled descendants |
| `skipped` | not executed because an upstream branch is disabled/skipped (or a failed parent at run time) |
| `failed` | failed result state; declared `failed` nodes are not built (only enabled nodes get handlers) |

### 14.2 Disabled cascade

`resolve_states(document)` (`core/graph/resolver.py`):

1. initialize each node's declared state;
2. build inbound parent lists from `edges` (all edges);
3. repeatedly: for every **enabled** node, if any inbound parent is `disabled` or `skipped`, set it to `skipped`; repeat until stable.

A join node is skipped if **any** inbound parent is disabled/skipped. Isolated nodes keep their declared state. Declared `failed` nodes do not cascade.

### 14.3 Building and execution

`build_graph` (`core/graph/builder.py`):

* computes a full topological order over all nodes; a cycle raises `GraphValidationError` even if the cycle involves disabled nodes;
* keeps only `enabled` nodes in `ordered_node_ids`;
* looks up handlers only for enabled nodes; an unknown type on a disabled node therefore does not fail the build;
* copies resolved states for all nodes.

At run time (`Executor.execute`):

* for each ordered node, inspect direct inbound parents; if a parent has a result in `skipped`, `disabled`, or `failed`, skip the node (record `NodeResult(state=SKIPPED)`, no logs); if a parent has no result, its resolved state is used for the same check;
* `flow.retry` (`_inject_on_error=True`) is special: it ignores failed parents (skips only on skipped/disabled) and receives `on_error=True`;
* a skipped node produces no `ActionLog` entries.

### 14.4 `settings.error_handling`

`WorkflowErrorHandling`: `strategy`, `max_retries`, `retry_delay_seconds`, `backoff`.

| Strategy | Behavior |
|---|---|
| `fail` (default) | first failed node sets `success=false`, aborts the run; downstream nodes do not execute |
| `retry` | retry the **same** node up to `max_retries` times after the first attempt; then mark failed |
| `continue` | record the failure, continue other independent branches; direct/transitive descendants of the failed node are skipped because a failed parent triggers the skip rule |
| unknown | treated as `fail` |

Graph-level retry delay (`_retry_delay`):

| `backoff` | Delay before retry number `n` (1-based failed attempt) |
|---|---|
| `none` or unknown | `retry_delay_seconds` |
| `linear` | `retry_delay_seconds * n` |
| `exponential` | `retry_delay_seconds * 2**(n-1)` |

`max_retries` is the number of retries after the first attempt. `max_retries=0` means one attempt.

### 14.5 Per-node `params.retry_policy`

Any node payload may include `retry_policy` (`core/executor/engine.py:_read_node_retry_policy`). It is read only when **not** in dry-run and only for real execution.

| Field | Type | Default | Rules |
|---|---|---|---|
| `attempts` | integer | `1` | `>= 1`; total attempts, not retries |
| `backoff` | number | `0.0` | `>= 0`; base delay in seconds |
| `retry_on` | string or list[string] | `()` | Exception **class names** to retry; string is wrapped in a tuple |

Semantics:

* The policy is rejected (`NodeExecutionError`) if malformed, if `attempts < 1`, if `backoff < 0`, or if `retry_on` contains a non-string.
* A per-node policy on a node whose class has `retry_safe=False` emits a warning and still runs by default (`RunResult.warnings`: `node '<id>': retry_policy on non-retry-safe type '<type>'; retry may duplicate side effects`). Set `error_handling.strict_retry_safe = true` (or `settings.strict_retry_safe` in JSON) to restore the old fail-before-`run` behavior (`retry_policy on non-retry-safe node ...`). All fs_ops nodes default `retry_safe=False`.
* If a per-node policy is present, it takes precedence over `settings.error_handling`; the graph-level strategy is not used for that node, even if the policy does not match the exception.
* Retry occurs only when `type(exc).__name__` matches `retry_on` and `attempts < attempts_limit`. The match walks the exception's `__cause__`/`__context__` chain as well as the wrapper, so `retry_on = ["TimeoutError"]` still matches a `TimeoutError` wrapped in `NodeExecutionError`. `retry_on = ["NodeExecutionError"]` matches every ordinary node failure.
* Per-node retry delay is `backoff * 2**(attempts-1)` and sleeps via `ctx.clock.sleep`, not the executor's `sleep` argument.
* Dry-run does not read or apply `retry_policy`; the node's `dry_run` runs once.
* `flow.retry` is **not** the same mechanism. It is a marker/router node; it does not retry by itself.

### 14.6 Audit finalization

For every `ActionLog` returned by an executed node (`core/executor/engine.py`):

1. fill `run_id`/`automation_id` if empty;
2. if `status == "planned"`:
   * dry-run → `"dry_run"`,
   * enabled result → `"applied"`,
   * failed result → `"failed"`;
3. append to `ctx.audit`, the run's `action_logs`, and emit `EVENT_AUDIT_LOGGED`.

Nodes may pre-set `status` (`"applied"`, `"skipped"`) and the executor leaves it unchanged. Skipped nodes do not run, so they produce no logs. A failed node that returned logs has them finalized to `"failed"` under `continue` (or `fail`).

`RunResult.success` is `success and not aborted`; it is false if any node failed.

`RunResult.warnings` is a list of non-fatal diagnostics. The executor aggregates every node result's warnings. The main producer is `flow.loop_batch` / `flow.subflow` under `on_child_error="warn"` (the default), which emits one entry per failed child in the form `loop_batch node '<id>' item <index> failed: <error_type>: <error>`. Warnings are printed to stderr by the CLI `run` command; they do not on their own block execution, but `warn` mode also marks the producing node failed so `success` becomes `false` when a child failed.

---

## 15. Subflows

### 15.1 `flow.subflow` addressing

`flow.subflow` and `flow.loop_batch` share `_resolve_child_graph` (`src/lokdocsqr/nodes/flow/_common.py`).

Payload requirements:

* exactly one of `graph_id` (non-empty string) or `graph` (non-empty dict);
* both or neither raises `NodeExecutionError("subflow: exactly one of 'graph_id' or 'graph' is required")`;
* `max_depth` resolves from payload if present, else `ctx.config.flow.max_subflow_depth`, else `4`;
* `max_depth` must be a positive non-bool integer;
* depth guard: `ctx.flow_depth >= max_depth` raises (`max_depth` is the maximum child depth, inclusive);
* `graph_id` must not contain `/` or `\` and must not end with `.json`; it is a catalog id, not a path;
* `graph_id` must not already be in `ctx.subflow_stack` (cycle detection);
* `ctx.automations` must be an `AutomationCatalog`; the id must resolve to a prebuilt `ResolvedGraph`.

`graph_id` skips parse/validate/resolve/build because the catalog stores `ResolvedGraph`. An inline `graph` is a raw Workflow v1.0 dict and runs `parse_graph → validate_graph → resolve_states → build_graph`.

### 15.2 Catalog and path rules

`Runtime._catalog()` builds a fresh `AutomationCatalog` from all currently registered automations, calling `_build` for each; failures are logged and the automation is omitted. The catalog is a read-only-by-convention `{graph_id: ResolvedGraph}` map (`core/catalog.py`).

There is no filesystem subflow addressing inside a document. `graph_id="other.json"`, `"a/b"`, or `"a\\b"` is rejected with a message telling you to use an `AutomationCatalog` graph id.

The CLI is the exception: `--include <path>` (repeatable; file or directory of `.ldsq`/`.json`) registers extra documents in the runtime catalog before execution. With `--catalog-fallback`, a missing `graph_id` is looked up on disk as `<base_dir>/<graph_id>.ldsq` then `<base_dir>/<graph_id>.json`, with a warning. This is CLI-only sugar; the library still requires an explicitly registered catalog.

### 15.3 Scope inheritance

The child context is created with `dataclasses.replace(ctx, variables=..., flow_depth=..., subflow_stack=...)` (`subflow.py:_execute`):

* `scope`, `config`, `audit`, `events`, `clock`, `policy`, and `automations` are inherited by identity;
* variables are `{**ctx.variables, "flow.run_id": ..., "flow.automation_id": graph_id, "flow.depth": ctx.flow_depth+1, "flow.parent_run_id": ctx.run_id}`;
* the child document's `settings.scope` is **not** applied; the parent's `ScopeManager` is reused.

For inline graphs the same inheritance applies; `graph_id` is `None` and no stack entry is pushed. Documents registered through `run --include` (or the opt-in `--catalog-fallback`) participate in the same catalog and therefore the same scope inheritance as any other registered automation.

### 15.4 Cycle detection and depth

* `graph_id` cycles are detected through `ctx.subflow_stack`; a repeated id raises `NodeExecutionError("subflow: cycle detected: ...")`.
* Inline graphs do not push a stack entry, so recursive inline graphs are bounded only by `max_depth`.
* The child receives `flow_depth + 1`. `max_depth=4` allows child depths 1–4 and rejects a call made at `flow_depth >= 4`.

### 15.5 `loop_batch` differences

`flow.loop_batch` uses the same child resolution but differs:

* per-unit child contexts add `item`, `index`, `batch` and keep parent variables;
* `batch` mode sets `item=None` and `batch` to the chunk;
* empty `items` still resolves the child graph (fail-closed) and returns empty results;
* per-unit `LokError` or failed child `RunResult` is captured in `errors`; non-`LokError` propagates;
* `on_child_error` defaults to `warn`: failures also populate `RunResult.warnings` and fail the node; `continue` keeps the old success semantics, `fail` re-raises at the first `LokError`;
* `dry_run` fails closed unless `recursive_preview=true` and `ctx.planning_callback` exists;
* the shared error text says `subflow:` even when the caller is `loop_batch`.

### 15.6 Preview behavior

`flow.subflow._preview` and `flow.loop_batch._preview` raise `NodeExecutionError("... nested graph is not supported by the action plan builder")` unless `recursive_preview=true`. With `recursive_preview=true`, they require `ctx.planning_callback` and return a child action plan in `result`/`results`. This means a normal library `dry_run` of a graph containing a subflow records a planning error unless the planner supplies the recursive callback.

The CLI is deliberately more forgiving: `lokdocsqr run --dry-run` sets `recursive_preview=true` on nested nodes and wires `ctx.planning_callback` to `core.planning.builder.build_action_plan` when the context has none. Thus a CLI dry-run of a graph containing `flow.loop_batch` or `flow.subflow` succeeds and produces child plans, while the library API remains strict and fail-closed unless the caller opts in.
## 16. End-to-end examples

Each DSL example was compiled, parsed, emitted to a fixed point, and round-tripped through `parse_graph(document_to_dict(...))`; see the verification log at the end.

### 16.1 (a) `watch → make_dir → write_file`

A watch trigger node scans `inbox`; `mk` creates `work`; `wr` writes `work/report.txt`. The trigger declaration binds a runtime watcher, while the `w` node supplies the same scan data to the graph. Both edges inject values into ports the downstream nodes do not read; they exist to make the execution order explicit.

**DSL**

```ldsq
automation "watch-make-write"

name = "Watch, make dir, write"
default_dry_run = false

scope {
  roots = ["inbox", "work"]
  writable = ["work"]
}

trigger "watch-inbox" {
  type = "triggers.watch_folder"
  title = "Watch inbox"
  params {
    path = "inbox"
    patterns = ["*.txt"]
  }
}

node "w" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = ["*.txt"]
  }
  outputs = [{name = "files", type = "FileRef[]"}]
}

node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
  inputs = ["files"]
  outputs = [{name = "path", type = "string"}]
}

node "wr" {
  type = "fs.write_file"
  params {
    path_template = "work/report.txt"
    content = "from-dsl"
  }
  inputs = ["path"]
}

edge "w-mk" {
  source = "w"
  target = "mk"
  source_port = "files"
  target_port = "files"
}

edge "mk-wr" {
  source = "mk"
  target = "wr"
  source_port = "path"
  target_port = "path"
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "watch-make-write",
    "name": "Watch, make dir, write"
  },
  "nodes": [
    {
      "id": "w",
      "type": "triggers.watch_folder",
      "params": {
        "path": "inbox",
        "patterns": [
          "*.txt"
        ]
      },
      "outputs": [
        {
          "name": "files",
          "type": "FileRef[]"
        }
      ]
    },
    {
      "id": "mk",
      "type": "fs.make_dir",
      "params": {
        "path_template": "work"
      },
      "inputs": [
        "files"
      ],
      "outputs": [
        {
          "name": "path",
          "type": "string"
        }
      ]
    },
    {
      "id": "wr",
      "type": "fs.write_file",
      "params": {
        "path_template": "work/report.txt",
        "content": "from-dsl"
      },
      "inputs": [
        "path"
      ]
    }
  ],
  "edges": [
    {
      "id": "w-mk",
      "source": "w",
      "target": "mk",
      "source_port": "files",
      "target_port": "files"
    },
    {
      "id": "mk-wr",
      "source": "mk",
      "target": "wr",
      "source_port": "path",
      "target_port": "path"
    }
  ],
  "settings": {
    "default_dry_run": false,
    "scope": {
      "roots": [
        "inbox",
        "work"
      ],
      "writable": [
        "work"
      ]
    }
  },
  "triggers": [
    {
      "id": "watch-inbox",
      "type": "triggers.watch_folder",
      "title": "Watch inbox",
      "params": {
        "path": "inbox",
        "patterns": [
          "*.txt"
        ]
      }
    }
  ]
}
```

**Ordering-only variant.** When two nodes must run in a fixed order but do not exchange data, use `kind = "order"` instead of inventing a fake port:

```ldsq
automation "order-two-steps"

node "first" {
  type = "fs.make_dir"
  params {
    path_template = "work"
  }
}

node "second" {
  type = "fs.make_dir"
  params {
    path_template = "work/done"
  }
}

edge "first-second" {
  source = "first"
  target = "second"
  kind = "order"
}
```

The edge forces `first` before `second`; it requires no `target_port`, injects no payload, and passes `validate_graph` even though the two nodes declare no matching ports.

### 16.2 (b) Conditional evaluation with `flow.if_branch`

`decide` evaluates `score > 5` (true for the declared `score = 7`) and returns `result`/`branch`. The edge from `branch` to `report.in` is a data dependency, not a control-flow gate: `report` executes regardless. See §11.1.

**DSL**

```ldsq
automation "branch-report"

name = "Branch evaluation"
default_dry_run = false

variable "score" {
  type = "integer"
  value = 7
}

node "decide" {
  type = "flow.if_branch"
  params {
    condition = "score > 5"
    items = [1, 2, 3]
  }
  outputs = [{name = "result", type = "any"}, {name = "branch", type = "any"}, {name = "items", type = "any[]"}]
}

node "report" {
  type = "fs.write_file"
  params {
    path_template = "work/branch.txt"
    content = "checked"
  }
  inputs = ["in"]
}

edge "decide-report" {
  source = "decide"
  target = "report"
  source_port = "branch"
  target_port = "in"
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "branch-report",
    "name": "Branch evaluation"
  },
  "nodes": [
    {
      "id": "decide",
      "type": "flow.if_branch",
      "params": {
        "condition": "score > 5",
        "items": [
          1,
          2,
          3
        ]
      },
      "outputs": [
        {
          "name": "result",
          "type": "any"
        },
        {
          "name": "branch",
          "type": "any"
        },
        {
          "name": "items",
          "type": "any[]"
        }
      ]
    },
    {
      "id": "report",
      "type": "fs.write_file",
      "params": {
        "path_template": "work/branch.txt",
        "content": "checked"
      },
      "inputs": [
        "in"
      ]
    }
  ],
  "edges": [
    {
      "id": "decide-report",
      "source": "decide",
      "target": "report",
      "source_port": "branch",
      "target_port": "in"
    }
  ],
  "settings": {
    "default_dry_run": false
  },
  "variables": {
    "score": {
      "type": "integer",
      "value": 7
    }
  }
}
```

### 16.3 (c) `fs.move` with a conflict policy

`wr` writes `invoice.txt` at `base_dir`; `mv` moves it to `archive/invoice.txt` using `template = "archive/{name}{ext}"` (relative to `source.parent`) and `conflict_policy = "overwrite"`. The source is in the writable scope so the move can be applied.

**DSL**

```ldsq
automation "move-invoice"

name = "Move an invoice with conflict policy"
default_dry_run = false

scope {
  roots = [".", "archive"]
  writable = [".", "archive"]
}

node "wr" {
  type = "fs.write_file"
  params {
    path_template = "invoice.txt"
    content = "invoice 42"
    create_parents = false
  }
}

node "mv" {
  type = "fs.move"
  params {
    source = "invoice.txt"
    template = "archive/{name}{ext}"
    conflict_policy = "overwrite"
  }
  inputs = ["file"]
  outputs = [{name = "file", type = "string"}]
}

edge "wr-mv" {
  source = "wr"
  target = "mv"
  source_port = "path"
  target_port = "file"
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "move-invoice",
    "name": "Move an invoice with conflict policy"
  },
  "nodes": [
    {
      "id": "wr",
      "type": "fs.write_file",
      "params": {
        "path_template": "invoice.txt",
        "content": "invoice 42",
        "create_parents": false
      }
    },
    {
      "id": "mv",
      "type": "fs.move",
      "params": {
        "source": "invoice.txt",
        "template": "archive/{name}{ext}",
        "conflict_policy": "overwrite"
      },
      "inputs": [
        "file"
      ],
      "outputs": [
        {
          "name": "file",
          "type": "string"
        }
      ]
    }
  ],
  "edges": [
    {
      "id": "wr-mv",
      "source": "wr",
      "target": "mv",
      "source_port": "path",
      "target_port": "file"
    }
  ],
  "settings": {
    "default_dry_run": false,
    "scope": {
      "roots": [
        ".",
        "archive"
      ],
      "writable": [
        ".",
        "archive"
      ]
    }
  }
}
```

### 16.4 (d) Child automation for the loop

`copy-child` is addressed by `graph_id = "copy-child"` from the parent. `cp` copies `inbox/seed.txt` next to itself with `conflict_policy = "rename"`; the loop still executes the child once per filtered item. Register both documents in `Runtime` so `Runtime._catalog()` can resolve the id.

**DSL**

```ldsq
automation "copy-child"

name = "Copy one filtered file"
default_dry_run = true

scope {
  roots = ["inbox"]
  writable = ["inbox"]
}

node "cp" {
  type = "fs.copy"
  params {
    source = "inbox/seed.txt"
    template = "copied-{name}{ext}"
    conflict_policy = "rename"
  }
  outputs = [{name = "file", type = "string"}]
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "copy-child",
    "name": "Copy one filtered file"
  },
  "nodes": [
    {
      "id": "cp",
      "type": "fs.copy",
      "params": {
        "source": "inbox/seed.txt",
        "template": "copied-{name}{ext}",
        "conflict_policy": "rename"
      },
      "outputs": [
        {
          "name": "file",
          "type": "string"
        }
      ]
    }
  ],
  "edges": [],
  "settings": {
    "default_dry_run": true,
    "scope": {
      "roots": [
        "inbox"
      ],
      "writable": [
        "inbox"
      ]
    }
  }
}
```

### 16.5 (e) `access.list_files → flow.filter → flow.loop_batch → fs.copy`

`ls` lists `.txt` files, `flt` keeps entries with `size > 0`, and `loop` executes `copy-child` once per item. The parent and child documents are separate because `flow.loop_batch` addresses the child by `graph_id`.

**DSL**

```ldsq
automation "list-filter-loop-copy"

name = "List, filter, loop, copy"
default_dry_run = true

scope {
  roots = ["inbox", "archive"]
  writable = ["inbox", "archive"]
}

node "ls" {
  type = "access.list_files"
  params {
    path = "inbox"
    pattern = "*.txt"
    recursive = true
  }
  outputs = [{name = "files", type = "FileRef[]"}]
}

node "flt" {
  type = "flow.filter"
  params {
    condition = "size > 0"
  }
  inputs = ["items"]
  outputs = [{name = "items", type = "any[]"}, {name = "results", type = "any[]"}, {name = "total", type = "int"}, {name = "kept", type = "int"}, {name = "filtered", type = "int"}, {name = "errors", type = "int"}]
}

node "loop" {
  type = "flow.loop_batch"
  params {
    mode = "loop"
    graph_id = "copy-child"
  }
  inputs = ["items"]
}

edge "ls-flt" {
  source = "ls"
  target = "flt"
  source_port = "files"
  target_port = "items"
}

edge "flt-loop" {
  source = "flt"
  target = "loop"
  source_port = "items"
  target_port = "items"
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "list-filter-loop-copy",
    "name": "List, filter, loop, copy"
  },
  "nodes": [
    {
      "id": "ls",
      "type": "access.list_files",
      "params": {
        "path": "inbox",
        "pattern": "*.txt",
        "recursive": true
      },
      "outputs": [
        {
          "name": "files",
          "type": "FileRef[]"
        }
      ]
    },
    {
      "id": "flt",
      "type": "flow.filter",
      "params": {
        "condition": "size > 0"
      },
      "inputs": [
        "items"
      ],
      "outputs": [
        {
          "name": "items",
          "type": "any[]"
        },
        {
          "name": "results",
          "type": "any[]"
        },
        {
          "name": "total",
          "type": "int"
        },
        {
          "name": "kept",
          "type": "int"
        },
        {
          "name": "filtered",
          "type": "int"
        },
        {
          "name": "errors",
          "type": "int"
        }
      ]
    },
    {
      "id": "loop",
      "type": "flow.loop_batch",
      "params": {
        "mode": "loop",
        "graph_id": "copy-child"
      },
      "inputs": [
        "items"
      ]
    }
  ],
  "edges": [
    {
      "id": "ls-flt",
      "source": "ls",
      "target": "flt",
      "source_port": "files",
      "target_port": "items"
    },
    {
      "id": "flt-loop",
      "source": "flt",
      "target": "loop",
      "source_port": "items",
      "target_port": "items"
    }
  ],
  "settings": {
    "default_dry_run": true,
    "scope": {
      "roots": [
        "inbox",
        "archive"
      ],
      "writable": [
        "inbox",
        "archive"
      ]
    }
  }
}
```

### 16.6 (f) `llm.local_llm` classification

`classify` asks the local model for JSON with `task = "classify"`; `validate` checks the returned `data` against the same schema. The LLM node does not enforce the schema itself; `utils.json_validate` does.

**DSL**

```ldsq
automation "llm-classify"

name = "Classify an invoice with a local LLM"
default_dry_run = true

node "classify" {
  type = "llm.local_llm"
  params {
    text = "Invoice 42 from ACME, total 100 EUR"
    task = "classify"
    model = "llama3.2"
    schema {
      type = "object"
      required = ["label", "confidence"]
      properties {
        label {
          type = "string"
        }
        confidence {
          type = "number"
          minimum = 0
          maximum = 1
        }
      }
    }
  }
  outputs = [{name = "data", type = "any"}]
}

node "validate" {
  type = "utils.json_validate"
  params {
    schema {
      type = "object"
      required = ["label", "confidence"]
      properties {
        label {
          type = "string"
        }
        confidence {
          type = "number"
          minimum = 0
          maximum = 1
        }
      }
    }
  }
  inputs = ["data"]
}

edge "classify-validate" {
  source = "classify"
  target = "validate"
  source_port = "data"
  target_port = "data"
}
```

**JSON**

```json
{
  "spec_version": "1.0",
  "metadata": {
    "id": "llm-classify",
    "name": "Classify an invoice with a local LLM"
  },
  "nodes": [
    {
      "id": "classify",
      "type": "llm.local_llm",
      "params": {
        "text": "Invoice 42 from ACME, total 100 EUR",
        "task": "classify",
        "model": "llama3.2",
        "schema": {
          "type": "object",
          "required": [
            "label",
            "confidence"
          ],
          "properties": {
            "label": {
              "type": "string"
            },
            "confidence": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            }
          }
        }
      },
      "outputs": [
        {
          "name": "data",
          "type": "any"
        }
      ]
    },
    {
      "id": "validate",
      "type": "utils.json_validate",
      "params": {
        "schema": {
          "type": "object",
          "required": [
            "label",
            "confidence"
          ],
          "properties": {
            "label": {
              "type": "string"
            },
            "confidence": {
              "type": "number",
              "minimum": 0,
              "maximum": 1
            }
          }
        }
      },
      "inputs": [
        "data"
      ]
    }
  ],
  "edges": [
    {
      "id": "classify-validate",
      "source": "classify",
      "target": "validate",
      "source_port": "data",
      "target_port": "data"
    }
  ],
  "settings": {
    "default_dry_run": true
  }
}
```


---

## 17. Common pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| `DSL parse error ... expected a newline or '}' after an entry` | Two entries on one line, or a multi-line inline map | Put one block entry per line; inline maps stay on one line (lists may span lines) |
| `DSL parse error ... expected a label` after `edge {` | The DSL has no implicit edge id | Write `edge "e1" { ... }` |
| `a DSL automation must start with 'automation "<id>"'` | Missing header | Start the file with `automation "<id>"` |
| `unsupported spec_version` | `spec_version` not `"1.0"` | Omit it or set `"1.0"` |
| `unknown top-level statement` | Custom top-level key | Put custom data under `extra_meta { ... }` |
| A trigger/edge/variable custom key disappears | Parser drops unknown keys in those blocks | Put custom data under `params` (triggers/nodes) or `extra_meta` |
| A node param became `params.<key>` unexpectedly | Any node key outside the reserved eight is routed to params | Use exact reserved key names or a `params` block |
| `variable "n" = 5` has type `"string"` | Shorthand forces string type | Use the block form with `type = "integer"` |
| A condition always errors on `true`/`false` | Condition constants are `True`/`False`/`None`; lowercase words are identifiers | Use capitalized constants or a variable |
| `path == "/tmp"` is a condition error | Conditions reject `/` and `\` anywhere | Compare a path-free field, or compute the path in `utils.path_template` |
| `in` condition errors | Right operand is not a string/list/tuple/set/dict | Use a supported container |
| `item.path` is a condition parse error | Dotted names are not identifiers | Use `path` after dict-item merging, or `item` for a scalar |
| `edge` uses `condition` | Edges have no condition field | Use `flow.if_branch`/`flow.filter` and explicit graph structure |
| `fs.make_dir`/`fs.write_file` leaves `{name}` literal | These nodes do not inject file-derived variables | Pass `variables = {name = ...}` or use a source→target node |
| `absolute paths are not allowed` during validation | A path-like param starts with `/`, `~`, or a drive letter | Use a relative path; absolute **sources** are resolved by the node, but templates are rejected |
| Template with `..` is rejected | `render_template`/`render_and_check` policy | Use a source whose parent makes the relative destination correct |
| `outside scope` / `no writable roots` | Path is outside `roots`/`writable`, or no roots configured | Add the path to `scope.writable` and `scope.roots`; configure `base_dir` |
| A required input port fails at execution even though the node class lists it | The document did not declare `inputs`; registry metadata is not injected | Declare `inputs` on the node, or provide `params`/edges/defaults |
| Downstream receives `None` from an fs node | The upstream port name or the document edge does not match the produced key | Wire the declared `file`/`files` output; `fs.copy`, `fs.move`, `fs.rename`, `fs.archive`, `fs.convert`, and `fs.trash` now populate it |
| `retry_policy` on an fs node warns about side effects | `retry_safe=False` | Remove the policy, use a retry-safe node, or set `error_handling.strict_retry_safe = true` to reject it |
| `flow.wait` does not pause during dry-run | Dry-run is side-effect free by design | Read `would_sleep` for the duration it would have waited |
| A `flow.subflow`/`flow.loop_batch` plan errors | Nested previews fail closed | Set `recursive_preview=true` only when a planning callback is wired |
| `subflow: graph_id not found` | Id is not in `ctx.automations` | Register the child automation with `Runtime`; use a catalog id, not a path |
| `graph contains cycle(s)` even with disabled nodes | Cycle check covers all nodes | Remove the cycle; disabling does not excuse it |
| `Unknown node type` only at run/build | `parse_dsl`/`validate_graph` do not check the registry | Run `build_graph` or register/import the node family |
| `desktop` notify channel fails | Only `email` and `webhook` are supported | Change the channel |
| You want the old one-error-at-a-time validation output | `validate_graph` and the CLI report all errors by default | Pass `--first-error` to `validate`/`run` |

---

## 18. Versioning and compatibility

### 18.1 `spec_version`

* JSON: `spec_version` is required and must equal the string `"1.0"`; missing or any other value raises `GraphParseError` (`graph/parser.py:parse_graph`).
* DSL: `spec_version` defaults to `"1.0"` and may be overridden with `spec_version = "..."`, but `parse_dsl` then rejects anything other than `"1.0"`.
* `validate_graph` also reports a non-`"1.0"` document, but `parse_graph` would already have rejected it.
* There is no negotiation, migration, or forward-compatible reader for future versions. A new version requires code changes in `parse_graph`.

### 18.2 Unknown keys

| Context | Behavior |
|---|---|
| JSON top level | Unknown keys are folded into `extra_meta` via `setdefault`, preserving them (`graph/parser.py:parse_graph`) |
| JSON `metadata` | Unknown keys are ignored by `_parse_metadata` |
| JSON `settings` | Unknown keys are ignored by `_parse_settings` |
| JSON variable spec | Unknown keys are ignored by `_parse_variable` |
| JSON trigger/node/edge | Unknown keys are ignored (node `params` is the container for extras) |
| DSL top level | Unknown statement keyword → `GraphParseError` |
| DSL node block | Unknown keys → `params` |
| DSL trigger/edge/variable block | Unknown keys are silently dropped, except `edge.condition`, which is rejected |
| Node params | Any key is accepted and passed to the node; the node decides whether to use it |

`extra_meta` is the supported forward-compatibility bucket for JSON producers. It is preserved by `document_to_dict` and emitted as `extra_meta { ... }`.

### 18.3 Node and trigger extension

Third-party nodes register through Python entry points in the `lokdocsqr.nodes` group and `@register_node` (`core/plugins.py`). The DSL and JSON schema do not need changes to reference a new type id; `build_graph` resolves it from the registry. Unknown node types are only detected at build time.

New trigger kinds are not extensible through the registry: `core/runtime.py:_bind_document_triggers` recognizes a fixed alias set and logs/skips unknown kinds.

---

## 19. Appendix

### 19.1 Module map

| Path | Responsibility |
|---|---|
| `src/lokdocsqr/core/models.py` | Dataclasses/enums for documents, ports, edges, results, audit |
| `src/lokdocsqr/core/dsl/__init__.py` | Public DSL API (`compile_dsl`, `parse_dsl`, `emit_dsl`, `document_to_dict`) |
| `src/lokdocsqr/core/dsl/parser.py` | DSL lexer/parser → raw schema dict |
| `src/lokdocsqr/core/dsl/serializer.py` | `document_to_dict` and canonical `dsl_from_dict` |
| `src/lokdocsqr/core/graph/parser.py` | Structural JSON → `WorkflowDocument` |
| `src/lokdocsqr/core/graph/validator.py` | Semantic validation (ids, edges, ports, path policy) |
| `src/lokdocsqr/core/graph/resolver.py` | Disabled/skipped cascade |
| `src/lokdocsqr/core/graph/builder.py` | Topological order, handler lookup, `ResolvedGraph` |
| `src/lokdocsqr/core/payload.py` | Shared payload precedence and edge-list behavior |
| `src/lokdocsqr/core/executor/context.py` | `ExecutionContext`, reserved variables, `set_variable` |
| `src/lokdocsqr/core/executor/engine.py` | Execution, policy gate, retries, audit finalization |
| `src/lokdocsqr/core/node_api/base.py` | `Node` base class, `retry_safe`, port contract |
| `src/lokdocsqr/core/node_api/registry.py` | `NodeRegistry` singleton |
| `src/lokdocsqr/core/node_api/decorators.py` | `@register_node` |
| `src/lokdocsqr/core/paths.py` | `resolve_path`, `render_template`, `SafeDict` |
| `src/lokdocsqr/core/scope.py` | `ScopeManager` read/write/symlink enforcement |
| `src/lokdocsqr/core/config.py` | `AppConfig` / `FlowConfig`, `base_dir` |
| `src/lokdocsqr/core/runtime.py` | Trigger host, aliases, cron/interval/watcher, dry-run resolution |
| `src/lokdocsqr/core/catalog.py` | `AutomationCatalog` for subflow ids |
| `src/lokdocsqr/core/planning/builder.py` | Dry-run action plans and planner payload behavior |
| `src/lokdocsqr/core/planning/registry.py` | Operation classification and path-key extraction |
| `src/lokdocsqr/nodes/fs_ops/` | All `fs.*` nodes and shared conflict/template logic |
| `src/lokdocsqr/nodes/access/` | All `access.*` read-only nodes |
| `src/lokdocsqr/nodes/flow/` | Flow/control nodes and condition evaluator |
| `src/lokdocsqr/nodes/utils/` | `utils.*` nodes |
| `src/lokdocsqr/nodes/llm/` | `llm.local_llm` |
| `src/lokdocsqr/nodes/triggers/` | Trigger nodes that normalize events |
| `src/lokdocsqr/cli/main.py` | CLI load/compile/validate/run/serve |

### 19.2 Glossary

| Term | Meaning |
|---|---|
| **Automation / workflow** | A `WorkflowDocument`: metadata, settings, variables, triggers, nodes, edges |
| **Node** | One `NodeSpec` in the graph; `type` names a registered class |
| **Port** | Named input/output on a node; `PortSpec` in the document |
| **Edge** | Directed dependency from `source`/`source_port` to `target`/`target_port`; also carries payload |
| **Payload** | The dict passed to `run`/`dry_run`, built from params, inbound edges, and port defaults |
| **ActionLog** | Audit record of one attempted action; constructed by nodes, appended/finalized by the executor |
| **Dry-run** | Simulated execution: `dry_run()` is called instead of `run()`, no filesystem mutation; audit statuses become `dry_run` |
| **Scope** | Readable/writable filesystem roots enforced by `ScopeManager` |
| **Disabled cascade** | Enabled nodes with a disabled/skipped inbound parent become skipped |
| **Retry-safe** | Node class flag (`retry_safe=True`) allowing per-node `retry_policy` |
| **Subflow** | A child automation addressed by `graph_id` (catalog) or an inline `graph` dict |
| **Catalog** | `AutomationCatalog`, a `{graph_id: ResolvedGraph}` map used by subflows |
| **Canonical DSL** | The deterministic form produced by `emit_dsl` |

---

## Known divergences

These are real differences that remain between prose/documentation, declared metadata, and executable behavior after the Phase 10.5 fixes. The four previously listed defects — inert `edge.condition`, unpopulated fs output ports, silent `flow.filter` non-list input, and indexed-placeholder validation failure — are fixed and removed from this list (see `docs/DSL.md.changelog.md`). Do not treat the entries below as supported behavior.

1. **DSL edge ids are mandatory; JSON ids are optional.**
   `edge { ... }` fails in DSL (`parser.py:_parse_edge` label required). `docs/SCHEMA.md` documents the JSON default `"{source}->{target}"`, which is accurate for JSON only. There is no DSL equivalent.

2. **`params.dry_run` only affects watch and scheduled bindings.**
   `_bind_document_triggers` reads `params.dry_run` generically, but the manual/webhook branch `continue`s without storing it. For manual/webhook, pass `dry_run=` to `Runtime.trigger`/`run_once`.

3. **`compile_dsl` is not normalized.**
   It returns the raw parser dict, including empty `settings` entries from `scope {}`/`error_handling {}`. `document_to_dict(parse_dsl(text))` is the normalized schema. Both are valid; they are not interchangeable.

4. **`fs.move.cross_volume` is reserved, not wired.**
   `_describe_payload` exposes it, but `_execute` always calls `safe_fs.safe_move`, which handles EXDEV transparently; the flag does not change behavior.

5. **`fs.convert.supported` is informational.**
   It is returned by `describe()` as a `value`, not read from the payload. Setting it has no effect.

6. **`fs.make_dir`/`fs.write_file` lack standard template variables.**
   They render against `payload["variables"]` only. `fs.copy`/`fs.move`/`fs.rename` add `name`, `ext`, `date`, `author`, `tags`. This asymmetry is easy to miss.

7. **`access.extract_text` does not forward OCR options.**
   The image handler supports `lang`/`psm`/`oem`, but `ExtractTextNode._process` passes only `csv_*` and `html_*` params. Image OCR through `extract_text` always uses `eng`, `psm=3`, `oem=3`. Use `access.ocr` for control.

8. **Nested previews fail closed in the library; the CLI auto-wires.**
   Library `flow.subflow`/`flow.loop_batch` `dry_run` raises unless `recursive_preview=true` and `ctx.planning_callback` exists. `lokdocsqr run --dry-run` now sets `recursive_preview=true` on those nodes and supplies the planning callback automatically; the library API deliberately keeps its strict opt-in behavior.

9. **Subflow/loop_batch child scope ignores the child document's scope settings.**
   Child contexts inherit the parent `ScopeManager` by identity. A child document with different `settings.scope.roots`/`writable` will not use them when invoked through `flow.subflow` or `flow.loop_batch`. File to change: `nodes/flow/_common.py:_resolve_child_graph`/callers would need to construct a child `ScopeManager`.

10. **`flow.loop_batch` error messages say `subflow:`.**
    `_resolve_child_graph` is shared and hard-codes the `subflow:` prefix. This is cosmetic but confusing when debugging loop_batch.

11. **Trigger node event files bypass scope checking.**
    `triggers._base.files_from_event`/`normalise_files` do not call `scope.check`; only scan-discovered files are checked. Downstream access/fs nodes still enforce scope, so this is a defense-in-depth gap rather than an escape.

12. **`triggers.scheduled_scan.cron` is metadata only.**
    The node exposes a `cron` input but does not schedule anything; scheduling is owned by the runtime trigger declaration. A graph that sets `cron` only on the node will never be scheduled automatically.

13. **`parse_dsl`/`validate_graph` do not validate node types or params.**
    `parse_dsl` only runs `parse_graph`. An unregistered type compiles and parses; `build_graph` raises later. Param semantics are validated inside node execution. This is intentional separation, but it means “parse succeeded” is not “the automation is runnable”.

14. **`fs.tag` xattr persistence is best-effort.**
    If `safe_fs.set_xattr` is unavailable, the node logs and continues with the sidecar only. The sidecar is the durable record.

16. **`utils.notify` is not transactional.**
    Delivery is fail-soft: partial SMTP/HTTP failures are reported as `sent=false` with an error, and there is no retry/rollback.

17. **`access.list_files` returns keys beyond its declared port.**
    `truncated` and `scanned` are in the result dict but not declared in `outputs`. The same pattern applies to other access nodes (`hash_dedupe`, `index_search`, etc.). Declared ports are a subset of runtime keys.

18. **Port metadata is not injected from the registry.**
    `Node.inputs`/`outputs` in Python are GUI/documentation metadata. `parse_graph` never copies them into `NodeSpec`; required-port enforcement uses only document-declared `inputs`. This is why a graph can run with no declared ports even though the class declares required ones.

19. **Registry-declared but undeclared document ports are warnings.**
    When an edge references a port that exists on the registered node class but is absent from the document's `inputs`/`outputs`, `validate_graph` returns no error but appends a warning (via its `warnings=` out-parameter). The document is still the only source of ports; adding the declaration silences the warning. This is intentional: auto-injecting registry ports would break `parse_graph(document_to_dict(doc)) == doc`.

20. **Parent traversal is opt-in.**
    By default any `..` in a rendered path is rejected before scope resolution. With `scope.allow_parent_traversal = true` (or `AppConfig.allow_parent_traversal = true`), `..` is permitted and only the final writable-root containment check decides. This resolves the former inability to reference a sibling folder; the default remains closed for compatibility.

---

## Verification log

Verified on 2026-11-03 against repository HEAD `de376f49a2db5221ecffc6c01a3b27d33567ccd1` (`git rev-parse HEAD`) with the Phase 10.5 working-tree changes applied. No source changes were made during this verification run.

### Commands

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python tools/verify_dsl_docs.py
```

`tools/verify_dsl_docs.py` is also wired into the suite as `tests/test_dsl_docs.py::test_docs_examples`, so the same checks run in CI.

### Automated checks

| Check | Method | Result |
|---|---|---|
| Full test suite | `PYTHONPATH=src .venv/bin/python -m pytest -q` | `1451 passed, 6 skipped, 2 xfailed` |
| Registered node enumeration | `registry.list()` after `import lokdocsqr.nodes` | 32 ids; all 32 appear in `docs/DSL.md` |
| Fenced DSL blocks | Every `ldsq` fence compiled, parsed, emitted to a fixed point, and `parse_graph(document_to_dict(...))`-inverted; fragments wrapped in `automation "_snippet"` | 56/56 pass |
| JSON examples | All `json` fences parsed with `json.loads` | 9/9 pass |
| DSL/JSON equivalence pairs | `compile_dsl(ldsq) == json.loads(json)` for every complete automation with an adjacent JSON block (quick start + six §16 examples) | 7/7 pass |
| Section 16 DSL/JSON equivalence | The six §16 pairs compared with `compile_dsl` | 6/6 true |

The two `xfailed` tests are pre-existing, unrelated to D1/D5/D10/D23, and still carry their own documented markers. The removed `tests/test_phase_8a.py::test_c2_filter_non_list_input_reports_error` xfail is now a normal passing test. No new `xfail` or `skip` markers were added.

### Dedicated regression tests

| Defect | Test(s) | Result |
|---|---|---|
| D1 — `edge.condition` removed | `tests/test_phase_10_5_fixes.py::test_d1_dsl_edge_condition_is_rejected`, `::test_d1_json_edge_condition_is_ignored_and_not_serialized` | pass |
| D5 — fs output ports populated | `tests/test_phase_10_5_fixes.py::test_d5_copy_output_reaches_downstream`, `::test_d5_move_output_reaches_downstream`, `::test_d5_rename_output_reaches_downstream`, `::test_d5_archive_output_reaches_downstream`, `::test_d5_convert_output_reaches_downstream`, `::test_d5_trash_output_reaches_downstream` | pass (6/6) |
| D10 — filter rejects non-lists | `tests/test_phase_8a.py::test_c2_filter_non_list_input_reports_error`, `tests/test_phase_10_5_fixes.py::test_d10_filter_scalar_input_reports_error`, `::test_d10_filter_dict_without_sequence_key_reports_error`, `::test_d10_filter_empty_list_is_not_an_error` | pass |
| D23 — indexed placeholders | `tests/test_phase_10_5_fixes.py::test_d23_indexed_template_passes_validate_and_build`, `::test_d23_indexed_template_renders_inside_loop_batch`, `::test_d23_static_path_checks_still_reject` | pass |

### Section 16 examples executed

| Example | `compile_dsl` | `parse_dsl` | Emit fixed point | `parse_graph(document_to_dict(...)) == doc` | Runtime smoke |
|---|---|---|---|---|---|
| 16.1 (a) watch → make_dir → write_file | pass | pass | pass | pass | dry-run: success, no errors |
| 16.2 (b) if_branch evaluation | pass | pass | pass | pass | dry-run: success, no errors |
| 16.3 (c) fs.move conflict policy | pass | pass | pass | pass | dry-run: success, no errors |
| 16.4 (d) child `copy-child` | pass | pass | pass | pass | registered in `Runtime`; dry-run: success |
| 16.5 (e) list → filter → loop_batch → copy | pass | pass | pass | pass | real run: success; loop executed twice, produced `copied-seed.txt` and `copied-seed (1).txt` |
| 16.6 (f) llm.local_llm classification | pass | pass | pass | pass | dry-run: success, no errors |

Runtime smoke used a temporary `AppConfig(base_dir=...)`, `policy=None`, `single_instance=False`, and `Runtime.run_once`. Example 16.5 registered both the child and parent documents so `Runtime._catalog()` could resolve `graph_id="copy-child"`.

### Fixed-item checks that previously failed

* **D1:** edge conditions are no longer stored. The DSL rejects `condition` on an edge and the serializer has no condition output.
* **D5:** all six fs_ops nodes now wire their declared output port into a downstream node and deliver a non-`None` value.
* **D10:** scalar and dict-without-list input to `flow.filter` return `results=["error"]`, `errors=1`, and `total=0`; empty lists remain valid.
* **D23:** `template = "{item[path]}"` passes `validate_graph` and `build_graph`, renders inside `flow.loop_batch`, and literal `"/etc/passwd"` / `"../x/{name}"` templates are still rejected.

Remaining limitations are exactly those listed under [Known divergences](#known-divergences).
