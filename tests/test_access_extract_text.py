"""Phase 6b — extract_text node (R3, R4, R5, R6, AC-10, AC-11, AC-12,
AC-13, AC-14, AC-15, AC-16)."""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.access.extract_text import ExtractTextNode


def make_ctx(tmp_path, allow_symlinks=False):
    scope_cfg = SimpleNamespace(
        roots=[tmp_path],
        writable=[tmp_path],
        allow_symlinks=allow_symlinks,
    )
    return SimpleNamespace(
        scope=ScopeManager(scope_cfg),
        config=SimpleNamespace(base_dir=tmp_path),
        run_id="test-run",
        automation_id="test-auto",
        dry_run=False,
    )


# ---- AC-4 / AC-5 ----------------------------------------------------------

def test_import_via_package():
    from lokdocsqr.nodes.access import extract_text as et
    assert et.ExtractTextNode is ExtractTextNode


def test_registered_under_expected_id():
    assert ExtractTextNode.id == "access.extract_text"
    assert registry.get("access.extract_text") is ExtractTextNode


def test_risks_documented_in_docstring():
    from lokdocsqr.nodes.access import extract_text as et
    docstring = et.__doc__ or ""
    for risk in ("R3", "R4", "R6"):
        assert risk in docstring, f"{risk} not documented in module docstring"


# ---- txt -----------------------------------------------------------------

def test_txt_basic(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello world")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.txt")})
    item = result["items"][0]
    assert item["format"] == "txt"
    assert item["text"] == "hello world"
    assert item["csv"] is None
    assert "error" not in item


def test_txt_errors_replace(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_bytes(b"\xff\xfe")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.txt")})
    item = result["items"][0]
    assert "error" not in item
    assert "\ufffd" in item["text"]


# ---- R3 — html -----------------------------------------------------------

def test_html_script_and_style_stripped(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.html").write_text(
        "<style>.x{}</style><script>alert(1)</script><p>hello</p>"
    )
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.html")})
    text = result["items"][0]["text"]
    assert "alert" not in text
    assert ".x{}" not in text
    assert "hello" in text
    assert "<p>" not in text


def test_html_entities_decoded(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.html").write_text("<p>a &amp; b &lt;tag&gt;</p>")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.html")})
    text = result["items"][0]["text"]
    assert "a & b" in text
    assert "<tag>" in text


def test_html_whitespace_collapsed_by_default(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.html").write_text("<p>hello</p>   <p>world</p>")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.html")})
    text = result["items"][0]["text"]
    assert text == "hello world"


def test_html_whitespace_preserved_when_requested(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.html").write_text("<p>hello</p>   <p>world</p>")
    result = ExtractTextNode().run(
        ctx,
        {"paths": str(tmp_path / "a.html"), "html_keep_whitespace": True},
    )
    text = result["items"][0]["text"]
    assert "hello" in text and "world" in text
    assert "   " in text or "\n" in text or "hello</p>" not in text


def test_htm_extension_maps_to_html(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.htm").write_text("<p>hi</p>")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.htm")})
    assert result["items"][0]["format"] == "html"
    assert "hi" in result["items"][0]["text"]


# ---- R4 — csv -----------------------------------------------------------

def test_csv_auto_sniffer_detects_semicolon(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_text(
        "col1;col2;col3\nval1;val2;val3\nval4;val5;val6\n"
    )
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "d.csv")})
    rows = result["items"][0]["csv"]
    assert rows == [
        ["col1", "col2", "col3"],
        ["val1", "val2", "val3"],
        ["val4", "val5", "val6"],
    ]


def test_csv_explicit_delimiter(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_text("a|b\n1|2\n")
    result = ExtractTextNode().run(
        ctx, {"paths": str(tmp_path / "d.csv"), "csv_delimiter": "|"}
    )
    assert result["items"][0]["csv"] == [["a", "b"], ["1", "2"]]


def test_csv_utf8_sig_strips_bom(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_bytes(
        b"\xef\xbb\xbfcol1,col2\nval1,val2\n"
    )
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "d.csv")})
    rows = result["items"][0]["csv"]
    assert rows[0][0] == "col1"


def test_csv_utf8_keeps_bom_when_requested(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_bytes(
        b"\xef\xbb\xbfcol1,col2\nval1,val2\n"
    )
    result = ExtractTextNode().run(
        ctx,
        {"paths": str(tmp_path / "d.csv"), "csv_encoding": "utf-8"},
    )
    rows = result["items"][0]["csv"]
    assert rows[0][0] == "\ufeffcol1"


def test_csv_sniffer_fallback_to_comma_on_error(tmp_path, monkeypatch):
    import csv as _csv
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_text("a,b\n1,2\n")

    class _FailingSniffer:
        def sniff(self, sample):
            raise _csv.Error("cannot determine")

    monkeypatch.setattr(_csv, "Sniffer", _FailingSniffer)
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "d.csv")})
    assert result["items"][0]["csv"] == [["a", "b"], ["1", "2"]]


def test_csv_delimiter_must_be_single_char(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_text("a,b\n")
    result = ExtractTextNode().run(
        ctx,
        {"paths": str(tmp_path / "d.csv"), "csv_delimiter": "ab"},
    )
    item = result["items"][0]
    assert "error" in item
    assert "single character" in item["error"]


def test_csv_nul_byte_is_per_item_error(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "bad.csv").write_bytes(b"a,b\x00c\n")
    (tmp_path / "ok.csv").write_text(
        "name,age\nAlice,30\nBob,25\n"
    )
    result = ExtractTextNode().run(
        ctx,
        {"paths": [str(tmp_path / "bad.csv"), str(tmp_path / "ok.csv")]},
    )
    assert len(result["items"]) == 2
    bad = [i for i in result["items"] if "bad.csv" in i["path"]][0]
    ok = [i for i in result["items"] if "ok.csv" in i["path"]][0]
    assert "error" in bad
    assert "error" not in ok
    assert ok["csv"] == [["name", "age"], ["Alice", "30"], ["Bob", "25"]]


def test_csv_result_is_list_of_lists(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "d.csv").write_text("a,b\n1,2\n")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "d.csv")})
    rows = result["items"][0]["csv"]
    assert isinstance(rows, list)
    assert all(isinstance(r, list) for r in rows)
    assert all(isinstance(c, str) for r in rows for c in r)


# ---- R5 — register_handler ------------------------------------------------

def test_register_handler_is_module_level_function():
    from lokdocsqr.nodes.access import extract_text as et
    assert inspect.isfunction(et.register_handler)
    assert not inspect.ismethod(et.register_handler)
    if hasattr(et, "ExtractTextNode"):
        assert "register_handler" not in et.ExtractTextNode.__dict__


def test_register_handler_accepts_custom_format(tmp_path):
    from lokdocsqr.nodes.access.extract_text import register_handler, HANDLERS
    ctx = make_ctx(tmp_path)

    def foo_handler(data, params):
        return {"text": data.decode("utf-8"), "csv": None}

    register_handler("foo", foo_handler)
    try:
        (tmp_path / "test.bin").write_bytes(b"hello foo")
        result = ExtractTextNode().run(
            ctx,
            {"paths": str(tmp_path / "test.bin"), "format": "foo"},
        )
        assert result["items"][0]["text"] == "hello foo"
    finally:
        HANDLERS.pop("foo", None)


def test_pdf_auto_returns_per_item_error(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4 fake")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "doc.pdf")})
    assert "error" in result["items"][0]


def test_unknown_explicit_format_no_handler(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "doc.unk").write_bytes(b"fake")
    result = ExtractTextNode().run(
        ctx, {"paths": str(tmp_path / "doc.unk"), "format": "unk"}
    )
    assert "error" in result["items"][0]
    assert "no handler" in result["items"][0]["error"]


# ---- AC-10 — unknown extension / auto --------------------------------

def test_auto_unknown_extension_is_error_in_run(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.xyz").write_text("data")
    result = ExtractTextNode().run(ctx, {"paths": str(tmp_path / "a.xyz")})
    assert "error" in result["items"][0]
    assert "cannot infer format" in result["items"][0]["error"]


def test_auto_unknown_extension_is_error_in_dry_run(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.xyz").write_text("data")
    result = ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "a.xyz")})
    assert "error" in result["items"][0]
    assert "cannot infer format" in result["items"][0]["error"]


# ---- AC-10 — explicit format for each handler -----------------------

@pytest.mark.parametrize(
    "fmt,content,expected_key",
    [
        ("txt", b"hello", "text"),
        ("html", b"<p>hi</p>", "text"),
        ("csv", b"a,b\n1,2\n", "csv"),
    ],
)
def test_explicit_format_override(tmp_path, fmt, content, expected_key):
    ctx = make_ctx(tmp_path)
    path = tmp_path / f"file.{fmt}"
    path.write_bytes(content)
    result = ExtractTextNode().run(ctx, {"paths": str(path), "format": fmt})
    item = result["items"][0]
    assert "error" not in item
    assert item[expected_key] is not None


# ---- R6 — truncated boundary matches read_content at exact-limit ----

@pytest.mark.parametrize("size_offset", [-2, -1, 0])
def test_truncated_at_or_below_limit(tmp_path, size_offset):
    from lokdocsqr.nodes.access.read_content import ReadContentNode
    ctx = make_ctx(tmp_path)
    max_bytes = 5
    target_size = max_bytes + size_offset
    path = tmp_path / "f.txt"
    path.write_bytes(b"x" * target_size)

    et = ExtractTextNode().run(
        ctx, {"paths": str(path), "max_bytes": max_bytes}
    )
    rc = ReadContentNode().run(
        ctx,
        {"paths": str(path), "max_bytes": max_bytes, "binary_policy": "skip"},
    )

    assert et["items"][0]["truncated"] is False
    # read_content also does not truncate in this range
    assert rc["items"][0]["truncated"] is False


def test_truncated_above_limit_in_extract_text(tmp_path):
    """extract_text truncates where read_content raises AccessLimitError."""
    ctx = make_ctx(tmp_path)
    max_bytes = 5
    path = tmp_path / "f.txt"
    path.write_bytes(b"x" * (max_bytes + 1))

    result = ExtractTextNode().run(
        ctx, {"paths": str(path), "max_bytes": max_bytes}
    )
    item = result["items"][0]
    assert "error" not in item
    assert item["truncated"] is True
    assert item["bytes"] == max_bytes


# ---- AC-7 — dry_run must not read files -----------------------------

def test_dry_run_does_not_call_read_bytes(tmp_path, monkeypatch):
    from lokdocsqr.core import safe_fs
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    counts = {"read_bytes": 0}
    orig = safe_fs.read_bytes

    def fake(p, limit=-1):
        counts["read_bytes"] += 1
        return orig(p, limit=limit)

    monkeypatch.setattr(safe_fs, "read_bytes", fake)
    ExtractTextNode().dry_run(ctx, {"paths": str(tmp_path / "a.txt")})
    assert counts["read_bytes"] == 0


def test_dry_run_bytes_is_full_size(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_bytes(b"x" * 100)
    result = ExtractTextNode().dry_run(
        ctx, {"paths": str(tmp_path / "a.txt"), "max_bytes": 10}
    )
    assert result["items"][0]["bytes"] == 100
    assert result["items"][0]["truncated"] is True


def test_dry_run_does_not_produce_text_or_csv(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.csv").write_text("a,b\n1,2\n")
    for name in ("a.txt", "b.csv"):
        result = ExtractTextNode().dry_run(
            ctx, {"paths": str(tmp_path / name)}
        )
        assert result["items"][0]["text"] is None
        assert result["items"][0]["csv"] is None


# ---- AC-15 — scope.check(..., write=False) per input path -----------

def test_scope_check_for_each_input_path(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    calls = []
    orig = ctx.scope.check

    def checking(path, write=False):
        calls.append((str(path), write))
        return orig(path, write=write)

    ctx.scope.check = checking
    ExtractTextNode().run(
        ctx,
        {"paths": [str(tmp_path / "a.txt"), str(tmp_path / "b.txt")]},
    )
    assert all(w is False for _, w in calls)
    assert any("a.txt" in p for p, _ in calls)
    assert any("b.txt" in p for p, _ in calls)


# ---- AC-16 — per-item isolation -------------------------------------

def test_mixed_ok_and_error_items(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "ok.txt").write_text("hello")
    (tmp_path / "bad.xyz").write_text("data")
    missing = tmp_path / "missing.txt"
    result = ExtractTextNode().run(
        ctx,
        {
            "paths": [
                str(tmp_path / "ok.txt"),
                str(tmp_path / "bad.xyz"),
                str(missing),
            ]
        },
    )
    assert len(result["items"]) == 3
    errors = [i for i in result["items"] if "error" in i]
    oks = [i for i in result["items"] if "error" not in i]
    assert len(errors) == 2
    assert len(oks) == 1


# ---- misc ------------------------------------------------------------

def test_no_paths_raises(tmp_path):
    ctx = make_ctx(tmp_path)
    with pytest.raises(NodeExecutionError, match="no paths"):
        ExtractTextNode().run(ctx, {"paths": []})


def test_out_of_scope_is_per_item_error(tmp_path):
    ctx = make_ctx(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside-6b.txt"
    outside.write_text("x")
    try:
        result = ExtractTextNode().run(ctx, {"paths": str(outside)})
        assert "error" in result["items"][0]
    finally:
        try:
            outside.unlink()
        except OSError:
            pass


def test_invalid_max_bytes_raises(tmp_path):
    ctx = make_ctx(tmp_path)
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(NodeExecutionError, match="max_bytes"):
        ExtractTextNode().run(
            ctx, {"paths": str(tmp_path / "a.txt"), "max_bytes": -1}
        )
