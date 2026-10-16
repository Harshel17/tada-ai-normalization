"""No source value may silently disappear.

For every leaf value in every fixture: replace just that value with a unique sentinel, normalize,
and require the sentinel to show up somewhere in the output (a mapped field, a `*_raw` field or
`extras`) - or the field to be listed as deliberately redacted. A shared payload (for example
PrismHR getEmployee) is allowed to be kept by any of the entities that read it.
"""

import copy
import re
from decimal import Decimal

import pytest

from conftest import load_fixture
from normalization import service

# Response-envelope / paging metadata and a documented duplicate: not record data.
# A documented unit conversion (milliseconds -> hours): the number changes, and the result is
# flagged under provenance.derived_fields; asserted in test_employee_coverage.py.
CONVERTED = {"standard_work_day"}
ENVELOPE = {"total", "startpage", "count", "totalItems", "totalPages", "_total_count", "checkNum"}

P, ISO, U = "prismhr", "isolved", "ukg-ready"
# (fixture dir, file) -> [(provider, entity, extra context)]
RUNS = {
    ("prismhr", "client_master.json"): [(P, "company", {"company_id": "ACME"})],
    ("prismhr", "employee_get_employee.json"): [
        (P, e, {"company_id": "ACME"}) for e in ("employee", "direct_deposit", "deduction")
    ],
    ("prismhr", "deductions_get_deductions.json"): [
        (P, "deduction", {"company_id": "ACME", "employee_id": "E1001"})
    ],
    ("prismhr", "payroll_vouchers.json"): [(P, "payroll", {"company_id": "ACME"})],
    ("isolved", "legals.json"): [(ISO, "company", {})],
    ("isolved", "employees.json"): [
        (ISO, "employee", {"company_id": "4501"}),
        (ISO, "direct_deposit", {"company_id": "4501"}),
    ],
    ("isolved", "direct_deposits.json"): [
        (ISO, "direct_deposit", {"company_id": "4501", "employee_id": "90001"})
    ],
    ("isolved", "deductions.json"): [(ISO, "deduction", {"company_id": "4501"})],
    ("isolved", "payroll.json"): [(ISO, "payroll", {"company_id": "4501"})],
    ("ukg_ready", "eins.json"): [(U, "company", {"company_id": "C"})],
    ("ukg_ready", "employee_bundle.json"): [(U, "employee", {"company_id": "C"})],
    ("ukg_ready", "direct_deposits.json"): [
        (U, "direct_deposit", {"company_id": "C", "employee_id": "1"})
    ],
    ("ukg_ready", "deductions.json"): [(U, "deduction", {"company_id": "C", "employee_id": "1"})],
    ("ukg_ready", "payroll.json"): [(U, "payroll", {"company_id": "C"})],
}


def leaf_paths(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in ("links", "_links"):
                yield from leaf_paths(value, (*path, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaf_paths(value, (*path, i))
    else:
        yield path, node


def sentinel(value, n):
    """A same-typed replacement that cannot occur in the fixtures, or None if not testable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, Decimal)):
        return Decimal(f"7777.{n % 90 + 10}")
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d\d-\d\d(T[\d:]+)?", value):
            return "2031-02-03" + value[10:]
        return f"SENT{n:04d}"
    return None


def needle(s):
    return s[:10] if isinstance(s, str) and s.startswith("2031-") else str(s)


@pytest.mark.parametrize(("folder", "name"), list(RUNS), ids=[f"{d}/{n}" for d, n in RUNS])
def test_every_source_value_is_kept_or_redacted(folder, name):
    base = load_fixture(folder, name)
    lost = []
    for n, (path, value) in enumerate(leaf_paths(base)):
        if value is None or value == "" or path[-1] in ENVELOPE | CONVERTED:
            continue
        new = sentinel(value, n)
        if new is None:
            continue
        doc = copy.deepcopy(base)
        target = doc
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = new
        kept = False
        for provider, entity, ctx in RUNS[(folder, name)]:
            result = service.normalize(provider, entity, doc, {"tenant_id": "t", **ctx})
            redacted = [r for rec in result.records for r in rec.provenance.redacted_paths]
            dotted = "".join(f"[{k}]" if isinstance(k, int) else f".{k}" for k in path)
            if needle(new) in result.model_dump_json() or any(
                r.endswith(dotted.split("].", 1)[-1]) for r in redacted
            ):
                kept = True
                break
        if not kept:
            lost.append(".".join(map(str, path)))
    assert not lost, f"values that vanished: {lost}"
