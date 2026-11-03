"""Trigger nodes — read-only entry points for runtime-dispatched runs.

Importing this package registers:

  * ``triggers.watch_folder``
  * ``triggers.scheduled_scan``
  * ``triggers.manual_run``
  * ``triggers.webhook``

The background watcher/cron host lives in :mod:`lokdocsqr.core.runtime`; the
nodes only normalize the event into ordinary output ports.
"""
from __future__ import annotations

from . import manual_run, scheduled_scan, watch_folder, webhook
from .manual_run import ManualRunNode
from .scheduled_scan import ScheduledScanNode
from .watch_folder import WatchFolderNode
from .webhook import WebhookNode

__all__ = [
    "ManualRunNode",
    "ScheduledScanNode",
    "WatchFolderNode",
    "WebhookNode",
    "manual_run",
    "scheduled_scan",
    "watch_folder",
    "webhook",
]
