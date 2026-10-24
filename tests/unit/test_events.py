"""Unit tests for lokdocsqr.core.events."""

from __future__ import annotations

import logging

from lokdocsqr.core.events import EventBus


def test_emit_invokes_single_subscriber():
    bus = EventBus()
    received: list[object] = []
    bus.subscribe("evt", received.append)
    bus.emit("evt", "payload")
    assert received == ["payload"]


def test_emit_invokes_all_subscribers_in_order():
    bus = EventBus()
    calls: list[tuple[str, object]] = []
    bus.subscribe("evt", lambda p: calls.append(("a", p)))
    bus.subscribe("evt", lambda p: calls.append(("b", p)))
    bus.subscribe("evt", lambda p: calls.append(("c", p)))
    bus.emit("evt", 42)
    assert calls == [("a", 42), ("b", 42), ("c", 42)]


def test_emit_without_subscribers_is_noop():
    bus = EventBus()
    # Must not raise when no subscriber is registered for this event.
    bus.emit("unknown-event", object())


def test_emit_isolates_failing_subscriber():
    bus = EventBus()
    calls: list[str] = []

    def bad(payload):
        calls.append("bad")
        raise ValueError("boom")

    def good(payload):
        calls.append("good")

    bus.subscribe("evt", bad)
    bus.subscribe("evt", good)
    bus.emit("evt", None)
    # The raising subscriber must not prevent the remaining ones.
    assert calls == ["bad", "good"]


def test_emit_logs_subscriber_failure(caplog):
    bus = EventBus()

    def bad(payload):
        raise RuntimeError("kaboom")

    bus.subscribe("evt", bad)
    with caplog.at_level(logging.ERROR, logger="lokdocsqr.core.events"):
        bus.emit("evt", None)
    assert any(
        "Event subscriber failed" in rec.getMessage() for rec in caplog.records
    )


def test_clear_removes_all_subscribers():
    bus = EventBus()
    calls: list[object] = []
    bus.subscribe("evt", calls.append)
    bus.clear()
    bus.emit("evt", "ignored")
    assert calls == []
