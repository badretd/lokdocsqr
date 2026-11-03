"""Release-hardening tests for the CLI.

Covers the behaviours added for the release-readiness pass:

* the local ``trigger`` command (manual_run / webhook, no network listener);
* early, actionable refusal when a real-run document lacks approval;
* zero-trigger handling for ``serve`` and ``run --daemon``;
* a real ``serve`` watch -> run end-to-end path;
* the trigger nodes' base-relative ``files[].path`` contract.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.models import WorkspaceScope
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.triggers.watch_folder import WatchFolderNode

cli_main = importlib.import_module("lokdocsqr.cli.main")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _automation(nodes: list[dict[str, Any]], *, automation_id: str) -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": automation_id},
        "settings": {"default_dry_run": False},
        "nodes": nodes,
        "edges": [],
    }


def _run_json(capsys: Any) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# trigger command (manual_run / webhook, fully local)
# ---------------------------------------------------------------------------


def test_trigger_manual_run_delivers_files(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "manual.json",
        _automation(
            [{"id": "manual", "type": "triggers.manual_run", "params": {}}],
            automation_id="manual-demo",
        ),
    )

    assert (
        cli_main.main(
            [
                "trigger",
                str(automation),
                "--base-dir",
                str(base),
                "--type",
                "manual_run",
                "--files",
                "inbox/a.txt",
                "--json",
                "--no-dry-run",
            ]
        )
        == 0
    )
    result = _run_json(capsys)
    assert result["success"] is True
    assert result["node_results"]["manual"]["outputs"]["paths"] == [
        "inbox/a.txt"
    ]


def test_trigger_webhook_passes_payload_without_network(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "hook.json",
        _automation(
            [{"id": "hook", "type": "triggers.webhook", "params": {}}],
            automation_id="hook-demo",
        ),
    )

    assert (
        cli_main.main(
            [
                "trigger",
                str(automation),
                "--base-dir",
                str(base),
                "--type",
                "webhook",
                "--payload",
                '{"hello": "world"}',
                "--json",
                "--no-dry-run",
            ]
        )
        == 0
    )
    result = _run_json(capsys)
    assert result["success"] is True
    assert result["node_results"]["hook"]["outputs"]["payload"] == {
        "hello": "world"
    }


def test_trigger_reads_payload_from_file(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    payload_file = tmp_path / "payload.json"
    payload_file.write_text('{"from": "file"}', encoding="utf-8")
    automation = _write_json(
        tmp_path / "hook.json",
        _automation(
            [{"id": "hook", "type": "triggers.webhook", "params": {}}],
            automation_id="hook-file",
        ),
    )

    assert (
        cli_main.main(
            [
                "trigger",
                str(automation),
                "--base-dir",
                str(base),
                "--type",
                "webhook",
                "--payload",
                f"@{payload_file}",
                "--json",
                "--no-dry-run",
            ]
        )
        == 0
    )
    assert _run_json(capsys)["node_results"]["hook"]["outputs"]["payload"] == {
        "from": "file"
    }


def test_trigger_rejects_non_object_payload(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "hook.json",
        _automation(
            [{"id": "hook", "type": "triggers.webhook", "params": {}}],
            automation_id="hook-bad",
        ),
    )

    assert (
        cli_main.main(
            [
                "trigger",
                str(automation),
                "--base-dir",
                str(base),
                "--type",
                "webhook",
                "--payload",
                "[1, 2]",
                "--no-dry-run",
            ]
        )
        == 1
    )
    assert "--payload must be a JSON object" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Real-run approval guardrail
# ---------------------------------------------------------------------------


def test_real_run_without_approval_is_refused_early(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "real.json",
        _automation(
            [{"id": "mk", "type": "fs.make_dir", "params": {"path_template": "w"}}],
            automation_id="real-demo",
        ),
    )

    assert cli_main.main(["run", str(automation), "--base-dir", str(base)]) == 1
    err = capsys.readouterr().err
    assert "default_dry_run = false" in err
    assert "--no-dry-run" in err
    assert not (base / "w").exists()


# ---------------------------------------------------------------------------
# Zero-trigger handling
# ---------------------------------------------------------------------------


def test_serve_without_triggers_exits_with_guidance(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "none.json",
        _automation(
            [{"id": "mk", "type": "fs.make_dir", "params": {"path_template": "w"}}],
            automation_id="no-triggers",
        ),
    )

    assert (
        cli_main.main(
            ["serve", str(automation), "--base-dir", str(base), "--dry-run"]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "no watch_folder/scheduled_scan triggers" in err


def test_run_daemon_without_triggers_runs_once_and_exits(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write_json(
        tmp_path / "none.json",
        _automation(
            [{"id": "mk", "type": "fs.make_dir", "params": {"path_template": "w"}}],
            automation_id="daemon-no-triggers",
        ),
    )

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--no-dry-run",
                "--daemon",
            ]
        )
        == 0
    )
    assert "running once and exiting" in capsys.readouterr().err
    assert (base / "w").is_dir()


# ---------------------------------------------------------------------------
# Real serve loop: watcher fires -> automation runs
# ---------------------------------------------------------------------------


def test_serve_watch_trigger_fires_a_real_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    inbox = base / "inbox"
    inbox.mkdir(parents=True)
    automation = tmp_path / "watch.ldsq"
    automation.write_text(
        """\
automation "watch-e2e"

default_dry_run = false

scope {
  roots = ["."]
  writable = ["."]
}

trigger "w" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = "*.txt"
  }
}

node "mk" {
  type = "fs.make_dir"
  params { path_template = "out/ran" }
}
""",
        encoding="utf-8",
    )

    def _drive(runtime: Any) -> None:
        # ``serve`` normally blocks in ``Runtime.serve``. Drive the real
        # watcher thread instead: start, drop a file, wait for a run, stop.
        runtime.start()
        try:
            (inbox / "hello.txt").write_text("payload", encoding="utf-8")
            assert runtime.wait_for_runs(1, timeout=5.0), runtime.results
        finally:
            runtime.stop()

    monkeypatch.setattr(cli_main, "_serve_until_signal", _drive)

    assert (
        cli_main.main(
            [
                "serve",
                str(automation),
                "--base-dir",
                str(base),
                "--no-dry-run",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (base / "out" / "ran").is_dir()


# ---------------------------------------------------------------------------
# Nested per-item dry-run preview
# ---------------------------------------------------------------------------


def test_dry_run_previews_each_loop_item_without_errors(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    watch = base / "watch"
    watch.mkdir(parents=True)
    (watch / "a.txt").write_text("a", encoding="utf-8")
    (watch / "b.txt").write_text("b", encoding="utf-8")
    automation = tmp_path / "mover.ldsq"
    automation.write_text(
        """\
automation "mover"

default_dry_run = false

scope {
  roots = ["watch"]
  writable = ["watch"]
}

node "mk" {
  type = "fs.make_dir"
  params { path_template = "watch/tmp" }
}

node "ls" {
  type = "access.list_files"
  params { path = "watch" }
  outputs = [{name = "files", type = "FileRef[]"}]
}

node "loop" {
  type = "flow.loop_batch"
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
  inputs = ["items"]
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
""",
        encoding="utf-8",
    )

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    loop = result["node_results"]["loop"]
    assert loop["state"] == "enabled"
    assert loop["outputs"]["errors"] == []
    assert loop["outputs"]["total"] == 2
    # A dry run must not create or move anything.
    assert not (watch / "tmp").exists()
    assert (watch / "a.txt").exists()
    assert (watch / "b.txt").exists()


# ---------------------------------------------------------------------------
# Daemon mode: an explicit CLI mode must govern trigger-driven runs too
# ---------------------------------------------------------------------------

_WATCH_MOVE_DSL = """\
automation "watch-daemon-mode"

default_dry_run = true

scope {
  roots = ["inbox", "processed"]
  writable = ["inbox", "processed"]
}

trigger "watch-inbox" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = "*.pdf"
    recursive = false
  }
}

node "watch" {
  type = "triggers.watch_folder"
  params {
    path = "inbox"
    patterns = "*.pdf"
    recursive = false
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
      metadata { id = "processed-move-child" }
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

edge "watch-loop" {
  source = "watch"
  target = "loop"
  source_port = "files"
  target_port = "items"
}
"""


def _write_watch_automation(path: Path, *, default_dry_run: bool) -> Path:
    value = "true" if default_dry_run else "false"
    path.write_text(
        _WATCH_MOVE_DSL.replace(
            "default_dry_run = true", f"default_dry_run = {value}"
        ),
        encoding="utf-8",
    )
    return path


def test_daemon_no_dry_run_applies_to_trigger_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    inbox = base / "inbox"
    inbox.mkdir(parents=True)
    # The document explicitly says dry-run; the CLI flag must still win for
    # every triggered run, not just the daemon's initial run.
    automation = _write_watch_automation(
        tmp_path / "watch.ldsq", default_dry_run=True
    )

    def _drive(runtime: Any) -> None:
        before = len(runtime.results)
        (inbox / "new.pdf").write_text("%PDF-1.4\n", encoding="utf-8")
        assert runtime.wait_for_runs(before + 1, timeout=5.0), runtime.results

    monkeypatch.setattr(cli_main, "_serve_until_signal", _drive)

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--no-dry-run",
                "--daemon",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (base / "processed" / "new.pdf").exists()
    assert not (inbox / "new.pdf").exists()


def test_daemon_dry_run_keeps_trigger_runs_dry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    inbox = base / "inbox"
    inbox.mkdir(parents=True)
    # The document says real-run; --dry-run must keep every triggered run dry.
    automation = _write_watch_automation(
        tmp_path / "watch.ldsq", default_dry_run=False
    )

    def _drive(runtime: Any) -> None:
        before = len(runtime.results)
        (inbox / "new.pdf").write_text("%PDF-1.4\n", encoding="utf-8")
        assert runtime.wait_for_runs(before + 1, timeout=5.0), runtime.results

    monkeypatch.setattr(cli_main, "_serve_until_signal", _drive)

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(base),
                "--dry-run",
                "--daemon",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (inbox / "new.pdf").exists()
    assert not (base / "processed" / "new.pdf").exists()


# ---------------------------------------------------------------------------
# Trigger node path contract (mirrors access.list_files)
# ---------------------------------------------------------------------------


def test_watch_folder_files_are_base_relative(tmp_path: Path) -> None:
    base = tmp_path / "ws"
    inbox = base / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "a.txt").write_text("a", encoding="utf-8")
    ctx = ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(roots=[base], writable=[base])
        ),
        config=AppConfig(base_dir=base, default_dry_run=False),
        audit=None,  # type: ignore[arg-type]
        run_id="trigger-run",
        automation_id="watch-path",
        events=EventBus(),
        policy=None,
    )

    result = WatchFolderNode().run(
        ctx, {"path": "inbox", "patterns": ["*.txt"]}
    )

    assert result["paths"] == ["inbox/a.txt"]
    assert result["files"][0]["absolute_path"] == str(inbox / "a.txt")
