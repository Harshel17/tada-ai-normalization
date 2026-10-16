# ruff: noqa: E501
"""A minimal checker of a request body against the DOCUMENTED UKG JSON Schema (loaded from the local contract).

It supports exactly the keywords the documented schema uses (type, enum, required, properties,
additionalProperties, maxLength, pattern, minimum/maximum, items, $ref to local definitions), so the preview is
validated against the contract itself and not against our own opinion of it. No new dependency.
"""

import json
import re
from functools import cache
from typing import Any

from ..references import ROOT

CONTRACT_FILE = "contracts/ukg-ready/reference-schemas.json"
EMPLOYEE_PAGE = "reference-docs/res/__v2__companies__(cid)__employees.html"


@cache
def employee_create_schema() -> dict[str, Any]:
    doc = json.loads((ROOT / CONTRACT_FILE).read_text(encoding="utf-8"))
    for entry in doc["schemas"]:
        if entry["source_page"] == EMPLOYEE_PAGE and entry["method"] == "POST":
            return {**entry["schema"], "_anchor": entry["source_schema_anchor"]}
    raise RuntimeError(
        "the documented UKG create-employee schema is missing from the local contract"
    )


_TYPES = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


def validate(
    value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = ""
) -> list[tuple[str, str]]:
    """Violations as (dotted path, message). Empty means the body satisfies the documented schema."""
    if "$ref" in schema:
        schema = root["definitions"][schema["$ref"].rsplit("/", 1)[-1]]
    out: list[tuple[str, str]] = []
    here = path or "$"
    kind = schema.get("type")
    if kind and not _TYPES[kind](value):
        return [(here, f"expected {kind}")]
    if "enum" in schema and value not in schema["enum"]:
        out.append((here, f"must be one of {schema['enum']}"))
    if isinstance(value, str):
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append((here, f"longer than the documented maximum of {schema['maxLength']}"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append((here, f"does not match the documented pattern {schema['pattern']}"))
    if isinstance(value, int | float) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append((here, f"below the documented minimum {schema['minimum']}"))
        if "maximum" in schema and value > schema["maximum"]:
            out.append((here, f"above the documented maximum {schema['maximum']}"))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                out.append((f"{path}.{name}".lstrip("."), "is required by the documented schema"))
        for name, item in value.items():
            sub = f"{path}.{name}".lstrip(".")
            if name in props:
                out += validate(item, props[name], root, sub)
            elif schema.get("additionalProperties") is False:
                out.append((sub, "is not a documented field"))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            out += validate(item, schema["items"], root, f"{path}[{i}]")
    return out
