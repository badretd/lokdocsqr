"""Backwards-compatible normalization for pre-v1.0 automation documents.

The canonical schema (see ``docs/SCHEMA.md``) requires ``spec_version``,
``metadata``, ``nodes`` and ``edges``. Older automations produced by early
phases of this project used a shorter, flatter shape:

```json
{"id": "demo", "title": "Demo", "nodes": [...], "edges": [...]}
```

and legacy node type ids such as ``list_files`` or ``move_files``.

This module is deliberately *not* imported by :mod:`lokdocsqr.core.graph`.
``parse_graph`` remains strict so library callers keep the documented
contract. The CLI opts into this compatibility layer at its input boundary
so a user can still run an older file with the shipped binary.

The normalization is intentionally conservative: it only fills in missing
top-level defaults and renames known legacy ids/keys. Everything else is
left for the strict parser/validator to accept or reject with a precise
error.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

__all__ = ["LEGACY_NODE_TYPES", "normalize_document_data"]

#: Old bare node ids -> current registered node ids.
LEGACY_NODE_TYPES: dict[str, str] = {
    "archive": "fs.archive",
    "convert": "fs.convert",
    "copy": "fs.copy",
    "copy_file": "fs.copy",
    "copy_files": "fs.copy",
    "delete": "fs.trash",
    "delete_files": "fs.trash",
    "extract_text": "access.extract_text",
    "hash_dedupe": "access.hash_dedupe",
    "index_search": "access.index_search",
    "list_files": "access.list_files",
    "make_dir": "fs.make_dir",
    "move": "fs.move",
    "move_file": "fs.move",
    "move_files": "fs.move",
    "ocr": "access.ocr",
    "read_content": "access.read_content",
    "read_metadata": "access.read_metadata",
    "rename": "fs.rename",
    "rename_file": "fs.rename",
    "rename_files": "fs.rename",
    "tag": "fs.tag",
    "trash": "fs.trash",
    "trash_files": "fs.trash",
    "write": "fs.write_file",
    "write_file": "fs.write_file",
    "if_branch": "flow.if_branch",
    "filter": "flow.filter",
    "loop_batch": "flow.loop_batch",
    "merge": "flow.merge",
    "wait": "flow.wait",
    "retry": "flow.retry",
    "subflow": "flow.subflow",
    "json_validate": "utils.json_validate",
    "notify": "utils.notify",
    "path_template": "utils.path_template",
    "set_variables": "utils.set_variables",
    "local_llm": "llm.local_llm",
    "watch_folder": "triggers.watch_folder",
    "scheduled_scan": "triggers.scheduled_scan",
    "manual_run": "triggers.manual_run",
    "webhook": "triggers.webhook",
}

#: Old parameter names accepted by the file-based compatibility layer.
_LEGACY_PARAMS: dict[str, dict[str, str]] = {
    "fs.copy": {
        "src": "source",
        "source_path": "source",
        "dest": "template",
        "destination": "template",
        "target": "template",
        "dest_path": "template",
    },
    "fs.move": {
        "src": "source",
        "source_path": "source",
        "dest": "template",
        "destination": "template",
        "target": "template",
        "dest_path": "template",
    },
    "fs.rename": {
        "src": "source",
        "source_path": "source",
        "dest": "template",
        "destination": "template",
        "new_name": "template",
        "target": "template",
    },
    "fs.make_dir": {
        "path": "path_template",
        "dir": "path_template",
    },
    "fs.write_file": {
        "path": "path_template",
        "dest": "path_template",
        "destination": "path_template",
        "text": "content",
        "body": "content",
    },
    "fs.trash": {
        "file": "path",
        "files": "path",
        "target": "path",
    },
    "access.list_files": {
        "folder": "path",
        "directory": "path",
        "glob": "pattern",
    },
}


def _first_str(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _normalise_spec_version(data: dict[str, Any]) -> None:
    value = data.get("spec_version")
    if value is None or (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float(value) == 1.0
    ):
        data["spec_version"] = "1.0"


def _normalise_metadata(data: dict[str, Any], default_id: str | None) -> None:
    raw = data.get("metadata")
    metadata: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}

    if not metadata.get("id"):
        legacy_id = _first_str(data, "id", "automation_id", "automationId")
        if legacy_id is not None:
            metadata["id"] = legacy_id
        elif default_id is not None:
            metadata["id"] = default_id

    if not metadata.get("name"):
        legacy_name = _first_str(data, "title", "name")
        if legacy_name is not None:
            metadata["name"] = legacy_name

    if not metadata.get("description"):
        legacy_description = _first_str(data, "description")
        if legacy_description is not None:
            metadata["description"] = legacy_description

    if not metadata.get("version"):
        legacy_version = _first_str(data, "version")
        if legacy_version is not None:
            metadata["version"] = legacy_version

    data["metadata"] = metadata


def _normalise_edge(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    edge = dict(raw)
    if "source" not in edge:
        for key in ("from", "source_id", "sourceId"):
            if key in edge:
                edge["source"] = edge[key]
                break
    if "target" not in edge:
        for key in ("to", "target_id", "targetId"):
            if key in edge:
                edge["target"] = edge[key]
                break
    return edge


def _normalise_node(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    node = dict(raw)

    node_type = node.get("type")
    if isinstance(node_type, str) and node_type in LEGACY_NODE_TYPES:
        node_type = LEGACY_NODE_TYPES[node_type]
        node["type"] = node_type

    params = node.get("params")
    if isinstance(params, dict) and isinstance(node_type, str):
        aliases = _LEGACY_PARAMS.get(node_type)
        if aliases:
            params = dict(params)
            for old, new in aliases.items():
                if new not in params and old in params:
                    params[new] = params[old]
            node["params"] = params

    return node


def normalize_document_data(
    data: dict[str, Any],
    *,
    default_id: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``data`` normalized toward the v1.0 schema.

    ``default_id`` is used when neither ``metadata.id`` nor a legacy
    top-level id exists (the CLI passes the automation file stem).
    """

    if not isinstance(data, dict):
        return data  # let the strict parser report the exact type error

    normalized = deepcopy(data)
    _normalise_spec_version(normalized)
    _normalise_metadata(normalized, default_id)

    nodes = normalized.get("nodes")
    if isinstance(nodes, list):
        normalized["nodes"] = [_normalise_node(item) for item in nodes]

    if normalized.get("edges") is None:
        normalized["edges"] = []
    elif isinstance(normalized["edges"], list):
        normalized["edges"] = [
            _normalise_edge(item) for item in normalized["edges"]
        ]

    if normalized.get("triggers") is None:
        normalized["triggers"] = []

    return normalized
