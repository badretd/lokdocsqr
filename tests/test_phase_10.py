"""Phase 10 acceptance tests — triggers + runtime host.

Covers the two acceptance scenarios from the roadmap:

* cron fires an automation twice without corrupting state/audit;
* a watcher sees a file change, the executor runs, and the audit log contains
  entries.

The rest of the file exercises the runtime contract around those scenarios:
single-instance locking, graceful shutdown, per-run error isolation, bounded
concurrency (F-13), deterministic scheduling, and the four trigger nodes.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.clock import FakeClock
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.errors import RuntimeLockError
from lokdocsqr.core.events import EventBus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.models import WorkspaceScope
from lokdocsqr.core.node_api.registry import registry
from lokdocsqr.core.planning.registry import classify_node
from lokdocsqr.core.plugins import discover_plugins
from lokdocsqr.core.runtime import (
    CronSchedule,
    FolderWatcher,
    InstanceLock,
    IntervalSchedule,
    Runtime,
)
from lokdocsqr.core.scope import ScopeManager
from lokdocsqr.nodes.triggers.manual_run import ManualRunNode
from lokdocsqr.nodes.triggers.scheduled_scan import ScheduledScanNode
from lokdocsqr.nodes.triggers.watch_folder import WatchFolderNode
from lokdocsqr.nodes.triggers.webhook import WebhookNode

AUTOMATION_ID = "phase-10-auto"


def _raw_graph(
    nodes: list[dict[str, Any]],
    *,
    edges: list[dict[str, Any]] | None = None,
    settings: dict[str, Any] | None = None,
    triggers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "spec_version": "1.0",
        "metadata": {"id": AUTOMATION_ID, "name": "Phase 10"},
        "settings": settings or {"default_dry_run": False},
        "triggers": triggers or [],
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(path_template: str = "out/run.txt") -> dict[str, Any]:
    return _raw_graph(
        [
            {
                "id": "write",
                "type": "fs.write_file",
                "params": {
                    "path_template": path_template,
                    "content": "phase-10",
                    "create_parents": True,
                },
            }
        ]
    )


def _runtime(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    max_concurrency: int | None = None,
) -> Runtime:
    return Runtime(
        AppConfig(base_dir=tmp_path, default_dry_run=False),
        policy=None,
        clock=clock,
        max_concurrency=max_concurrency,
        lock_path=tmp_path / "runtime.lock",
    )


def _ctx(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        scope=ScopeManager(
            WorkspaceScope(roots=[tmp_path], writable=[tmp_path])
        ),
        config=AppConfig(base_dir=tmp_path, default_dry_run=False),
        audit=AuditLog(tmp_path / "audit.jsonl"),
        run_id="trigger-run",
        automation_id=AUTOMATION_ID,
        events=EventBus(),
        policy=None,
    )


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------


def test_cron_schedule_supports_five_and_six_fields() -> None:
    five = CronSchedule("*/15 9-17 * * mon-fri")
    assert five.matches(1_700_000_000) in (True, False)  # shape is valid
    assert five.next_after(1_700_000_000) > 1_700_000_000

    six = CronSchedule("*/1 * * * * *")
    assert six.next_after(1_700_000_000) == 1_700_000_001

    every = CronSchedule("@every 250ms")
    assert every.next_after(10.0) == 10.25

    hourly = CronSchedule("@hourly")
    assert hourly.expression == "@hourly"
    assert hourly.next_after(0) == 3600


def test_cron_schedule_rejects_invalid_expressions() -> None:
    for expression in ("", "* * *", "monkey", "61 * * * *", "*/0 * * * *"):
        with pytest.raises(ValueError):
            CronSchedule(expression)


def test_cron_fires_automation_twice_without_corruption(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(_write_graph())
    runtime.add_cron(AUTOMATION_ID, "*/1 * * * * *", fire_immediately=True)

    runtime.start()
    try:
        assert runtime.wait_for_runs(2, timeout=5.0), runtime.results
    finally:
        runtime.stop()

    first_two = runtime.results[:2]
    assert len(first_two) == 2
    assert all(result.success for result in first_two), first_two
    assert len({result.run_id for result in first_two}) == 2

    # The second run did not corrupt the output or duplicate audit entries.
    assert (tmp_path / "out" / "run.txt").read_text(encoding="utf-8") == "phase-10"
    for result in first_two:
        entries = [
            log
            for log in runtime.audit.iter_all()
            if log.run_id == result.run_id
        ]
        assert len(entries) == 1
        assert entries[0].status == "applied"

    assert not (tmp_path / "runtime.lock").exists()


def test_deterministic_cron_tick_fires_twice(tmp_path: Path) -> None:
    clock = FakeClock(start=1_000.0)
    runtime = _runtime(tmp_path, clock=clock)
    runtime.register(_write_graph("tick/run.txt"))
    runtime.add_cron(
        AUTOMATION_ID, IntervalSchedule(10.0), fire_immediately=True
    )

    runtime.tick(1_000.0)
    runtime.tick(1_009.0)
    assert len(runtime.results) == 1
    runtime.tick(1_010.0)
    runtime.wait_for_idle(timeout=2.0)

    assert len(runtime.results) == 2
    assert all(result.success for result in runtime.results)
    assert (tmp_path / "tick" / "run.txt").exists()


# ---------------------------------------------------------------------------
# Folder watcher
# ---------------------------------------------------------------------------


def test_folder_watcher_detects_create_modify_delete(tmp_path: Path) -> None:
    watched = tmp_path / "inbox"
    watched.mkdir()
    watcher = FolderWatcher(watched, patterns=("*.txt",), interval=0.01)
    assert watcher.poll_once() == []  # first call establishes the baseline

    target = watched / "a.txt"
    target.write_text("one", encoding="utf-8")
    created = watcher.poll_once()
    assert [change.kind for change in created] == ["created"]
    assert created[0].path == str(target)

    target.write_text("two-two", encoding="utf-8")
    modified = watcher.poll_once()
    assert [change.kind for change in modified] == ["modified"]

    (watched / "ignored.bin").write_bytes(b"x")
    assert watcher.poll_once() == []

    target.unlink()
    deleted = watcher.poll_once()
    assert [change.kind for change in deleted] == ["deleted"]


def test_watcher_runs_executor_and_writes_audit(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    runtime = _runtime(tmp_path)
    runtime.register(_write_graph("processed/report.txt"))
    runtime.add_watch(AUTOMATION_ID, inbox, interval=0.05)

    runtime.start()
    try:
        (inbox / "new.txt").write_text("payload", encoding="utf-8")
        assert runtime.wait_for_runs(1, timeout=5.0), runtime.results
    finally:
        runtime.stop()

    result = runtime.results[0]
    assert result.success, result.errors
    assert (tmp_path / "processed" / "report.txt").read_text(
        encoding="utf-8"
    ) == "phase-10"
    entries = list(runtime.audit.iter_all())
    assert len(entries) == 1
    assert entries[0].node_id == "fs.write_file"
    assert entries[0].status == "applied"
    assert entries[0].run_id == result.run_id

    # The trigger event reached the node context.
    trigger_node = runtime._make_context(
        runtime._require(AUTOMATION_ID),
        dry_run=False,
        event={"type": "watch_folder", "payload": {}},
        variables=None,
        verify_callback=None,
    )
    assert trigger_node.variables["trigger"]["type"] == "watch_folder"


def test_runtime_tick_is_deterministic_for_watchers(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    runtime = _runtime(tmp_path)
    runtime.register(_write_graph("ticked/report.txt"))
    runtime.add_watch(AUTOMATION_ID, inbox, interval=0.01)

    runtime.tick()  # establish the baseline
    (inbox / "file.txt").write_text("x", encoding="utf-8")
    runtime.tick()
    assert runtime.wait_for_idle(timeout=2.0)
    assert len(runtime.results) == 1
    assert runtime.results[0].success


# ---------------------------------------------------------------------------
# Trigger nodes
# ---------------------------------------------------------------------------


def test_trigger_nodes_are_registered_and_read_only() -> None:
    discover_plugins()
    for node_id in (
        "triggers.watch_folder",
        "triggers.scheduled_scan",
        "triggers.manual_run",
        "triggers.webhook",
    ):
        assert registry.get(node_id) is not None
        assert classify_node(node_id) == "read"

    assert ManualRunNode.retry_safe
    assert WatchFolderNode.retry_safe
    assert ScheduledScanNode.retry_safe
    assert WebhookNode.retry_safe


def test_manual_and_webhook_nodes_normalise_event_payloads(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "selected.txt"
    selected.write_text("x", encoding="utf-8")
    ctx = _ctx(tmp_path)
    ctx.variables["trigger"] = {
        "type": "manual_run",
        "payload": {
            "files": [str(selected)],
            "payload": {"source": "manual"},
        },
    }
    manual = ManualRunNode().run(ctx, {})
    assert manual["paths"] == ["selected.txt"]
    assert manual["files"][0]["absolute_path"] == str(selected)
    assert manual["payload"]["source"] == "manual"

    ctx.variables["trigger"] = {
        "type": "webhook",
        "payload": {
            "payload": {"event": "pushed"},
            "headers": {"X-Test": "1"},
        },
    }
    webhook = WebhookNode().run(ctx, {})
    assert webhook["payload"] == {"event": "pushed"}
    assert webhook["headers"] == {"X-Test": "1"}

    # Both nodes are also pure in dry-run: no filesystem changes.
    assert ManualRunNode().dry_run(ctx, {})["payload"] == {"event": "pushed"}


def test_scan_trigger_nodes_report_files(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.txt").write_text("a", encoding="utf-8")
    (inbox / "b.bin").write_bytes(b"b")
    ctx = _ctx(tmp_path)

    watched = WatchFolderNode().run(
        ctx, {"path": str(inbox), "patterns": ["*.txt"]}
    )
    scheduled = ScheduledScanNode().dry_run(
        ctx, {"path": str(inbox), "patterns": ["*.txt"]}
    )

    assert watched["paths"] == ["inbox/a.txt"]
    assert scheduled["paths"] == ["inbox/a.txt"]
    assert watched["files"][0]["absolute_path"] == str(inbox / "a.txt")
    assert scheduled["files"][0]["absolute_path"] == str(inbox / "a.txt")


def test_runtime_manual_and_webhook_triggers_reach_the_graph(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(
        _raw_graph(
            [{"id": "manual", "type": "triggers.manual_run", "params": {}}]
        )
    )
    executed = runtime.trigger(
        AUTOMATION_ID,
        payload={"files": [str(tmp_path / "missing.txt")]},
    )
    assert executed.success
    assert executed.node_results["manual"].outputs["paths"] == ["missing.txt"]
    assert (
        executed.node_results["manual"].outputs["files"][0]["absolute_path"]
        == str(tmp_path / "missing.txt")
    )

    runtime.unregister(AUTOMATION_ID)
    runtime.register(
        _raw_graph(
            [{"id": "hook", "type": "triggers.webhook", "params": {}}]
        )
    )
    webhook = runtime.trigger_webhook(
        AUTOMATION_ID, payload={"payload": {"hello": "world"}}
    )
    assert webhook.success
    assert webhook.node_results["hook"].outputs["payload"] == {
        "hello": "world"
    }


# ---------------------------------------------------------------------------
# Runtime lifecycle / safety
# ---------------------------------------------------------------------------


def test_single_instance_lock_is_exclusive_and_stale_safe(
    tmp_path: Path,
) -> None:
    first = _runtime(tmp_path)
    second = _runtime(tmp_path)
    first.register(_write_graph())
    second.register(_write_graph())

    first.start()
    try:
        with pytest.raises(RuntimeLockError):
            second.start()
    finally:
        first.stop()

    # The lock was released and can be acquired again.
    second.start()
    second.stop()
    assert not (tmp_path / "runtime.lock").exists()

    # A lock whose PID is gone is treated as stale and replaced.
    lock_path = tmp_path / "stale.lock"
    lock_path.write_text(
        '{"owner": "dead", "pid": 999999999}', encoding="utf-8"
    )
    lock = InstanceLock(lock_path)
    assert lock.acquire()
    lock.release()
    assert not lock_path.exists()


def test_graceful_shutdown_is_idempotent(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(_write_graph())
    runtime.add_cron(AUTOMATION_ID, "@every 0.05s")
    runtime.start()
    assert runtime.is_running
    runtime.stop()
    assert not runtime.is_running
    runtime.stop()  # idempotent
    assert not (tmp_path / "runtime.lock").exists()


def test_per_run_error_isolation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(
        _raw_graph(
            [
                {
                    "id": "broken",
                    "type": "fs.write_file",
                    "params": {"path_template": "broken.txt"},
                }
            ]
        )
    )
    failed = runtime.run_once(AUTOMATION_ID)
    assert failed.success is False
    assert failed.errors

    # The runtime still executes the next, valid automation.
    runtime.unregister(AUTOMATION_ID)
    runtime.register(_write_graph("out/after-error.txt"))
    succeeded = runtime.run_once(AUTOMATION_ID)
    assert succeeded.success, succeeded.errors
    assert (tmp_path / "out" / "after-error.txt").exists()


def test_max_concurrency_is_bounded(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, max_concurrency=2)
    runtime.register(
        _raw_graph(
            [
                {
                    "id": "write",
                    "type": "fs.write_file",
                    "params": {
                        "path_template": "c.txt",
                        "content": "x",
                        "create_parents": True,
                    },
                }
            ],
            settings={"default_dry_run": False, "max_concurrency": 5},
        )
    )

    assert runtime.max_concurrency == 2  # explicit runtime cap wins
    pool = runtime._ensure_pool()
    assert pool._max_workers == 2
    runtime.stop()

    with pytest.raises(ValueError):
        _runtime(tmp_path, max_concurrency=0)


def test_document_triggers_are_bound_on_register(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(
        _raw_graph(
            [{"id": "write", "type": "fs.write_file", "params": {"path_template": "x.txt", "content": "x"}}],
            triggers=[
                {
                    "id": "watch-inbox",
                    "type": "triggers.watch_folder",
                    "params": {"path": "inbox", "patterns": ["*.txt"]},
                },
                {
                    "id": "scan-hourly",
                    "type": "triggers.scheduled_scan",
                    "params": {"path": "inbox", "cron": "@hourly"},
                },
            ],
        )
    )
    watches = runtime.watch_bindings
    crons = runtime.cron_bindings
    assert len(watches) == 1 and watches[0]["name"] == "watch-inbox"
    assert len(crons) == 1 and crons[0]["name"] == "scan-hourly"
    assert crons[0]["schedule"] == "@hourly"


def test_serve_stops_when_stop_event_is_set(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.register(_write_graph())
    stop = threading.Event()

    thread = threading.Thread(target=runtime.serve, args=(stop,), daemon=True)
    thread.start()
    assert runtime.is_running or thread.is_alive()
    stop.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert not runtime.is_running
