"""Automation DSL serializer + schema serialization.

Two responsibilities live here:

* :func:`document_to_dict` — turn a :class:`WorkflowDocument` into the raw
  JSON automation-schema dictionary. It is the inverse of
  :func:`lokdocsqr.core.graph.parser.parse_graph`.
* :func:`dsl_from_dict` — render that dictionary as canonical DSL text.

Round-trip guarantees (pinned by ``tests/test_phase_10_5.py``):

* ``parse_graph(document_to_dict(doc)) == doc``.
* ``dsl_to_dict(dsl_from_dict(d)) == d`` for canonical dicts, so
  ``emit_dsl(parse_dsl(text)) == text`` for canonical text.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from ..models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PortSpec,
    TriggerSpec,
    VariableSpec,
    WorkflowDocument,
)

__all__ = ["document_to_dict", "dsl_from_dict"]

_WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")


def _fmt_key(key: str) -> str:
    """Render a map key, bare when it is a valid word, otherwise quoted."""
    if _WORD_RE.match(key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _fmt_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fmt_value(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _fmt_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"cannot represent non-finite number {value!r}")
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_fmt_value(item) for item in value) + "]"
    if isinstance(value, dict):
        items = [f"{_fmt_key(str(key))} = {_fmt_value(item)}" for key, item in value.items()]
        return "{" + ", ".join(items) + "}"
    raise TypeError(f"cannot serialize value of type {type(value).__name__}")


def _emit_entry(lines: list[str], indent: str, key: str, value: Any, *, block: bool) -> None:
    """Emit one ``key = value`` line, or a ``key { ... }`` block when block."""
    if not block:
        lines.append(f"{indent}{_fmt_key(key)} = {_fmt_value(value)}")
        return
    mapping = value
    assert isinstance(mapping, dict)
    if not mapping:
        lines.append(f"{indent}{_fmt_key(key)} {{}}")
        return
    lines.append(f"{indent}{_fmt_key(key)} {{")
    for child_key, child_value in mapping.items():
        lines.append(f"{indent}  {_fmt_key(str(child_key))} = {_fmt_value(child_value)}")
    lines.append(f"{indent}}}")


# -- document -> dict -----------------------------------------------------


def _ports_to_json(ports: list[PortSpec]) -> list[Any]:
    result: list[Any] = []
    for port in ports:
        if (
            port.type == "any"
            and port.required is True
            and not port.description
            and port.default is None
        ):
            result.append(port.name)
            continue
        entry: dict[str, Any] = {"name": port.name}
        if port.type != "any":
            entry["type"] = port.type
        if port.required is not True:
            entry["required"] = port.required
        if port.description:
            entry["description"] = port.description
        if port.default is not None:
            entry["default"] = port.default
        result.append(entry)
    return result


def _node_to_dict(node: NodeSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": node.id, "type": node.type}
    if node.title:
        entry["title"] = node.title
    if node.state != NodeState.ENABLED:
        entry["state"] = node.state.value
    if node.params:
        entry["params"] = dict(node.params)
    if node.inputs:
        entry["inputs"] = _ports_to_json(node.inputs)
    if node.outputs:
        entry["outputs"] = _ports_to_json(node.outputs)
    if node.position:
        entry["position"] = dict(node.position)
    if node.description:
        entry["description"] = node.description
    return entry


def _edge_to_dict(edge: EdgeSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": edge.id,
        "source": edge.source,
        "target": edge.target,
    }
    if edge.source_port != "out":
        entry["source_port"] = edge.source_port
    if edge.target_port != "in":
        entry["target_port"] = edge.target_port
    if edge.label:
        entry["label"] = edge.label
    if edge.kind != "data":
        entry["kind"] = edge.kind
    return entry


def _trigger_to_dict(trigger: TriggerSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": trigger.id, "type": trigger.type}
    if trigger.title:
        entry["title"] = trigger.title
    if trigger.params:
        entry["params"] = dict(trigger.params)
    return entry


def _variable_to_dict(variable: VariableSpec) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    if variable.type != "string":
        entry["type"] = variable.type
    if variable.value is not None:
        entry["value"] = variable.value
    if variable.description:
        entry["description"] = variable.description
    return entry


def _metadata_to_dict(metadata: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": metadata.id}
    if metadata.name:
        entry["name"] = metadata.name
    if metadata.description:
        entry["description"] = metadata.description
    if metadata.version != "1.0":
        entry["version"] = metadata.version
    if metadata.author:
        entry["author"] = metadata.author
    if metadata.created_at is not None:
        entry["created_at"] = metadata.created_at
    if metadata.updated_at is not None:
        entry["updated_at"] = metadata.updated_at
    if metadata.tags:
        entry["tags"] = list(metadata.tags)
    return entry


def _settings_to_dict(settings: Any) -> dict[str, Any] | None:
    entry: dict[str, Any] = {}
    scope: dict[str, Any] = {}
    if settings.scope.roots:
        scope["roots"] = list(settings.scope.roots)
    if settings.scope.writable:
        scope["writable"] = list(settings.scope.writable)
    if settings.scope.allow_symlinks:
        scope["allow_symlinks"] = True
    if settings.scope.policy_ref is not None:
        scope["policy_ref"] = settings.scope.policy_ref
    if settings.scope.allow_parent_traversal:
        scope["allow_parent_traversal"] = True

    handling: dict[str, Any] = {}
    if settings.error_handling.strategy != "fail":
        handling["strategy"] = settings.error_handling.strategy
    if settings.error_handling.max_retries:
        handling["max_retries"] = settings.error_handling.max_retries
    if settings.error_handling.retry_delay_seconds:
        handling["retry_delay_seconds"] = settings.error_handling.retry_delay_seconds
    if settings.error_handling.backoff != "none":
        handling["backoff"] = settings.error_handling.backoff
    if getattr(settings, "strict_retry_safe", False):
        handling["strict_retry_safe"] = True

    if settings.default_dry_run is False:
        entry["default_dry_run"] = False
    if scope:
        entry["scope"] = scope
    if handling:
        entry["error_handling"] = handling
    if settings.max_concurrency != 1:
        entry["max_concurrency"] = settings.max_concurrency
    return entry or None


def document_to_dict(document: WorkflowDocument) -> dict[str, Any]:
    """Return the raw JSON automation-schema dictionary for ``document``.

    Only non-default fields are emitted; :func:`parse_graph` restores the
    defaults, making this the exact inverse of the JSON parser.
    """
    raw: dict[str, Any] = {
        "spec_version": document.spec_version,
        "metadata": _metadata_to_dict(document.metadata),
        "nodes": [_node_to_dict(node) for node in document.nodes],
        "edges": [_edge_to_dict(edge) for edge in document.edges],
    }
    settings = _settings_to_dict(document.settings)
    if settings is not None:
        raw["settings"] = settings
    if document.variables:
        raw["variables"] = {
            name: _variable_to_dict(spec) for name, spec in sorted(document.variables.items())
        }
    if document.triggers:
        raw["triggers"] = [_trigger_to_dict(trigger) for trigger in document.triggers]
    if document.extra_meta:
        raw["extra_meta"] = dict(document.extra_meta)
    return raw


# -- dict -> DSL text -----------------------------------------------------


def dsl_from_dict(raw: dict[str, Any]) -> str:
    """Render a raw schema dictionary as canonical DSL text."""
    lines: list[str] = []
    metadata = raw.get("metadata") or {}
    automation_id = metadata.get("id", "")
    lines.append(f"automation {_fmt_string(str(automation_id))}")

    if raw.get("spec_version", "1.0") != "1.0":
        lines.append(f"spec_version = {_fmt_string(str(raw['spec_version']))}")

    for key in ("name", "description", "author", "created_at", "updated_at"):
        if metadata.get(key):
            lines.append(f"{key} = {_fmt_value(metadata[key])}")
    if metadata.get("version", "1.0") != "1.0":
        lines.append(f"version = {_fmt_value(metadata['version'])}")
    if metadata.get("tags"):
        lines.append(f"tags = {_fmt_value(metadata['tags'])}")

    settings = raw.get("settings") or {}
    if settings.get("default_dry_run") is False:
        lines.append("default_dry_run = false")
    if settings.get("max_concurrency", 1) != 1:
        lines.append(f"max_concurrency = {_fmt_value(settings['max_concurrency'])}")
    if settings.get("scope"):
        _emit_entry(lines, "", "scope", settings["scope"], block=True)
    if settings.get("error_handling"):
        _emit_entry(lines, "", "error_handling", settings["error_handling"], block=True)

    variables = raw.get("variables") or {}
    for name in sorted(variables):
        spec = variables[name] or {}
        if (
            "value" not in spec
            and "description" not in spec
            and spec.get("type", "string") == "string"
        ):
            lines.append(f"variable {_fmt_string(str(name))} {{}}")
            continue
        lines.append(f"variable {_fmt_string(str(name))} {{")
        if spec.get("type", "string") != "string":
            lines.append(f"  type = {_fmt_value(spec['type'])}")
        if spec.get("value") is not None:
            lines.append(f"  value = {_fmt_value(spec['value'])}")
        if spec.get("description"):
            lines.append(f"  description = {_fmt_value(spec['description'])}")
        lines.append("}")

    for trigger in raw.get("triggers") or []:
        _emit_trigger(lines, trigger)

    for node in raw.get("nodes") or []:
        _emit_node(lines, node)

    for edge in raw.get("edges") or []:
        _emit_edge(lines, edge)

    if raw.get("extra_meta"):
        _emit_entry(lines, "", "extra_meta", raw["extra_meta"], block=True)

    return "\n".join(lines) + "\n"


def _emit_trigger(lines: list[str], trigger: dict[str, Any]) -> None:
    lines.append(f"trigger {_fmt_string(str(trigger.get('id', '')))} {{")
    lines.append(f"  type = {_fmt_value(trigger.get('type', ''))}")
    if trigger.get("title"):
        lines.append(f"  title = {_fmt_value(trigger['title'])}")
    if trigger.get("params"):
        _emit_entry(lines, "  ", "params", trigger["params"], block=True)
    lines.append("}")


def _emit_node(lines: list[str], node: dict[str, Any]) -> None:
    lines.append(f"node {_fmt_string(str(node.get('id', '')))} {{")
    lines.append(f"  type = {_fmt_value(node.get('type', ''))}")
    if node.get("title"):
        lines.append(f"  title = {_fmt_value(node['title'])}")
    if node.get("state", "enabled") != "enabled":
        lines.append(f"  state = {_fmt_value(node['state'])}")
    if node.get("description"):
        lines.append(f"  description = {_fmt_value(node['description'])}")
    if node.get("params"):
        _emit_entry(lines, "  ", "params", node["params"], block=True)
    if node.get("inputs"):
        lines.append(f"  inputs = {_fmt_value(node['inputs'])}")
    if node.get("outputs"):
        lines.append(f"  outputs = {_fmt_value(node['outputs'])}")
    if node.get("position"):
        lines.append(f"  position = {_fmt_value(node['position'])}")
    lines.append("}")


def _emit_edge(lines: list[str], edge: dict[str, Any]) -> None:
    lines.append(f"edge {_fmt_string(str(edge.get('id', '')))} {{")
    lines.append(f"  source = {_fmt_value(edge.get('source', ''))}")
    lines.append(f"  target = {_fmt_value(edge.get('target', ''))}")
    if edge.get("source_port", "out") != "out":
        lines.append(f"  source_port = {_fmt_value(edge['source_port'])}")
    if edge.get("target_port", "in") != "in":
        lines.append(f"  target_port = {_fmt_value(edge['target_port'])}")
    if edge.get("kind", "data") != "data":
        lines.append(f"  kind = {_fmt_value(edge['kind'])}")
    if edge.get("label"):
        lines.append(f"  label = {_fmt_value(edge['label'])}")
    lines.append("}")
