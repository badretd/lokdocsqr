"""utils.json_validate — validate JSON data against a stdlib JSON Schema subset.

This is a deliberately small draft-07 subset. It does not import
``jsonschema``, does not use the network, and does not touch the filesystem.

Supported keywords::

    type: string | integer | number | boolean | array | object | null
          (also a list of these strings)
    required: list[str] (for objects)
    properties: dict[str, subschema] (for objects)
    items: subschema (for arrays)
    enum: list[Any]
    minimum / maximum: number
    minLength / maxLength: string
    minItems / maxItems: array

Unknown schema keywords are rejected (not silently ignored), so a typo such
as ``required_typo`` fails validation. This is intentional. In a future
phase this subset may be replaced by the ``jsonschema`` library if needed.

Payload contract::

    {"data": <JSON string | dict | list | scalar>, "schema": <dict>}

Output contract (all keys always present, both run and dry-run)::

    {"valid": bool, "data": <parsed data>, "errors": [...], "error_count": int}
"""
from __future__ import annotations

import json
from typing import Any

from lokdocsqr.core.node_api.decorators import register_node
from lokdocsqr.nodes.flow._base import _FlowBase

__all__ = ["JsonValidateNode"]

_ALLOWED_KEYWORDS = frozenset(
    {
        "type",
        "required",
        "properties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
    }
)

_JSON_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "array", "object", "null"}
)


def _err(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return _is_number(value)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return False


def _validate(
    data: Any,
    schema: Any,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    if not isinstance(schema, dict):
        errors.append(_err(path, "schema must be a dict"))
        return

    for keyword in schema:
        if keyword not in _ALLOWED_KEYWORDS:
            errors.append(_err("$schema", f"unsupported keyword {keyword!r}"))

    if "type" in schema:
        expected = schema["type"]
        expected_types: list[str] = []
        if isinstance(expected, str):
            expected_types = [expected]
        elif isinstance(expected, list):
            expected_types = [t for t in expected if isinstance(t, str)]
            if len(expected_types) != len(expected):
                errors.append(
                    _err(path, "type list must contain only strings")
                )
        else:
            errors.append(
                _err(path, "type must be a string or list of strings")
            )

        for expected_type in expected_types:
            if expected_type not in _JSON_TYPES:
                errors.append(
                    _err(path, f"unsupported type {expected_type!r}")
                )

        if expected_types and not any(
            expected_type in _JSON_TYPES
            and _matches_type(data, expected_type)
            for expected_type in expected_types
        ):
            errors.append(
                _err(path, f"expected type {'|'.join(expected_types)}")
            )

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list):
            errors.append(_err(path, "enum must be a list"))
        elif not any(data == candidate for candidate in enum):
            errors.append(_err(path, "value not in enum"))

    if "minimum" in schema:
        minimum = schema["minimum"]
        if not _is_number(minimum):
            errors.append(_err(path, "minimum must be a number"))
        elif _is_number(data) and data < minimum:
            errors.append(_err(path, f"value must be >= {minimum}"))

    if "maximum" in schema:
        maximum = schema["maximum"]
        if not _is_number(maximum):
            errors.append(_err(path, "maximum must be a number"))
        elif _is_number(data) and data > maximum:
            errors.append(_err(path, f"value must be <= {maximum}"))

    if "minLength" in schema:
        min_length = schema["minLength"]
        if not isinstance(min_length, int) or isinstance(min_length, bool):
            errors.append(_err(path, "minLength must be an integer"))
        elif isinstance(data, str) and len(data) < min_length:
            errors.append(
                _err(path, f"string length must be >= {min_length}")
            )

    if "maxLength" in schema:
        max_length = schema["maxLength"]
        if not isinstance(max_length, int) or isinstance(max_length, bool):
            errors.append(_err(path, "maxLength must be an integer"))
        elif isinstance(data, str) and len(data) > max_length:
            errors.append(
                _err(path, f"string length must be <= {max_length}")
            )

    if "minItems" in schema:
        min_items = schema["minItems"]
        if not isinstance(min_items, int) or isinstance(min_items, bool):
            errors.append(_err(path, "minItems must be an integer"))
        elif isinstance(data, list) and len(data) < min_items:
            errors.append(
                _err(path, f"array length must be >= {min_items}")
            )

    if "maxItems" in schema:
        max_items = schema["maxItems"]
        if not isinstance(max_items, int) or isinstance(max_items, bool):
            errors.append(_err(path, "maxItems must be an integer"))
        elif isinstance(data, list) and len(data) > max_items:
            errors.append(
                _err(path, f"array length must be <= {max_items}")
            )

    if "required" in schema:
        required = schema["required"]
        if not isinstance(required, list):
            errors.append(_err(path, "required must be a list of strings"))
        else:
            for name in required:
                if not isinstance(name, str):
                    errors.append(
                        _err(path, "required must be a list of strings")
                    )
                    continue
                if isinstance(data, dict) and name not in data:
                    errors.append(
                        _err(f"{path}.{name}", "required property missing")
                    )

    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            errors.append(_err(path, "properties must be a dict"))
        elif isinstance(data, dict):
            for key, subschema in properties.items():
                if not isinstance(key, str):
                    errors.append(_err(path, "property names must be strings"))
                    continue
                if key not in data:
                    continue
                if not isinstance(subschema, dict):
                    errors.append(
                        _err(f"{path}.{key}", "subschema must be a dict")
                    )
                    continue
                _validate(data[key], subschema, f"{path}.{key}", errors)

    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, dict):
            errors.append(_err(path, "items must be a dict"))
        elif isinstance(data, list):
            for index, item in enumerate(data):
                _validate(item, items, f"{path}[{index}]", errors)


def _parse_data(raw: Any) -> tuple[Any, dict[str, str] | None]:
    if isinstance(raw, str):
        try:
            return json.loads(raw), None
        except json.JSONDecodeError as exc:
            return None, _err(
                "$", f"parse error: {type(exc).__name__}: {exc}"
            )
    return raw, None


def _validate_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "valid": False,
            "data": None,
            "errors": [_err("$", "payload must be a dict")],
            "error_count": 1,
        }

    data, parse_error = _parse_data(payload.get("data"))
    if parse_error is not None:
        return {
            "valid": False,
            "data": None,
            "errors": [parse_error],
            "error_count": 1,
        }

    schema = payload.get("schema")
    errors: list[dict[str, str]] = []
    if not isinstance(schema, dict):
        errors.append(_err("$schema", "schema must be a dict"))
    else:
        _validate(data, schema, "$", errors)

    return {
        "valid": not errors,
        "data": data,
        "errors": errors,
        "error_count": len(errors),
    }


@register_node("utils.json_validate")
class JsonValidateNode(_FlowBase):
    id = "utils.json_validate"
    retry_safe = True

    @property
    def title(self) -> str:
        return "JSON Parse / Validate"

    @property
    def inputs(self) -> dict[str, str]:
        return {"data": "any", "schema": "dict"}

    @property
    def outputs(self) -> dict[str, str]:
        return {
            "valid": "bool",
            "data": "any",
            "errors": "any[]",
            "error_count": "int",
        }

    def run(self, ctx: Any, payload: Any) -> Any:
        return self._run_template(ctx, payload)

    def dry_run(self, ctx: Any, payload: Any) -> Any:
        return self._dry_run_template(ctx, payload)

    def _execute(self, ctx: Any, payload: Any) -> Any:
        return _validate_payload(payload)

    def _preview(self, ctx: Any, payload: Any) -> Any:
        result = _validate_payload(payload)
        if result["valid"]:
            preview = "валидно: 0 errors"
        else:
            preview = f"{result['error_count']} errors"
        return {**result, "preview": preview, "dry_run": True}
