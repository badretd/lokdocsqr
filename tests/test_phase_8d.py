"""Verification tests for LokDocSQR Phase 8d — flow.merge, flow.wait, AppConfig.flow.

Gate 8d. Covers 46 tests across 10 groups (see report for mapping).

D8d deviations (NOT bugs):
  D8d.1  wait.dry_run never sleeps; it reports the duration as would_sleep
  D8d.2  last_wins filters None
  D8d.3  concat flattens one level
  D8d.4  dict_merge shallow, later-wins, non-dict -> errors
  D8d.5  merge treats payload["items"] as list of incoming values
  D8d.6  load_config/save_config don't read/write "flow" (deferred to 8e)

Run:
    pytest tests/test_phase_8d.py -v
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from lokdocsqr.core.config import AppConfig, FlowConfig, load_config
from lokdocsqr.core.errors import NodeExecutionError
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.nodes.flow.merge import MergeNode
from lokdocsqr.nodes.flow.wait import WaitNode

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _merge_run(payload):
    """Run flow.merge in normal mode with a bare ctx (merge ignores it)."""
    return MergeNode().run(SimpleNamespace(), payload)


def _merge_dry(payload):
    """Run flow.merge in dry-run mode with a bare ctx."""
    return MergeNode().dry_run(SimpleNamespace(), payload)


def _wait_ctx(dry_run=False, start=0.0):
    from lokdocsqr.core.clock import FakeClock
    return SimpleNamespace(clock=FakeClock(start=start), dry_run=dry_run)


# ---------------------------------------------------------------------------
# Group 1 — flow.merge: last_wins (default)
# ---------------------------------------------------------------------------


def test_01_last_wins_empty_payload():
    r = _merge_run({})
    assert r["merged"] is None
    assert r["items"] == []
    assert r["count"] == 0
    assert r["mode"] == "last_wins"
    assert r["errors"] == []
    assert r["error_count"] == 0


def test_02_last_wins_empty_items():
    r = _merge_run({"items": []})
    assert r["merged"] is None
    assert r["count"] == 0


def test_03_last_wins_single_none():
    r = _merge_run({"items": [None]})
    assert r["merged"] is None
    assert r["count"] == 1


def test_04_last_wins_two_nones():
    r = _merge_run({"items": [None, None]})
    assert r["merged"] is None


def test_05_last_wins_none_then_value():
    # D8d.2: None values are ignored.
    r = _merge_run({"items": [None, 1]})
    assert r["merged"] == 1


def test_06_last_wins_value_then_none():
    # D8d.2.
    r = _merge_run({"items": [1, None]})
    assert r["merged"] == 1


def test_07_last_wins_two_values():
    r = _merge_run({"items": [1, 2]})
    assert r["merged"] == 2


def test_08_last_wins_mixed():
    r = _merge_run({"items": [1, None, 2, None]})
    assert r["merged"] == 2


def test_09_last_wins_scalar():
    r = _merge_run({"items": 42})
    assert r["items"] == [42]
    assert r["merged"] == 42


def test_10_last_wins_one_filter():
    # D8d.5: one filter delivers a list -> list elements become incoming values.
    r = _merge_run({"items": [1, 2, 3], "mode": "last_wins"})
    assert r["merged"] == 3


def test_11_last_wins_two_filters():
    # D8d.5: two filters deliver a list of lists.
    r = _merge_run({"items": [[1, 2], [3, 4]], "mode": "last_wins"})
    assert r["merged"] == [3, 4]


# ---------------------------------------------------------------------------
# Group 2 — flow.merge: concat
# ---------------------------------------------------------------------------


def test_12_concat_empty():
    r = _merge_run({"items": [], "mode": "concat"})
    assert r["merged"] == []


def test_13_concat_scalars():
    r = _merge_run({"items": [1, 2, 3], "mode": "concat"})
    assert r["merged"] == [1, 2, 3]


def test_14_concat_lists_flatten():
    # D8d.3: one-level flatten.
    r = _merge_run({"items": [[1, 2], [3, 4]], "mode": "concat"})
    assert r["merged"] == [1, 2, 3, 4]


def test_15_concat_mixed():
    r = _merge_run({"items": [[1, 2], 3], "mode": "concat"})
    assert r["merged"] == [1, 2, 3]


def test_16_concat_none_preserved():
    # In concat, None is preserved (unlike last_wins).
    r = _merge_run({"items": [None, 1], "mode": "concat"})
    assert r["merged"] == [None, 1]


# ---------------------------------------------------------------------------
# Group 3 — flow.merge: dict_merge
# ---------------------------------------------------------------------------


def test_17_dict_merge_empty():
    r = _merge_run({"items": [], "mode": "dict_merge"})
    assert r["merged"] == {}


def test_18_dict_merge_two_dicts():
    r = _merge_run({"items": [{"a": 1}, {"b": 2}], "mode": "dict_merge"})
    assert r["merged"] == {"a": 1, "b": 2}


def test_19_dict_merge_later_wins():
    r = _merge_run({"items": [{"a": 1}, {"a": 2}], "mode": "dict_merge"})
    assert r["merged"] == {"a": 2}


def test_20_dict_merge_non_dict_error():
    # D8d.4: non-dict items -> per-item errors.
    r = _merge_run({"items": [{"a": 1}, 42], "mode": "dict_merge"})
    assert r["merged"] == {"a": 1}
    assert r["errors"]
    assert r["error_count"] >= 1


def test_21_dict_merge_shallow():
    # D8d.4: shallow, no recursion.
    r = _merge_run(
        {"items": [{"x": {"a": 1}}, {"x": {"b": 2}}], "mode": "dict_merge"}
    )
    assert r["merged"] == {"x": {"b": 2}}


# ---------------------------------------------------------------------------
# Group 4 — flow.merge: invalid mode / port-contract
# ---------------------------------------------------------------------------


def test_22_invalid_mode_no_raise():
    r = _merge_run({"mode": "bogus"})
    assert r["merged"] is None
    assert r["errors"]
    assert r["error_count"] >= 1
    assert r["mode"] == "bogus"


def test_23_empty_mode_default():
    r = _merge_run({"mode": ""})
    assert r["mode"] == "last_wins"


def test_24_none_mode_default():
    r = _merge_run({"mode": None})
    assert r["mode"] == "last_wins"


_PORT_CONTRACT_PAYLOADS = [
    {},
    {"items": []},
    {"items": [None]},
    {"items": [None, None]},
    {"items": [None, 1]},
    {"items": [1, None]},
    {"items": [1, 2]},
    {"items": [1, None, 2, None]},
    {"items": 42},
    {"items": [1, 2, 3], "mode": "last_wins"},
    {"items": [[1, 2], [3, 4]], "mode": "last_wins"},
    {"items": [], "mode": "concat"},
    {"items": [1, 2, 3], "mode": "concat"},
    {"items": [[1, 2], [3, 4]], "mode": "concat"},
    {"items": [[1, 2], 3], "mode": "concat"},
    {"items": [None, 1], "mode": "concat"},
    {"items": [], "mode": "dict_merge"},
    {"items": [{"a": 1}, {"b": 2}], "mode": "dict_merge"},
    {"items": [{"a": 1}, {"a": 2}], "mode": "dict_merge"},
    {"items": [{"a": 1}, 42], "mode": "dict_merge"},
    {"items": [{"x": {"a": 1}}, {"x": {"b": 2}}], "mode": "dict_merge"},
    {"mode": "bogus"},
    {"mode": ""},
    {"mode": None},
]

_REQUIRED_KEYS = {
    "merged", "count", "items", "mode", "errors", "error_count",
}


def test_25_port_contract_all_modes():
    for payload in _PORT_CONTRACT_PAYLOADS:
        r = _merge_run(payload)
        assert isinstance(r, dict), f"not a dict for payload={payload!r}: {r!r}"
        missing = _REQUIRED_KEYS - set(r.keys())
        assert not missing, (
            f"payload={payload!r} missing keys {missing}; got {sorted(r)}"
        )


def test_26_dry_run_matches_run():
    for payload in [
        {},
        {"items": [1, 2, 3]},
        {"items": [[1, 2], [3, 4]], "mode": "concat"},
        {"items": [{"a": 1}, {"b": 2}], "mode": "dict_merge"},
        {"mode": "bogus"},
    ]:
        assert _merge_run(payload) == _merge_dry(payload), (
            f"run/dry_run mismatch for payload={payload!r}"
        )


# ---------------------------------------------------------------------------
# Group 5 — flow.wait: real sleep via FakeClock
# ---------------------------------------------------------------------------


def test_27_wait_5_seconds():
    ctx = _wait_ctx()
    before = ctx.clock.now()
    r = WaitNode().run(ctx, {"seconds": 5})
    after = ctx.clock.now()
    assert after - before == pytest.approx(5)
    assert r == {"slept": 5, "seconds": 5, "dry_run": False}


def test_28_wait_zero():
    ctx = _wait_ctx()
    before = ctx.clock.now()
    r = WaitNode().run(ctx, {"seconds": 0})
    after = ctx.clock.now()
    assert after - before == 0
    assert r["slept"] == 0


def test_29_wait_empty_default():
    ctx = _wait_ctx()
    before = ctx.clock.now()
    r = WaitNode().run(ctx, {})
    after = ctx.clock.now()
    assert after - before == pytest.approx(1.0)
    assert r["slept"] == pytest.approx(1.0)


def test_30_wait_none_default():
    ctx = _wait_ctx()
    before = ctx.clock.now()
    WaitNode().run(ctx, {"seconds": None})
    after = ctx.clock.now()
    assert after - before == pytest.approx(1.0)


def test_31_wait_float():
    ctx = _wait_ctx()
    before = ctx.clock.now()
    WaitNode().run(ctx, {"seconds": 2.5})
    after = ctx.clock.now()
    assert after - before == pytest.approx(2.5)


def test_32_wait_string_numeric():
    # Spec: float(raw) without isinstance — "3" becomes 3.0. NOT a bug.
    ctx = _wait_ctx()
    before = ctx.clock.now()
    r = WaitNode().run(ctx, {"seconds": "3"})
    after = ctx.clock.now()
    assert after - before == pytest.approx(3.0)
    assert r["slept"] == pytest.approx(3.0)


def test_33_wait_negative_raises():
    ctx = _wait_ctx()
    with pytest.raises(NodeExecutionError):
        WaitNode().run(ctx, {"seconds": -1})


def test_34_wait_nonnumeric_raises():
    ctx = _wait_ctx()
    with pytest.raises(NodeExecutionError):
        WaitNode().run(ctx, {"seconds": "abc"})


# ---------------------------------------------------------------------------
# Group 6 — flow.wait: dry-run (D8d.1)
# ---------------------------------------------------------------------------


def test_35_dry_run_does_not_sleep():
    ctx = _wait_ctx(dry_run=True)
    before = ctx.clock.now()
    r = WaitNode().dry_run(ctx, {"seconds": 5})
    after = ctx.clock.now()
    assert after == before
    assert r["slept"] == 0
    assert r["seconds"] == pytest.approx(5)
    assert r["would_sleep"] == pytest.approx(5)
    assert r["dry_run"] is True


def test_36_run_with_dry_run_ctx():
    ctx = _wait_ctx(dry_run=True)
    before = ctx.clock.now()
    r = WaitNode().run(ctx, {"seconds": 5})
    after = ctx.clock.now()
    assert after - before == pytest.approx(5)
    assert r["dry_run"] is True


def test_37_dry_run_does_not_advance_clock():
    ctx_run = _wait_ctx()
    ctx_dry = _wait_ctx()
    WaitNode().run(ctx_run, {"seconds": 3})
    WaitNode().dry_run(ctx_dry, {"seconds": 3})
    assert ctx_run.clock.now() == pytest.approx(3)
    assert ctx_dry.clock.now() == 0


def test_37b_dry_run_validates_seconds():
    ctx = _wait_ctx(dry_run=True)
    with pytest.raises(NodeExecutionError):
        WaitNode().dry_run(ctx, {"seconds": -1})
    with pytest.raises(NodeExecutionError):
        WaitNode().dry_run(ctx, {"seconds": "abc"})


# ---------------------------------------------------------------------------
# Group 7 — flow.wait: time.sleep not called (runtime monkeypatch)
# ---------------------------------------------------------------------------


def test_38_no_time_sleep(monkeypatch):
    import time as _time

    def _boom(*a, **kw):
        raise AssertionError("time.sleep called")

    monkeypatch.setattr(_time, "sleep", _boom)

    ctx = _wait_ctx()
    # If flow.wait ever imported/called time.sleep directly, this raises.
    WaitNode().run(ctx, {"seconds": 0.001})


# ---------------------------------------------------------------------------
# Group 8 — discovery, retry_safe
# ---------------------------------------------------------------------------


def test_39_discover_plugins_has_merge_wait():
    from lokdocsqr.core.plugins import discover_plugins
    ids = set(discover_plugins())
    assert "flow.merge" in ids, f"flow.merge missing; ids={sorted(ids)}"
    assert "flow.wait" in ids, f"flow.wait missing; ids={sorted(ids)}"


def test_40_registry_get_returns_class():
    merge_cls = registry.get("flow.merge")
    assert merge_cls is not None
    wait_cls = registry.get("flow.wait")
    assert wait_cls is not None


def test_41_retry_safe_true():
    assert MergeNode.retry_safe is True
    assert WaitNode.retry_safe is True


# ---------------------------------------------------------------------------
# Group 9 — config
# ---------------------------------------------------------------------------


def test_42_appconfig_flow_depth(tmp_path):
    cfg = AppConfig(base_dir=tmp_path)
    assert cfg.flow.max_subflow_depth == 4


def test_43_flowconfig_default():
    assert FlowConfig().max_subflow_depth == 4


def test_44_appconfig_flow_independent(tmp_path):
    a = AppConfig(base_dir=tmp_path)
    b = AppConfig(base_dir=tmp_path)
    assert a.flow is not b.flow
    a.flow.max_subflow_depth = 99
    assert b.flow.max_subflow_depth == 4


def test_45_load_config_without_flow_key(tmp_path):
    # D8d.6: old JSON without "flow" key -> default_factory gives depth=4.
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps({"base_dir": str(tmp_path), "default_dry_run": True}),
        encoding="utf-8",
    )
    cfg = load_config(cfg_path)
    assert cfg.flow.max_subflow_depth == 4


# ---------------------------------------------------------------------------
# Group 10 — runtime smoke registration (8a/8b/8c)
# ---------------------------------------------------------------------------


def test_46_discovery_regression():
    import importlib
    for modname in [
        "lokdocsqr.nodes.flow.filter",
        "lokdocsqr.nodes.flow.if_branch",
        "lokdocsqr.nodes.utils.set_variables",
        "lokdocsqr.nodes.utils.json_validate",
        "lokdocsqr.nodes.utils.path_template",
    ]:
        importlib.import_module(modname)

    from lokdocsqr.core.plugins import discover_plugins
    ids = set(discover_plugins())
    for expected in [
        "flow.filter",
        "flow.if_branch",
        "utils.set_variables",
        "utils.json_validate",
        "utils.path_template",
    ]:
        assert expected in ids, f"{expected} missing; ids={sorted(ids)}"
