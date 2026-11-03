#!/usr/bin/env python3
"""Verify that ``docs/DSL.md`` examples still match executable behavior.

Checks
------
1. Every fenced ``ldsq`` block compiles, parses, emits to a fixed point, and
   survives ``parse_graph(document_to_dict(...))``.
2. Every ``ldsq`` block immediately followed by a ``json`` block (the paired
   examples) compiles byte-for-byte to that JSON object.
3. Every registered node id appears somewhere in the document.

Run directly (``python tools/verify_dsl_docs.py``) or through
``tests/test_dsl_docs.py``.
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DOC_PATH = ROOT / "docs" / "DSL.md"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import lokdocsqr.nodes  # noqa: E402,F401  (register built-in nodes)
from lokdocsqr.core.dsl import (  # noqa: E402
    compile_dsl,
    document_to_dict,
    emit_dsl,
    parse_dsl,
)
from lokdocsqr.core.graph import parse_graph  # noqa: E402
from lokdocsqr.core.node_api.registry import registry  # noqa: E402


class DocsVerificationError(AssertionError):
    """Raised on the first documentation/behavior mismatch."""


@dataclass(frozen=True)
class Block:
    lang: str
    text: str
    line: int


_FENCE_OPEN = re.compile(r"^```([^\s`]*)\s*$")
_AUTOMATION = re.compile(r"^\s*automation\b", re.MULTILINE)


def extract_blocks(document: str) -> list[Block]:
    """Return every fenced code block in document order."""
    lines = document.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        match = _FENCE_OPEN.match(lines[i])
        if not match:
            i += 1
            continue
        lang = match.group(1)
        start = i + 1
        i += 1
        body: list[str] = []
        while i < len(lines) and not lines[i].strip().startswith("```"):
            body.append(lines[i])
            i += 1
        if i >= len(lines):
            raise DocsVerificationError(
                f"unterminated code fence opened at line {start}"
            )
        blocks.append(
            Block(lang=lang or "text", text="\n".join(body), line=start)
        )
        i += 1
    return blocks


def _as_document_text(text: str) -> str:
    """Wrap a fragment in an automation header so it can be parsed."""
    if _AUTOMATION.search(text):
        return text
    return 'automation "_snippet"\n' + text


def _labels(block: Block) -> str:
    return f"ldsq block starting at line {block.line}"


def _unified_diff(expected: Any, actual: Any, *, label: str) -> str:
    expected_text = json.dumps(expected, indent=2, sort_keys=True).splitlines()
    actual_text = json.dumps(actual, indent=2, sort_keys=True).splitlines()
    diff = difflib.unified_diff(
        expected_text,
        actual_text,
        fromfile=f"{label} (expected)",
        tofile=f"{label} (actual)",
        lineterm="",
    )
    return "\n".join(diff)


def _check_ldsq_block(block: Block) -> None:
    source = _as_document_text(block.text)
    try:
        compiled = compile_dsl(source)
        document = parse_dsl(source)
    except Exception as exc:  # noqa: BLE001 - report the exact block
        raise DocsVerificationError(
            f"{_labels(block)} does not compile/parse: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    normalized = document_to_dict(document)
    try:
        round_tripped = parse_graph(normalized)
    except Exception as exc:  # noqa: BLE001
        raise DocsVerificationError(
            f"{_labels(block)} fails parse_graph(document_to_dict(...)): "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if round_tripped != document:
        raise DocsVerificationError(
            f"{_labels(block)} fails "
            "parse_graph(document_to_dict(doc)) == doc"
        )

    canonical = emit_dsl(document)
    reparsed = parse_dsl(canonical)
    if emit_dsl(reparsed) != canonical:
        raise DocsVerificationError(
            f"{_labels(block)} emit_dsl is not a fixed point"
        )

    # ``compile_dsl`` returns the raw parser dict; keep it exercised.
    if not isinstance(compiled, dict):
        raise DocsVerificationError(
            f"{_labels(block)} compile_dsl did not return a dict"
        )


def _check_pair(dsl_block: Block, json_block: Block) -> None:
    expected = json.loads(json_block.text)
    actual = compile_dsl(dsl_block.text)
    if actual != expected:
        diff = _unified_diff(
            expected, actual, label=f"DSL block at line {dsl_block.line}"
        )
        raise DocsVerificationError(
            f"DSL/JSON pair at lines {dsl_block.line}/{json_block.line} "
            f"does not match:\n{diff}"
        )


def verify(doc_path: Path = DOC_PATH) -> dict[str, int]:
    """Run every check, returning counts. Raise on the first mismatch."""
    document = doc_path.read_text(encoding="utf-8")
    blocks = extract_blocks(document)

    ldsq_blocks = [block for block in blocks if block.lang == "ldsq"]
    json_blocks = [block for block in blocks if block.lang == "json"]

    # Paired examples are adjacent ``ldsq``/``json`` fences where the DSL
    # block is a complete automation. Runtime trigger fragments have no
    # automation header and are deliberately not paired with unrelated JSON.
    pairs: list[tuple[Block, Block]] = []
    for index, block in enumerate(blocks):
        if block.lang != "ldsq" or not _AUTOMATION.search(block.text):
            continue
        if index + 1 < len(blocks) and blocks[index + 1].lang == "json":
            pairs.append((block, blocks[index + 1]))

    for block in ldsq_blocks:
        _check_ldsq_block(block)

    for dsl_block, json_block in pairs:
        _check_pair(dsl_block, json_block)

    for item in registry.list():
        node_id = item["id"]
        if node_id not in document:
            raise DocsVerificationError(
                f"registered node id {node_id!r} is missing from "
                f"{doc_path.name}"
            )

    return {
        "ldsq_blocks": len(ldsq_blocks),
        "json_blocks": len(json_blocks),
        "pairs": len(pairs),
        "registered_nodes": len(registry.list()),
    }


def main() -> int:
    try:
        counts = verify()
    except DocsVerificationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "OK: "
        f"{counts['ldsq_blocks']} ldsq blocks, "
        f"{counts['json_blocks']} json blocks, "
        f"{counts['pairs']} DSL/JSON pairs, "
        f"{counts['registered_nodes']} registered node ids"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
