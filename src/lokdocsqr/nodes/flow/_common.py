"""Shared condition parser and payload helpers for flow nodes.

Internal helper module — **not** a registered node.  All internals are
underscore-prefixed; the names intended for use by sibling node
modules are listed in ``__all__``.

Phase 8f: inline-graph resolution is delegated via the ``build_inline``
callback of :func:`_resolve_child_graph`.  This lets ``flow.subflow``
keep its own ``_build_inline`` / ``parse_graph`` / ... names in its
module namespace so the 8e regression can patch them (D8f.1 does not
override D8e tests).
"""
from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any

from lokdocsqr.core.errors import GraphValidationError, NodeExecutionError
from lokdocsqr.core.graph.builder import build_graph
from lokdocsqr.core.graph.parser import parse_graph
from lokdocsqr.core.graph.resolver import resolve_states
from lokdocsqr.core.graph.validator import validate_graph
from lokdocsqr.core.models import ResolvedGraph
from lokdocsqr.core.node_api.registry import registry


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


def _base_mapping(ctx: Any, payload: Any) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    variables = getattr(ctx, "variables", None)
    if isinstance(variables, dict):
        mapping.update(variables)

    if isinstance(payload, dict):
        extra = payload.get("variables")
        if isinstance(extra, dict):
            mapping.update(extra)
        for key, value in payload.items():
            if key in (
                "items",
                "files",
                "file_refs",
                "input",
                "data",
                "condition",
                "variables",
            ):
                continue
            mapping[key] = value
    return mapping


def _extract_items(payload: Any) -> tuple[list[Any], bool]:
    if isinstance(payload, list):
        return payload, True
    if isinstance(payload, tuple):
        return list(payload), True
    if isinstance(payload, dict):
        for key in ("items", "files", "file_refs", "input", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value, False
    return [], False


# ── nested-graph resolution (Phase 8f) ────────────────────────────────
_DEFAULT_MAX_DEPTH = 4

BuildInline = Callable[[dict], ResolvedGraph]


def _resolve_max_depth(ctx: Any, payload: dict[str, Any]) -> Any:
    """Pick max_depth source: payload when present, else config, else 4.

    Validation is a separate step so that an invalid payload value
    surfaces with the original ``subflow: max_depth must be a positive
    integer`` message (regression 8e).
    """
    if "max_depth" in payload:
        return payload.get("max_depth")
    config = getattr(ctx, "config", None)
    flow = getattr(config, "flow", None)
    return getattr(flow, "max_subflow_depth", _DEFAULT_MAX_DEPTH)


def _current_depth(ctx: Any) -> int:
    value = getattr(ctx, "flow_depth", 0)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _subflow_stack(ctx: Any) -> tuple[str, ...]:
    value = getattr(ctx, "subflow_stack", ())
    if isinstance(value, tuple):
        return value
    try:
        return tuple(value)
    except TypeError:
        return ()


def _build_inline_default(graph: dict) -> ResolvedGraph:
    """Default inline builder (used by flow.loop_batch).

    ``flow.subflow`` deliberately does NOT use this — it defines its own
    module-level ``_build_inline`` so the 8e regression can patch it.
    """
    document = parse_graph(graph)
    errors = validate_graph(document)
    if errors:
        raise GraphValidationError("; ".join(errors))
    states = resolve_states(document)
    return build_graph(document, states, registry)


def _resolve_child_graph(
    ctx: Any,
    payload: dict,
    *,
    build_inline: BuildInline | None = None,
) -> tuple[str | None, ResolvedGraph]:
    """Resolve a child graph for nested nodes (subflow, loop_batch).

    Order of checks (fail-closed, R26):

      1. exactly-one of ``graph_id`` / ``graph``;
      2. resolve ``max_depth`` (payload value if present, else
         ``AppConfig.flow.max_subflow_depth``, else 4);
      3. validate ``max_depth`` as a positive int (booleans rejected);
      4. filesystem-path guard for ``graph_id``;
      5. depth guard against ``ctx.flow_depth`` (max inclusive, D8e.6);
      6. ``graph_id`` branch: cycle guard against ``ctx.subflow_stack``,
         then ``ctx.automations.get(graph_id)`` (already ``ResolvedGraph``);
      7. inline branch: delegate to ``build_inline`` (default:
         :func:`_build_inline_default`);
      8. return ``(graph_id, resolved_graph)`` — ``graph_id`` is ``None``
         for inline graphs.

    ``build_inline`` lets the caller keep parse/validate/resolve/build
    in its own module namespace; ``flow.subflow`` uses this so that
    monkeypatching ``subflow.parse_graph`` etc. is honoured (8e contract).
    """
    if not isinstance(payload, dict):
        payload = {}

    graph_id_raw = payload.get("graph_id")
    graph_id = (
        graph_id_raw
        if isinstance(graph_id_raw, str) and graph_id_raw
        else None
    )
    graph_raw = payload.get("graph")
    graph = (
        graph_raw
        if isinstance(graph_raw, dict) and graph_raw
        else None
    )

    if (graph_id is None) == (graph is None):
        raise NodeExecutionError(
            "subflow: exactly one of 'graph_id' or 'graph' is required"
        )

    max_depth = _resolve_max_depth(ctx, payload)
    if (
        not isinstance(max_depth, int)
        or isinstance(max_depth, bool)
        or max_depth <= 0
    ):
        raise NodeExecutionError(
            f"subflow: max_depth must be a positive integer, got {max_depth!r}"
        )

    current_depth = _current_depth(ctx)
    if current_depth >= max_depth:
        raise NodeExecutionError(
            f"subflow: max depth exceeded: current={current_depth}, "
            f"limit={max_depth}"
        )

    if graph_id is not None:
        if (
            "/" in graph_id
            or "\\" in graph_id
            or graph_id.endswith(".json")
        ):
            raise NodeExecutionError(
                "subflow: filesystem paths are not supported; "
                "use AutomationCatalog graph_id"
            )
        if graph_id in _subflow_stack(ctx):
            raise NodeExecutionError(
                f"subflow: cycle detected: {graph_id}"
            )
        catalog = getattr(ctx, "automations", None)
        if catalog is None:
            raise NodeExecutionError(
                "subflow: AutomationCatalog not configured in ctx.automations"
            )
        built = catalog.get(graph_id)
        if built is None:
            raise NodeExecutionError(
                f"subflow: graph_id not found in catalog: {graph_id}"
            )
        return graph_id, built

    builder = build_inline if build_inline is not None else _build_inline_default
    resolved = builder(graph)
    return None, resolved


__all__ = [
    "_CondError",
    "_evaluate",
    "_base_mapping",
    "_extract_items",
    "_resolve_child_graph",
    "_build_inline_default",
    "BuildInline",
]
