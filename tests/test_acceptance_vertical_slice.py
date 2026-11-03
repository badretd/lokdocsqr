"""8.6 vertical slice: params-only graph through the real pipeline.

parse → validate → resolve → build → Executor.execute, using real
``fs_ops`` nodes and no edges. This is the acceptance test that the
planner/executor payload fix and the single-owner audit contract exist
for something real.

Phase 9 extension: a real ``llm.local_llm`` node feeds a real
``utils.json_validate`` node through an edge. The LLM client is faked;
the real Ollama client is never constructed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lokdocsqr.nodes.fs_ops.make_dir
import lokdocsqr.nodes.fs_ops.move
import lokdocsqr.nodes.fs_ops.write_file
import lokdocsqr.nodes.llm.local_llm as local_llm_mod
import lokdocsqr.nodes.utils.json_validate  # noqa: F401
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.events import EVENT_AUDIT_LOGGED, EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import WorkspaceScope
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager

LLM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["label"],
    "properties": {
        "label": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


class _FakeOllamaClient:
    """Minimal chat-only stand-in for ``ollama.Client``."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "message": {"role": "assistant", "content": self.content},
            "prompt_eval_count": 5,
            "eval_count": 3,
        }


RAW_GRAPH = {
    "spec_version": "1.0",
    "metadata": {"id": "vertical-slice", "name": "Vertical slice"},
    "nodes": [
        {
            "id": "llm",
            "type": "llm.local_llm",
            "params": {
                "text": "alpha",
                "task": "classify",
                "schema": LLM_SCHEMA,
            },
        },
        {
            "id": "validate",
            "type": "utils.json_validate",
            "params": {"schema": LLM_SCHEMA},
        },
        {
            "id": "mk",
            "type": "fs.make_dir",
            "params": {"path_template": "work"},
        },
        {
            "id": "wr",
            "type": "fs.write_file",
            "params": {
                "path_template": "work/a.txt",
                "content": "alpha",
                "mode": "write",
                "create_parents": False,
            },
        },
        {
            "id": "mv",
            "type": "fs.move",
            "params": {
                "source": "work/a.txt",
                "template": "b.txt",
                "conflict_policy": "overwrite",
            },
        },
    ],
    "edges": [
        {
            "id": "llm-data",
            "source": "llm",
            "source_port": "data",
            "target": "validate",
            "target_port": "data",
        }
    ],
}


def _ctx(tmp_path: Path, *, dry_run: bool) -> tuple[ExecutionContext, EventBus]:
    events = EventBus()
    ctx = ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(
                roots=[tmp_path], writable=[tmp_path], allow_symlinks=False
            )
        ),
        config=AppConfig(base_dir=tmp_path),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        run_id="vertical-run",
        automation_id="vertical-auto",
        dry_run=dry_run,
        events=events,
        policy=None,
    )
    return ctx, events


def _graph():
    doc = parse_graph(RAW_GRAPH)
    assert validate_graph(doc) == []
    states = resolve_states(doc)
    return build_graph(doc, states, registry)


def test_params_only_vertical_slice_applies_audits_and_rolls_back(
    tmp_path: Path, monkeypatch
) -> None:
    client = _FakeOllamaClient(
        json.dumps({"label": "document", "confidence": 0.95})
    )
    monkeypatch.setattr(local_llm_mod, "_get_client", lambda: client)
    ctx, events = _ctx(tmp_path, dry_run=False)
    logged: list[dict] = []
    events.subscribe(EVENT_AUDIT_LOGGED, logged.append)

    result = Executor(ctx).execute(_graph())

    work = tmp_path / "work"
    assert result.success, result.errors
    assert (work / "b.txt").read_text(encoding="utf-8") == "alpha"
    assert not (work / "a.txt").exists()

    # Phase 9: the executor really called the (mock) LLM and the real
    # json_validate node accepted its structured output.
    assert len(client.calls) == 1
    assert client.calls[0]["format"] == "json"
    assert result.node_results["llm"].outputs["data"] == {
        "label": "document",
        "confidence": 0.95,
    }
    assert result.node_results["validate"].outputs["valid"] is True

    # One result log per mutating node, in execution order.
    assert [log.node_id for log in result.action_logs] == [
        "fs.make_dir",
        "fs.write_file",
        "fs.move",
    ]
    assert all(log.status == "applied" for log in result.action_logs)

    # One disk copy per entry, and one event per entry.
    stored = list(ctx.audit.iter_all())
    assert len(stored) == 3
    assert [log.node_id for log in stored] == [
        "fs.make_dir",
        "fs.write_file",
        "fs.move",
    ]
    assert len(logged) == 3

    # The rollback plan reverses the move; the rest of the run remains.
    assert len(result.rollback_plan) == 1
    ctx.audit.apply_rollback(result.rollback_plan, ctx.scope)
    assert (work / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert not (work / "b.txt").exists()


def test_params_only_vertical_slice_dry_run_is_pure(
    tmp_path: Path, monkeypatch
) -> None:
    client = _FakeOllamaClient('{"label": "unused"}')
    monkeypatch.setattr(local_llm_mod, "_get_client", lambda: client)
    ctx, events = _ctx(tmp_path, dry_run=True)
    logged: list[dict] = []
    events.subscribe(EVENT_AUDIT_LOGGED, logged.append)

    result = Executor(ctx).execute(_graph())

    work = tmp_path / "work"
    assert result.success, result.errors
    assert not work.exists()

    # Dry-run must not call the model either.
    assert client.calls == []
    assert result.node_results["llm"].outputs["data"] is None
    assert result.node_results["llm"].outputs["usage"]["dry_run"] is True

    assert [log.node_id for log in result.action_logs] == [
        "fs.make_dir",
        "fs.write_file",
        "fs.move",
    ]
    assert all(log.status == "dry_run" for log in result.action_logs)
    assert len(logged) == 3
