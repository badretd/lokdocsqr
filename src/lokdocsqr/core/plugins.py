"""Node discovery for built-in and third-party plugins."""

from __future__ import annotations

import logging
from importlib import import_module, metadata

from .node_api import registry

logger = logging.getLogger(__name__)

_BUILTIN_PACKAGE = "lokdocsqr.nodes"
_ENTRY_POINT_GROUP = "lokdocsqr.nodes"


def discover_plugins() -> list[str]:
    """Discover built-in and third-party nodes.

    Loads the built-in ``lokdocsqr.nodes`` package (so module-level
    ``@register_node`` decorators fire) and every third-party entry point in
    the ``lokdocsqr.nodes`` group, then returns the list of node ids
    currently registered in the global :data:`registry`.

    The function is tolerant: missing built-in packages and failing entry
    points are logged and skipped rather than raised.
    """
    _load_builtin_nodes()
    _load_entry_point_nodes()
    return [item["id"] for item in registry.list()]


def _load_builtin_nodes() -> None:
    try:
        import_module(_BUILTIN_PACKAGE)
    except ModuleNotFoundError:
        logger.debug(
            "Built-in package %r is not present", _BUILTIN_PACKAGE
        )
    except Exception:
        logger.exception(
            "Failed to import built-in package %r", _BUILTIN_PACKAGE
        )


def _load_entry_point_nodes() -> None:
    try:
        entry_points = metadata.entry_points(group=_ENTRY_POINT_GROUP)
    except Exception:
        logger.exception(
            "Failed to enumerate entry points for group %r",
            _ENTRY_POINT_GROUP,
        )
        return
    for entry_point in entry_points:
        try:
            entry_point.load()
        except Exception:
            logger.exception(
                "Failed to load plugin entry point %r", entry_point.name
            )
