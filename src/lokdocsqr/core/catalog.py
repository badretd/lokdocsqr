"""In-memory automation catalog for subflow resolution (Phase 8, SPEC §3)."""
from __future__ import annotations

from lokdocsqr.core.models import ResolvedGraph

__all__ = ["AutomationCatalog"]


class AutomationCatalog:
    """Read-only-by-convention in-memory map of graph ids to resolved graphs."""

    def __init__(self) -> None:
        self._graphs: dict[str, ResolvedGraph] = {}

    def get(self, graph_id: str) -> ResolvedGraph | None:
        """Return the graph registered under ``graph_id``, if any."""
        return self._graphs.get(graph_id)

    def register(self, graph_id: str, graph: ResolvedGraph) -> None:
        """Register ``graph`` under ``graph_id``."""
        self._graphs[graph_id] = graph

    def list_ids(self) -> tuple[str, ...]:
        """Return registered graph ids in deterministic sorted order (R32)."""
        return tuple(sorted(self._graphs))
