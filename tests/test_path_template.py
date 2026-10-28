from __future__ import annotations
import os
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

_HERE = Path(__file__).resolve()
ROOT = _HERE.parent
if not (ROOT / "src").exists() and (ROOT.parent / "src").exists():
    ROOT = ROOT.parent
SRC = ROOT / "src" / "lokdocsqr"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from lokdocsqr.nodes.utils.path_template import PathTemplateNode


def _pt_ctx(variables: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(variables=dict(variables or {}))


def test_pt_simple_name() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(ctx, {"template": "{name}.txt", "variables": {"name": "x"}})
    assert result["path"] == "x.txt"
    assert result["path_template"] == "x.txt"
    assert result["errors"] == []
    assert result["error_count"] == 0


def test_pt_nested_path() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(
        ctx,
        {
            "template": "{year}/{category}/{name}.pdf",
            "variables": {"year": 2026, "category": "docs", "name": "f"},
        },
    )
    assert result["path"] == "2026/docs/f.pdf"
    assert result["path_template"] == "2026/docs/f.pdf"
    assert result["error_count"] == 0


def test_pt_missing_placeholder_is_error_not_raise() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(ctx, {"template": "{missing}.txt"})
    assert result["path"] is None
    assert result["path_template"] is None
    assert result["errors"]
    assert result["error_count"] >= 1


def test_pt_absolute_rejected_not_raise() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    # Build a template that renders to an absolute path on the current OS:
    #   POSIX:   "/abs/{name}.txt"   -> "/abs/x.txt"   (is_absolute() == True)
    #   Windows: "C:\abs\{name}.txt" -> "C:\abs\x.txt" (is_absolute() == True)
    # NB: on Windows Path("/abs/x.txt").is_absolute() is False
    # (drive-less rooted path), so the POSIX-only literal would slip
    # through render_template and render successfully.
    absolute_root = os.path.abspath(os.sep)
    template = f"{absolute_root}abs{os.sep}{{name}}.txt"
    result = node.run(ctx, {"template": template, "variables": {"name": "x"}})
    assert result["path"] is None
    assert result["errors"]
    assert any("absolute" in e["message"].lower() for e in result["errors"])


def test_pt_escape_rejected_not_raise() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(ctx, {"template": "../{name}.txt", "variables": {"name": "x"}})
    assert result["path"] is None
    assert result["errors"]
    text = " ".join(e["message"] for e in result["errors"]).lower()
    assert ".." in text or "escape" in text


def test_pt_empty_template_error_not_raise() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(ctx, {"template": ""})
    assert result["path"] is None
    assert result["errors"]


def test_pt_non_string_template_error_not_raise() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    result = node.run(ctx, {"template": 123})
    assert result["path"] is None
    assert result["errors"]


def test_pt_payload_variables_override_ctx() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx({"a": 1})
    result = node.run(ctx, {"template": "{a}", "variables": {"a": 2}})
    assert result["path"] == "2"
    assert result["error_count"] == 0


def test_pt_does_not_mutate_ctx_variables() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx({"a": 1})
    before = dict(ctx.variables)
    payload = {"template": "{a}", "variables": {"a": 2}}
    payload_before = dict(payload["variables"])
    node.run(ctx, payload)
    assert ctx.variables == before
    assert payload["variables"] == payload_before


def test_pt_dry_run_equals_run() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx({"name": "x"})
    payload = {"template": "{name}.txt"}
    run_result = node.run(ctx, dict(payload))
    dry_result = node.dry_run(ctx, dict(payload))
    assert run_result == dry_result


def test_pt_dry_run_no_fs_mutation(tmp_path: Path) -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx({"name": "x"})
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    node.dry_run(ctx, {"template": "{name}.txt"})

    # Verify that the sentinel file is intact and no new files were created
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert len(list(tmp_path.iterdir())) == 1


def test_port_contract_always_keys() -> None:
    node = PathTemplateNode()
    ctx = _pt_ctx()
    expected = set(node.outputs)
    cases = [
        ("run", node.run, {"template": "{name}.txt", "variables": {"name": "x"}}),
        ("dry", node.dry_run, {"template": "{name}.txt", "variables": {"name": "x"}}),
        ("run-error", node.run, {"template": ""}),
        ("dry-error", node.dry_run, {"template": 123}),
    ]
    for label, fn, payload in cases:
        result = fn(ctx, payload)
        missing = expected - set(result)
        assert not missing, f"{label}: missing output keys {missing}"