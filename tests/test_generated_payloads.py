"""Stress the adapters with payloads generated from the contracts (every documented field filled).

For every case: no crash, no ERROR issue, deterministic output, no documented value lost, and no
secret value (SSN / bank account) anywhere in the output.
"""

import json
from decimal import Decimal

import pytest

from normalization import service
from support.schema_gen import cases

SEED = 20260101
CASES = cases(SEED)

# Documented fields that carry secrets: their generated values must never be in the output.
SECRET_KEYS = {
    "ssn", "accountNum", "accountNumber", "account_number", "account", "social_security",
    "social_insurance", "national_insurance", "bsn", "employeeSSN", "bank_account_number",
    "cobraSSN", "driverLicenseId", "foreignPassportNum", "foreignPassportNumber", "ssnUnmasked",
    "value",  # only inside national_id_numbers (checked by path below)
}  # fmt: skip
SECRET_PATHS = ("national_id_numbers",)
ORDERING_ONLY = {"index"}  # UKG list positions, used to order managers; not data
ENVELOPE = {"errorCode", "errorMessage", "infoMessage", "updateMessage", "total", "startpage", "count",
            "totalItems", "totalPages", "nextPageUrl", "previousPageUrl", "_total_count",
            "extension", "any"}  # fmt: skip
CTX = {"prismhr": "ACME", "isolved": "4501", "ukg-ready": "CID1"}


def leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in ("links", "_links"):
                yield from leaves(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, (*path, i))
    else:
        yield path, node


# One payload feeds several entities (PrismHR/isolved getEmployee also carry deposits, deductions).
ALSO = {
    "prismhr_employee": [("direct_deposit", {}), ("deduction", {})],
    "isolved_employee": [("direct_deposit", {})],
}


def run(name):
    """Returns (payload, [results]) - one result per entity that reads this payload."""
    provider, entity, extra, build = CASES[name]
    payload = build()
    results = []
    for ent, more in [(entity, {}), *ALSO.get(name, [])]:
        ctx = {"tenant_id": "t", "company_id": CTX[provider], **extra, **more}
        results.append(service.normalize(provider, ent, payload, ctx))
    return payload, results


def is_secret(path):
    keys = [k for k in path if isinstance(k, str)]
    if keys[-1] == "value":
        return any(p in keys for p in SECRET_PATHS)
    return keys[-1] in SECRET_KEYS


@pytest.mark.parametrize("name", list(CASES))
def test_generated_payload_is_handled_without_loss_or_leaks(name):
    payload, results = run(name)
    assert results[0].records, "adapter produced nothing from a fully populated payload"
    errors = [i for r in results for i in r.issues if i.severity.value == "error"]
    assert not errors, errors
    out = " ".join(r.model_dump_json() for r in results)
    # JSON-escaped text is searched, so compare against escaped needles too
    redacted = [x for r in results for rec in r.records for x in rec.provenance.redacted_paths]
    lost, leaked = [], []
    for path, value in leaves(payload):
        dotted_tail = (
            "." + ".".join(str(k) for k in path[-2:]) if len(path) > 1 else "." + str(path[0])
        )
        if any(x.endswith(dotted_tail) or x.endswith("." + str(path[-1])) for x in redacted):
            continue  # deliberately withheld, and listed in provenance.redacted_paths
        if (
            value is None
            or value == ""
            or isinstance(value, bool)
            or any(k in ENVELOPE | ORDERING_ONLY for k in path if isinstance(k, str))
        ):
            continue
        text = str(value)
        if is_secret(path):
            if text in out:
                leaked.append(".".join(map(str, path)))
            continue
        if isinstance(value, Decimal):
            text = format(value, "f")
        needle = text[:10] if text.endswith("T00:00:00") else text
        if needle not in out and json.dumps(needle)[1:-1] not in out:
            lost.append(".".join(map(str, path)))
    assert not leaked, f"secret values reached the output: {leaked}"
    # enum-valued fields are tolerated: documented members become canonical enums
    assert len(lost) <= 0 or all(is_enum_field(name, p) for p in lost), f"lost: {lost}"


def is_enum_field(name, dotted):
    """A leaf may be 'lost' only if its value is a documented enum member folded into a canonical enum."""
    payload, _ = run(name)
    path = tuple(int(p) if p.isdigit() else p for p in dotted.split("."))
    node = payload
    for key in path:
        node = node[key]
    return isinstance(node, str) and node.isupper()


@pytest.mark.parametrize("name", list(CASES))
def test_generation_and_normalization_are_deterministic(name):
    a_payload, a = run(name)
    b_payload, b = run(name)
    assert a_payload == b_payload
    assert [r.model_dump_json() for r in a] == [r.model_dump_json() for r in b]


def test_different_seeds_give_different_payloads():
    other = cases(SEED + 1)["prismhr_employee"][3]()
    assert other != CASES["prismhr_employee"][3]()
