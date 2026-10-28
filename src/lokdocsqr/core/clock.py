"""Deterministic clock abstraction for the executor (Phase 8, SPEC §2)."""
from __future__ import annotations

import time
from typing import Protocol

__all__ = ["Clock", "SystemClock", "FakeClock"]


class Clock(Protocol):
    """Per-run time source used by ``wait`` and retry backoff."""

    def now(self) -> float:
        """Return the current time in seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Advance/sleep for ``seconds``."""
        ...


class SystemClock:
    """Wall-clock implementation backed by :mod:`time`."""

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock:
    """Deterministic clock for tests; ``sleep`` advances virtual time."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        """Move virtual time forward by ``delta`` seconds."""
        self._now += float(delta)

    def sleep(self, seconds: float) -> None:
        """Advance virtual time without calling :func:`time.sleep`."""
        self._now += float(seconds)
