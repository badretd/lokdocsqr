"""CLI audit inspection: run list, full history and per-file history.

Each daemon/watch cycle is a separate run id, so ``audit show <run-id>`` only
shows that run. These tests pin the broader views added for release:
``audit runs``, ``audit show`` without a run id, and ``audit show --path``.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

cli_main = importlib.import_module("lokdocsqr.cli.main")

MOVE_ALL_DSL = """\
automation "audit-demo"

default_dry_run = false

scope {
  roots = ["inbox", "processed"]
  writable = ["inbox", "processed"]
}

node "ls" {
  type = "access.list_files"
  params { path = "inbox" }
  outputs = [{name = "files", type = "FileRef[]"}]
}

node "loop" {
  type = "flow.loop_batch"
  inputs = ["items"]
  params {
    mode = "loop"
    graph {
      spec_version = "1.0"
      metadata { id = "audit-child" }
      nodes = [
        {id = "pt", type = "utils.path_template", params = {template = "{item[path]}"}, outputs = [{name = "path", type = "string"}]},
        {id = "mv", type = "fs.move", params = {target_dir = "processed", template = "{name}{ext}", conflict_policy = "rename"}, inputs = ["source"]}
      ]
      edges = [
        {id = "pt-mv", source = "pt", target = "mv", source_port = "path", target_port = "source"}
      ]
    }
  }
}

edge "ls-loop" {
  source = "ls"
  target = "loop"
  source_port = "files"
  target_port = "items"
}
"""


@pytest.fixture()
def workspace(tmp_path: Path):
    base = tmp_path / "ws"
    (base / "inbox").mkdir(parents=True)
    (base / "processed").mkdir()
    automation = tmp_path / "move.ldsq"
    automation.write_text(MOVE_ALL_DSL, encoding="utf-8")
    return base, automation


def _run_once(
    base: Path, automation: Path, filename: str, capsys: Any
) -> dict[str, Any]:
    (base / "inbox" / filename).write_text("%PDF-1.4", encoding="utf-8")
    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--no-dry-run",
                "--json",
            ]
        )
        == 0
    )
    return json.loads(capsys.readouterr().out)


def _two_runs(workspace: Any, capsys: Any) -> tuple[Path, Path, dict, dict]:
    base, automation = workspace
    first = _run_once(base, automation, "one.pdf", capsys)
    second = _run_once(base, automation, "two.pdf", capsys)
    assert first["run_id"] != second["run_id"]
    return base, automation, first, second


def _cli(capsys: Any, *args: str) -> str:
    assert cli_main.main(list(args)) == 0
    return capsys.readouterr().out


def test_audit_runs_lists_every_run(workspace, capsys) -> None:
    base, _automation, first, second = _two_runs(workspace, capsys)
    out = _cli(capsys, "audit", "runs", "--base-dir", str(base))

    assert first["run_id"] in out
    assert second["run_id"] in out
    assert "entries=1" in out


def test_audit_runs_json(workspace, capsys) -> None:
    base, _automation, first, second = _two_runs(workspace, capsys)
    assert (
        cli_main.main(
            ["audit", "runs", "--base-dir", str(base), "--json"]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert [run["run_id"] for run in data] == [
        first["run_id"],
        second["run_id"],
    ]
    assert data[0]["automation_id"] == "audit-demo"


def test_audit_show_without_run_id_shows_full_history(workspace, capsys) -> None:
    base, _automation, first, second = _two_runs(workspace, capsys)
    out = _cli(capsys, "audit", "show", "--base-dir", str(base))

    assert first["run_id"] in out
    assert second["run_id"] in out
    assert out.count("fs.move move [applied]") == 2


def test_audit_show_path_follows_one_file_across_runs(workspace, capsys) -> None:
    base, _automation, _first, second = _two_runs(workspace, capsys)
    _cli(capsys, "audit", "rollback", second["run_id"], "--base-dir", str(base))

    out = _cli(
        capsys,
        "audit",
        "show",
        "--path",
        "processed/two.pdf",
        "--base-dir",
        str(base),
    )
    assert "inbox/two.pdf -> " in out
    assert "-> " in out and "inbox/two.pdf" in out
    assert "(rollback)" in out


def test_audit_rollback_is_scoped_to_one_run(workspace, capsys) -> None:
    base, _automation, _first, second = _two_runs(workspace, capsys)

    _cli(capsys, "audit", "rollback", second["run_id"], "--base-dir", str(base))

    assert (base / "inbox" / "two.pdf").exists()
    assert not (base / "processed" / "two.pdf").exists()
    # The other run is untouched.
    assert (base / "processed" / "one.pdf").exists()


def test_audit_show_last_limits_entries(workspace, capsys) -> None:
    base, _automation, _first, second = _two_runs(workspace, capsys)
    out = _cli(
        capsys,
        "audit",
        "show",
        "--last",
        "1",
        "--base-dir",
        str(base),
    )
    assert second["run_id"] in out
    assert out.count("fs.move move [applied]") == 1
