"""AutomationCatalog (SPEC §3)."""
import subprocess
import sys
from pathlib import Path

import pytest


def test_get_miss_returns_none():
    from lokdocsqr.core.catalog import AutomationCatalog
    assert AutomationCatalog().get("missing") is None


def test_register_then_get_same_object():
    from lokdocsqr.core.catalog import AutomationCatalog
    c = AutomationCatalog()
    obj = object()
    c.register("g1", obj)
    assert c.get("g1") is obj


def test_list_ids_sorted_tuple():
    from lokdocsqr.core.catalog import AutomationCatalog
    c = AutomationCatalog()
    for k in ("z", "a", "m"):
        c.register(k, object())
    ids = c.list_ids()
    assert isinstance(ids, tuple)
    assert ids == ("a", "m", "z")
    assert c.list_ids() == ids


def test_catalog_import_does_not_pull_safe_fs():
    """subprocess + sys.modules: importing catalog must not load core.safe_fs."""
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import lokdocsqr.core.catalog\n"
        "after = set(sys.modules)\n"
        "new = after - before\n"
        "bad = sorted(m for m in new if 'safe_fs' in m)\n"
        "if bad:\n"
        "    print('FORBIDDEN:' + ','.join(bad))\n"
        "    sys.exit(1)\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=30,
    )
    assert res.returncode == 0, f"safe_fs imported: {res.stdout}{res.stderr}"


def test_catalog_source_has_no_pathlib_import():
    """catalog.py source must not import pathlib directly."""
    import lokdocsqr.core.catalog as cat
    src = Path(cat.__file__).read_text(encoding="utf-8")
    assert "import pathlib" not in src
    assert "from pathlib" not in src
