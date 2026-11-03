"""Phase 9 acceptance tests — the local LLM node and its JSON contract.

Every test uses a fake Ollama client. The real ``ollama.Client`` is never
constructed: ``_get_client`` is either injected through the node constructor or
monkeypatched at module level.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import lokdocsqr.nodes.llm.local_llm as local_llm_mod
import lokdocsqr.nodes.utils.json_validate  # noqa: F401  (registers the node)
import pytest
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.events import EventBus
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
from lokdocsqr.core.planning.builder import build_action_plan
from lokdocsqr.core.planning.registry import classify_node
from lokdocsqr.core.plugins import discover_plugins
from lokdocsqr.core.policy import PolicyEngine
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.llm.local_llm import LocalLlmNode
from lokdocsqr.nodes.utils.json_validate import JsonValidateNode

ROOT = Path(__file__).resolve().parents[1]

SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["label"],
    "properties": {
        "label": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


class _FakeOllamaClient:
    """Tiny stand-in for ``ollama.Client`` with a chat-only surface."""

    def __init__(
        self,
        content: str,
        *,
        prompt_eval_count: int | None = 11,
        eval_count: int | None = 7,
        total_duration: int | None = 12_345,
    ) -> None:
        self.content = content
        self.prompt_eval_count = prompt_eval_count
        self.eval_count = eval_count
        self.total_duration = total_duration
        self.calls: list[dict[str, Any]] = []

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "message": {"role": "assistant", "content": self.content},
            "prompt_eval_count": self.prompt_eval_count,
            "eval_count": self.eval_count,
            "total_duration": self.total_duration,
        }


class _BoomClient:
    def chat(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("connection refused")


def _ctx(tmp_path: Path, *, dry_run: bool = False) -> ExecutionContext:
    return ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(
                roots=[tmp_path], writable=[tmp_path], allow_symlinks=False
            )
        ),
        config=AppConfig(base_dir=tmp_path),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        run_id="phase-9",
        automation_id="phase-9",
        dry_run=dry_run,
        events=EventBus(),
        policy=PolicyEngine.default(),
    )


RAW_GRAPH: dict[str, Any] = {
    "spec_version": "1.0",
    "metadata": {"id": "phase-9-llm", "name": "Phase 9 LLM"},
    "nodes": [
        {
            "id": "llm",
            "type": "llm.local_llm",
            "params": {
                "text": "Invoice 42 from ACME, total 100 EUR",
                "task": "classify",
                "schema": SCHEMA,
                "model": "mock-model",
            },
        },
        {
            "id": "validate",
            "type": "utils.json_validate",
            "params": {"schema": SCHEMA},
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


def _graph():
    document = parse_graph(RAW_GRAPH)
    assert validate_graph(document) == []
    states = resolve_states(document)
    return build_graph(document, states, registry)


# ---------------------------------------------------------------------------
# Registration / policy classification
# ---------------------------------------------------------------------------


def test_local_llm_is_registered_and_classified_read() -> None:
    assert "llm.local_llm" in discover_plugins()
    assert registry.get("llm.local_llm") is LocalLlmNode
    assert classify_node("llm.local_llm") == "read"
    assert LocalLlmNode.retry_safe is True


def test_local_llm_declares_structured_output_ports() -> None:
    node = LocalLlmNode()
    assert {"data", "text", "confidence", "usage"} <= set(node.outputs)
    assert node.title == "Local LLM"


# ---------------------------------------------------------------------------
# Task modes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("task", "document"),
    [
        ("classify", {"label": "invoice", "confidence": 0.91}),
        ("extract", {"vendor": "ACME", "total": 100, "confidence": 0.77}),
        ("summarize", {"summary": "ACME invoice", "confidence": 0.66}),
        (
            "plan",
            {
                "actions": [
                    {
                        "type": "move",
                        "source": "a.txt",
                        "target": "b.txt",
                        "reason": "match",
                        "confidence": 0.8,
                    }
                ],
                "confidence": 0.8,
            },
        ),
    ],
)
def test_task_modes_return_parsed_structured_json(
    task: str, document: dict[str, Any]
) -> None:
    client = _FakeOllamaClient(json.dumps(document))
    result = LocalLlmNode(client=client).run(
        None, {"text": "input text", "task": task}
    )

    assert result["data"] == document
    assert result["text"] == json.dumps(document)
    assert result["confidence"] == document["confidence"]
    assert result["usage"] == {
        "model": "llama3.2",
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "total_duration_ns": 12_345,
    }
    assert client.calls[0]["format"] == "json"


def test_schema_prompt_system_prompt_model_and_options_are_forwarded() -> None:
    client = _FakeOllamaClient('{"label": "invoice", "confidence": 0.5}')
    node = LocalLlmNode(client=client)
    node.run(
        None,
        {
            "text": "hello",
            "task": "classify",
            "schema": SCHEMA,
            "model": "custom-model",
            "system_prompt": "SYS",
            "options": {"temperature": 0},
        },
    )

    request = client.calls[0]
    assert request["model"] == "custom-model"
    assert request["options"] == {"temperature": 0}
    assert request["messages"][0]["content"] == "SYS"
    payload = request["messages"][1]["content"]
    assert "Classify the input text" in payload
    assert json.dumps(SCHEMA, sort_keys=True) in payload
    assert "hello" in payload


def test_schema_can_be_a_json_string() -> None:
    client = _FakeOllamaClient('{"label": "ok", "confidence": 1}')
    result = LocalLlmNode(client=client).run(
        None,
        {
            "text": "x",
            "task": "classify",
            "schema": json.dumps(SCHEMA),
        },
    )
    assert result["data"]["label"] == "ok"


def test_code_fenced_json_is_accepted() -> None:
    client = _FakeOllamaClient('```json\n{"label": "fenced"}\n```')
    result = LocalLlmNode(client=client).run(
        None, {"text": "x", "task": "classify"}
    )
    assert result["data"] == {"label": "fenced"}
    assert result["confidence"] is None


# ---------------------------------------------------------------------------
# Failure modes (fail closed)
# ---------------------------------------------------------------------------


def test_non_json_model_output_fails_closed() -> None:
    client = _FakeOllamaClient("this is not JSON")
    with pytest.raises(NodeExecutionError, match="not valid JSON"):
        LocalLlmNode(client=client).run(None, {"text": "x", "task": "classify"})


def test_unsupported_task_fails_closed() -> None:
    with pytest.raises(NodeExecutionError, match="unsupported task"):
        LocalLlmNode(client=_FakeOllamaClient("{}")).run(
            None, {"text": "x", "task": "translate"}
        )


def test_missing_text_fails_closed() -> None:
    with pytest.raises(NodeExecutionError, match="no input text"):
        LocalLlmNode(client=_FakeOllamaClient("{}")).run(
            None, {"task": "classify"}
        )


def test_client_error_wrapped_as_node_execution_error() -> None:
    with pytest.raises(NodeExecutionError, match="ollama request failed"):
        LocalLlmNode(client=_BoomClient()).run(
            None, {"text": "x", "task": "classify"}
        )


# ---------------------------------------------------------------------------
# Dry-run purity / optional dependency
# ---------------------------------------------------------------------------


def test_dry_run_returns_all_ports_and_never_calls_the_client() -> None:
    client = _FakeOllamaClient('{"label": "never"}')
    node = LocalLlmNode(client=client)

    result = node.dry_run(None, {"text": "x", "task": "classify"})

    assert client.calls == []
    assert set(node.outputs) <= set(result)
    assert result["data"] is None
    assert result["text"] is None
    assert result["confidence"] is None
    assert result["usage"]["dry_run"] is True


def test_module_does_not_touch_the_filesystem() -> None:
    source = Path(local_llm_mod.__file__).read_text(encoding="utf-8")
    assert "safe_fs" not in source
    assert "open(" not in source


def test_module_imports_without_ollama() -> None:
    code = """
import sys
sys.modules['ollama'] = None
import lokdocsqr.nodes.llm.local_llm as mod
print(mod.LocalLlmNode.id)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "import failed without the optional ollama dependency:\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr}"
    )
    assert "llm.local_llm" in proc.stdout


# ---------------------------------------------------------------------------
# Interaction with utils.json_validate
# ---------------------------------------------------------------------------


def test_llm_output_validates_through_json_validate_node() -> None:
    document = {"label": "invoice", "confidence": 0.9}
    llm_result = LocalLlmNode(
        client=_FakeOllamaClient(json.dumps(document))
    ).run(None, {"text": "x", "task": "classify"})

    validated = JsonValidateNode().run(
        None, {"data": llm_result["data"], "schema": SCHEMA}
    )

    assert validated["valid"] is True
    assert validated["data"] == document
    assert validated["error_count"] == 0


def test_invalid_llm_output_is_rejected_by_json_validate_node() -> None:
    llm_result = LocalLlmNode(
        client=_FakeOllamaClient(json.dumps({"label": 123}))
    ).run(None, {"text": "x", "task": "classify"})

    validated = JsonValidateNode().run(
        None, {"data": llm_result["data"], "schema": SCHEMA}
    )

    assert validated["valid"] is False
    assert validated["error_count"] == 1
    assert validated["errors"][0]["path"] == "$.label"


# ---------------------------------------------------------------------------
# Acceptance: plan -> executor -> LLM -> json_validate
# ---------------------------------------------------------------------------


def test_llm_graph_plans_executes_and_validates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeOllamaClient(
        json.dumps({"label": "invoice", "confidence": 0.88})
    )
    monkeypatch.setattr(local_llm_mod, "_get_client", lambda: client)
    ctx = _ctx(tmp_path)
    graph = _graph()

    # Planning is a dry-run pass: it must not call the model.
    plan = build_action_plan(graph, ctx, PolicyEngine.default())
    assert plan.errors == ()
    assert plan.blocked is False
    assert client.calls == []

    result = Executor(ctx).execute(graph)

    assert result.success, result.errors
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "mock-model"
    assert client.calls[0]["format"] == "json"

    llm_outputs = result.node_results["llm"].outputs
    assert llm_outputs["data"] == {"label": "invoice", "confidence": 0.88}
    assert llm_outputs["confidence"] == 0.88

    validate_outputs = result.node_results["validate"].outputs
    assert validate_outputs["valid"] is True
    assert validate_outputs["errors"] == []
    assert validate_outputs["data"] == {
        "label": "invoice",
        "confidence": 0.88,
    }

    # Read-only nodes produce no audit entries.
    assert result.action_logs == []
    assert list(ctx.audit.iter_all()) == []


def test_llm_graph_dry_run_never_calls_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _FakeOllamaClient('{"label": "unused"}')
    monkeypatch.setattr(local_llm_mod, "_get_client", lambda: client)
    ctx = _ctx(tmp_path, dry_run=True)

    result = Executor(ctx).execute(_graph())

    assert result.success, result.errors
    assert client.calls == []
    llm_outputs = result.node_results["llm"].outputs
    assert llm_outputs["data"] is None
    assert llm_outputs["usage"]["dry_run"] is True
    assert result.action_logs == []
