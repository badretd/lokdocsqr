"""Automation DSL lexer + parser: DSL text -> JSON automation schema dict.

The DSL is a small, brace-delimited configuration language. Indentation is
cosmetic; blocks are delimited by ``{``/``}`` and statements are separated by
newlines. Values are JSON-like (strings, numbers, booleans, null, lists, and
maps written either inline ``{ k = v, ... }`` or as a block ``{ k = v ... }``).

The parser produces the raw JSON schema dictionary consumed by
:func:`lokdocsqr.core.graph.parser.parse_graph`; it does no semantic
validation itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, NoReturn

from ..errors import GraphParseError

__all__ = ["dsl_to_dict"]


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    line: int
    col: int


_WORD_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789.-")


def _tokenize(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    i = 0
    n = len(text)
    line = 1
    col = 1

    def _advance_line() -> None:
        nonlocal i, line, col
        i += 1
        line += 1
        col = 1

    while i < n:
        ch = text[i]
        if ch in " \t\r":
            i += 1
            col += 1
            continue
        if ch == "\n":
            tokens.append(_Token("NEWLINE", None, line, col))
            _advance_line()
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                i += 1
                col += 1
            continue
        if ch == "{":
            tokens.append(_Token("LBRACE", None, line, col))
            i += 1
            col += 1
            continue
        if ch == "}":
            tokens.append(_Token("RBRACE", None, line, col))
            i += 1
            col += 1
            continue
        if ch == "[":
            tokens.append(_Token("LBRACK", None, line, col))
            i += 1
            col += 1
            continue
        if ch == "]":
            tokens.append(_Token("RBRACK", None, line, col))
            i += 1
            col += 1
            continue
        if ch == "=":
            tokens.append(_Token("EQ", None, line, col))
            i += 1
            col += 1
            continue
        if ch == ",":
            tokens.append(_Token("COMMA", None, line, col))
            i += 1
            col += 1
            continue

        if ch == '"':
            start_line, start_col = line, col
            j = i + 1
            buf = ['"']
            while j < n:
                c = text[j]
                buf.append(c)
                if c == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if c == '"':
                    j += 1
                    break
                if c == "\n":
                    raise GraphParseError(f"DSL string at line {start_line} is unterminated")
                j += 1
            else:
                raise GraphParseError(f"DSL string at line {start_line} is unterminated")
            raw = "".join(buf)
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GraphParseError(f"DSL string at line {start_line} is invalid: {exc}") from exc
            tokens.append(_Token("STRING", decoded, start_line, start_col))
            col += j - i
            i = j
            continue

        if ch.isdigit() or (ch == "-" and i + 1 < n and text[i + 1].isdigit()):
            start_line, start_col = line, col
            j = i
            if text[j] == "-":
                j += 1
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j < n and text[j] == ".":
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            if j < n and text[j] in "eE":
                is_float = True
                j += 1
                if j < n and text[j] in "+-":
                    j += 1
                while j < n and text[j].isdigit():
                    j += 1
            raw = text[i:j]
            try:
                value: Any = float(raw) if is_float else int(raw)
            except ValueError as exc:
                raise GraphParseError(
                    f"DSL number at line {start_line} is invalid: {raw!r}"
                ) from exc
            tokens.append(_Token("NUMBER", value, start_line, start_col))
            col += j - i
            i = j
            continue

        if ch in _WORD_START:
            start_line, start_col = line, col
            j = i + 1
            while j < n and text[j] in _WORD_CHARS:
                j += 1
            raw = text[i:j]
            tokens.append(_Token("WORD", raw, start_line, start_col))
            col += j - i
            i = j
            continue

        raise GraphParseError(f"DSL unexpected character {ch!r} at line {line}, column {col}")

    tokens.append(_Token("EOF", None, line, col))
    return tokens


class _Parser:
    """Recursive-descent parser over a DSL token stream."""

    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    # -- token helpers ---------------------------------------------------

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _advance(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _at(self, kind: str) -> bool:
        return self._peek().kind == kind

    def _expect(self, kind: str) -> _Token:
        token = self._peek()
        if token.kind != kind:
            self._error(f"expected {kind}, got {token.kind}")
        return self._advance()

    def _error(self, message: str) -> NoReturn:
        token = self._peek()
        raise GraphParseError(
            f"DSL parse error at line {token.line}, column {token.col}: {message}"
        )

    def _skip_separators(self) -> None:
        while self._at("NEWLINE"):
            self._advance()

    # -- keys and values -------------------------------------------------

    def _parse_key(self) -> str:
        token = self._peek()
        if token.kind in ("WORD", "STRING"):
            self._advance()
            return str(token.value)
        self._error("expected a key (word or string)")

    def _parse_label(self) -> str:
        token = self._peek()
        if token.kind in ("WORD", "STRING"):
            self._advance()
            label = str(token.value)
            if not label:
                self._error("label must be non-empty")
            return label
        self._error("expected a label (word or string)")

    def _parse_value(self) -> Any:
        token = self._peek()
        if token.kind == "STRING":
            self._advance()
            return token.value
        if token.kind == "NUMBER":
            self._advance()
            return token.value
        if token.kind == "WORD":
            self._advance()
            text = str(token.value)
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "null":
                return None
            return text
        if token.kind == "LBRACK":
            return self._parse_list()
        if token.kind == "LBRACE":
            return self._parse_inline_map()
        self._error("expected a value")

    def _skip_list_separators(self) -> None:
        while self._at("NEWLINE"):
            self._advance()

    def _parse_list(self) -> list[Any]:
        self._expect("LBRACK")
        items: list[Any] = []
        self._skip_list_separators()
        if self._at("RBRACK"):
            self._advance()
            return items
        while True:
            items.append(self._parse_value())
            self._skip_list_separators()
            if self._at("COMMA"):
                self._advance()
                self._skip_list_separators()
                if self._at("RBRACK"):
                    self._advance()
                    break
                continue
            if self._at("RBRACK"):
                self._advance()
                break
            self._error("expected ',' or ']' in list")
        return items

    def _parse_inline_map(self) -> dict[str, Any]:
        self._expect("LBRACE")
        result: dict[str, Any] = {}
        if self._at("RBRACE"):
            self._advance()
            return result
        while True:
            key = self._parse_key()
            self._expect("EQ")
            result[key] = self._parse_value()
            if self._at("COMMA"):
                self._advance()
                continue
            if self._at("RBRACE"):
                self._advance()
                break
            self._error("expected ',' or '}' in map")
        return result

    def _parse_block(self) -> dict[str, Any]:
        """Parse a ``{ ... }`` block of newline-separated ``key`` entries."""
        self._expect("LBRACE")
        result: dict[str, Any] = {}
        self._skip_separators()
        while not self._at("RBRACE"):
            if self._at("EOF"):
                self._error("unexpected end of input inside a block")
            key = self._parse_key()
            result[key] = self._parse_entry_value()
            if self._at("NEWLINE"):
                self._skip_separators()
            elif self._at("RBRACE"):
                break
            else:
                self._error("expected a newline or '}' after an entry")
        self._expect("RBRACE")
        return result

    def _parse_entry_value(self) -> Any:
        """Parse ``= value`` or a nested ``{ ... }`` block."""
        if self._at("EQ"):
            self._advance()
            return self._parse_value()
        if self._at("LBRACE"):
            return self._parse_block()
        self._error("expected '=' or '{' after a key")

    # -- document --------------------------------------------------------

    def parse(self) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "spec_version": "1.0",
            "metadata": {},
            "nodes": [],
            "edges": [],
        }

        self._skip_separators()
        if not self._at("WORD") or self._peek().value != "automation":
            self._error("a DSL automation must start with 'automation \"<id>\"'")
        self._advance()
        raw["metadata"]["id"] = self._parse_label()

        while True:
            self._skip_separators()
            token = self._peek()
            if token.kind == "EOF":
                break
            if token.kind not in ("WORD", "STRING"):
                self._error("expected a top-level statement")
            keyword = str(token.value)
            self._advance()
            self._parse_top_level(keyword, raw)

        return raw

    def _parse_top_level(self, keyword: str, raw: dict[str, Any]) -> None:
        metadata = raw["metadata"]
        if keyword == "spec_version":
            self._expect("EQ")
            raw["spec_version"] = self._parse_value()
            return
        if keyword == "automation":
            self._error("'automation' may only appear once, at the top")
        if keyword in ("name", "description", "version", "author"):
            self._expect("EQ")
            metadata[keyword] = self._parse_value()
            return
        if keyword == "tags":
            self._expect("EQ")
            metadata["tags"] = self._parse_value()
            return
        if keyword in ("created_at", "updated_at"):
            self._expect("EQ")
            metadata[keyword] = self._parse_value()
            return
        if keyword == "default_dry_run":
            self._expect("EQ")
            raw.setdefault("settings", {})["default_dry_run"] = self._parse_value()
            return
        if keyword == "max_concurrency":
            self._expect("EQ")
            raw.setdefault("settings", {})["max_concurrency"] = self._parse_value()
            return
        if keyword == "scope":
            raw.setdefault("settings", {})["scope"] = self._parse_block()
            return
        if keyword == "error_handling":
            raw.setdefault("settings", {})["error_handling"] = self._parse_block()
            return
        if keyword == "extra_meta":
            raw["extra_meta"] = self._parse_block()
            return
        if keyword == "variable":
            label = self._parse_label()
            raw.setdefault("variables", {})[label] = self._parse_variable()
            return
        if keyword == "trigger":
            label = self._parse_label()
            raw.setdefault("triggers", []).append(self._parse_trigger(label))
            return
        if keyword == "node":
            label = self._parse_label()
            raw["nodes"].append(self._parse_node(label))
            return
        if keyword == "edge":
            label = self._parse_label()
            raw["edges"].append(self._parse_edge(label))
            return
        self._error(f"unknown top-level statement {keyword!r}")

    def _parse_variable(self) -> dict[str, Any]:
        if self._at("EQ"):
            self._advance()
            return {"type": "string", "value": self._parse_value()}
        return self._parse_block()

    def _parse_trigger(self, label: str) -> dict[str, Any]:
        body = self._parse_block()
        trigger: dict[str, Any] = {
            "id": label,
            "type": body.get("type", ""),
        }
        if "title" in body:
            trigger["title"] = body["title"]
        if body.get("params"):
            trigger["params"] = body["params"]
        return trigger

    def _parse_node(self, label: str) -> dict[str, Any]:
        body = self._parse_node_body()
        node: dict[str, Any] = {
            "id": label,
            "type": body.get("type", ""),
        }
        if "title" in body:
            node["title"] = body["title"]
        if "state" in body:
            node["state"] = body["state"]
        if "description" in body:
            node["description"] = body["description"]
        if body.get("params"):
            node["params"] = body["params"]
        if "inputs" in body:
            node["inputs"] = body["inputs"]
        if "outputs" in body:
            node["outputs"] = body["outputs"]
        if "position" in body:
            node["position"] = body["position"]
        return node

    _NODE_KEYS = frozenset(
        {"type", "title", "state", "description", "params", "inputs", "outputs", "position"}
    )

    def _parse_node_body(self) -> dict[str, Any]:
        """Parse a node block, routing unknown keys into ``params``."""
        self._expect("LBRACE")
        body: dict[str, Any] = {}
        self._skip_separators()
        while not self._at("RBRACE"):
            if self._at("EOF"):
                self._error("unexpected end of input inside a node block")
            key = self._parse_key()
            value = self._parse_entry_value()
            if key in self._NODE_KEYS:
                body[key] = value
            else:
                body.setdefault("params", {})[key] = value
            if self._at("NEWLINE"):
                self._skip_separators()
            elif self._at("RBRACE"):
                break
            else:
                self._error("expected a newline or '}' after a node entry")
        self._expect("RBRACE")
        return body

    def _parse_edge(self, label: str) -> dict[str, Any]:
        body = self._parse_block()
        if "condition" in body:
            self._error(
                "edge field 'condition' is not supported; "
                "use flow.if_branch or flow.filter instead"
            )
        edge: dict[str, Any] = {
            "id": label,
            "source": body.get("source", ""),
            "target": body.get("target", ""),
        }
        for key in ("source_port", "target_port"):
            if key in body:
                edge[key] = body[key]
        if body.get("label"):
            edge["label"] = body["label"]
        if body.get("kind"):
            edge["kind"] = body["kind"]
        return edge


def dsl_to_dict(text: str) -> dict[str, Any]:
    """Parse DSL text into the raw JSON automation-schema dictionary."""
    if not isinstance(text, str):
        raise GraphParseError("DSL source must be a string")
    tokens = _tokenize(text)
    return _Parser(tokens).parse()
