"""Clock Protocol / FakeClock / SystemClock (SPEC §2)."""
import time
import pytest


def test_fakeclock_now_stable_without_sleep():
    from lokdocsqr.core.clock import FakeClock
    c = FakeClock()
    t0 = c.now()
    assert c.now() == t0
    time.sleep(0.01)
    assert c.now() == t0, "now() must not advance on its own"


def test_fakeclock_sleep_shifts_now_and_no_real_time():
    from lokdocsqr.core.clock import FakeClock
    c = FakeClock()
    t0 = c.now()
    p0 = time.perf_counter()
    c.sleep(10.0)
    p1 = time.perf_counter()
    assert c.now() == t0 + 10.0
    assert (p1 - p0) < 0.05, f"real time elapsed {(p1-p0)*1000:.1f}ms (expected <50ms)"


def test_fakeclock_advance():
    from lokdocsqr.core.clock import FakeClock
    c = FakeClock(start=100.0)
    c.advance(5.0)
    assert c.now() == 105.0


def test_systemclock_now_delegates_to_time_time(monkeypatch):
    from lokdocsqr.core.clock import SystemClock
    monkeypatch.setattr(time, "time", lambda: 42.5)
    assert SystemClock().now() == 42.5


def test_systemclock_sleep_delegates_to_time_sleep(monkeypatch):
    from lokdocsqr.core.clock import SystemClock
    calls = []
    monkeypatch.setattr(time, "sleep", lambda s: calls.append(s))
    SystemClock().sleep(1.5)
    assert calls == [1.5]


def test_clock_protocol_exposes_now_and_sleep():
    from lokdocsqr.core.clock import Clock
    assert hasattr(Clock, "now")
    assert hasattr(Clock, "sleep")
