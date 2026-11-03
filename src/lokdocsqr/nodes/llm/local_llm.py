"""llm.local_llm — local LLM node for classification, extraction, summarization
and plan generation.

Node id: ``llm.local_llm``. Policy operation: ``read``.

The node is a thin, deterministic adapter between a workflow graph and a local
Ollama model:

* it never touches the filesystem;
* it never imports ``ollama`` at module import time (R7/R20) — the optional
  dependency is resolved lazily inside :func:`_get_client`;
* it always asks the model for a single JSON document (``format="json"``);
* it returns the parsed document, the raw model text, a confidence value and
  token usage.

Payload contract::

    {
        "text": str,               # required input text
        "task": str,               # classify | extract | summarize | plan
        "schema": dict | str,      # optional JSON Schema used in the prompt
        "model": str,              # optional model override
        "system_prompt": str,      # optional system message override
        "prompt": str,             # optional instruction override
        "options": dict,           # optional Ollama options
    }

Output contract (all keys always present, both run and dry-run)::

    {
        "data": Any,               # parsed JSON returned by the model
        "text": str | None,        # raw model text
        "confidence": float | None,
        "usage": dict,             # model + token counters
    }

Schema validation is deliberately delegated to the ``utils.json_validate``
node: this node shapes the request and returns ``data``; the graph connects
``data`` to ``utils.json_validate.data`` and the validation node reports
``valid`` / ``errors``. ``dry_run`` never constructs an Ollama client and never
calls the network.

Tests use an injected/faked client; the real Ollama client is never used in the
test suite.
"""
from __future__ import annotations

import importlib.util
import json
from typing import Any

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.decorators import register_node

__all__ = ["LocalLlmNode"]

_DEFAULT_MODEL = "llama3.2"
_DEFAULT_TASK = "summarize"
_TASKS = ("classify", "extract", "summarize", "plan")

_INSTALL_HINT = (
    "local_llm: ollama is not installed — install the 'ollama' package"
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a local file-automation assistant. "
    "Reply with exactly one JSON object and nothing else."
)

_TASK_INSTRUCTIONS: dict[str, str] = {
    "classify": (
        "Classify the input text. Return a JSON object with 'label' "
        "(string), 'confidence' (number from 0 to 1) and an optional "
        "'reason' (string)."
    ),
    "extract": (
        "Extract the requested fields from the input text. Return a JSON "
        "object containing the extracted fields and a 'confidence' number "
        "from 0 to 1."
    ),
    "summarize": (
        "Summarize the input text. Return a JSON object with 'summary' "
        "(string) and 'confidence' (number from 0 to 1)."
    ),
    "plan": (
        "Propose filesystem actions for the input text. Return a JSON object "
        "with 'actions' (an array of objects with 'type', 'source', 'target', "
        "'reason' and 'confidence') and a top-level 'confidence' number from "
        "0 to 1."
    ),
}


try:  # R7: availability probe only — never an actual import at module load.
    _OLLAMA_AVAILABLE: bool = importlib.util.find_spec("ollama") is not None
except (ImportError, ValueError):  # pragma: no cover — hostile environments
    _OLLAMA_AVAILABLE = False


def _get_client() -> Any:
    """Lazily import ``ollama`` and construct a client.

    The import is intentionally inside this function so that importing the node
    (and the whole ``lokdocsqr.nodes`` package) works without the optional
    dependency installed.
    """
    try:
        import ollama
    except ImportError as exc:
        raise NodeExecutionError(_INSTALL_HINT) from exc
    return ollama.Client()


def _response_field(response: Any, name: str) -> Any:
    """Read ``name`` from an Ollama response dict or object."""
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def _message_content(response: Any) -> str:
    """Extract the assistant text from a chat/generate response."""
    message = _response_field(response, "message")
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = getattr(message, "content", None)

    if isinstance(content, dict):
        return json.dumps(content, sort_keys=True)
    if isinstance(content, str) and content.strip():
        return content

    fallback = _response_field(response, "response")
    if isinstance(fallback, str) and fallback.strip():
        return fallback
    if isinstance(response, str) and response.strip():
        return response
    raise NodeExecutionError("local_llm: response has no content")


def _strip_code_fence(text: str) -> str:
    """Accept fenced JSON even though ``format='json'`` normally prevents it."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json(content: str) -> Any:
    """Parse model output as JSON, failing closed on malformed output."""
    try:
        return json.loads(_strip_code_fence(content))
    except (TypeError, ValueError) as exc:
        raise NodeExecutionError(
            f"local_llm: model returned output that is not valid JSON: {exc}"
        ) from exc


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _usage(response: Any, model: str) -> dict[str, Any]:
    """Return a stable usage mapping for chat/generate responses."""
    prompt_tokens = _as_int(_response_field(response, "prompt_eval_count"))
    completion_tokens = _as_int(_response_field(response, "eval_count"))

    usage: dict[str, Any] = {
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    if prompt_tokens is not None and completion_tokens is not None:
        usage["total_tokens"] = prompt_tokens + completion_tokens

    total_duration = _as_int(_response_field(response, "total_duration"))
    if total_duration is not None:
        usage["total_duration_ns"] = total_duration
    return usage


def _extract_confidence(data: Any) -> float | None:
    """Read a confidence value from the structured document, if present."""
    if not isinstance(data, dict):
        return None
    for key in ("confidence", "score"):
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _normalize_schema(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NodeExecutionError(
                f"local_llm: schema is not valid JSON: {exc}"
            ) from exc
    if not isinstance(raw, dict):
        raise NodeExecutionError("local_llm: schema must be a JSON object")
    return raw


def _normalize_options(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise NodeExecutionError("local_llm: options must be a JSON object")
    return dict(raw)


def _input_text(payload: dict[str, Any]) -> str:
    for key in ("text", "input", "content", "document"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    raise NodeExecutionError("local_llm: no input text provided")


def _resolve_task(raw: Any, default: str) -> str:
    task = str(raw if raw is not None else default).strip().lower()
    if task not in _TASKS:
        raise NodeExecutionError(
            f"local_llm: unsupported task {task!r}; expected one of "
            f"{list(_TASKS)}"
        )
    return task


def _build_messages(
    *,
    task: str,
    text: str,
    schema: dict[str, Any] | None,
    system_prompt: str | None,
    prompt: str | None,
) -> list[dict[str, str]]:
    """Build the chat messages sent to the model."""
    instruction = (
        prompt
        if isinstance(prompt, str) and prompt.strip()
        else _TASK_INSTRUCTIONS[task]
    )
    if schema is not None:
        instruction += (
            "\nReturn a JSON value that validates against this JSON Schema:\n"
            + json.dumps(schema, ensure_ascii=False, sort_keys=True)
        )

    return [
        {
            "role": "system",
            "content": (
                system_prompt
                if isinstance(system_prompt, str) and system_prompt.strip()
                else _DEFAULT_SYSTEM_PROMPT
            ),
        },
        {
            "role": "user",
            "content": f"{instruction}\n\nInput:\n{text}",
        },
    ]


@register_node("llm.local_llm")
class LocalLlmNode(Node):
    """Read-only node that turns text into structured JSON via a local model."""

    id = "llm.local_llm"
    retry_safe = True

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        task: str = _DEFAULT_TASK,
        client: Any = None,
    ) -> None:
        self.model = model
        self.task = task
        self.client = client

    @property
    def title(self) -> str:
        return "Local LLM"

    @property
    def inputs(self) -> dict[str, str]:
        return {
            "text": "string",
            "task": "string",
            "schema": "dict",
            "model": "string",
            "system_prompt": "string",
            "prompt": "string",
            "options": "object",
        }

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "data": "any",
            "text": "string",
            "confidence": "number",
            "usage": "object",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._execute(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._preview(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise NodeExecutionError("local_llm: payload must be a dict")

        text = _input_text(payload)
        task = _resolve_task(payload.get("task"), self.task)
        schema = _normalize_schema(payload.get("schema"))
        options = _normalize_options(payload.get("options"))

        model = payload.get("model", self.model)
        if not isinstance(model, str) or not model.strip():
            raise NodeExecutionError("local_llm: model must be a non-empty string")
        model = model.strip()

        messages = _build_messages(
            task=task,
            text=text,
            schema=schema,
            system_prompt=payload.get("system_prompt"),
            prompt=payload.get("prompt"),
        )

        client = self.client if self.client is not None else _get_client()
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": "json",
        }
        if options:
            request["options"] = options

        try:
            response = client.chat(**request)
        except NodeExecutionError:
            raise
        except Exception as exc:
            raise NodeExecutionError(
                f"local_llm: ollama request failed: {exc}"
            ) from exc

        content = _message_content(response)
        data = _parse_json(content)
        return {
            "data": data,
            "text": content,
            "confidence": _extract_confidence(data),
            "usage": _usage(response, model),
        }

    def _preview(self, ctx: Any, payload: Any) -> dict[str, Any]:
        """Return the declared output shape without touching Ollama."""
        values = payload if isinstance(payload, dict) else {}
        model = values.get("model", self.model)
        task = values.get("task", self.task)
        return {
            "data": None,
            "text": None,
            "confidence": None,
            "usage": {
                "model": str(model),
                "task": str(task),
                "dry_run": True,
            },
            "dry_run": True,
        }
