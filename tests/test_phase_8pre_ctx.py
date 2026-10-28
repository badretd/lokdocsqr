"""ExecutionContext: set_variable / defaults / R37 (SPEC §1, §2)."""
import pytest

from lokdocsqr.core.errors import NodeExecutionError


def test_set_variable_normal_key(exec_ctx):
    exec_ctx.set_variable("my_custom_key", "value")
    assert exec_ctx.variables["my_custom_key"] == "value"


def test_set_variable_reserved_names_raise(exec_ctx):
    from lokdocsqr.core.executor.context import RESERVED_VARIABLES
    assert RESERVED_VARIABLES, "RESERVED_VARIABLES must be non-empty"
    for k in RESERVED_VARIABLES:
        with pytest.raises(NodeExecutionError):
            exec_ctx.set_variable(k, 1)


def test_variables_dict_isolated_per_instance(exec_ctx):
    from lokdocsqr.core.executor.context import ExecutionContext
    from lokdocsqr.core.scope import ScopeManager
    from lokdocsqr.core.audit import AuditLog
    from lokdocsqr.core.clock import FakeClock
    from lokdocsqr.core.events import EventBus
    from types import SimpleNamespace
    import tempfile

    tmp = __import__("pathlib").Path(tempfile.mkdtemp())
    scope = ScopeManager(SimpleNamespace(roots=[tmp], writable=[tmp], allow_symlinks=False))
    audit = AuditLog(tmp / "a.jsonl")
    ctx_a = ExecutionContext(scope=scope, config=SimpleNamespace(base_dir=tmp),
                             audit=audit, run_id="r1", automation_id="a1",
                             clock=FakeClock(), events=EventBus())
    exec_ctx.set_variable("shared_test_key", 1)
    assert "shared_test_key" not in ctx_a.variables


def test_new_fields_have_defaults():
    import inspect
    from lokdocsqr.core.executor.context import ExecutionContext
    sig = inspect.signature(ExecutionContext)
    for name in ("clock", "flow_depth", "automations"):
        assert name in sig.parameters, f"{name} missing from ExecutionContext"
        assert sig.parameters[name].default is not inspect.Parameter.empty, \
            f"{name} has no default (breaks 0-7 backward compat)"


def test_variables_default_factory_dict(exec_ctx):
    """Each ctx gets its own dict; not a shared class-level dict."""
    import inspect
    from lokdocsqr.core.executor.context import ExecutionContext
    field = ExecutionContext.__dataclass_fields__["variables"]
    assert field.default_factory is dict
    assert isinstance(exec_ctx.variables, dict)


def test_policy_none_old_path_r37(exec_ctx):
    """R37: ctx.policy is None -> executor behaves as pre-7c (no gate effect)."""
    from types import SimpleNamespace
    from lokdocsqr.core.executor.engine import Executor
    from lokdocsqr.core.models import NodeState

    class _Node:
        retry_safe = True
        def __init__(self):
            self.run_calls = 0
        def run(self, ctx, payload):
            self.run_calls += 1
            return {"ok": True}
        def dry_run(self, ctx, payload):
            return {"ok": True}

    node = _Node()
    node_spec = SimpleNamespace(id="n1", params={}, inputs=[], outputs=[])
    document = SimpleNamespace(
        nodes=[node_spec], edges=[],
        settings=SimpleNamespace(
            error_handling=SimpleNamespace(
                strategy="fail", max_retries=0,
                retry_delay_seconds=0.0, backoff="none",
            )
        ),
    )
    resolved = SimpleNamespace(
        document=document,
        ordered_node_ids=["n1"],
        handlers={"n1": node},
        states={"n1": NodeState.ENABLED},
    )
    exec_ctx.policy = None
    result = Executor(exec_ctx).execute(resolved)
    assert node.run_calls == 1
    assert result.success
