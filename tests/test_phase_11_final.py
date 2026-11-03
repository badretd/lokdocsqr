"""Regression tests for the Phase 11 final-state hardening.

These cover the behaviours found during the pre-implementation audit:
legacy schema compatibility, ``~`` expansion, config path rebasing,
directory arguments for ``serve``, daemon-mode initial runs, clean CLI
errors, and the silent missing-audit case.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lokdocsqr.core.audit import AuditLog

cli_main = importlib.import_module("lokdocsqr.cli.main")


def _write(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _minimal_doc(automation_id: str = "final-state") -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": automation_id},
        "nodes": [
            {
                "id": "mk",
                "type": "fs.make_dir",
                "params": {"path_template": "work"},
            }
        ],
        "edges": [],
    }


def test_legacy_simple_schema_is_accepted(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write(
        tmp_path / "legacy.json",
        {
            "id": "legacy",
            "title": "Legacy automation",
            "nodes": [
                {
                    "id": "mk",
                    "type": "make_dir",
                    "params": {"path_template": "work"},
                }
            ],
        },
    )

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
    result = json.loads(capsys.readouterr().out)
    assert result["automation_id"] == "legacy"
    assert result["success"] is True
    assert (base / "work").is_dir()


def test_legacy_base_placeholder_in_source_path(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    (base / "work").mkdir(parents=True)
    (base / "work" / "a.txt").write_text("alpha", encoding="utf-8")
    automation = _write(
        tmp_path / "base-placeholder.json",
        {
            "id": "base-placeholder",
            "settings": {"scope": {"roots": ["."], "writable": ["."]}},
            "nodes": [
                {
                    "id": "mv",
                    "type": "move_files",
                    "params": {
                        "source": "{base}/work/a.txt",
                        "dest": "b.txt",
                    },
                }
            ],
        },
    )

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
    capsys.readouterr()
    assert (base / "work" / "b.txt").read_text(encoding="utf-8") == "alpha"
    assert not (base / "work" / "a.txt").exists()


def test_python_dash_m_lokdocsqr_works() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "lokdocsqr", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "lokdocsqr" in result.stdout


def test_config_paths_are_rebased_on_base_dir(
    tmp_path: Path, capsys: Any
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    config = _write(
        tmp_path / "config.json",
        {
            "base_dir": "ws",
            "logs_dir": "logs",
            "trash_dir": "trash",
            "default_dry_run": False,
        },
    )
    automation = _write(tmp_path / "automation.json", _minimal_doc())

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--config",
                str(config),
                "--no-dry-run",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (base / "logs" / "audit.jsonl").exists()
    assert not (tmp_path / "logs" / "audit.jsonl").exists()


def test_base_dir_override_rebases_default_logs(
    tmp_path: Path, capsys: Any
) -> None:
    old_base = tmp_path / "old"
    new_base = tmp_path / "new"
    old_base.mkdir()
    new_base.mkdir()
    config = _write(tmp_path / "config.json", {"base_dir": str(old_base)})
    automation = _write(tmp_path / "automation.json", _minimal_doc())

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--config",
                str(config),
                "--base-dir",
                str(new_base),
                "--no-dry-run",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (new_base / "work").is_dir()
    assert (new_base / ".logs" / "audit.jsonl").exists()
    assert not (old_base / ".logs" / "audit.jsonl").exists()


def test_tilde_in_workflow_scope_is_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    automation = _write(
        tmp_path / "tilde.json",
        {
            "spec_version": "1.0",
            "metadata": {"id": "tilde"},
            "settings": {
                "scope": {
                    "roots": ["~/workspace"],
                    "writable": ["~/workspace"],
                }
            },
            "nodes": [
                {
                    "id": "mk",
                    "type": "fs.make_dir",
                    "params": {"path_template": "workspace/work"},
                }
            ],
            "edges": [],
        },
    )

    assert (
        cli_main.main(
            [
                "run",
                str(automation),
                "--base-dir",
                str(tmp_path),
                "--no-dry-run",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert (tmp_path / "workspace" / "work").is_dir()
    assert not (tmp_path / "~" / "workspace" / "work").exists()


def test_missing_audit_file_is_silent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    audit = AuditLog(tmp_path / "missing" / "audit.jsonl")
    assert list(audit.iter_all()) == []
    assert not any(
        "cannot read" in record.message for record in caplog.records
    )


def test_cli_unknown_node_has_no_traceback(
    tmp_path: Path, capsys: Any
) -> None:
    automation = _write(
        tmp_path / "unknown.json",
        {
            "spec_version": "1.0",
            "metadata": {"id": "unknown"},
            "nodes": [{"id": "n", "type": "does.not.exist"}],
            "edges": [],
        },
    )

    assert cli_main.main(["run", str(automation), "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "Unknown node type" in captured.err
    assert "Traceback" not in captured.err


def test_serve_accepts_directory_and_daemon_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automations = tmp_path / "automations"
    automations.mkdir()
    _write(automations / "one.json", _minimal_doc("one"))
    _write(automations / "two.json", _minimal_doc("two"))

    # Stop immediately after registration/initial runs. This exercises the
    # directory expansion, lock acquisition and initial daemon execution
    # without leaving a background thread in the test process.
    monkeypatch.setattr(
        cli_main, "_serve_until_signal", lambda runtime: None
    )

    assert (
        cli_main.main(
            [
                "serve",
                "--daemon",
                str(automations),
                "--base-dir",
                str(base),
                "--no-dry-run",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "registered one" in out
    assert "registered two" in out
    assert "[audit]" in out
    assert "[run]" in out
    assert (base / "work").is_dir()


def test_daemon_command_runs_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write(tmp_path / "automation.json", _minimal_doc("daemon-cmd"))
    monkeypatch.setattr(
        cli_main, "_serve_until_signal", lambda runtime: None
    )

    assert (
        cli_main.main(
            [
                "daemon",
                str(automation),
                "--base-dir",
                str(base),
                "--no-dry-run",
            ]
        )
        == 0
    )
    assert (base / "work").is_dir()


def test_serve_daemon_dry_run_wires_nested_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    base = tmp_path / "ws"
    base.mkdir()
    automation = _write(
        tmp_path / "nested.json",
        {
            "spec_version": "1.0",
            "metadata": {"id": "nested"},
            "nodes": [
                {
                    "id": "loop",
                    "type": "flow.loop_batch",
                    "params": {
                        "items": [1],
                        "graph": {
                            "spec_version": "1.0",
                            "metadata": {"id": "child"},
                            "nodes": [
                                {
                                    "id": "mk",
                                    "type": "fs.make_dir",
                                    "params": {"path_template": "w"},
                                }
                            ],
                            "edges": [],
                        },
                    },
                }
            ],
            "edges": [],
        },
    )

    monkeypatch.setattr(
        cli_main, "_serve_until_signal", lambda runtime: None
    )

    assert (
        cli_main.main(
            [
                "serve",
                "--daemon",
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
    assert result["success"] is True
    assert result["node_results"]["loop"]["state"] == "enabled"
