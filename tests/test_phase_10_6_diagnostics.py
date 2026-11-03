"""Regression tests for the Phase 10.6 ergonomics/diagnostics pass.

Each test group maps to one numbered item from the task brief.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import lokdocsqr.nodes  # noqa: F401  (register built-in node families)
from tests.helpers import make_ctx
from lokdocsqr.cli.main import main
from lokdocsqr.core.dsl import compile_dsl
from lokdocsqr.core.errors import ScopeViolation
from lokdocsqr.core.paths import render_template
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
    WorkflowSettings,
    WorkspaceScope,
)
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.payload import build_node_payload


def _run_document(tmp_path: Path, raw: dict[str, Any]) -> Any:
    document = parse_graph(raw)
    graph = build_graph(document, resolve_states(document), registry)
    return Executor(make_ctx(tmp_path)).execute(graph)


# ---------------------------------------------------------------------------
# Item 1 — loop_batch / subflow surface child errors
# ---------------------------------------------------------------------------


def _invalid_child_graph() -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": "bad-child"},
        "nodes": [
            {
                "id": "a",
                "type": "fs.make_dir",
                "params": {"path_template": "a"},
                "outputs": [{"name": "path"}],
            },
            {
                "id": "b",
                "type": "fs.make_dir",
                "params": {"path_template": "b"},
                "inputs": [{"name": "in"}],
            },
        ],
        "edges": [
            {
                "id": "bad-edge",
                "source": "a",
                "target": "b",
                "source_port": "missing",
                "target_port": "in",
            }
        ],
    }


def test_loop_batch_on_child_error_warn(tmp_path: Path) -> None:
    raw = {
        "spec_version": "1.0",
        "metadata": {"id": "loop-warn-parent"},
        "nodes": [
            {
                "id": "loop",
                "type": "flow.loop_batch",
                "params": {
                    "items": [1],
                    "graph": _invalid_child_graph(),
                    "on_child_error": "warn",
                },
            }
        ],
        "edges": [],
    }
    result = _run_document(tmp_path, raw)

    assert result.warnings, "expected a child failure warning"
    assert any("loop_batch node" in item for item in result.warnings)
    assert result.success is False


# ---------------------------------------------------------------------------
# Item 2 — validate_graph accumulates every error
# ---------------------------------------------------------------------------


def test_validate_reports_all_edge_port_errors() -> None:
    raw = {
        "spec_version": "1.0",
        "metadata": {"id": "two-bad-ports"},
        "nodes": [
            {
                "id": "a",
                "type": "fs.make_dir",
                "outputs": [{"name": "path"}],
            },
            {
                "id": "b",
                "type": "fs.make_dir",
                "inputs": [{"name": "in"}],
            },
        ],
        "edges": [
            {
                "id": "bad-source",
                "source": "a",
                "target": "b",
                "source_port": "missing-source",
                "target_port": "in",
            },
            {
                "id": "bad-target",
                "source": "a",
                "target": "b",
                "source_port": "path",
                "target_port": "missing-target",
            },
        ],
    }
    errors = validate_graph(parse_graph(raw))
    assert len(errors) == 2, errors
    assert any("missing-source" in error for error in errors)
    assert any("missing-target" in error for error in errors)


# ---------------------------------------------------------------------------
# Item 3 — run --explain
# ---------------------------------------------------------------------------


def test_run_explain_prints_outputs(
    tmp_path: Path, capsys: Any
) -> None:
    automation = tmp_path / "explain.json"
    automation.write_text(
        json.dumps(
            {
                "spec_version": "1.0",
                "metadata": {"id": "explain"},
                "settings": {"default_dry_run": True},
                "nodes": [
                    {
                        "id": "wr",
                        "type": "fs.write_file",
                        "params": {
                            "path_template": "out.txt",
                            "content": "hello",
                        },
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    base = tmp_path / "ws"
    base.mkdir()
    assert (
        main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--dry-run",
                "--explain",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "node wr" in out
    assert "outputs:" in out
    assert "path" in out


# ---------------------------------------------------------------------------
# Item 4 — scanned count
# ---------------------------------------------------------------------------


def test_run_explain_prints_scanned_count(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    (base / "inbox").mkdir(parents=True)
    automation = tmp_path / "scanned.json"
    automation.write_text(
        json.dumps(
            {
                "spec_version": "1.0",
                "metadata": {"id": "scanned"},
                "nodes": [
                    {
                        "id": "ls",
                        "type": "access.list_files",
                        "params": {"path": "inbox"},
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--scope",
                str(base),
                "--dry-run",
                "--explain",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "ls: scanned 0 file(s)" in out


# ---------------------------------------------------------------------------
# Item 5 — registry-declared ports are warnings, not errors
# ---------------------------------------------------------------------------


def test_validate_warns_on_registry_port_not_in_document() -> None:
    raw = {
        "spec_version": "1.0",
        "metadata": {"id": "registry-port"},
        "nodes": [
            {
                "id": "src",
                "type": "utils.path_template",
                "params": {"template": "a.txt"},
            },
            {"id": "dst", "type": "fs.make_dir", "params": {"path_template": "b"}},
        ],
        "edges": [
            {
                "id": "src-dst",
                "source": "src",
                "target": "dst",
                "source_port": "path",
            }
        ],
    }
    warnings: list[str] = []
    errors = validate_graph(parse_graph(raw), warnings=warnings)
    assert errors == []
    assert len(warnings) == 1
    assert "path" in warnings[0]
    assert "utils.path_template" in warnings[0]
    assert "outputs" in warnings[0]


# ---------------------------------------------------------------------------
# Item 6 — edge.kind = "order"
# ---------------------------------------------------------------------------


def test_order_edge_does_not_require_target_port() -> None:
    raw = {
        "spec_version": "1.0",
        "metadata": {"id": "order-edge"},
        "nodes": [
            {
                "id": "a",
                "type": "fs.make_dir",
                "params": {"path_template": "a"},
                "outputs": [{"name": "path"}],
            },
            {
                "id": "b",
                "type": "fs.make_dir",
                "params": {"path_template": "b"},
                "inputs": [{"name": "in"}],
            },
        ],
        "edges": [
            {
                "id": "a-b",
                "source": "a",
                "target": "b",
                "kind": "order",
            }
        ],
    }
    document = parse_graph(raw)
    assert document.edges[0].kind == "order"
    assert validate_graph(document) == []


def test_order_edge_is_ignored_in_payload() -> None:
    node = NodeSpec(
        id="target",
        type="fs.make_dir",
        inputs=[PortSpec(name="value", required=False)],
    )
    edge = EdgeSpec(
        id="order",
        source="source",
        target="target",
        kind="order",
    )
    payload = build_node_payload(
        node,
        inbound_edges=[edge],
        outputs_by_node={"source": {"path": "should-not-appear"}},
    )
    assert "value" not in payload


# ---------------------------------------------------------------------------
# Item 7 — block-form lists
# ---------------------------------------------------------------------------


def test_block_form_list_parses() -> None:
    inline = """\
automation "block-list"
node "loop" {
  type = "flow.loop_batch"
  params {
    items = [1, 2, 3]
  }
}
"""
    block = """\
automation "block-list"
node "loop" {
  type = "flow.loop_batch"
  params {
    items = [
      1,
      2,
      3,
    ]
  }
}
"""
    assert compile_dsl(block) == compile_dsl(inline)


# ---------------------------------------------------------------------------
# Item 8 — template path functions
# ---------------------------------------------------------------------------


def test_template_basename() -> None:
    assert (
        render_template("{basename(source)}", {"source": "a/b/c.txt"})
        == "c.txt"
    )
    assert (
        render_template("{stem(source)}", {"source": "a/b/c.txt"}) == "c"
    )
    assert (
        render_template("{suffix(source)}", {"source": "a/b/c.txt"})
        == ".txt"
    )
    assert (
        render_template("{dirname(source)}", {"source": "a/b/c.txt"})
        == "a/b"
    )


def test_template_join() -> None:
    assert (
        render_template(
            "archive/{join(folder, name)}",
            {"folder": "sub", "name": "a.txt"},
        )
        == "archive/sub/a.txt"
    )
    assert render_template("{join('a', 'b', 'c.txt')}", {}) == "a/b/c.txt"


def test_template_function_absolute_result_rejected() -> None:
    with pytest.raises(ScopeViolation, match="absolute"):
        render_template("{join('/etc', 'passwd')}", {})


# ---------------------------------------------------------------------------
# Item 9 — optional parent traversal inside writable roots
# ---------------------------------------------------------------------------


def test_parent_traversal_disabled_by_default(tmp_path: Path) -> None:
    scope = ScopeManager(
        WorkspaceScope(roots=[tmp_path], writable=[tmp_path])
    )
    with pytest.raises(ScopeViolation, match="'\\.\\.'"):
        scope.check(tmp_path / "sub" / ".." / "sibling", write=True)
    with pytest.raises(ScopeViolation, match="'\\.\\.'"):
        render_template("../sibling", {})


def test_parent_traversal_enabled_within_writable_root(
    tmp_path: Path,
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sibling").mkdir()
    scope = ScopeManager(
        WorkspaceScope(
            roots=[tmp_path],
            writable=[tmp_path],
            allow_parent_traversal=True,
        )
    )
    resolved = scope.check(
        tmp_path / "sub" / ".." / "sibling", write=True
    )
    assert resolved == (tmp_path / "sibling").resolve()
    assert (
        render_template(
            "../sibling", {}, allow_parent_traversal=True
        )
        == "../sibling"
    )


def test_parent_traversal_escape_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "sub").mkdir()
    scope = ScopeManager(
        WorkspaceScope(
            roots=[root],
            writable=[root],
            allow_parent_traversal=True,
        )
    )
    with pytest.raises(ScopeViolation, match="outside"):
        scope.check(
            root / "sub" / ".." / ".." / "outside", write=True
        )


# ---------------------------------------------------------------------------
# Item 10 — target_dir on fs.move / fs.copy
# ---------------------------------------------------------------------------


def test_move_target_dir(exec_ctx: Any, tmp_path: Path) -> None:
    from lokdocsqr.nodes.fs_ops.move import MoveNode

    (tmp_path / "archive").mkdir()
    source = tmp_path / "source.txt"
    source.write_text("data", encoding="utf-8")
    result = MoveNode().run(
        exec_ctx,
        {
            "source": str(source),
            "template": "{name}{ext}",
            "target_dir": "archive",
        },
    )
    assert Path(result["file"]) == tmp_path / "archive" / "source.txt"
    assert (tmp_path / "archive" / "source.txt").exists()
    assert not source.exists()


def test_copy_target_dir(exec_ctx: Any, tmp_path: Path) -> None:
    from lokdocsqr.nodes.fs_ops.copy import CopyNode

    (tmp_path / "archive").mkdir()
    source = tmp_path / "source.txt"
    source.write_text("data", encoding="utf-8")
    result = CopyNode().run(
        exec_ctx,
        {
            "source": str(source),
            "template": "{name}{ext}",
            "target_dir": "archive",
        },
    )
    assert Path(result["file"]) == tmp_path / "archive" / "source.txt"
    assert (tmp_path / "archive" / "source.txt").exists()
    assert source.exists()


def test_move_target_dir_outside_scope_rejected(
    exec_ctx: Any, tmp_path: Path
) -> None:
    from lokdocsqr.nodes.fs_ops.move import MoveNode

    exec_ctx.scope.allow_parent_traversal = True
    source = tmp_path / "source.txt"
    source.write_text("data", encoding="utf-8")
    with pytest.raises(ScopeViolation, match="outside"):
        MoveNode().run(
            exec_ctx,
            {
                "source": str(source),
                "template": "{name}{ext}",
                "target_dir": "../escape",
            },
        )


# ---------------------------------------------------------------------------
# Item 11 — CLI --include for catalogs
# ---------------------------------------------------------------------------


def test_cli_run_include_two_files(
    tmp_path: Path, capsys: Any
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    child = tmp_path / "child.ldsq"
    child.write_text(
        """\
automation "child"
node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "child-out"
  }
}
""",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.ldsq"
    parent.write_text(
        """\
automation "parent"
node "loop" {
  type = "flow.loop_batch"
  params {
    items = []
    graph_id = "child"
  }
}
""",
        encoding="utf-8",
    )
    assert (
        main(
            [
                "run",
                str(parent),
                "--include",
                str(tmp_path),
                "--base-dir",
                str(workspace),
                "--scope",
                str(workspace),
                "--no-dry-run",
            ]
        )
        == 0
    )
    result = capsys.readouterr()
    assert "parent" in result.out


# ---------------------------------------------------------------------------
# Item 12 — CLI dry-run auto-wires nested previews
# ---------------------------------------------------------------------------


def test_cli_dry_run_with_loop_batch_succeeds(
    tmp_path: Path, capsys: Any
) -> None:
    child = {
        "spec_version": "1.0",
        "metadata": {"id": "child"},
        "nodes": [
            {
                "id": "mk",
                "type": "fs.make_dir",
                "params": {"path_template": "child-out"},
            }
        ],
        "edges": [],
    }
    parent = tmp_path / "parent.json"
    parent.write_text(
        json.dumps(
            {
                "spec_version": "1.0",
                "metadata": {"id": "parent-dry"},
                "nodes": [
                    {
                        "id": "loop",
                        "type": "flow.loop_batch",
                        "params": {
                            "items": [1],
                            "graph": child,
                        },
                    }
                ],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert (
        main(
            [
                "run",
                str(parent),
                "--base-dir",
                str(workspace),
                "--scope",
                str(workspace),
                "--dry-run",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert not (workspace / "child-out").exists()


# ---------------------------------------------------------------------------
# Item 13 — retry_safe is a warning by default
# ---------------------------------------------------------------------------


class _NonSafeNode(Node):
    id = "test.non-safe"

    def __init__(self) -> None:
        self.run_calls = 0

    @property
    def title(self) -> str:
        return "Non safe"

    def run(self, ctx: Any, payload: Any) -> Any:
        self.run_calls += 1
        return {}

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self.run(ctx, payload)


def _retry_document(*, strict: bool) -> WorkflowDocument:
    return WorkflowDocument(
        spec_version="1.0",
        metadata=WorkflowMetadata(id="retry-safe"),
        settings=WorkflowSettings(strict_retry_safe=strict),
        nodes=[
            NodeSpec(
                id="n",
                type="test.non-safe",
                params={
                    "retry_policy": {
                        "attempts": 2,
                        "retry_on": ["NodeExecutionError"],
                    }
                },
            )
        ],
        edges=[],
    )


def _retry_graph(document: WorkflowDocument, node: Any) -> ResolvedGraph:
    return ResolvedGraph(
        document=document,
        ordered_node_ids=["n"],
        handlers={"n": node},
        states={"n": NodeState.ENABLED},
    )


def test_retry_policy_on_non_safe_warns_and_runs(
    tmp_path: Path,
) -> None:
    document = _retry_document(strict=False)
    node = _NonSafeNode()
    result = Executor(make_ctx(tmp_path)).execute(
        _retry_graph(document, node)
    )
    assert result.success is True
    assert node.run_calls == 1
    assert any(
        "retry_policy on non-retry-safe" in warning
        for warning in result.warnings
    )


def test_strict_retry_safe_rejects(tmp_path: Path) -> None:
    document = _retry_document(strict=True)
    node = _NonSafeNode()
    result = Executor(make_ctx(tmp_path)).execute(
        _retry_graph(document, node)
    )
    assert result.success is False
    assert node.run_calls == 0
    assert any("retry_safe=False" in error for error in result.errors)
