"""Structural parser for Workflow specification v1.0."""
from __future__ import annotations

from typing import Any

from ..errors import GraphParseError
from ..models import (
    EdgeSpec,
    NodeSpec,
    NodeState,
    PortSpec,
    TriggerSpec,
    VariableSpec,
    WorkflowDocument,
    WorkflowErrorHandling,
    WorkflowMetadata,
    WorkflowScopeSettings,
    WorkflowSettings,
)

_SPEC_VERSION = "1.0"


def _fail(message: str) -> None:
    raise GraphParseError(message)


def _as_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{context} must be an object")
    return value


def _require_str(obj: dict[str, Any], key: str, context: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value:
        _fail(f"{context}.{key} must be a non-empty string")
    return value


def _optional_str(
    obj: dict[str, Any], key: str, context: str, default: str = ""
) -> str:
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        _fail(f"{context}.{key} must be a string")
    return value


def _optional_nullable_str(
    obj: dict[str, Any], key: str, context: str
) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(f"{context}.{key} must be a string or null")
    return value


def _optional_bool(
    obj: dict[str, Any], key: str, context: str, default: bool = False
) -> bool:
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        _fail(f"{context}.{key} must be a boolean")
    return value


def _optional_int(
    obj: dict[str, Any], key: str, context: str, default: int = 0
) -> int:
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(f"{context}.{key} must be an integer")
    return value


def _optional_float(
    obj: dict[str, Any], key: str, context: str, default: float = 0.0
) -> float:
    value = obj.get(key)
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail(f"{context}.{key} must be a number")
    return float(value)


def _optional_dict(
    obj: dict[str, Any], key: str, context: str
) -> dict[str, Any]:
    value = obj.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        _fail(f"{context}.{key} must be an object")
    return value


def _list_of_str(obj: dict[str, Any], key: str, context: str) -> list[str]:
    value = obj.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        _fail(f"{context}.{key} must be a list")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            _fail(f"{context}.{key}[{index}] must be a string")
        result.append(item)
    return result


def _coerce_state(raw: Any, context: str) -> NodeState:
    if raw is None:
        return NodeState.ENABLED
    if isinstance(raw, NodeState):
        return raw
    if not isinstance(raw, str):
        _fail(f"{context}.state must be a string")
    try:
        return NodeState(raw.lower())
    except ValueError:
        _fail(f"{context}.state has unknown value: {raw!r}")


def _parse_metadata(raw: dict[str, Any], context: str) -> WorkflowMetadata:
    return WorkflowMetadata(
        id=_require_str(raw, "id", context),
        name=_optional_str(raw, "name", context, ""),
        description=_optional_str(raw, "description", context, ""),
        version=_optional_str(raw, "version", context, "1.0"),
        author=_optional_str(raw, "author", context, ""),
        created_at=_optional_nullable_str(raw, "created_at", context),
        updated_at=_optional_nullable_str(raw, "updated_at", context),
        tags=_list_of_str(raw, "tags", context),
    )


def _parse_scope_settings(
    raw: dict[str, Any], context: str
) -> WorkflowScopeSettings:
    return WorkflowScopeSettings(
        roots=_list_of_str(raw, "roots", context),
        writable=_list_of_str(raw, "writable", context),
        allow_symlinks=_optional_bool(raw, "allow_symlinks", context, False),
        policy_ref=_optional_nullable_str(raw, "policy_ref", context),
    )


def _parse_error_handling(
    raw: dict[str, Any], context: str
) -> WorkflowErrorHandling:
    return WorkflowErrorHandling(
        strategy=_optional_str(raw, "strategy", context, "fail"),
        max_retries=_optional_int(raw, "max_retries", context, 0),
        retry_delay_seconds=_optional_float(
            raw, "retry_delay_seconds", context, 0.0
        ),
        backoff=_optional_str(raw, "backoff", context, "none"),
    )


def _parse_settings(raw: dict[str, Any], context: str) -> WorkflowSettings:
    scope_raw = _optional_dict(raw, "scope", context)
    error_raw = _optional_dict(raw, "error_handling", context)
    return WorkflowSettings(
        default_dry_run=_optional_bool(raw, "default_dry_run", context, True),
        scope=_parse_scope_settings(scope_raw, f"{context}.scope"),
        error_handling=_parse_error_handling(
            error_raw, f"{context}.error_handling"
        ),
        max_concurrency=_optional_int(raw, "max_concurrency", context, 1),
    )


def _parse_variable(raw: Any, context: str) -> VariableSpec:
    obj = _as_dict(raw, context)
    return VariableSpec(
        type=_optional_str(obj, "type", context, "string"),
        value=obj.get("value"),
        description=_optional_str(obj, "description", context, ""),
    )


def _parse_trigger(raw: Any, index: int) -> TriggerSpec:
    context = f"triggers[{index}]"
    obj = _as_dict(raw, context)
    return TriggerSpec(
        id=_require_str(obj, "id", context),
        type=_require_str(obj, "type", context),
        title=_optional_str(obj, "title", context, ""),
        params=_optional_dict(obj, "params", context),
    )


def _parse_port(raw: Any, context: str) -> PortSpec:
    if isinstance(raw, str):
        if not raw:
            _fail(f"{context} must be a non-empty string")
        return PortSpec(name=raw)
    obj = _as_dict(raw, context)
    return PortSpec(
        name=_require_str(obj, "name", context),
        type=_optional_str(obj, "type", context, "any"),
        required=_optional_bool(obj, "required", context, True),
        description=_optional_str(obj, "description", context, ""),
        default=obj.get("default"),
    )


def _parse_ports(raw_ports: Any, context: str) -> list[PortSpec]:
    if raw_ports is None:
        return []
    if isinstance(raw_ports, (str, dict)):
        return [_parse_port(raw_ports, context)]
    if isinstance(raw_ports, list):
        return [
            _parse_port(item, f"{context}[{index}]")
            for index, item in enumerate(raw_ports)
        ]
    _fail(f"{context} must be a port, port object, or list")


def _parse_node(raw: Any, index: int) -> NodeSpec:
    context = f"nodes[{index}]"
    obj = _as_dict(raw, context)
    position_raw = obj.get("position", {})
    if position_raw is None:
        position_raw = {}
    position_obj = _as_dict(position_raw, f"{context}.position")
    position: dict[str, float] = {}
    for key, value in position_obj.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
        ):
            _fail(f"{context}.position.{key} must be a number")
        position[key] = float(value)
    return NodeSpec(
        id=_require_str(obj, "id", context),
        type=_require_str(obj, "type", context),
        title=_optional_str(obj, "title", context, ""),
        state=_coerce_state(obj.get("state"), context),
        params=_optional_dict(obj, "params", context),
        inputs=_parse_ports(obj.get("inputs"), f"{context}.inputs"),
        outputs=_parse_ports(obj.get("outputs"), f"{context}.outputs"),
        position=position,
        description=_optional_str(obj, "description", context, ""),
    )


def _parse_edge(raw: Any, index: int) -> EdgeSpec:
    context = f"edges[{index}]"
    obj = _as_dict(raw, context)
    source = _require_str(obj, "source", context)
    target = _require_str(obj, "target", context)
    raw_id = obj.get("id")
    if raw_id is None:
        edge_id = f"{source}->{target}"
    else:
        if not isinstance(raw_id, str) or not raw_id:
            _fail(f"{context}.id must be a non-empty string")
        edge_id = raw_id
    source_port = _optional_str(obj, "source_port", context, "out")
    target_port = _optional_str(obj, "target_port", context, "in")
    if not source_port:
        _fail(f"{context}.source_port must be non-empty")
    if not target_port:
        _fail(f"{context}.target_port must be non-empty")
    return EdgeSpec(
        id=edge_id,
        source=source,
        target=target,
        source_port=source_port,
        target_port=target_port,
        condition=_optional_nullable_str(obj, "condition", context),
        label=_optional_str(obj, "label", context, ""),
    )


def parse_graph(data: dict[str, Any]) -> WorkflowDocument:
    """Parse a JSON-decoded workflow dictionary into WorkflowDocument."""
    if not isinstance(data, dict):
        _fail(f"workflow root must be an object, got {type(data).__name__}")

    spec_version = data.get("spec_version")
    if spec_version != _SPEC_VERSION:
        _fail(
            "unsupported spec_version: "
            f"{spec_version!r}; expected {_SPEC_VERSION!r}"
        )

    metadata_raw = data.get("metadata")
    if metadata_raw is None:
        _fail("'metadata' is required")
    metadata = _parse_metadata(_as_dict(metadata_raw, "metadata"), "metadata")

    settings_raw = data.get("settings", {})
    if settings_raw is None:
        settings_raw = {}
    settings = _parse_settings(_as_dict(settings_raw, "settings"), "settings")

    variables_raw = data.get("variables", {})
    if variables_raw is None:
        variables_raw = {}
    variables_obj = _as_dict(variables_raw, "variables")
    variables: dict[str, VariableSpec] = {}
    for key, value in variables_obj.items():
        if not isinstance(key, str):
            _fail("variables keys must be strings")
        variables[key] = _parse_variable(value, f"variables[{key!r}]")

    triggers_raw = data.get("triggers", [])
    if triggers_raw is None:
        triggers_raw = []
    if not isinstance(triggers_raw, list):
        _fail("'triggers' must be a list")
    triggers = [
        _parse_trigger(item, index)
        for index, item in enumerate(triggers_raw)
    ]

    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, list):
        _fail("'nodes' must be a list")
    nodes = [
        _parse_node(item, index)
        for index, item in enumerate(nodes_raw)
    ]

    edges_raw = data.get("edges")
    if not isinstance(edges_raw, list):
        _fail("'edges' must be a list")
    edges = [
        _parse_edge(item, index)
        for index, item in enumerate(edges_raw)
    ]

    extra_raw = data.get("extra_meta", {})
    if extra_raw is None:
        extra_raw = {}
    extra_meta = dict(_as_dict(extra_raw, "extra_meta"))
    known = {
        "spec_version",
        "metadata",
        "settings",
        "variables",
        "triggers",
        "nodes",
        "edges",
        "extra_meta",
    }
    for key, value in data.items():
        if key not in known:
            extra_meta.setdefault(key, value)

    return WorkflowDocument(
        spec_version=spec_version,
        metadata=metadata,
        settings=settings,
        variables=variables,
        triggers=triggers,
        nodes=nodes,
        edges=edges,
        extra_meta=extra_meta,
    )
