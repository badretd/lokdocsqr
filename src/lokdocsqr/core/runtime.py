"""Headless runtime host for triggers (Phase 10).

The runtime owns long-running trigger backends (folder watchers and cron-style
schedules), dispatches each run through the real :class:`Executor`, isolates
run failures, and enforces a single-instance lock for ``serve`` mode.

Design constraints:

* This module is under ``core`` and therefore must not import
  ``lokdocsqr.nodes`` or a GUI toolkit. Nodes are resolved through the injected
  :class:`NodeRegistry` (the global registry by default).
* Filesystem mutations go through :mod:`lokdocsqr.core.safe_fs` (lock files)
  or through the executor's nodes. Reads (snapshots, lock inspection) use
  :mod:`pathlib`.
* A trigger never gets its own privileged execution path: every trigger
  dispatches the same ``parse -> validate -> resolve -> build -> execute``
  pipeline as :meth:`Runtime.run_once`.

The public surface is intentionally small:

    Runtime.register(...)            register a WorkflowDocument / raw graph
    Runtime.run_once(...)            execute once, synchronously
    Runtime.add_watch(...)           bind a folder watcher
    Runtime.add_cron(...)            bind a cron / interval schedule
    Runtime.start() / stop()         start and gracefully stop the host
    Runtime.serve()                  block until stopped
    Runtime.tick(...)                drive due work deterministically in tests

``max_concurrency`` (F-13) is implemented with a bounded
:class:`~concurrent.futures.ThreadPoolExecutor`; runs for one automation are
serialised by a per-automation lock so two ticks cannot corrupt its state.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from types import MappingProxyType
from typing import Any

from lokdocsqr.core import safe_fs
from lokdocsqr.core.audit import AuditLog
from lokdocsqr.core.clock import Clock, SystemClock
from lokdocsqr.core.config import AppConfig
from lokdocsqr.core.errors import GraphValidationError, RuntimeLockError, SafeFsError
from lokdocsqr.core.events import EventBus, bus
from lokdocsqr.core.executor.context import ExecutionContext
from lokdocsqr.core.executor.engine import Executor
from lokdocsqr.core.graph import (
    build_graph,
    parse_graph,
    resolve_states,
    validate_graph,
)
from lokdocsqr.core.models import (
    ActionSummary,
    ResolvedGraph,
    RunResult,
    TriggerSpec,
    VerifyResult,
    WorkflowDocument,
    WorkspaceScope,
)
from lokdocsqr.core.node_api.registry import NodeRegistry
from lokdocsqr.core.node_api.registry import registry as global_registry
from lokdocsqr.core.plugins import discover_plugins
from lokdocsqr.core.policy import PolicyEngine
from lokdocsqr.core.scope import ScopeManager

__all__ = [
    "CronSchedule",
    "FileChange",
    "FolderWatcher",
    "InstanceLock",
    "IntervalSchedule",
    "Runtime",
    "RuntimeLockError",
    "TriggerEvent",
    "WatchFolder",
    "Watcher",
]

logger = logging.getLogger(__name__)

DEFAULT_WATCH_INTERVAL = 0.5
DEFAULT_DEBOUNCE = 0.0
DEFAULT_LOCK_NAME = ".lokdocsqr-runtime.lock"
_MAX_CRON_CATCHUP = 1000


# ---------------------------------------------------------------------------
# Cron parsing
# ---------------------------------------------------------------------------

_MONTH_NAMES = {
    name: index
    for index, name in enumerate(
        (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ),
        start=1,
    )
}
_WEEKDAY_NAMES = {
    name: index
    for index, name in enumerate(
        ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
    )
}


def _parse_cron_atom(
    token: str,
    *,
    names: Mapping[str, int] | None,
    allow_seven_sunday: bool,
) -> int:
    lowered = token.strip().lower()
    if names is not None and lowered in names:
        return names[lowered]
    try:
        value = int(lowered)
    except ValueError as exc:
        raise ValueError(f"invalid cron value: {token!r}") from exc
    if allow_seven_sunday and value == 7:
        return 0
    return value


def _parse_cron_field(
    text: str,
    *,
    minimum: int,
    maximum: int,
    names: Mapping[str, int] | None = None,
    allow_seven_sunday: bool = False,
) -> tuple[frozenset[int], bool]:
    """Parse one cron field into values and a ``restricted`` flag."""
    token = text.strip()
    if not token:
        raise ValueError("empty cron field")
    restricted = token != "*"
    values: set[int] = set()

    for part in token.split(","):
        if not part:
            raise ValueError(f"invalid empty cron list item in {text!r}")
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            try:
                step = int(raw_step)
            except ValueError as exc:
                raise ValueError(
                    f"invalid cron step in {part!r}"
                ) from exc
            if step <= 0:
                raise ValueError(f"cron step must be positive: {part!r}")

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            start = _parse_cron_atom(
                raw_start,
                names=names,
                allow_seven_sunday=allow_seven_sunday,
            )
            end = _parse_cron_atom(
                raw_end,
                names=names,
                allow_seven_sunday=allow_seven_sunday,
            )
        else:
            start = end = _parse_cron_atom(
                base,
                names=names,
                allow_seven_sunday=allow_seven_sunday,
            )

        if start > end:
            raise ValueError(f"invalid cron range: {part!r}")
        values.update(range(start, end + 1, step))

    if allow_seven_sunday and 7 in values:
        values.discard(7)
        values.add(0)
    for value in values:
        if value < minimum or value > maximum:
            raise ValueError(
                f"cron value out of range: {value!r} in {text!r}"
            )
    if not values:
        raise ValueError(f"empty cron field: {text!r}")
    return frozenset(values), restricted


@dataclass(frozen=True)
class IntervalSchedule:
    """A fixed interval schedule (seconds)."""

    seconds: float

    def __post_init__(self) -> None:
        if self.seconds <= 0:
            raise ValueError("interval must be positive")

    def next_after(self, timestamp: float) -> float:
        return float(timestamp) + float(self.seconds)

    def __str__(self) -> str:
        return f"@every {self.seconds:g}s"


class CronSchedule:
    """A parsed cron expression.

    Supports standard five-field expressions
    (``minute hour day-of-month month day-of-week``) and the six-field
    extension with a leading seconds field. Month and weekday names are
    accepted. The optional ``@every <duration>`` form is useful for short
    test intervals; durations accept ``s``, ``m``, ``h`` and ``d`` suffixes.

    Times are interpreted in UTC by default so a scheduled run does not depend
    on the host timezone.
    """

    def __init__(
        self,
        expression: str,
        *,
        tz: tzinfo | None = None,
    ) -> None:
        text = expression.strip()
        if not text:
            raise ValueError("cron expression must not be empty")
        self.expression = text
        self._tz: tzinfo = tz or timezone.utc
        self._every_seconds: float | None = None
        self._has_seconds = False

        if text.startswith("@"):
            alias = text.lower()
            aliases = {
                "@yearly": "0 0 1 1 *",
                "@annually": "0 0 1 1 *",
                "@monthly": "0 0 1 * *",
                "@weekly": "0 0 * * 0",
                "@daily": "0 0 * * *",
                "@midnight": "0 0 * * *",
                "@hourly": "0 * * * *",
            }
            if alias.startswith("@every"):
                raw = text[len("@every") :].strip().lower()
                if not raw:
                    raise ValueError("@every requires a duration")
                if raw.endswith("ms"):
                    multiplier = 0.001
                    raw = raw[:-2]
                else:
                    units = {
                        "s": 1.0,
                        "m": 60.0,
                        "h": 3600.0,
                        "d": 86400.0,
                    }
                    unit = raw[-1]
                    if unit in units:
                        multiplier = units[unit]
                        raw = raw[:-1]
                    else:
                        multiplier = 1.0
                try:
                    amount = float(raw)
                except ValueError as exc:
                    raise ValueError(
                        f"invalid @every duration: {text!r}"
                    ) from exc
                if amount <= 0:
                    raise ValueError("@every duration must be positive")
                self._every_seconds = amount * multiplier
                return
            if alias not in aliases:
                raise ValueError(f"unknown cron alias: {text!r}")
            text = aliases[alias]

        parts = text.split()
        if len(parts) not in (5, 6):
            raise ValueError(
                "cron expression must have 5 or 6 fields (got "
                f"{len(parts)}): {self.expression!r}"
            )
        self._has_seconds = len(parts) == 6
        if self._has_seconds:
            (
                seconds,
                minutes,
                hours,
                days,
                months,
                weekdays,
            ) = parts
            self.seconds, _ = _parse_cron_field(
                seconds, minimum=0, maximum=59
            )
        else:
            minutes, hours, days, months, weekdays = parts
            self.seconds = frozenset({0})

        self.minutes, _ = _parse_cron_field(
            minutes, minimum=0, maximum=59
        )
        self.hours, _ = _parse_cron_field(hours, minimum=0, maximum=23)
        self.days, self._dom_restricted = _parse_cron_field(
            days, minimum=1, maximum=31
        )
        self.months, _ = _parse_cron_field(
            months, minimum=1, maximum=12, names=_MONTH_NAMES
        )
        self.weekdays, self._dow_restricted = _parse_cron_field(
            weekdays,
            minimum=0,
            maximum=6,
            names=_WEEKDAY_NAMES,
            allow_seven_sunday=True,
        )

    # -- matching ---------------------------------------------------
    def _day_matches(self, when: datetime) -> bool:
        dom = when.day in self.days
        weekday = (when.weekday() + 1) % 7  # Monday=1 .. Sunday=0
        dow = weekday in self.weekdays
        if self._dom_restricted and self._dow_restricted:
            return dom or dow
        if self._dom_restricted:
            return dom
        if self._dow_restricted:
            return dow
        return True

    def matches(self, timestamp: float) -> bool:
        """Return whether ``timestamp`` satisfies the whole expression."""
        when = datetime.fromtimestamp(float(timestamp), tz=self._tz)
        if self._every_seconds is not None:
            return True
        if when.month not in self.months:
            return False
        if not self._day_matches(when):
            return False
        if when.hour not in self.hours:
            return False
        if when.minute not in self.minutes:
            return False
        return not (self._has_seconds and when.second not in self.seconds)

    # -- next occurrence -------------------------------------------
    @staticmethod
    def _jump_month(when: datetime) -> datetime:
        if when.month == 12:
            return when.replace(
                year=when.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        return when.replace(
            month=when.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    def next_after(self, timestamp: float) -> float:
        """Return the first matching timestamp strictly after ``timestamp``."""
        if self._every_seconds is not None:
            return float(timestamp) + self._every_seconds

        when = datetime.fromtimestamp(float(timestamp), tz=self._tz)
        if self._has_seconds:
            candidate = when.replace(microsecond=0) + timedelta(seconds=1)
        else:
            candidate = when.replace(
                second=0, microsecond=0
            ) + timedelta(minutes=1)

        deadline = candidate + timedelta(days=366 * 5)
        while candidate <= deadline:
            if candidate.month not in self.months:
                candidate = self._jump_month(candidate)
                continue
            if not self._day_matches(candidate):
                candidate = (candidate + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                continue
            if candidate.hour not in self.hours:
                candidate = (candidate + timedelta(hours=1)).replace(
                    minute=0, second=0, microsecond=0
                )
                continue
            if candidate.minute not in self.minutes:
                candidate = (candidate + timedelta(minutes=1)).replace(
                    second=0, microsecond=0
                )
                continue
            if self._has_seconds and candidate.second not in self.seconds:
                candidate += timedelta(seconds=1)
                continue
            return candidate.timestamp()

        raise ValueError(
            f"cron expression never matches within five years: {self.expression!r}"
        )

    def __str__(self) -> str:
        return self.expression


# ---------------------------------------------------------------------------
# Folder watcher
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileChange:
    """One detected filesystem change."""

    path: str
    kind: str
    size: int = 0
    mtime_ns: int = 0
    previous_size: int | None = None
    previous_mtime_ns: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "previous_size": self.previous_size,
            "previous_mtime_ns": self.previous_mtime_ns,
        }


def _as_patterns(patterns: str | Sequence[str] | None) -> tuple[str, ...]:
    if patterns is None:
        return ("*",)
    raw = [patterns] if isinstance(patterns, str) else [str(item) for item in patterns]
    cleaned = tuple(item for item in (part.strip() for part in raw) if item)
    return cleaned or ("*",)


def _merge_changes(
    first: Sequence[FileChange], second: Sequence[FileChange]
) -> list[FileChange]:
    merged: dict[str, FileChange] = {}
    for change in list(first) + list(second):
        merged[change.path] = change
    return [merged[key] for key in sorted(merged)]


class FolderWatcher:
    """Polling folder watcher with glob filters and optional debounce.

    Polling is deliberate: it has no platform-specific dependencies and does
    not rely on inotify/FSEvents. ``poll_once()`` is public so tests and the
    runtime's deterministic ``tick()`` path can drive it without threads.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        patterns: str | Sequence[str] | None = ("*",),
        recursive: bool = True,
        interval: float = DEFAULT_WATCH_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
        events: Sequence[str] | None = None,
        on_changes: Callable[[list[FileChange]], None] | None = None,
    ) -> None:
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        self.path = resolved
        self.patterns = _as_patterns(patterns)
        self.recursive = bool(recursive)
        self.interval = max(0.01, float(interval))
        self.debounce = max(0.0, float(debounce))
        self.events = (
            frozenset(str(item).lower() for item in events)
            if events
            else frozenset({"created", "modified", "deleted"})
        )
        self._on_changes = on_changes
        self._baseline: dict[str, tuple[int, int]] | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- snapshot / diff -------------------------------------------
    def _matches(self, entry: Path) -> bool:
        try:
            relative = entry.relative_to(self.path)
        except ValueError:
            relative = Path(entry.name)
        name = entry.name
        relative_text = relative.as_posix()
        for pattern in self.patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
            if fnmatch.fnmatch(relative_text, pattern):
                return True
        return False

    def _stat_tuple(self, entry: Path) -> tuple[int, int] | None:
        try:
            stat = entry.stat()
        except OSError:
            return None
        return (int(stat.st_mtime_ns), int(stat.st_size))

    def snapshot(self) -> dict[str, tuple[int, int]]:
        """Return ``{absolute_path: (mtime_ns, size)}`` for matching files."""
        result: dict[str, tuple[int, int]] = {}
        try:
            exists = self.path.exists()
        except OSError:
            return result
        if not exists:
            return result

        try:
            if self.path.is_file():
                if self._matches(self.path):
                    stat = self._stat_tuple(self.path)
                    if stat is not None:
                        result[str(self.path)] = stat
                return result
        except OSError:
            return result

        stack: list[Path] = [self.path]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir():
                        if self.recursive and not entry.is_symlink():
                            stack.append(entry)
                        continue
                    if not entry.is_file():
                        continue
                except OSError:
                    continue
                if not self._matches(entry):
                    continue
                stat = self._stat_tuple(entry)
                if stat is not None:
                    result[str(entry)] = stat
        return result

    def poll_once(self) -> list[FileChange]:
        """Diff the current snapshot and return changes since the last call."""
        current = self.snapshot()
        with self._lock:
            previous = self._baseline
            self._baseline = current
        if previous is None:
            return []

        changes: list[FileChange] = []
        for path in sorted(set(previous) | set(current)):
            before = previous.get(path)
            after = current.get(path)
            if before is None and after is not None:
                kind = "created"
                size, mtime_ns = after
                change = FileChange(
                    path=path,
                    kind=kind,
                    size=size,
                    mtime_ns=mtime_ns,
                )
            elif before is not None and after is None:
                kind = "deleted"
                size, mtime_ns = before
                change = FileChange(
                    path=path,
                    kind=kind,
                    size=size,
                    mtime_ns=mtime_ns,
                    previous_size=size,
                    previous_mtime_ns=mtime_ns,
                )
            elif before is not None and after is not None and before != after:
                kind = "modified"
                size, mtime_ns = after
                change = FileChange(
                    path=path,
                    kind=kind,
                    size=size,
                    mtime_ns=mtime_ns,
                    previous_size=before[1],
                    previous_mtime_ns=before[0],
                )
            else:
                continue
            if change.kind in self.events:
                changes.append(change)
        return changes

    def emit(self, changes: Sequence[FileChange]) -> None:
        """Deliver ``changes`` to the callback (used by ``Runtime.tick``)."""
        if not changes or self._on_changes is None:
            return
        try:
            self._on_changes(list(changes))
        except Exception:
            logger.exception("folder watcher callback failed")

    # -- thread lifecycle ------------------------------------------
    def start(self) -> FolderWatcher:
        if self._thread is not None and self._thread.is_alive():
            return self
        with self._lock:
            self._baseline = self.snapshot()
        self._stop_event.clear()
        thread = threading.Thread(
            target=self._loop,
            name=f"lokdocsqr-watch-{self.path.name or 'root'}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval):
            changes = self.poll_once()
            if not changes:
                continue
            if self.debounce > 0:
                self._stop_event.wait(self.debounce)
                changes = _merge_changes(changes, self.poll_once())
            self.emit(changes)

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(1.0, self.interval * 2))
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# Common spellings used in the documentation.
Watcher = FolderWatcher
WatchFolder = FolderWatcher


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class InstanceLock:
    """Exclusive lock file with stale-lock recovery.

    The lock payload records the owner token, PID and creation time. A lock
    whose PID is no longer alive (or whose payload is malformed / marked
    released) is treated as stale and replaced.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        owner: str | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.path = Path(path)
        self.owner = owner or uuid.uuid4().hex
        self._clock = clock or SystemClock()
        self.acquired = False

    def _payload(self) -> str:
        return json.dumps(
            {
                "owner": self.owner,
                "pid": os.getpid(),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "started_at": self._clock.now(),
            },
            sort_keys=True,
        )

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns ``False`` if it is held."""
        payload = self._payload()
        if safe_fs.try_create_lock(self.path, payload):
            self.acquired = True
            return True

        stale = False
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            stale = True
        else:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                stale = True
            else:
                if not isinstance(data, dict) or data.get("released") is True:
                    stale = True
                else:
                    pid = data.get("pid")
                    if not isinstance(pid, int) or not _pid_is_alive(pid):
                        stale = True

        if not stale:
            return False

        try:
            safe_fs.atomic_write(self.path, payload)
        except SafeFsError:
            logger.exception("runtime: cannot replace stale lock %s", self.path)
            return False
        self.acquired = True
        return True

    def release(self) -> None:
        """Release this instance's lock (idempotent, ownership-checked)."""
        if not self.acquired:
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            self.acquired = False
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("owner") == self.owner:
            try:
                safe_fs.release_lock_file(self.path)
            except SafeFsError:
                logger.warning("runtime: cannot remove lock %s", self.path)
        self.acquired = False

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise RuntimeLockError(
                f"another LokDocSQR runtime holds {self.path}"
            )
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------


class _UnsetType:
    """Sentinel so ``policy=None`` can explicitly disable the policy engine."""


_UNSET = _UnsetType()

Schedule = CronSchedule | IntervalSchedule


@dataclass(frozen=True)
class TriggerEvent:
    """Normalized event passed to trigger nodes via ``ctx.variables['trigger']``."""

    type: str
    automation_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "automation_id": self.automation_id,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


@dataclass
class _Automation:
    document: WorkflowDocument
    id: str
    triggers: list[TriggerSpec] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock)


@dataclass
class _CronBinding:
    name: str
    automation_id: str
    schedule: Schedule
    next_due: float
    dry_run: bool | None = None


@dataclass
class _WatchBinding:
    name: str
    automation_id: str
    watcher: FolderWatcher
    dry_run: bool | None = None


def _trigger_kind(raw: str) -> str:
    value = raw.strip().lower()
    for prefix in ("triggers.", "trigger."):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _duration(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    return default


def _string_list(value: Any, default: Sequence[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(default)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return tuple(default)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Runtime:
    """Long-running trigger host for LokDocSQR automations."""

    def __init__(
        self,
        config: AppConfig,
        *,
        audit: AuditLog | None = None,
        registry: NodeRegistry | None = None,
        policy: PolicyEngine | _UnsetType | None = _UNSET,
        events: EventBus | None = None,
        clock: Clock | None = None,
        max_concurrency: int | None = None,
        lock_path: str | Path | None = None,
        single_instance: bool = True,
        default_dry_run: bool | None = None,
        verify_callback: Callable[[ActionSummary], VerifyResult] | None = None,
        max_results: int | None = 1000,
    ) -> None:
        self.config = config
        if max_concurrency is not None and max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._max_concurrency = max_concurrency
        if max_results is not None and max_results < 1:
            raise ValueError("max_results must be >= 1 or None")
        self._max_results = max_results
        self._default_dry_run = default_dry_run
        self._registry = registry
        self._events = events if events is not None else bus
        self._clock: Clock = clock or SystemClock()
        self._verify_callback = verify_callback
        # Set by the CLI for recursive dry-run planning of nested graphs.
        self._planning_callback: (
            Callable[[Any, Any, str | None], Any] | None
        ) = None
        if isinstance(policy, _UnsetType):
            self._policy: PolicyEngine | None = PolicyEngine.default()
        else:
            self._policy = policy

        if audit is not None:
            self.audit = audit
        else:
            logs_dir = getattr(config, "logs_dir", None)
            if not isinstance(logs_dir, (str, Path)):
                logs_dir = Path(config.base_dir).expanduser() / ".logs"
            self.audit = AuditLog(
                Path(logs_dir).expanduser() / "audit.jsonl"
            )

        self._single_instance = bool(single_instance)
        self.lock_path = (
            Path(lock_path)
            if lock_path is not None
            else Path(config.base_dir) / DEFAULT_LOCK_NAME
        )

        self._automations: dict[str, _Automation] = {}
        self._cron_bindings: list[_CronBinding] = []
        self._watch_bindings: list[_WatchBinding] = []
        self._results: list[RunResult] = []
        self._pending: set[Future[RunResult]] = set()
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._scheduler_thread: threading.Thread | None = None
        self._instance_lock: InstanceLock | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._running = False

    # -- registration ----------------------------------------------
    @property
    def automation_ids(self) -> tuple[str, ...]:
        with self._state_lock:
            return tuple(sorted(self._automations))

    @property
    def results(self) -> tuple[RunResult, ...]:
        with self._state_lock:
            return tuple(self._results)

    @property
    def last_result(self) -> RunResult | None:
        with self._state_lock:
            return self._results[-1] if self._results else None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def events(self) -> EventBus:
        """The event bus used by runs dispatched by this runtime."""
        return self._events

    @property
    def max_concurrency(self) -> int:
        if self._max_concurrency is not None:
            return self._max_concurrency
        with self._state_lock:
            values = [
                auto.document.settings.max_concurrency
                for auto in self._automations.values()
            ]
        return max([1, *values])

    def register(
        self,
        source: (
            WorkflowDocument
            | ResolvedGraph
            | Mapping[str, Any]
            | str
            | Path
        ),
        *,
        automation_id: str | None = None,
        validate: bool = True,
    ) -> str:
        """Register an automation and bind its declared triggers.

        ``source`` may be a :class:`WorkflowDocument`, a raw graph mapping, a
        path to a JSON file, or a JSON string.
        """
        document = self._coerce_document(source)
        if validate:
            errors = validate_graph(document)
            if errors:
                raise GraphValidationError("; ".join(errors))

        resolved_id = (
            automation_id
            or document.metadata.id
            or f"automation-{uuid.uuid4().hex[:12]}"
        )
        with self._state_lock:
            if resolved_id in self._automations:
                raise ValueError(
                    f"automation {resolved_id!r} is already registered"
                )
            automation = _Automation(
                document=document,
                id=resolved_id,
                triggers=list(document.triggers),
            )
            self._automations[resolved_id] = automation

        self._bind_document_triggers(automation)
        return resolved_id

    register_document = register

    def unregister(self, automation_id: str) -> None:
        """Remove an automation and its trigger bindings."""
        with self._state_lock:
            self._automations.pop(automation_id, None)
            cron = [
                item
                for item in self._cron_bindings
                if item.automation_id == automation_id
            ]
            watches = [
                item
                for item in self._watch_bindings
                if item.automation_id == automation_id
            ]
            self._cron_bindings = [
                item
                for item in self._cron_bindings
                if item.automation_id != automation_id
            ]
            self._watch_bindings = [
                item
                for item in self._watch_bindings
                if item.automation_id != automation_id
            ]
        del cron
        for binding in watches:
            binding.watcher.stop()

    @staticmethod
    def _coerce_document(
        source: (
            WorkflowDocument
            | ResolvedGraph
            | Mapping[str, Any]
            | str
            | Path
        ),
    ) -> WorkflowDocument:
        if isinstance(source, WorkflowDocument):
            return source
        if isinstance(source, ResolvedGraph):
            return source.document
        if isinstance(source, Mapping):
            return parse_graph(dict(source))
        if isinstance(source, (str, Path)):
            path = Path(source)
            try:
                is_file = path.exists()
            except (OSError, ValueError):
                is_file = False
            raw = (
                path.read_text(encoding="utf-8")
                if is_file
                else str(source)
            )
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GraphValidationError(
                    f"invalid automation JSON: {exc}"
                ) from exc
            if not isinstance(data, dict):
                raise GraphValidationError(
                    "automation JSON root must be an object"
                )
            return parse_graph(data)
        raise TypeError(
            "automation must be a WorkflowDocument, mapping, JSON text or path"
        )

    # -- bindings ----------------------------------------------------
    def _bind_document_triggers(self, automation: _Automation) -> None:
        for spec in automation.triggers:
            kind = _trigger_kind(spec.type)
            params = dict(spec.params)
            dry_run = _optional_bool(params.get("dry_run"))
            if kind in {"watch_folder", "watch", "watcher"}:
                path = params.get("path")
                if not isinstance(path, (str, Path)) or not str(path):
                    logger.warning(
                        "runtime: trigger %r (%s) has no path; skipped",
                        spec.id,
                        automation.id,
                    )
                    continue
                patterns = _string_list(
                    params.get("patterns", params.get("pattern")),
                    ("*",),
                )
                self.add_watch(
                    automation.id,
                    path,
                    patterns=patterns,
                    recursive=bool(params.get("recursive", True)),
                    interval=_duration(
                        params.get("interval_seconds"), DEFAULT_WATCH_INTERVAL
                    ),
                    debounce=_duration(
                        params.get("debounce_seconds"), DEFAULT_DEBOUNCE
                    ),
                    events=_string_list(
                        params.get("events"),
                        ("created", "modified", "deleted"),
                    ),
                    dry_run=dry_run,
                    name=spec.id,
                )
            elif kind in {
                "scheduled_scan",
                "scheduled",
                "schedule",
                "cron",
            }:
                schedule: str | Schedule | None = None
                for key in ("cron", "expression", "schedule"):
                    raw = params.get(key)
                    if isinstance(raw, str) and raw.strip():
                        schedule = raw
                        break
                interval = params.get(
                    "interval_seconds", params.get("every_seconds")
                )
                if schedule is None and isinstance(interval, (int, float)):
                    schedule = IntervalSchedule(float(interval))
                if schedule is None:
                    logger.warning(
                        "runtime: trigger %r (%s) has no schedule; skipped",
                        spec.id,
                        automation.id,
                    )
                    continue
                self.add_cron(
                    automation.id,
                    schedule,
                    dry_run=dry_run,
                    name=spec.id,
                )
            elif kind in {"manual_run", "manual", "webhook"}:
                # These triggers are driven by Runtime.trigger() / the caller.
                continue
            else:
                logger.warning(
                    "runtime: unknown trigger type %r on %s",
                    spec.type,
                    automation.id,
                )

    def add_watch(
        self,
        automation_id: str,
        path: str | Path,
        *,
        patterns: str | Sequence[str] | None = ("*",),
        recursive: bool = True,
        interval: float = DEFAULT_WATCH_INTERVAL,
        debounce: float = DEFAULT_DEBOUNCE,
        events: Sequence[str] | None = None,
        dry_run: bool | None = None,
        name: str | None = None,
    ) -> str:
        """Bind a folder watcher to ``automation_id`` and return its name."""
        self._require(automation_id)
        resolved = Path(path)
        if not resolved.is_absolute():
            resolved = Path(self.config.base_dir) / resolved

        binding_name = name or f"watch:{automation_id}:{uuid.uuid4().hex[:8]}"

        def _on_changes(changes: list[FileChange]) -> None:
            auto = self._automations.get(automation_id)
            if auto is None:
                return
            payload = {
                "changes": [change.to_dict() for change in changes],
                "paths": [
                    change.path
                    for change in changes
                    if change.kind != "deleted"
                ],
            }
            event = self._make_event(
                automation_id, "watch_folder", payload
            )
            self._dispatch_async(auto, event=event, dry_run=dry_run)

        watcher = FolderWatcher(
            resolved,
            patterns=patterns,
            recursive=recursive,
            interval=interval,
            debounce=debounce,
            events=events,
            on_changes=_on_changes,
        )
        binding = _WatchBinding(
            name=binding_name,
            automation_id=automation_id,
            watcher=watcher,
            dry_run=dry_run,
        )
        with self._state_lock:
            self._watch_bindings.append(binding)
        if self._running:
            watcher.start()
        return binding_name

    def add_cron(
        self,
        automation_id: str,
        schedule: str | Schedule,
        *,
        dry_run: bool | None = None,
        name: str | None = None,
        fire_immediately: bool = False,
    ) -> str:
        """Bind a cron/interval schedule to ``automation_id``."""
        self._require(automation_id)
        if isinstance(schedule, str):
            raw = schedule.strip()
            try:
                seconds = float(raw)
            except ValueError:
                parsed: Schedule = CronSchedule(raw)
            else:
                parsed = IntervalSchedule(seconds)
        else:
            parsed = schedule

        now = self._clock.now()
        next_due = now if fire_immediately else parsed.next_after(now)
        binding_name = name or f"cron:{automation_id}:{uuid.uuid4().hex[:8]}"
        binding = _CronBinding(
            name=binding_name,
            automation_id=automation_id,
            schedule=parsed,
            next_due=next_due,
            dry_run=dry_run,
        )
        with self._state_lock:
            self._cron_bindings.append(binding)
        if self._running:
            self._ensure_scheduler()
        return binding_name

    def _ensure_scheduler(self) -> None:
        """Start the scheduler thread if the runtime is already running."""
        with self._state_lock:
            if not self._running or not self._cron_bindings:
                return
            thread = self._scheduler_thread
            if thread is not None and thread.is_alive():
                return
            thread = threading.Thread(
                target=self._scheduler_loop,
                name="lokdocsqr-scheduler",
                daemon=True,
            )
            self._scheduler_thread = thread
        thread.start()

    # -- running -----------------------------------------------------
    def _require(self, automation_id: str) -> _Automation:
        with self._state_lock:
            automation = self._automations.get(automation_id)
        if automation is None:
            raise KeyError(f"automation {automation_id!r} is not registered")
        return automation

    def _active_registry(self) -> NodeRegistry:
        if self._registry is not None:
            return self._registry
        if not global_registry.list():
            discover_plugins()
        return global_registry

    def _build(self, automation: _Automation) -> Any:
        registry = self._active_registry()
        document = automation.document
        states = resolve_states(document, registry)
        return build_graph(document, states, registry)

    def _resolve_roots(self, values: Sequence[str | Path]) -> list[Path]:
        roots: list[Path] = []
        for value in values:
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = Path(self.config.base_dir).expanduser() / path
            roots.append(path.resolve())
        return roots

    def _trash_dir_for_scope(self) -> Path | None:
        """Return the configured trash directory when it is inside the base.

        The default trash directory lives at ``<base_dir>/.trash``. A
        workflow may legitimately narrow its readable roots to ``inbox`` and
        ``out``; without adding the internal trash directory to the writable
        set, ``fs.trash`` would fail even though the operator configured
        nothing unusual. A trash directory outside ``base_dir`` remains
        explicitly opt-in through the workflow scope/config.
        """
        raw = getattr(self.config, "trash_dir", None)
        if raw is None:
            return None
        base = Path(self.config.base_dir).expanduser().resolve()
        trash = Path(raw).expanduser()
        if not trash.is_absolute():
            trash = base / trash
        trash = trash.resolve()
        try:
            trash.relative_to(base)
        except ValueError:
            return None
        return trash

    def _scope_for(self, document: WorkflowDocument) -> ScopeManager:
        settings = document.settings.scope
        roots = self._resolve_roots(settings.roots)
        if not roots:
            roots = self._resolve_roots(self.config.scope_roots)
        if not roots:
            roots = [
                Path(self.config.base_dir).expanduser().resolve()
            ]
        writable = self._resolve_roots(settings.writable) or list(roots)
        trash = self._trash_dir_for_scope()
        if trash is not None and trash not in writable:
            writable.append(trash)
        return ScopeManager(
            WorkspaceScope(
                roots=roots,
                writable=writable,
                allow_symlinks=settings.allow_symlinks,
                policy_ref=settings.policy_ref,
                allow_parent_traversal=bool(
                    settings.allow_parent_traversal
                    or getattr(self.config, "allow_parent_traversal", False)
                ),
            )
        )

    def _initial_variables(self, document: WorkflowDocument) -> dict[str, Any]:
        variables: dict[str, Any] = {}
        for name, spec in document.variables.items():
            if spec.value is not None:
                variables[name] = spec.value
        return variables

    def _catalog(self) -> Any:
        from lokdocsqr.core.catalog import AutomationCatalog

        catalog = AutomationCatalog()
        with self._state_lock:
            automations = list(self._automations.values())
        for auto in automations:
            try:
                catalog.register(auto.id, self._build(auto))
            except Exception:
                logger.debug(
                    "runtime: cannot add %s to the subflow catalog",
                    auto.id,
                    exc_info=True,
                )
        return catalog

    def _make_event(
        self,
        automation_id: str,
        trigger_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return TriggerEvent(
            type=trigger_type,
            automation_id=automation_id,
            payload=dict(payload or {}),
            timestamp=_utc_now_iso(),
        ).to_dict()

    def _make_context(
        self,
        automation: _Automation,
        *,
        dry_run: bool,
        event: dict[str, Any] | None,
        variables: Mapping[str, Any] | None,
        verify_callback: Callable[[ActionSummary], VerifyResult] | None,
    ) -> ExecutionContext:
        merged_variables = self._initial_variables(automation.document)
        if variables:
            merged_variables.update(dict(variables))
        if event is not None:
            merged_variables["trigger"] = event

        return ExecutionContext(
            scope=self._scope_for(automation.document),
            config=self.config,
            audit=self.audit,
            run_id=f"{automation.id}-{uuid.uuid4().hex[:12]}",
            automation_id=automation.id,
            dry_run=dry_run,
            events=self._events,
            variables=merged_variables,
            policy=self._policy,
            verify_callback=(
                verify_callback
                if verify_callback is not None
                else self._verify_callback
            ),
            clock=self._clock,
            automations=self._catalog(),
            planning_callback=getattr(self, "_planning_callback", None),
        )

    def _execute(
        self,
        automation: _Automation,
        *,
        dry_run: bool | None,
        event: dict[str, Any] | None,
        variables: Mapping[str, Any] | None,
        verify_callback: Callable[[ActionSummary], VerifyResult] | None,
    ) -> RunResult:
        if dry_run is not None:
            resolved_dry_run = bool(dry_run)
        elif (
            getattr(
                automation.document.settings,
                "default_dry_run_explicit",
                False,
            )
            or automation.document.settings.default_dry_run is False
        ):
            # An explicit document setting is absolute: it wins over both the
            # Runtime default and the host config.
            resolved_dry_run = bool(
                automation.document.settings.default_dry_run
            )
        elif self._default_dry_run is not None:
            resolved_dry_run = bool(self._default_dry_run)
        else:
            # The document kept the model default (not explicitly set), so
            # the runtime host may choose.
            resolved_dry_run = bool(
                getattr(self.config, "default_dry_run", True)
            )
        started_at = _utc_now_iso()
        with automation.lock:
            context: ExecutionContext | None = None
            try:
                context = self._make_context(
                    automation,
                    dry_run=resolved_dry_run,
                    event=event,
                    variables=variables,
                    verify_callback=verify_callback,
                )
                result = Executor(context).execute(self._build(automation))
            except Exception as exc:
                # Keep the operator-facing diagnostic concise. A traceback
                # is still available at DEBUG level (file logs / --verbose).
                logger.error(
                    "runtime: automation %s failed: %s: %s",
                    automation.id,
                    type(exc).__name__,
                    exc,
                )
                logger.debug(
                    "runtime: automation %s traceback", automation.id,
                    exc_info=True,
                )
                result = RunResult(
                    run_id=(
                        context.run_id
                        if context is not None
                        else f"{automation.id}-{uuid.uuid4().hex[:12]}"
                    ),
                    automation_id=automation.id,
                    dry_run=resolved_dry_run,
                    success=False,
                    errors=[f"{type(exc).__name__}: {exc}"],
                    started_at=started_at,
                    finished_at=_utc_now_iso(),
                )
        with self._state_lock:
            self._results.append(result)
            if (
                self._max_results is not None
                and len(self._results) > self._max_results
            ):
                del self._results[: -(self._max_results)]
        return result

    def run_once(
        self,
        automation_id: str,
        *,
        dry_run: bool | None = None,
        variables: Mapping[str, Any] | None = None,
        trigger_type: str | None = None,
        trigger_payload: Mapping[str, Any] | None = None,
        trigger_event: Mapping[str, Any] | None = None,
        verify_callback: Callable[[ActionSummary], VerifyResult] | None = None,
    ) -> RunResult:
        """Execute an automation once, synchronously."""
        automation = self._require(automation_id)
        if trigger_event is not None:
            event = dict(trigger_event)
        elif trigger_type is not None:
            event = self._make_event(
                automation_id, trigger_type, trigger_payload
            )
        else:
            event = None
        return self._execute(
            automation,
            dry_run=dry_run,
            event=event,
            variables=variables,
            verify_callback=verify_callback,
        )

    # Common spellings.
    run = run_once
    execute = run_once

    def trigger(
        self,
        automation_id: str,
        *,
        trigger_type: str = "manual_run",
        payload: Mapping[str, Any] | None = None,
        variables: Mapping[str, Any] | None = None,
        dry_run: bool | None = None,
        verify_callback: Callable[[ActionSummary], VerifyResult] | None = None,
    ) -> RunResult:
        """Fire a manual/webhook trigger synchronously."""
        return self.run_once(
            automation_id,
            dry_run=dry_run,
            variables=variables,
            trigger_type=trigger_type,
            trigger_payload=payload,
            verify_callback=verify_callback,
        )

    def trigger_webhook(
        self,
        automation_id: str,
        payload: Mapping[str, Any] | None = None,
        *,
        variables: Mapping[str, Any] | None = None,
        dry_run: bool | None = None,
    ) -> RunResult:
        """Fire the webhook trigger path synchronously."""
        return self.trigger(
            automation_id,
            trigger_type="webhook",
            payload=payload,
            variables=variables,
            dry_run=dry_run,
        )

    # -- dispatch / concurrency ------------------------------------
    def _ensure_pool(self) -> ThreadPoolExecutor:
        with self._state_lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self.max_concurrency,
                    thread_name_prefix="lokdocsqr-run",
                )
            return self._pool

    def _dispatch_async(
        self,
        automation: _Automation,
        *,
        event: dict[str, Any] | None,
        dry_run: bool | None,
    ) -> Future[RunResult]:
        pool = self._ensure_pool()
        future = pool.submit(
            self._execute,
            automation,
            dry_run=dry_run,
            event=event,
            variables=None,
            verify_callback=None,
        )
        with self._state_lock:
            self._pending.add(future)
        future.add_done_callback(self._future_done)
        return future

    def _future_done(self, future: Future[RunResult]) -> None:
        with self._state_lock:
            self._pending.discard(future)
        try:
            future.result()
        except Exception:  # pragma: no cover - _execute isolates failures
            logger.exception("runtime: background run failed")

    def _poll_cron(self, now: float | None = None) -> None:
        when = self._clock.now() if now is None else float(now)
        with self._state_lock:
            bindings = list(self._cron_bindings)
        for binding in bindings:
            for _ in range(_MAX_CRON_CATCHUP):
                if binding.next_due > when:
                    break
                scheduled_for = binding.next_due
                binding.next_due = binding.schedule.next_after(
                    binding.next_due
                )
                automation = self._automations.get(binding.automation_id)
                if automation is None:
                    continue
                event = self._make_event(
                    binding.automation_id,
                    "scheduled_scan",
                    {
                        "scheduled_for": scheduled_for,
                        "schedule": str(binding.schedule),
                    },
                )
                self._dispatch_async(
                    automation, event=event, dry_run=binding.dry_run
                )

    def tick(self, now: float | None = None, *, wait: bool = True) -> None:
        """Run due triggers once without starting background threads.

        Folder watchers are diffed at the moment of the call; cron bindings
        whose ``next_due`` is at or before ``now`` are dispatched. By default
        the call waits for the dispatched runs to finish so deterministic
        tests can inspect results immediately; pass ``wait=False`` to only
        enqueue them.
        """
        with self._state_lock:
            watchers = list(self._watch_bindings)
        for binding in watchers:
            changes = binding.watcher.poll_once()
            if changes:
                binding.watcher.emit(changes)
        self._poll_cron(now)
        if wait:
            self.wait_for_idle()

    poll_once = tick

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_cron()
            except Exception:
                logger.exception("runtime: scheduler tick failed")
            now = self._clock.now()
            with self._state_lock:
                due = [item.next_due for item in self._cron_bindings]
            timeout = (
                max(0.01, min(1.0, min(due) - now)) if due else 1.0
            )
            self._stop_event.wait(timeout)

    def start(self, *, start_triggers: bool = True) -> Runtime:
        """Acquire the lock and start watcher/scheduler threads.

        ``start_triggers=False`` is used by the CLI daemon path: the
        single-instance lock is acquired first, the initial run is executed
        synchronously, and triggers are only started afterwards. Calling
        :meth:`start_triggers` separately is idempotent.
        """
        if self._running:
            if start_triggers:
                self.start_triggers()
            return self
        self._stop_event = threading.Event()

        instance_lock: InstanceLock | None = None
        if self._single_instance:
            instance_lock = InstanceLock(self.lock_path, clock=self._clock)
            if not instance_lock.acquire():
                raise RuntimeLockError(
                    f"another LokDocsqr runtime holds {self.lock_path}"
                )
            self._instance_lock = instance_lock

        self._running = True
        if not start_triggers:
            return self

        try:
            self.start_triggers()
        except Exception:
            self._running = False
            for binding in self._watch_bindings:
                binding.watcher.stop()
            if instance_lock is not None:
                instance_lock.release()
                self._instance_lock = None
            raise
        return self

    def start_triggers(self) -> None:
        """Start watching/scheduling for an already-started runtime."""
        with self._state_lock:
            if not self._running:
                raise RuntimeError("runtime is not running")
            watchers = list(self._watch_bindings)
            has_cron = bool(self._cron_bindings)
            scheduler = self._scheduler_thread
            needs_scheduler = (
                has_cron
                and (scheduler is None or not scheduler.is_alive())
            )
            thread: threading.Thread | None = None
            if needs_scheduler:
                thread = threading.Thread(
                    target=self._scheduler_loop,
                    name="lokdocsqr-scheduler",
                    daemon=True,
                )
                self._scheduler_thread = thread
        for binding in watchers:
            binding.watcher.start()
        if thread is not None:
            thread.start()

    def stop(self) -> None:
        """Stop triggers and the run pool, then release the lock."""
        self._stop_event.set()
        with self._state_lock:
            watchers = list(self._watch_bindings)
            scheduler = self._scheduler_thread
            pool = self._pool
            self._pool = None
            self._scheduler_thread = None
        for binding in watchers:
            binding.watcher.stop()
        if scheduler is not None:
            scheduler.join(timeout=5.0)
        if pool is not None:
            pool.shutdown(wait=True)
        instance_lock = self._instance_lock
        self._instance_lock = None
        if instance_lock is not None:
            instance_lock.release()
        self._running = False

    def serve(self, stop_event: threading.Event | None = None) -> None:
        """Block until ``stop()`` (or an external stop event) is signalled."""
        if not self._running:
            self.start()
        target = stop_event or self._stop_event
        try:
            while not target.is_set():
                target.wait(0.1)
        finally:
            self.stop()

    # -- idle helpers (used by tests and graceful shutdown) ---------
    def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Wait until every dispatched run has completed."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._state_lock:
                pending = list(self._pending)
            if not pending:
                return True
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
            _, not_done = futures_wait(
                pending, timeout=remaining
            )
            if not_done and deadline is not None:
                return False

    def wait_for_runs(self, count: int, timeout: float = 5.0) -> bool:
        """Wait until at least ``count`` runs have completed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() <= deadline:
            with self._state_lock:
                if len(self._results) >= count:
                    return True
            time.sleep(0.01)
        with self._state_lock:
            return len(self._results) >= count

    def sweep_trash(self, now: float | None = None) -> list[Path]:
        """Remove quarantine entries whose sidecar TTL has expired.

        Explicit maintenance: the runtime never deletes quarantined data on
        its own. Call this from your scheduler/systemd timer, or use
        ``lokdocsqr trash sweep``. Returns the removed file paths.
        """
        raw = getattr(self.config, "trash_dir", None)
        if raw is None:
            return []
        trash_dir = Path(raw).expanduser()
        removed = safe_fs.purge_expired_trash(trash_dir, now=now)
        if removed:
            logger.info(
                "runtime: trash sweep removed %d expired entrie(s) from %s",
                len(removed),
                trash_dir,
            )
        return removed

    # -- introspection ---------------------------------------------
    @property
    def triggers(self) -> tuple[TriggerSpec, ...]:
        """All trigger declarations across registered automations."""
        with self._state_lock:
            return tuple(
                trigger
                for auto in self._automations.values()
                for trigger in auto.triggers
            )

    @property
    def cron_bindings(self) -> tuple[Mapping[str, Any], ...]:
        with self._state_lock:
            return tuple(
                MappingProxyType(
                    {
                        "name": item.name,
                        "automation_id": item.automation_id,
                        "schedule": str(item.schedule),
                        "next_due": item.next_due,
                    }
                )
                for item in self._cron_bindings
            )

    @property
    def watch_bindings(self) -> tuple[Mapping[str, Any], ...]:
        with self._state_lock:
            return tuple(
                MappingProxyType(
                    {
                        "name": item.name,
                        "automation_id": item.automation_id,
                        "path": str(item.watcher.path),
                        "patterns": item.watcher.patterns,
                    }
                )
                for item in self._watch_bindings
            )
