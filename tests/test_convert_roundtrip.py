"""csv ↔ json equivalence via sorted(list_of_dicts) — not string comparison."""
import csv
import io
import json

import pytest

from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.nodes.fs_ops.convert import (
    ConvertNode,
    _csv_to_json,
    _json_to_csv,
)


def _canon(rows):
    """Order-insensitive canonical form of a list-of-dicts."""
    return sorted(rows, key=lambda r: tuple(sorted(r.items())))


def test_csv_to_json_to_csv_roundtrip_via_files(ctx, tmp_path):
    original = [
        {"name": "Alice", "age": "30"},
        {"name": "Bob",   "age": "25"},
    ]
    csv_in = tmp_path / "in.csv"
    csv_in.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")

    json_out = tmp_path / "out.json"
    ConvertNode().run(ctx, {
        "source": str(csv_in),
        "target": "out.json",
        "from_mime": "text/csv",
        "to_mime": "application/json",
    })
    parsed = json.loads(json_out.read_text(encoding="utf-8"))
    assert _canon(parsed) == _canon(original)

    csv_back = tmp_path / "back.csv"
    ConvertNode().run(ctx, {
        "source": str(json_out),
        "target": "back.csv",
        "from_mime": "application/json",
        "to_mime": "text/csv",
    })
    with csv_back.open(newline="", encoding="utf-8") as f:
        back = list(csv.DictReader(f))
    assert _canon(back) == _canon(original)


def test_csv_to_json_unit_equivalence():
    raw = b"a,b\n1,2\n3,4\n"
    rows = json.loads(_csv_to_json(raw).decode("utf-8"))
    assert _canon(rows) == _canon([
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ])


def test_json_to_csv_unit_equivalence():
    raw = json.dumps([
        {"a": 1, "b": 2},
        {"a": 3, "b": 4},
    ]).encode("utf-8")
    out = _json_to_csv(raw).decode("utf-8")
    back = list(csv.DictReader(io.StringIO(out)))
    assert _canon([{k: str(v) for k, v in r.items()} for r in back]) == _canon([
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ])


def test_json_to_csv_rejects_non_array():
    with pytest.raises(NodeExecutionError):
        _json_to_csv(b'{"a": 1}')


def test_json_to_csv_rejects_non_object_rows():
    with pytest.raises(NodeExecutionError):
        _json_to_csv(b'["a", "b"]')


def test_convert_dry_run_does_not_write(ctx, tmp_path):
    src = tmp_path / "in.csv"
    src.write_text("a\n1\n", encoding="utf-8")
    target = tmp_path / "out.json"
    log = ConvertNode().dry_run(ctx, {
        "source": str(src),
        "target": "out.json",
        "from_mime": "text/csv",
        "to_mime": "application/json",
    })
    assert not target.exists()
    assert log.dry_run is True
