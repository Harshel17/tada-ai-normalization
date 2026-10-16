"""Fixtures must only use field names that exist in the provider contracts in /contracts.

This keeps the synthetic fixtures honest: a typo or an invented field fails here.
"""

import json
from functools import cache

import pytest

from conftest import FIXTURES
from normalization.references import ROOT

# Wrapper keys that are OUR convention for sending several documented responses together.
BUNDLE_KEYS = {
    "isolved": {"payroll_run", "checks"},
    "ukg_ready": {"employee", "demographics", "pay_info", "compensation_history", "payroll"},
}


def property_names(node, out: set[str]) -> None:
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            out.update(props)
        for value in node.values():
            property_names(value, out)
    elif isinstance(node, list):
        for value in node:
            property_names(value, out)


@cache
def documented_names(provider_dir: str) -> frozenset[str]:
    names: set[str] = set()
    if provider_dir == "prismhr":
        doc = json.loads(
            (ROOT / "contracts/prismhr/openapi/prismhr-2025.09.openapi.json").read_text()
        )
        property_names(doc["components"]["schemas"], names)
    elif provider_dir == "isolved":
        doc = json.loads((ROOT / "contracts/isolved/openapi/isolved-v6.swagger.json").read_text())
        property_names(doc["definitions"], names)
    else:
        doc = json.loads((ROOT / "contracts/ukg-ready/reference-schemas.json").read_text())
        property_names([s["schema"] for s in doc["schemas"]], names)
    return frozenset(names)


def keys_in(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from keys_in(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys_in(v)


CASES = [
    (provider_dir, path.name)
    for provider_dir in ("prismhr", "isolved", "ukg_ready")
    for path in sorted((FIXTURES / provider_dir).glob("*.json"))
]


@pytest.mark.parametrize(("provider_dir", "name"), CASES)
def test_fixture_keys_exist_in_contract(provider_dir, name):
    payload = json.loads((FIXTURES / provider_dir / name).read_text())
    known = documented_names(provider_dir) | BUNDLE_KEYS.get(provider_dir, set())
    unknown = sorted({k for k in keys_in(payload) if k not in known})
    assert not unknown, f"{provider_dir}/{name} uses fields not found in the contract: {unknown}"
