"""Semantic validator for Workflow v1.0 documents."""
from __future__ import annotations

import re
from typing import Any

from ..errors import ScopeViolation
from ..models import WorkflowDocument
from ..paths import render_template

_PATH_KEYS = frozenset(
    {
        "path",
        "source",
        "target",
        "src",
        "dst",
        "dest",
        "destination",
        "folder",
        "directory",
        "dir",
        "template",
        "file",
        "filepath",
        "out",
        "in",
    }
)
_PATH_KEY_SUFFIXES = ("_path", "_template", "_dir", "_folder", "_file")


def _is_path_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _PATH_KEYS:
        return True
    return lowered.endswith(_PATH_KEY_SUFFIXES)


def _looks_absolute(value: str) -> bool:
    if not value:
        return False
    if value.startswith(("/", "\\", "~")):
        return True
    return re.match(r"^[A-Za-z]:[\\/]", value) is not None


def _iter_string_values(value: Any, prefix: str = "") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            label = f"{prefix}.{key}" if prefix else str(key)
            result.extend(_iter_string_values(item, label))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(_iter_string_values(item, f"{prefix}[{index}]"))
    elif isinstance(value, str):
        result.append((prefix, value))
    return result


def _validate_path_value(
    node_id: str,
    key: str,
    value: str,
    *,
    allow_parent_traversal: bool = False,
) -> list[str]:
    errors: list[str] = []
    normalized = value.replace("\\", "/")
    if not allow_parent_traversal and ".." in normalized.split("/"):
        errors.append(
            f"node {node_id!r} param {key!r}: contains '..' segment"
        )
    if _looks_absolute(value):
        errors.append(
            f"node {node_id!r} param {key!r}: absolute paths are not allowed"
        )
    try:
        render_template(
            value,
            {},
            allow_parent_traversal=allow_parent_traversal,
        )
    except ScopeViolation as exc:
        errors.append(
            f"node {node_id!r} param {key!r}: rejected by path policy ({exc})"
        )
    except (TypeError, KeyError, IndexError, AttributeError):
        # The template may contain an indexed/subscripted placeholder such
        # as ``{item[path]}`` that cannot be resolved without runtime data.
        # The static checks above still reject literal absolute paths and
        # ``..`` segments. Runtime rendering re-applies the same policy with
        # the populated context.
        pass
    return errors


def _node_class_port_names(node_type: str, direction: str) -> set[str]:
    """Return port names declared by the registered node class, if any."""
    try:
        from ..node_api.registry import registry

        node_cls = registry.get(node_type)
    except (KeyError, ImportError):
        return set()
    try:
        source = node_cls()
    except Exception:  # noqa: BLE001 - metadata lookup must stay best-effort
        source = node_cls
    ports = getattr(source, direction, None)
    if isinstance(ports, dict):
        return {str(name) for name in ports}
    if isinstance(ports, (list, tuple)):
        names: set[str] = set()
        for item in ports:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))
        return names
    return set()


def _port_warning(
    *,
    node_id: str,
    node_type: str,
    port: str,
    direction: str,
) -> str:
    return (
        f"node {node_id!r}: port {port!r} is declared on node type "
        f"{node_type!r} but not in the document; add "
        f"`{direction} = [{{name = \"{port}\"}}]` to silence"
    )


def validate_graph(
    document: WorkflowDocument, *, warnings: list[str] | None = None
) -> list[str]:
    """Validate a WorkflowDocument and return all discovered errors.

    ``warnings`` is an optional out-parameter. When supplied, non-fatal
    diagnostics are appended to it (for example a port that exists on the
    registered node class but is missing from the document). Warnings never
    cause validation to fail and are intentionally not copied into
    ``NodeSpec``, preserving the round-trip guarantee.
    """
    errors: list[str] = []
    if warnings is None:
        warnings = []

    if document.spec_version != "1.0":
        errors.append(
            f"unsupported spec_version: {document.spec_version!r}"
        )
    if not document.metadata.id:
        errors.append("workflow.metadata.id must be a non-empty string")
    if document.settings.max_concurrency < 1:
        errors.append("settings.max_concurrency must be >= 1")

    nodes = document.nodes
    edges = document.edges
    if not nodes:
        errors.append("graph must contain at least one node")

    node_by_id: dict[str, Any] = {}
    for node in nodes:
        if not isinstance(node.id, str) or not node.id:
            errors.append("found a node with missing or empty 'id'")
            continue
        if node.id in node_by_id:
            errors.append(f"duplicate node id: {node.id!r}")
        else:
            node_by_id[node.id] = node

    edge_ids: set[str] = set()
    for index, edge in enumerate(edges):
        if not edge.id:
            errors.append(f"edges[{index}] has empty id")
        elif edge.id in edge_ids:
            errors.append(f"duplicate edge id: {edge.id!r}")
        else:
            edge_ids.add(edge.id)

        if not isinstance(edge.source, str) or not edge.source:
            errors.append(f"edges[{index}] has no valid 'source'")
        elif edge.source not in node_by_id:
            errors.append(
                f"edges[{index}] references unknown source node "
                f"{edge.source!r}"
            )

        if not isinstance(edge.target, str) or not edge.target:
            errors.append(f"edges[{index}] has no valid 'target'")
        elif edge.target not in node_by_id:
            errors.append(
                f"edges[{index}] references unknown target node "
                f"{edge.target!r}"
            )

        if edge.source == edge.target:
            errors.append(
                f"edges[{index}] is a self-loop on node {edge.source!r}"
            )

    for index, edge in enumerate(edges):
        if getattr(edge, "kind", "data") == "order":
            # Ordering-only edges never carry payload; their ports are
            # irrelevant and must not require document declarations.
            continue

        source_node = node_by_id.get(edge.source)
        target_node = node_by_id.get(edge.target)

        if source_node is not None:
            output_names = {port.name for port in source_node.outputs}
            if output_names:
                if not edge.source_port:
                    errors.append(
                        f"edges[{index}] has empty source_port for node "
                        f"{source_node.id!r}"
                    )
                elif edge.source_port not in output_names:
                    class_ports = _node_class_port_names(
                        source_node.type, "outputs"
                    )
                    if edge.source_port in class_ports:
                        warnings.append(
                            _port_warning(
                                node_id=source_node.id,
                                node_type=source_node.type,
                                port=edge.source_port,
                                direction="outputs",
                            )
                        )
                    else:
                        errors.append(
                            f"edges[{index}] references unknown output port "
                            f"{edge.source_port!r} on node "
                            f"{source_node.id!r}"
                        )
            elif edge.source_port:
                class_ports = _node_class_port_names(
                    source_node.type, "outputs"
                )
                if edge.source_port in class_ports:
                    warnings.append(
                        _port_warning(
                            node_id=source_node.id,
                            node_type=source_node.type,
                            port=edge.source_port,
                            direction="outputs",
                        )
                    )

        if target_node is not None and getattr(edge, "kind", "data") != "order":
            input_names = {port.name for port in target_node.inputs}
            if input_names:
                if not edge.target_port:
                    errors.append(
                        f"edges[{index}] has empty target_port for node "
                        f"{target_node.id!r}"
                    )
                elif edge.target_port not in input_names:
                    class_ports = _node_class_port_names(
                        target_node.type, "inputs"
                    )
                    if edge.target_port in class_ports:
                        warnings.append(
                            _port_warning(
                                node_id=target_node.id,
                                node_type=target_node.type,
                                port=edge.target_port,
                                direction="inputs",
                            )
                        )
                    else:
                        errors.append(
                            f"edges[{index}] references unknown input port "
                            f"{edge.target_port!r} on node "
                            f"{target_node.id!r}"
                        )
            elif edge.target_port:
                class_ports = _node_class_port_names(
                    target_node.type, "inputs"
                )
                if edge.target_port in class_ports:
                    warnings.append(
                        _port_warning(
                            node_id=target_node.id,
                            node_type=target_node.type,
                            port=edge.target_port,
                            direction="inputs",
                        )
                    )

    allow_parent_traversal = bool(
        document.settings.scope.allow_parent_traversal
    )
    for node in nodes:
        if not node.id:
            continue
        for key, raw in node.params.items():
            if not _is_path_key(str(key)):
                continue
            for sub_key, value in _iter_string_values(raw, str(key)):
                errors.extend(
                    _validate_path_value(
                        node.id,
                        sub_key,
                        value,
                        allow_parent_traversal=allow_parent_traversal,
                    )
                )

    return errors
