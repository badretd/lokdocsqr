"""Regression tests for the Phase 10.6 ergonomics/diagnostics pass.

Each test group maps to one numbered item from the task brief.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lokdocsqr.nodes  # noqa: F401  (register built-in node families)
from tests.helpers import make_ctx
from lokdocsqr.cli.main import main
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.node_api.registry import registry


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
