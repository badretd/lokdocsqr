"""Phase 10.5 acceptance tests — the automation DSL.

Covers the roadmap gate for 10.5:

* a sample DSL compiles into the JSON schema, parses to a WorkflowDocument,
  executes through the real runtime host, and produces audit entries;
* round-trip is stable: ``emit_dsl(parse_dsl(text)) == text`` for canonical
  text, and ``parse_graph(document_to_dict(doc)) == doc`` for any document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lokdocsqr.nodes  # noqa: F401  (registers all built-in nodes)
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.dsl import (
    compile_dsl,
    document_to_dict,
    emit_dsl,
    parse_dsl,
)
from lokdocsqr.core.errors import GraphParseError
from lokdocsqr.core.graph import parse_graph
from lokdocsqr.core.runtime import Runtime

SAMPLE_DSL = """\
automation "dsl-inbox"

name = "DSL inbox processor"
default_dry_run = false

trigger "watch-inbox" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = ["*.txt"]
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
    create_parents = false
  }
}
"""


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def test_sample_dsl_compiles_to_json_schema() -> None:
    compiled = compile_dsl(SAMPLE_DSL)
    assert compiled["spec_version"] == "1.0"
    assert compiled["metadata"]["id"] == "dsl-inbox"
    assert compiled["settings"]["default_dry_run"] is False
    assert compiled["triggers"][0]["type"] == "triggers.watch_folder"
    assert [node["type"] for node in compiled["nodes"]] == [
        "fs.make_dir",
        "fs.write_file",
    ]
    assert compiled["nodes"][1]["params"]["content"] == "from-dsl"
    assert compiled["edges"] == []


def test_bare_words_and_node_param_shorthand() -> None:
    text = """\
automation "shorthand"
node mk {
  type = fs.make_dir
  path_template = work
}
variable "inbox" = "inbox"
"""
    doc = parse_dsl(text)
    node = doc.nodes[0]
    assert node.type == "fs.make_dir"
    assert node.params == {"path_template": "work"}
    assert doc.variables["inbox"].value == "inbox"


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_is_text_idempotent() -> None:
    canonical = emit_dsl(parse_dsl(SAMPLE_DSL))
    reparsed = emit_dsl(parse_dsl(canonical))
    assert reparsed == canonical


def test_document_to_dict_inverts_parse_graph() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
    }
    raw: dict[str, Any] = {
        "spec_version": "1.0",
        "metadata": {"id": "rich", "name": "Rich", "tags": ["a"]},
        "settings": {
            "default_dry_run": False,
            "scope": {"roots": ["in"], "writable": ["out"]},
            "error_handling": {"strategy": "retry", "max_retries": 2},
        },
        "variables": {"n": {"type": "integer", "value": 5}},
        "triggers": [
            {"id": "scan", "type": "triggers.scheduled_scan", "params": {"cron": "@daily"}}
        ],
        "nodes": [
            {"id": "llm", "type": "llm.local_llm", "params": {"schema": schema}},
            {
                "id": "mv",
                "type": "fs.move",
                "state": "disabled",
                "params": {"source": "a", "template": "b"},
                "inputs": ["file"],
                "outputs": [{"name": "file", "type": "FileRef[]", "required": False}],
                "position": {"x": 1.5, "y": 2.0},
            },
        ],
        "edges": [
            {
                "id": "e",
                "source": "llm",
                "target": "mv",
                "source_port": "data",
                "target_port": "file",
                "condition": "true",
                "label": "move it",
            }
        ],
        "extra_meta": {"custom-key": [1, {"nested": True}]},
    }
    document = parse_graph(raw)
    assert parse_graph(document_to_dict(document)) == document
    # And the emitted text is a fixed point.
    text = emit_dsl(document)
    assert emit_dsl(parse_dsl(text)) == text


# ---------------------------------------------------------------------------
# Acceptance: DSL through the runtime
# ---------------------------------------------------------------------------


def test_sample_dsl_parses_executes_and_produces_audit(tmp_path: Path) -> None:
    runtime = Runtime(
        AppConfig(base_dir=tmp_path, default_dry_run=False),
        policy=None,
        lock_path=tmp_path / "runtime.lock",
    )
    automation_id = runtime.register(parse_dsl(SAMPLE_DSL))

    # The declared watch trigger is bound at registration time.
    assert [binding["name"] for binding in runtime.watch_bindings] == ["watch-inbox"]

    result = runtime.run_once(automation_id)

    assert result.success, result.errors
    assert (tmp_path / "work" / "report.txt").read_text(encoding="utf-8") == "from-dsl"

    # One audit entry per mutating node, applied, in execution order.
    assert [log.node_id for log in result.action_logs] == [
        "fs.make_dir",
        "fs.write_file",
    ]
    assert all(log.status == "applied" for log in result.action_logs)

    # The entries really landed in the on-disk audit log.
    stored = list(runtime.audit.iter_all())
    assert [log.node_id for log in stored] == ["fs.make_dir", "fs.write_file"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",  # no automation header
        'node mk { type = "fs.make_dir" }',  # missing automation header
        'automation "a"\nunknown_thing = 1\n',
        'automation "a"\nnode mk { type = "fs.make_dir" ',
        'automation "a"\nname = "unterminated\n',
    ],
)
def test_malformed_dsl_raises_graph_parse_error(text: str) -> None:
    with pytest.raises(GraphParseError):
        parse_dsl(text)
