"""Regression tests for the Phase 10.5 divergence fixes.

Each group maps to one documented defect:

* D1  — ``edge.condition`` is removed from the schema/grammar.
* D5  — ``fs.copy``/``move``/``rename``/``archive``/``convert``/``trash``
        populate their declared output ports.
* D10 — ``flow.filter`` reports non-sequence input as an error.
* D23 — indexed placeholders pass ``validate_graph`` and render at runtime.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import lokdocsqr.nodes  # noqa: F401  (register built-in node families)
from tests.helpers import make_ctx
from lokdocsqr.core.dsl import document_to_dict, emit_dsl, parse_dsl
from lokdocsqr.core.errors import GraphParseError
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PortSpec,
    ResolvedGraph,
    WorkflowDocument,
    WorkflowMetadata,
)
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.nodes.fs_ops.archive import ArchiveNode
from lokdocsqr.nodes.fs_ops.convert import ConvertNode
from lokdocsqr.nodes.fs_ops.copy import CopyNode
from lokdocsqr.nodes.fs_ops.move import MoveNode
from lokdocsqr.nodes.fs_ops.rename import RenameNode
from lokdocsqr.nodes.fs_ops.trash import TrashNode
from lokdocsqr.nodes.flow.filter import FilterNode
from lokdocsqr.nodes.flow.loop_batch import LoopBatchNode


# ---------------------------------------------------------------------------
# D1 — edge.condition is removed
# ---------------------------------------------------------------------------


def test_d1_dsl_edge_condition_is_rejected() -> None:
    text = """\
automation "edge-condition"
node "a" { type = "fs.make_dir" }
node "b" { type = "fs.make_dir" }
edge "e" {
  source = "a"
  target = "b"
  condition = "true"
}
"""
    with pytest.raises(GraphParseError, match="condition"):
        parse_dsl(text)


def test_d1_json_edge_condition_is_ignored_and_not_serialized() -> None:
    raw = {
        "spec_version": "1.0",
        "metadata": {"id": "d1-json"},
        "nodes": [
            {"id": "a", "type": "fs.make_dir"},
            {"id": "b", "type": "fs.make_dir"},
        ],
        "edges": [
            {
                "id": "e",
                "source": "a",
                "target": "b",
                "condition": "score > 5",
            }
        ],
    }
    document = parse_graph(raw)
    assert not hasattr(document.edges[0], "condition")
    assert "condition" not in document_to_dict(document)["edges"][0]
    assert "  condition =" not in emit_dsl(document)


# ---------------------------------------------------------------------------
# D5 — fs_ops declared output ports are populated
# ---------------------------------------------------------------------------


class _CaptureNode(Node):
    """Downstream spy that records the payload built from inbound edges."""

    id = "test.capture"

    def __init__(self) -> None:
        self.captured: list[dict[str, Any]] = []

    @property
    def title(self) -> str:
        return "Capture"

    def run(self, ctx: Any, payload: Any) -> Any:
        self.captured.append(dict(payload))
        return {}

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self.run(ctx, payload)


def _run_capture(
    ctx: Any,
    *,
    type_id: str,
    handler: Any,
    params: dict[str, Any],
    output_port: str,
) -> Any:
    source = NodeSpec(
        id="source",
        type=type_id,
        params=params,
        outputs=[PortSpec(name=output_port)],
    )
    sink = NodeSpec(
        id="sink",
        type="test.capture",
        inputs=[PortSpec(name="value")],
    )
    document = WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="d5"),
        nodes=[source, sink],
        edges=[
            EdgeSpec(
                id="source->sink",
                source="source",
                target="sink",
                source_port=output_port,
                target_port="value",
            )
        ],
    )
    capture = _CaptureNode()
    graph = ResolvedGraph(
        document=document,
        ordered_node_ids=["source", "sink"],
        handlers={"source": handler, "sink": capture},
        states={"source": NodeState.ENABLED, "sink": NodeState.ENABLED},
    )
    ctx.dry_run = False
    result = Executor(ctx).execute(graph)
    assert result.success, result.errors
    assert capture.captured, "downstream node did not run"
    return capture.captured[0]["value"]


def test_d5_copy_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "copy-src.txt"
    source.write_text("payload", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.copy",
        handler=CopyNode(),
        params={
            "source": str(source),
            "template": "copy-{name}{ext}",
            "conflict_policy": "overwrite",
        },
        output_port="file",
    )
    assert value is not None
    assert Path(value) == tmp_path / "copy-copy-src.txt"
    assert Path(value).exists()


def test_d5_move_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "move-src.txt"
    source.write_text("payload", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.move",
        handler=MoveNode(),
        params={"source": str(source), "template": "moved.txt"},
        output_port="file",
    )
    assert value is not None
    assert Path(value) == tmp_path / "moved.txt"
    assert Path(value).exists()


def test_d5_rename_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "rename-src.txt"
    source.write_text("payload", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.rename",
        handler=RenameNode(),
        params={"source": str(source), "template": "renamed.txt"},
        output_port="file",
    )
    assert value is not None
    assert Path(value) == tmp_path / "renamed.txt"
    assert Path(value).exists()


def test_d5_archive_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "archive-src.txt"
    source.write_text("payload", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.archive",
        handler=ArchiveNode(),
        params={
            "operation": "create",
            "sources": [str(source)],
            "target": "bundle.zip",
            "format": "zip",
        },
        output_port="file",
    )
    assert value is not None
    assert Path(value) == tmp_path / "bundle.zip"
    assert Path(value).exists()


def test_d5_convert_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "in.csv"
    source.write_text("a\n1\n", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.convert",
        handler=ConvertNode(),
        params={
            "source": str(source),
            "target": "out.json",
            "from_mime": "text/csv",
            "to_mime": "application/json",
        },
        output_port="file",
    )
    assert value is not None
    assert Path(value) == tmp_path / "out.json"
    assert Path(value).exists()


def test_d5_trash_output_reaches_downstream(exec_ctx, tmp_path: Path) -> None:
    source = tmp_path / "trash-me.txt"
    source.write_text("payload", encoding="utf-8")
    value = _run_capture(
        exec_ctx,
        type_id="fs.trash",
        handler=TrashNode(),
        params={"path": str(source)},
        output_port="files",
    )
    assert value
    assert isinstance(value, list)
    assert Path(value[0]).exists()
    assert not source.exists()


# ---------------------------------------------------------------------------
# D10 — flow.filter reports non-sequence input
# ---------------------------------------------------------------------------


def test_d10_filter_scalar_input_reports_error(exec_ctx) -> None:
    result = FilterNode()._execute(exec_ctx, 123)
    assert result["errors"] >= 1
    assert result["total"] == 0
    assert result["results"] == ["error"]


def test_d10_filter_dict_without_sequence_key_reports_error(exec_ctx) -> None:
    result = FilterNode()._execute(
        exec_ctx, {"condition": "True", "other": 1}
    )
    assert result["errors"] >= 1
    assert result["total"] == 0
    assert result["items"] == []


def test_d10_filter_empty_list_is_not_an_error(exec_ctx) -> None:
    result = FilterNode()._execute(exec_ctx, [])
    assert result["errors"] == 0
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# D23 — indexed placeholders validate and render
# ---------------------------------------------------------------------------


def _path_template_document(template: str) -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": "template-validate"},
        "nodes": [
            {
                "id": "pt",
                "type": "utils.path_template",
                "params": {"template": template},
            }
        ],
        "edges": [],
    }


def test_d23_indexed_template_passes_validate_and_build() -> None:
    document = parse_graph(_path_template_document("{item[path]}"))
    assert validate_graph(document) == []
    graph = build_graph(
        document, resolve_states(document), registry
    )
    assert graph.ordered_node_ids == ["pt"]


def test_d23_indexed_template_renders_inside_loop_batch(
    tmp_path: Path,
) -> None:
    child = _path_template_document("{item[path]}")
    ctx = make_ctx(tmp_path)
    out = LoopBatchNode()._execute(
        ctx,
        {
            "items": [{"path": "a/b.txt"}, {"path": "c/d.txt"}],
            "graph": child,
        },
    )
    assert out["error_count"] == 0, out["errors"]
    rendered = [
        result.node_results["pt"].outputs["path_template"]
        for result in out["results"]
    ]
    assert rendered == ["a/b.txt", "c/d.txt"]


@pytest.mark.parametrize(
    "template, expected",
    [
        ("/etc/passwd", "absolute"),
        ("../x/{name}", "segment"),
    ],
)
def test_d23_static_path_checks_still_reject(
    template: str, expected: str
) -> None:
    document = parse_graph(_path_template_document(template))
    errors = validate_graph(document)
    assert any(expected in error for error in errors), errors
