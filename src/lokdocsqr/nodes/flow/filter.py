"""flow.filter — filter a sequence using a safe whitelist condition.

Reading-only node. ``_preview`` is overridden: it evaluates the
condition against **metadata only** (content-like keys are stripped by
``_metadata_item``) and never reads file contents. The preview
``items`` list is the metadata-filtered subset; downstream consumers
running in dry-run mode receive that subset, so content-dependent
conditions are approximate in preview (documented in ``_preview``).

Output contract (always a dict for both ``_execute`` and ``_preview``):

  * ``_execute``:
      ``{"items": [...], "results": [...], "total": int,
         "kept": int, "filtered": int, "errors": int}``
      ``items`` contains only elements that passed the filter.
      Failed evaluations contribute ``"error"`` to ``results`` and a
      bump to ``errors``; no ``"error"`` markers appear in ``items``.
  * ``_preview``: same keys plus ``"preview"`` (human summary) and
    ``"dry_run": True``.
"""
from __future__ import annotations

from typing import Any

from lokdocsqr.core.node_api.decorators import register_node

from ._base import _FlowBase
from ._common import _CondError, _base_mapping, _evaluate, _extract_items


_CONTENT_KEYS = frozenset({
    "content", "text", "body", "data", "raw", "bytes",
    "ocr", "extracted_text", "extracted", "content_text",
})


def _metadata_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    out: dict[str, Any] = {}
    for key, value in item.items():
        if isinstance(key, str) and key.lower() in _CONTENT_KEYS:
            continue
        out[key] = value
    return out


@register_node("flow.filter")
class FilterNode(_FlowBase):
    id = "flow.filter"
    retry_safe = True

    @property
    def title(self) -> str:
        return "Filter"

    @property
    def inputs(self) -> dict[str, str]:
        return {"items": "any[]", "condition": "string"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "items": "any[]",
            "results": "any[]",
            "total": "int",
            "kept": "int",
            "filtered": "int",
            "errors": "int",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _collect(
        self,
        ctx: Any,
        payload: Any,
        *,
        metadata_only: bool,
    ) -> tuple[list[Any], list[Any], int, int, int, int]:
        condition = (
            payload.get("condition")
            if isinstance(payload, dict)
            else None
        )
        items, _ = _extract_items(payload)
        base = _base_mapping(ctx, payload)

        kept_items: list[Any] = []
        results: list[Any] = []
        kept = 0
        filtered = 0
        errors = 0

        for item in items:
            maybe = _metadata_item(item) if metadata_only else item
            mapping = dict(base)
            if isinstance(maybe, dict):
                mapping.update(maybe)
            else:
                mapping["item"] = maybe
            try:
                keep = bool(_evaluate(condition, mapping))
            except _CondError:
                # M2: never append the ``"error"`` marker to ``items``.
                results.append("error")
                errors += 1
                continue
            results.append(keep)
            if keep:
                kept_items.append(maybe if metadata_only else item)
                kept += 1
            else:
                filtered += 1

        return kept_items, results, len(items), kept, filtered, errors

    def _execute(self, ctx: Any, payload: Any) -> Any:
        kept_items, results, total, kept, filtered, errors = (
            self._collect(ctx, payload, metadata_only=False)
        )
        return {
            "items": kept_items,
            "results": results,
            "total": total,
            "kept": kept,
            "filtered": filtered,
            "errors": errors,
        }

    def _preview(self, ctx: Any, payload: Any) -> Any:
        kept_items, results, total, kept, filtered, errors = (
            self._collect(ctx, payload, metadata_only=True)
        )
        # B5: preview mirrors ``_execute`` key set plus ``preview`` /
        # ``dry_run``.  ``items`` carries the metadata-filtered subset.
        return {
            "items": kept_items,
            "results": results,
            "total": total,
            "kept": kept,
            "filtered": filtered,
            "errors": errors,
            "preview": f"будет отфильтровано {filtered} из {total}",
            "dry_run": True,
        }
