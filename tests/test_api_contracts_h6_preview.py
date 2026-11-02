"""H6 — every non-fail-closed node must return all declared output ports
from dry_run.

Two invariants this file respects:

1. ``FsNode._resolve`` rejects absolute templates ("Rendered template is
   absolute"). All payload paths below are therefore RELATIVE. Scope
   resolution turns e.g. ``"x.txt"`` into ``tmp_path/x.txt``.

2. The project-level leak detector flags any file remaining in CWD at
   pytest teardown. ``monkeypatch.chdir`` is insufficient because
   teardown happens *after* the detector runs. We therefore ``os.chdir``
   into ``tmp_path`` in a ``try`` and restore in ``finally`` — CWD is
   back at the project root before this function returns.
"""
from __future__ import annotations

import os

import pytest

from lokdocsqr.core.models import NodeResult
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.plugins import discover_plugins


def _non_fail_closed_ids() -> list[str]:
    try:
        discover_plugins()
    except Exception:
        pass
    ids: list[str] = []
    try:
        metadata = registry.list()
    except Exception:
        metadata = []
    for item in metadata:
        node_id = item.get("id")
        if not isinstance(node_id, str):
            continue
        try:
            cls = registry.get(node_id)
        except Exception:
            continue
        if not getattr(cls, "_preview_fail_closed", False):
            ids.append(node_id)
    return sorted(ids)


_H6_MINIMUM_IDS = [
    "utils.path_template",
    "utils.set_variables",
    "utils.json_validate",
    "flow.filter",
    "flow.if_branch",
    "flow.merge",
    "flow.retry",
    "flow.wait",
    "fs.make_dir",
    "fs.write_file",
    "fs.tag",
    "access.list_files",
]

_H6_IDS = _non_fail_closed_ids() or _H6_MINIMUM_IDS


_SPECIFIC_PAYLOADS = {
    "access.extract_text":      lambda t: {"paths": ["x.txt"]},
    "access.hash_dedupe":       lambda t: {"paths": ["x.txt"]},
    "access.index_search":      lambda t: {"query": "x", "paths": ["x.txt"]},
    "access.list_files":        lambda t: {"path": "."},
    "access.ocr":               lambda t: {"paths": ["x.txt"]},
    "access.read_content":      lambda t: {"paths": ["x.txt"]},
    "access.read_metadata":     lambda t: {"paths": ["x.txt"]},
    "flow.filter":              lambda t: {"items": [], "condition": "True"},
    "flow.if_branch":           lambda t: {"condition": "True"},
    "flow.merge":               lambda t: {"items": []},
    "flow.retry":               lambda t: {"on_error": False, "retry_policy": None},
    "flow.wait":                lambda t: {"seconds": 0},
    "fs.archive":               lambda t: {"sources": ["x.txt"], "target": "out.zip"},
    "fs.convert":               lambda t: {"source": "x.csv", "target": "out.json"},
    "fs.copy":                  lambda t: {"source": "x.txt", "target": "cp.txt"},
    "fs.make_dir":              lambda t: {"path_template": "d2"},
    "fs.move":                  lambda t: {"source": "x.txt", "target": "mv.txt"},
    "fs.rename":                lambda t: {"source": "x.txt", "target": "rn.txt"},
    "fs.tag":                   lambda t: {"path": "x.txt", "tags": ["t"]},
    "fs.trash":                 lambda t: {"path": "x.txt"},
    "fs.write_file":            lambda t: {"path_template": "x.txt", "content": "hello"},
    "utils.json_validate":      lambda t: {"data": {}, "schema": {}},
    "utils.notify":             lambda t: {"channel": "email",
                                            "to": "a@b.c",
                                            "subject": "s",
                                            "message": "m"},
    "utils.path_template":      lambda t: {"template": "x.txt"},
    "utils.set_variables":      lambda t: {"variables": {"a": 1}},
}


def _dummy(name: str, typ: str, tmp_path) -> object:
    if name in ("source", "source_path", "path", "path_template"):
        return "x.txt"
    if name in ("target", "target_path", "dest"):
        return "y.txt"
    if "condition" in name:
        return "True"
    if name in ("items", "files", "file_refs", "input", "data"):
        return []
    if name in ("content", "text"):
        return "hello"
    if name == "seconds":
        return 0.0
    if typ in ("any[]", "list", "array"):
        return []
    if typ in ("object", "dict"):
        return {}
    if typ in ("int", "integer"):
        return 0
    if typ in ("float", "number"):
        return 0.0
    if typ in ("bool", "boolean"):
        return False
    return "x"


def _payload_for(node_id: str, node, tmp_path) -> dict:
    factory = _SPECIFIC_PAYLOADS.get(node_id)
    if factory is not None:
        return factory(tmp_path)
    payload: dict = {}
    inputs = getattr(node, "inputs", {})
    if isinstance(inputs, dict):
        for name, spec in inputs.items():
            if isinstance(spec, dict):
                if spec.get("default") is not None:
                    payload[name] = spec["default"]
                    continue
                if not spec.get("required", True):
                    continue
                typ = str(spec.get("type", "any"))
            else:
                typ = str(spec)
            payload[name] = _dummy(name, typ, tmp_path)
    return payload


def test_node_base_has_preview_fail_closed_false() -> None:
    assert Node._preview_fail_closed is False


def test_subflow_preview_fail_closed_true() -> None:
    assert registry.get("flow.subflow")._preview_fail_closed is True


def test_loop_batch_preview_fail_closed_true() -> None:
    assert registry.get("flow.loop_batch")._preview_fail_closed is True


_FIXTURE_FILES = (
    "x.txt",
    "x.csv",
    "cp.txt",
    "mv.txt",
    "rn.txt",
)


@pytest.mark.parametrize("node_id", _H6_IDS)
def test_h6_non_fail_closed_nodes_return_declared_ports(
    node_id: str, exec_ctx, tmp_path
) -> None:
    # NOTE: do NOT use monkeypatch.chdir — its teardown runs after the
    # leak detector and would flag tmp_path as a CWD leak. try/finally
    # restores CWD before this function returns.
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        for name in _FIXTURE_FILES:
            (tmp_path / name).write_text("hello", encoding="utf-8")
        (tmp_path / "d2").mkdir(exist_ok=True)

        cls = registry.get(node_id)
        assert getattr(cls, "_preview_fail_closed", False) is False
        node = cls()
        payload = _payload_for(node_id, node, tmp_path)
        result = node.dry_run(exec_ctx, payload)
        declared = set(node.outputs)
    finally:
        os.chdir(original_cwd)

    if isinstance(result, NodeResult):
        result = result.outputs
    if not isinstance(result, dict):
        assert len(declared) == 1, (
            f"{node_id}: non-dict dry_run result with "
            f"{len(declared)} declared ports"
        )
        return
    missing = declared - set(result)
    assert not missing, (
        f"{node_id}: dry_run missing declared ports {sorted(missing)}"
    )