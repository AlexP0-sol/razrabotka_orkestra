"""Built-in skill: validate JSON data payloads against JSON Schema rules."""

from __future__ import annotations

import json
import sys
from typing import Any

SKILL_METADATA: dict[str, Any] = {
    "id": "json_schema_validator",
    "name": "JSON Schema Payload Validator",
    "version": "1.0.0",
    "description": "Validate JSON instance against object/array/primitive JSON Schema constraints.",
    "tags": ("json", "schema", "validation"),
    "input_schema": {
        "type": "object",
        "required": ["instance", "schema"],
        "properties": {
            "instance": {},
            "schema": {"type": "object"},
        },
    },
    "output_schema": {
        "type": "object",
        "required": ["valid", "errors"],
        "properties": {
            "valid": {"type": "boolean"},
            "errors": {"type": "array"},
        },
    },
}


def _validate(val: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    t = schema.get("type")
    if t == "object":
        if not isinstance(val, dict):
            errors.append(f"{path}: expected object")
            return
        for req in schema.get("required", ()):
            if req not in val:
                errors.append(f"{path}: missing required key '{req}'")
        for k, sub in schema.get("properties", {}).items():
            if k in val and isinstance(sub, dict):
                _validate(val[k], sub, f"{path}.{k}", errors)
    elif t == "array":
        if not isinstance(val, list):
            errors.append(f"{path}: expected array")
            return
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(val):
                _validate(item, item_schema, f"{path}[{idx}]", errors)
    elif t == "string" and not isinstance(val, str):
        errors.append(f"{path}: expected string")
    elif t == "integer" and (isinstance(val, bool) or not isinstance(val, int)):
        errors.append(f"{path}: expected integer")
    elif t == "number" and (isinstance(val, bool) or not isinstance(val, (int, float))):
        errors.append(f"{path}: expected number")
    elif t == "boolean" and not isinstance(val, bool):
        errors.append(f"{path}: expected boolean")

    if "enum" in schema and val not in schema["enum"]:
        errors.append(f"{path}: {val!r} not in enum {schema['enum']}")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if "minimum" in schema and val < schema["minimum"]:
            errors.append(f"{path}: {val} < minimum {schema['minimum']}")
        if "maximum" in schema and val > schema["maximum"]:
            errors.append(f"{path}: {val} > maximum {schema['maximum']}")


def run(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate `payload['instance']` against `payload['schema']`."""
    errors: list[str] = []
    _validate(payload.get("instance"), dict(payload["schema"]), "$", errors)
    return {"valid": len(errors) == 0, "errors": errors}


def _main() -> None:
    raw = sys.stdin.read()
    data: dict[str, Any] = json.loads(raw) if raw.strip() else {}
    sys.stdout.write(json.dumps(run(data)))


if __name__ == "__main__":
    _main()
