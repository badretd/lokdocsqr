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
    node_id: str, key: str, value: str
) -> list[str]:
    errors: list[str] = []
    normalized = value.replace("\\", "/")
    if ".." in normalized.split("/"):
        errors.append(
            f"node {node_id!r} param {key!r}: contains '..' segment"
        )
    if _looks_absolute(value):
        errors.append(
            f"node {node_id!r} param {key!r}: absolute paths are not allowed"
        )
    try:
        render_template(value, {})
    except ScopeViolation as exc:
        errors.append(
            f"node {node_id!r} param {key!r}: rejected by path policy ({exc})"
        )
    return errors


def validate_graph(document: WorkflowDocument) -> list[str]:
    """Validate a WorkflowDocument and return all discovered errors."""
    errors: list[str] = []

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
                    errors.append(
                        f"edges[{index}] references unknown output port "
                        f"{edge.source_port!r} on node {source_node.id!r}"
                    )

        if target_node is not None:
            input_names = {port.name for port in target_node.inputs}
            if input_names:
                if not edge.target_port:
                    errors.append(
                        f"edges[{index}] has empty target_port for node "
                        f"{target_node.id!r}"
                    )
                elif edge.target_port not in input_names:
                    errors.append(
                        f"edges[{index}] references unknown input port "
                        f"{edge.target_port!r} on node {target_node.id!r}"
                    )

    for node in nodes:
        if not node.id:
            continue
        for key, raw in node.params.items():
            if not _is_path_key(str(key)):
                continue
            for sub_key, value in _iter_string_values(raw, str(key)):
                errors.extend(
                    _validate_path_value(node.id, sub_key, value)
                )

    return errors
