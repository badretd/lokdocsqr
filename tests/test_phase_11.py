"""Phase 11 acceptance tests — the CLI.

The important test is end-to-end: a params-only automation is executed through
the real ``parse -> validate -> resolve -> build -> Executor`` pipeline by the
CLI, its audit entries are readable through ``audit show``, and
``audit rollback`` restores the filesystem using that audit log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lokdocsqr.cli.main import build_parser, main

AUTOMATION: dict[str, Any] = {
    "spec_version": "1.0",
    "metadata": {"id": "cli-vertical-slice", "name": "CLI vertical slice"},
    "nodes": [
        {
            "id": "mk",
            "type": "fs.make_dir",
            "params": {"path_template": "work"},
        },
        {
            "id": "wr",
            "type": "fs.write_file",
            "params": {
                "path_template": "work/a.txt",
                "content": "alpha",
                "mode": "write",
                "create_parents": False,
            },
        },
        {
            "id": "mv",
            "type": "fs.move",
            "params": {
                "source": "work/a.txt",
                "template": "b.txt",
                "conflict_policy": "overwrite",
            },
        },
    ],
    "edges": [],
}


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    base = tmp_path / "ws"
    base.mkdir()
    automation = tmp_path / "automation.json"
    automation.write_text(json.dumps(AUTOMATION), encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "base_dir": str(base),
                "default_dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    return automation, config, base


def _read_json(capsys: Any) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


# ---------------------------------------------------------------------------
# End-to-end acceptance
# ---------------------------------------------------------------------------


def test_cli_run_audit_show_and_rollback_round_trip(tmp_path: Path, capsys: Any) -> None:
    automation, config, base = _workspace(tmp_path)

    assert (
        main(
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
    result = _read_json(capsys)

    assert result["success"] is True
    assert result["dry_run"] is False
    assert (base / "work" / "b.txt").read_text(encoding="utf-8") == "alpha"
    assert not (base / "work" / "a.txt").exists()
    assert [log["node_id"] for log in result["action_logs"]] == [
        "fs.make_dir",
        "fs.write_file",
        "fs.move",
    ]
    assert all(log["status"] == "applied" for log in result["action_logs"])

    run_id = result["run_id"]
    assert main(["audit", "show", run_id, "--config", str(config)]) == 0
    shown = capsys.readouterr().out
    assert "fs.make_dir" in shown
    assert "fs.write_file" in shown
    assert "fs.move" in shown

    # Previewing the rollback does not touch the filesystem.
    assert (
        main(
            [
                "audit",
                "rollback",
                run_id,
                "--config",
                str(config),
                "--dry-run",
            ]
        )
        == 0
    )
    assert (base / "work" / "b.txt").exists()

    assert main(["audit", "rollback", run_id, "--config", str(config)]) == 0
    assert (base / "work" / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert not (base / "work" / "b.txt").exists()

    # Only the move was rollback-eligible, so exactly one rollback entry was
    # appended to the audit log (3 run entries + 1 rollback entry).
    audit_path = base / ".logs" / "audit.jsonl"
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 4


def test_cli_dry_run_reaches_the_same_pipeline_without_mutating(
    tmp_path: Path, capsys: Any
) -> None:
    automation, config, base = _workspace(tmp_path)

    assert (
        main(
            [
                "run",
                str(automation),
                "--config",
                str(config),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = _read_json(capsys)

    assert result["success"] is True
    assert result["dry_run"] is True
    assert not (base / "work").exists()
    assert [log["node_id"] for log in result["action_logs"]] == [
        "fs.make_dir",
        "fs.write_file",
        "fs.move",
    ]
    assert all(log["status"] == "dry_run" for log in result["action_logs"])


def test_cli_high_risk_without_explicit_opt_in_fails_closed(tmp_path: Path, capsys: Any) -> None:
    automation, config, base = _workspace(tmp_path)

    # The config says "real run", but the operator did not pass --no-dry-run
    # or --yes, so the CLI refuses early with actionable guidance instead of
    # running until the first mutation hits the policy gate.
    assert main(["run", str(automation), "--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert "default_dry_run = false" in captured.err
    assert "--no-dry-run" in captured.err
    assert not (base / "work").exists()


# ---------------------------------------------------------------------------
# validate / list-nodes / flags
# ---------------------------------------------------------------------------


def test_cli_validate_accepts_good_and_rejects_bad(tmp_path: Path, capsys: Any) -> None:
    automation, _config, _base = _workspace(tmp_path)

    assert main(["validate", str(automation)]) == 0
    assert "OK: cli-vertical-slice" in capsys.readouterr().out

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(
            {
                "spec_version": "1.0",
                "metadata": {"id": "bad"},
                "nodes": [{"id": "x", "type": "does.not.exist"}],
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    assert main(["validate", str(bad)]) == 1
    assert "Unknown node type" in capsys.readouterr().err


def test_cli_list_nodes_includes_fs_ops_and_json(capsys: Any) -> None:
    assert main(["list-nodes", "--json"]) == 0
    nodes = json.loads(capsys.readouterr().out)
    ids = {node["id"] for node in nodes}
    assert {"fs.make_dir", "fs.write_file", "fs.move", "fs.trash"} <= ids

    assert main(["list-nodes"]) == 0
    assert "fs.make_dir" in capsys.readouterr().out


def test_cli_scope_flag_supplies_the_workspace(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "scope-only"
    base.mkdir()
    automation = tmp_path / "automation.json"
    automation.write_text(json.dumps(AUTOMATION), encoding="utf-8")

    assert (
        main(
            [
                "run",
                str(automation),
                "--scope",
                str(base),
                "--no-dry-run",
                "--json",
            ]
        )
        == 0
    )
    result = _read_json(capsys)
    assert result["success"] is True
    assert (base / "work" / "b.txt").read_text(encoding="utf-8") == "alpha"


def test_cli_compile_ldsq_to_json_schema(tmp_path: Path, capsys: Any) -> None:
    automation = tmp_path / "automation.ldsq"
    automation.write_text(
        """\
automation "compiled"
default_dry_run = false

node mk {
  type = "fs.make_dir"
  path_template = "work"
}
""",
        encoding="utf-8",
    )

    assert main(["compile", str(automation)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["id"] == "compiled"
    assert payload["settings"]["default_dry_run"] is False
    assert payload["nodes"][0]["type"] == "fs.make_dir"
    assert payload["nodes"][0]["params"]["path_template"] == "work"

    destination = tmp_path / "compiled.json"
    assert main(["compile", str(automation), "--output", str(destination)]) == 0
    on_disk = json.loads(destination.read_text(encoding="utf-8"))
    assert on_disk == payload
    assert capsys.readouterr().out == ""


def test_cli_rejects_obsolete_dsl_suffixes(tmp_path: Path, capsys: Any) -> None:
    source = 'automation "legacy"\nnode mk {\n  type = "fs.make_dir"\n  path_template = "w"\n}\n'
    for suffix in (".dsl", ".lok", ".loks", ".txt", ""):
        automation = tmp_path / f"legacy{suffix}"
        automation.write_text(source, encoding="utf-8")

        assert main(["compile", str(automation)]) == 1
        assert "unsupported automation extension" in capsys.readouterr().err


def test_cli_compile_canonicalises_json(tmp_path: Path, capsys: Any) -> None:
    automation, _config, _base = _workspace(tmp_path)

    assert main(["compile", str(automation)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metadata"]["id"] == "cli-vertical-slice"
    assert [node["id"] for node in payload["nodes"]] == ["mk", "wr", "mv"]


def test_cli_accepts_ldsq_automation(tmp_path: Path, capsys: Any) -> None:
    base = tmp_path / "dsl-ws"
    base.mkdir()
    automation = tmp_path / "automation.ldsq"
    automation.write_text(
        """\
automation "dsl-cli"
default_dry_run = false

node mk {
  type = "fs.make_dir"
  path_template = "work"
}

node wr {
  type = "fs.write_file"
  path_template = "work/report.txt"
  content = "from-dsl"
  create_parents = false
}
""",
        encoding="utf-8",
    )

    assert (
        main(
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
    result = _read_json(capsys)
    assert result["automation_id"] == "dsl-cli"
    assert (base / "work" / "report.txt").read_text(encoding="utf-8") == "from-dsl"


def test_canonical_dsl_extension_is_ldsq() -> None:
    import lokdocsqr.core.dsl as dsl

    assert dsl.DSL_EXTENSION == ".ldsq"
    assert not hasattr(dsl, "DSL_EXTENSIONS")


def test_cli_help_and_version_contract() -> None:
    parser = build_parser()
    subcommands = parser._subparsers._group_actions[0].choices
    assert {"run", "compile", "validate", "list-nodes", "serve", "audit"} <= set(subcommands)
    assert main([]) == 0
