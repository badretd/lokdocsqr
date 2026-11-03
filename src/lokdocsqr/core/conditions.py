"""Safe condition expression parser shared by flow nodes and edge gates.

This module lives in ``core`` so the executor can evaluate edge conditions
without importing ``lokdocsqr.nodes`` (architecture invariant 8). The
``nodes.flow._common`` module re-exports thin wrappers for compatibility.
"""
from __future__ import annotations

import types
from typing import Any

class _CondError(Exception):
    """Internal condition parse/evaluation failure."""


_IDENT_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)
_IDENT_CONT = _IDENT_START | frozenset("0123456789")


def _tokenize(expr: str) -> list[tuple[str, str]]:
    if ".." in expr or "/" in expr or "\\" in expr:
        raise _CondError("path-like condition rejected")

    tokens: list[tuple[str, str]] = []
    i = 0
    n = len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue

        if c in ("'", '"'):
            quote = c
            i += 1
            buf: list[str] = []
            while i < n:
                ch = expr[i]
                if ch == "\\":
                    i += 1
                    if i >= n:
                        raise _CondError("bad escape")
                    esc = expr[i]
                    buf.append(
                        {
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                            "\\": "\\",
                            "'": "'",
                            '"': '"',
                        }.get(esc, esc)
                    )
                    i += 1
                    continue
                if ch == quote:
                    i += 1
                    break
                buf.append(ch)
                i += 1
            else:
                raise _CondError("unterminated string")
            tokens.append(("STRING", "".join(buf)))
            continue

        if c.isdigit() or (
            c == "-" and i + 1 < n and expr[i + 1].isdigit()
        ):
            start = i
            if c == "-":
                i += 1
            while i < n and expr[i].isdigit():
                i += 1
            if i < n and expr[i] == ".":
                i += 1
                while i < n and expr[i].isdigit():
                    i += 1
            tokens.append(("NUMBER", expr[start:i]))
            continue

        if c in _IDENT_START:
            start = i
            i += 1
            while i < n and expr[i] in _IDENT_CONT:
                i += 1
            word = expr[start:i]
            # Only ``item.<key>`` is dotted; general attribute access stays
            # unsupported (and therefore a parse error).
            if (
                word == "item"
                and i + 1 < n
                and expr[i] == "."
                and expr[i + 1] in _IDENT_START
            ):
                key_start = i + 1
                i = key_start + 1
                while i < n and expr[i] in _IDENT_CONT:
                    i += 1
                word = expr[start:i]
            if word in ("and", "or", "not", "in"):
                tokens.append(("OP", word))
            elif word in ("True", "False", "None"):
                tokens.append(("CONST", word))
            else:
                tokens.append(("IDENT", word))
            continue

        two = expr[i : i + 2]
        if two in ("==", "!=", "<=", ">="):
            tokens.append(("OP", two))
            i += 2
            continue
        if c in "<>":
            tokens.append(("OP", c))
            i += 1
            continue
        if c == "(":
            tokens.append(("LPAREN", "("))
            i += 1
            continue
        if c == ")":
            tokens.append(("RPAREN", ")"))
            i += 1
            continue
        if c == "[":
            tokens.append(("LBRACKET", "["))
            i += 1
            continue
        if c == "]":
            tokens.append(("RBRACKET", "]"))
            i += 1
            continue
        if c == ",":
            tokens.append(("COMMA", ","))
            i += 1
            continue
        raise _CondError("unsupported token")

    tokens.append(("EOF", ""))
    return tokens


def _lookup(name: str, mapping: dict[str, Any]) -> Any:
    if name.startswith("item."):
        item = mapping.get("item")
        key = name[len("item.") :]
        if not isinstance(item, dict) or key not in item:
            raise _CondError("unknown item field")
        value = item[key]
    else:
        if name not in mapping:
            raise _CondError("unknown name")
        value = mapping[name]
    if isinstance(value, (types.ModuleType, type)) or callable(value):
        raise _CondError("unsafe value")
    return value


def _compare(left: Any, op: str, right: Any) -> bool:
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "in":
            if not isinstance(
                right, (str, list, tuple, set, frozenset, dict)
            ):
                raise _CondError("in requires a safe container")
            return left in right
        if op == "not in":
            if not isinstance(
                right, (str, list, tuple, set, frozenset, dict)
            ):
                raise _CondError("not in requires a safe container")
            return left not in right
    except _CondError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to per-item error
        raise _CondError("comparison failed") from exc
    raise _CondError("unsupported operator")


class _Parser:
    def __init__(
        self, tokens: list[tuple[str, str]], mapping: dict[str, Any]
    ) -> None:
        self.tokens = tokens
        self.mapping = mapping
        self.pos = 0

    def _peek(self, offset: int = 0) -> tuple[str, str]:
        idx = self.pos + offset
        if idx >= len(self.tokens):
            return ("EOF", "")
        return self.tokens[idx]

    def _accept(self, kind: str) -> bool:
        if self._peek()[0] == kind:
            self.pos += 1
            return True
        return False

    def _accept_op(self, op: str) -> bool:
        tok = self._peek()
        if tok[0] == "OP" and tok[1] == op:
            self.pos += 1
            return True
        return False

    def parse(self) -> bool:
        value = self._or()
        if self._peek()[0] != "EOF":
            raise _CondError("trailing tokens")
        return bool(value)

    def _or(self) -> Any:
        value = self._and()
        while self._accept_op("or"):
            rhs = self._and()
            value = bool(value) or bool(rhs)
        return value

    def _and(self) -> Any:
        value = self._not()
        while self._accept_op("and"):
            rhs = self._not()
            value = bool(value) and bool(rhs)
        return value

    def _not(self) -> Any:
        if self._accept_op("not"):
            return not bool(self._not())
        return self._comparison()

    def _comparison(self) -> Any:
        left = self._primary()
        tok = self._peek()
        if tok[0] == "OP" and tok[1] in (
            "==",
            "!=",
            "<",
            "<=",
            ">",
            ">=",
            "in",
        ):
            op = tok[1]
            self.pos += 1
            right = self._primary()
            return _compare(left, op, right)

        if (
            tok[0] == "OP"
            and tok[1] == "not"
            and self._peek(1) == ("OP", "in")
        ):
            self.pos += 2
            right = self._primary()
            return _compare(left, "not in", right)

        return left

    def _primary(self) -> Any:
        tok = self._peek()
        kind = tok[0]

        if kind == "LPAREN":
            self.pos += 1
            first = self._or()
            if self._accept("COMMA"):
                values = [first]
                while True:
                    values.append(self._or())
                    if self._accept("COMMA"):
                        continue
                    if not self._accept("RPAREN"):
                        raise _CondError("missing )")
                    return tuple(values)
            if not self._accept("RPAREN"):
                raise _CondError("missing )")
            return first

        if kind == "LBRACKET":
            self.pos += 1
            values = []
            if self._accept("RBRACKET"):
                return values
            while True:
                values.append(self._or())
                if self._accept("COMMA"):
                    continue
                if not self._accept("RBRACKET"):
                    raise _CondError("missing ]")
                return values

        if kind == "NUMBER":
            self.pos += 1
            text = tok[1]
            return float(text) if "." in text else int(text)

        if kind == "STRING":
            self.pos += 1
            return tok[1]

        if kind == "CONST":
            self.pos += 1
            if tok[1] == "True":
                return True
            if tok[1] == "False":
                return False
            return None

        if kind == "IDENT":
            self.pos += 1
            return _lookup(tok[1], self.mapping)

        raise _CondError("expected value")


def _evaluate(condition: Any, mapping: dict[str, Any]) -> bool:
    if not isinstance(condition, str) or not condition.strip():
        raise _CondError("missing condition")
    return _Parser(_tokenize(condition), mapping).parse()
