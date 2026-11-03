"""Regression tests for absolute ``list_files`` paths feeding a loop.

The documented ``{item[path]}`` pattern only works when the item's
``path`` is relative: ``core.paths.render_template`` rejects absolute
results, and ``fs.move`` resolves a relative source against
``config.base_dir``. ``access.list_files`` now emits base-relative
``path`` values (with the absolute form under ``absolute_path``).
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.dsl import parse_dsl
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.models import NodeState
from lokdocsqr.core.node_api.base import Node
from lokdocsqr.core.runtime import Runtime
from lokdocsqr.nodes.access.list_files import ListFilesNode
from lokdocsqr.nodes.fs_ops.move import MoveNode
from lokdocsqr.nodes.utils.path_template import PathTemplateNode
from tests.helpers import edge, make_ctx, make_doc, node_spec, port, resolved

MOVE_WATCH_TO_TMP = """\
automation "move-watch-to-tmp"
default_dry_run = false
scope {
  roots = ["watch"]
  writable = ["watch"]
}

node "mk" {
  type = "fs.make_dir"
  params {
    path_template = "watch/tmp"
  }
}

node "ls" {
  type = "access.list_files"
  params {
    path = "watch"
  }
  outputs = [{name = "files", type = "FileRef[]"}]
}

node "loop" {
  type = "flow.loop_batch"
  inputs = ["items"]
  params {
    mode = "loop"
    graph {
      spec_version = "1.0"
      metadata { id = "move-child" }
      nodes = [
        {id = "pt", type = "utils.path_template", params = {template = "{item[path]}"}, outputs = [{name = "path", type = "string"}]},
        {id = "mv", type = "fs.move", params = {template = "tmp/{name}{ext}", conflict_policy = "rename"}, inputs = ["source"]}
      ]
      edges = [
        {id = "pt-mv", source = "pt", target = "mv", source_port = "path", target_port = "source"}
      ]
    }
  }
}

edge "mk-ls" {
  source = "mk"
  target = "ls"
  kind = "order"
}
edge "ls-loop" {
  source = "ls"
  target = "loop"
  source_port = "files"
  target_port = "items"
}
"""


def test_list_files_paths_are_relative_to_base_dir(tmp_path: Path) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    target = watch / "test.txt"
    target.write_text("hello", encoding="utf-8")

    result = ListFilesNode().run(make_ctx(tmp_path), {"path": "watch"})

    assert [item["path"] for item in result["files"]] == ["watch/test.txt"]
    assert result["files"][0]["absolute_path"] == str(target)


def test_loop_batch_item_path_renders_for_fs_move(tmp_path: Path) -> None:
    base = tmp_path / "base"
    watch = base / "watch"
    watch.mkdir(parents=True)
    (watch / "test.txt").write_text("hello", encoding="utf-8")

    runtime = Runtime(
        AppConfig(base_dir=base, default_dry_run=False),
        policy=None,
        lock_path=tmp_path / "runtime.lock",
    )
    automation_id = runtime.register(parse_dsl(MOVE_WATCH_TO_TMP))

    result = runtime.run_once(automation_id)

    assert result.success, result.errors
    assert (watch / "tmp" / "test.txt").read_text(encoding="utf-8") == "hello"
    assert not (watch / "test.txt").exists()

    loop_outputs = result.node_results["loop"].outputs
    assert loop_outputs["errors"] == []
    child = loop_outputs["results"][0]
    assert child.success, child.errors
    assert child.node_results["pt"].outputs["path"] == "watch/test.txt"
    assert child.node_results["mv"].state == NodeState.ENABLED
    assert child.node_results["mv"].error is None


class _NoneSourceNode(Node):
    """Test node that delivers ``None`` on its ``path`` output port."""

    id = "test.none_source"

    @property
    def title(self) -> str:
        return "None source"

    def run(self, ctx: Any, payload: Any) -> Any:
        return {"path": None}

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return {"path": None}


def test_fs_move_none_source_raises_clear_error(tmp_path: Path) -> None:
    document = make_doc(
        [
            node_spec(
                "pt",
                type_="test.none_source",
                outputs=[port("path")],
            ),
            node_spec(
                "mv",
                type_="fs.move",
                inputs=[port("source")],
                outputs=[port("file")],
            ),
        ],
        [
            edge(
                "pt",
                "mv",
                source_port="path",
                target_port="source",
            )
        ],
    )
    graph = resolved(
        document,
        ["pt", "mv"],
        {"pt": _NoneSourceNode(), "mv": MoveNode()},
    )

    result = Executor(make_ctx(tmp_path)).execute(graph)

    assert result.success is False
    message = "\n".join(result.errors)
    assert "fs.move: 'source' resolved to None" in message
    assert "'pt'" in message
    assert "ScopeViolation" not in message
    assert "/None" not in message


def test_path_template_absolute_render_does_not_silently_return_none() -> None:
    absolute = str(Path(os.sep).resolve() / "abs" / "test.txt")
    ctx = SimpleNamespace(variables={"item": {"path": absolute}})

    result = PathTemplateNode().run(ctx, {"template": "{item[path]}"})

    assert result["path"] is None
    assert result["errors"]
    text = " ".join(error["message"] for error in result["errors"]).lower()
    assert "absolute" in text
    assert "relative" in text
