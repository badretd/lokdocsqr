"""Phase 7b — build_action_plan contracts."""
from __future__ import annotations

from pathlib import Path

import pytest

from lokdocsqr.core.models import PolicyAction
from lokdocsqr.core.planning import build_action_plan
from lokdocsqr.core.policy import PolicyEngine


class _FakeSpec:
    def __init__(self, node_id, params):
        self.id = node_id
        self.params = params


class _FakeDocument:
    def __init__(self, specs):
        self.nodes = specs


class _FakeGraph:
    def __init__(self, ordered_ids, handlers, document):
        self.ordered_node_ids = list(ordered_ids)
        self.handlers = handlers
        self.document = document


class _DryNode:
    def __init__(self, fail=False):
        self.fail = fail
        self.dry_run_calls = 0
        self.run_calls = 0

    def dry_run(self, ctx, payload):
        self.dry_run_calls += 1
        if self.fail:
            raise RuntimeError("dry failure")
        return {"ok": True}

    def run(self, ctx, payload):
        self.run_calls += 1
        raise AssertionError("run must not be called")


class _ScopeSpy:
    def __init__(self):
        self.calls = []

    def check(self, path, write=False):
        self.calls.append((Path(path), write))
        return Path(path)


class _Ctx:
    def __init__(self):
        self.scope = _ScopeSpy()
        self.variables = {}


def _make_graph(entries):
    ids = []
    specs = []
    handlers = {}
    for node_id, params, node in entries:
        ids.append(node_id)
        specs.append(_FakeSpec(node_id, params))
        if node is not None:
            handlers[node_id] = node
    return _FakeGraph(ids, handlers, _FakeDocument(specs))


def test_build_action_plan_calls_dry_run_not_run():
    node = _DryNode()
    graph = _make_graph([("access.list_files", {"path": "x"}, node)])
    build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert node.dry_run_calls == 1
    assert node.run_calls == 0


def test_build_action_plan_collects_parallel_success(tmp_path):
    read_node = _DryNode()
    write_node = _DryNode()
    out = tmp_path / "out.txt"
    graph = _make_graph(
        [
            ("access.list_files", {"path": "a"}, read_node),
            ("fs.write_file", {"path_template": str(out)}, write_node),
        ]
    )
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert plan.errors == ()
    assert len(plan.proposals) == 2
    assert len(plan.decisions) == 2
    assert len(plan.previews) == 2
    assert [d.action for d in plan.decisions] == [
        PolicyAction.ALLOW,
        PolicyAction.REQUIRE_VERIFY,
    ]
    assert plan.proposals[0].target_paths == ("a",)
    assert plan.proposals[1].target_paths == (str(out),)
    assert plan.previews[0].path == "a"
    assert plan.previews[1].path == str(out)
    assert plan.blocked is False


def test_build_action_plan_dry_run_exception_becomes_plan_error():
    node = _DryNode(fail=True)
    graph = _make_graph([("access.list_files", {"path": "x"}, node)])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert len(plan.errors) == 1
    assert plan.errors[0].node_id == "access.list_files"
    assert plan.errors[0].exc_type == "RuntimeError"
    assert plan.proposals == ()
    assert plan.decisions == ()
    assert plan.previews == ()
    assert plan.blocked is True


def test_build_action_plan_validation_error_becomes_plan_error():
    node = _DryNode()
    graph = _make_graph([("fs.write_file", {}, node)])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert len(plan.errors) == 1
    assert plan.errors[0].exc_type == "ProposalValidationError"
    assert plan.proposals == ()
    assert plan.blocked is True


def test_build_action_plan_blocked_on_policy_block():
    node = _DryNode()
    graph = _make_graph([("unknown.node", {}, node)])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert len(plan.decisions) == 1
    assert plan.decisions[0].action is PolicyAction.BLOCK
    assert plan.errors == ()
    assert plan.blocked is True


def test_build_action_plan_not_blocked_on_require_verify(tmp_path):
    node = _DryNode()
    out = tmp_path / "out.txt"
    graph = _make_graph([("fs.write_file", {"path_template": str(out)}, node)])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert plan.decisions[0].action is PolicyAction.REQUIRE_VERIFY
    assert plan.errors == ()
    assert plan.blocked is False


def test_build_action_plan_empty_graph():
    graph = _make_graph([])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert plan.proposals == ()
    assert plan.decisions == ()
    assert plan.previews == ()
    assert plan.errors == ()
    assert plan.blocked is False


def test_build_action_plan_deterministic():
    node = _DryNode()
    graph = _make_graph([("access.list_files", {"path": "x"}, node)])
    ctx = _Ctx()
    policy = PolicyEngine.default()
    first = build_action_plan(graph, ctx, policy)
    second = build_action_plan(graph, ctx, policy)
    assert first == second


def test_build_action_plan_does_not_use_ctx_variables():
    node = _DryNode()
    graph = _make_graph([("access.list_files", {"path": "x"}, node)])
    ctx1 = _Ctx()
    ctx1.variables = {"x": 1}
    ctx2 = _Ctx()
    ctx2.variables = {"x": 2}
    policy = PolicyEngine.default()
    assert build_action_plan(graph, ctx1, policy) == build_action_plan(
        graph, ctx2, policy
    )


def test_build_action_plan_missing_handler_is_plan_error():
    graph = _make_graph([("x", {}, None)])
    plan = build_action_plan(graph, _Ctx(), PolicyEngine.default())
    assert len(plan.errors) == 1
    assert plan.errors[0].node_id == "x"
    assert plan.errors[0].exc_type == "KeyError"
    assert plan.decisions == ()
    assert plan.blocked is True
