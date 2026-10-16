"""Deterministic synthetic payloads generated from the provider contracts.

Given a response schema (PrismHR OpenAPI, isolved Swagger, UKG extracted JSON Schema), build an
instance that fills EVERY documented field with a unique, seeded value. Strings are `<field>-<n>`,
numbers are `<1000+n>.25`, dates are real ISO dates, enums pick a documented member. No real data.
"""

import json
import random
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAX_DEPTH = 7


class Generator:
    def __init__(self, document: dict, seed: int):
        self.doc = document
        self.rng = random.Random(seed)
        self.n = 0

    def resolve(self, node: dict, stack: tuple = ()) -> tuple[dict, tuple]:
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if ref in stack:  # recursive definition: stop here
                return {"type": "object", "properties": {}}, stack
            target = self.doc
            for part in ref.lstrip("#/").split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
            node, stack = target, (*stack, ref)
        return node, stack

    def value(self, node: dict, key: str, depth: int = 0, stack: tuple = ()):
        node, stack = self.resolve(node, stack)
        if "allOf" in node:
            merged: dict = {"type": "object", "properties": {}}
            for part in node["allOf"]:
                part, stack = self.resolve(part, stack)
                merged["properties"].update(part.get("properties", {}))
            node = merged
        kind = node.get("type")
        if kind == "object" or "properties" in node:
            if depth >= MAX_DEPTH:
                return {}
            out = {}
            for name, sub in (node.get("properties") or {}).items():
                out[name] = self.value(sub, name, depth + 1, stack)
            return out
        if kind == "array":
            items = node.get("items", {})
            count = 1 if depth >= 3 else self.rng.choice([1, 2])
            return [self.value(items, key, depth + 1, stack) for _ in range(count)]
        self.n += 1
        if "enum" in node:
            return self.rng.choice(node["enum"])
        if kind == "boolean":
            return self.rng.choice([True, False])
        if kind == "integer":
            return 1000 + self.n
        if kind == "number":
            return Decimal(f"{1000 + self.n}.25")
        fmt = node.get("format")
        day = f"2024-{self.n % 12 + 1:02d}-{self.n % 27 + 1:02d}"
        if fmt == "date":
            return day
        if fmt == "date-time":
            return f"{day}T00:00:00"
        return f"{key}-{self.n}"


def prism(seed: int, path: str) -> dict:
    doc = json.loads((ROOT / "contracts/prismhr/openapi/prismhr-2025.09.openapi.json").read_text())
    schema = doc["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    return Generator(doc, seed).value(schema, "root")


def isolved(seed: int, path: str) -> dict:
    doc = json.loads((ROOT / "contracts/isolved/openapi/isolved-v6.swagger.json").read_text())
    schema = doc["paths"][path]["get"]["responses"]["200"]["schema"]
    return Generator(doc, seed).value(schema, "root")


def ukg(seed: int, page_suffix: str) -> dict:
    doc = json.loads((ROOT / "contracts/ukg-ready/reference-schemas.json").read_text())
    for entry in doc["schemas"]:
        if entry["source_page"].endswith(page_suffix) and entry["method"] == "GET":
            schema = entry["schema"]
            return Generator(schema, seed).value(schema, "root")
    raise KeyError(page_suffix)


# name -> (provider, entity, extra context, builder)
def cases(seed: int = 20260101):
    p = "/payroll/v1/getPayrollVouchers"
    legals = "/api/clients/{clientId}/legals"
    emps = "/api/clients/{clientId}/legals/{legalId}/employees"
    dds = emps + "/{employeeId}/directdeposits"
    checks = emps + "/{employeeId}/checks/{id}"
    prefix = "__v2__companies__(cid)__"
    return {
        "prismhr_company": (
            "prismhr",
            "company",
            {},
            lambda: prism(seed, "/clientMaster/v1/getClientMaster"),
        ),
        "prismhr_employee": (
            "prismhr",
            "employee",
            {},
            lambda: prism(seed, "/employee/v1/getEmployee"),
        ),
        "prismhr_deductions": (
            "prismhr",
            "deduction",
            {"employee_id": "E-1"},
            lambda: prism(seed, "/deductions/v1/getDeductions"),
        ),
        "prismhr_payroll": ("prismhr", "payroll", {}, lambda: prism(seed, p)),
        "isolved_company": ("isolved", "company", {}, lambda: isolved(seed, legals)),
        "isolved_employee": ("isolved", "employee", {}, lambda: isolved(seed, emps)),
        "isolved_direct_deposit": (
            "isolved",
            "direct_deposit",
            {"employee_id": "90001"},
            lambda: isolved(seed, dds),
        ),
        "isolved_deduction": (
            "isolved",
            "deduction",
            {},
            lambda: isolved(
                seed,
                "/api/corestream/clients/{clientId}/legals/{legalId}/employees/{employeeId}/deductions",
            ),
        ),
        "isolved_payroll": ("isolved", "payroll", {}, lambda: isolved(seed, checks)),
        "ukg_company": (
            "ukg-ready",
            "company",
            {},
            lambda: ukg(seed, prefix + "lookup__eins.html"),
        ),
        "ukg_employee": (
            "ukg-ready",
            "employee",
            {},
            lambda: {
                "employee": ukg(seed, prefix + "employees__(aid).html"),
                "demographics": ukg(seed, prefix + "employees__(aid)__demographics.html"),
                "pay_info": ukg(seed, prefix + "employees__(aid)__pay-info.html"),
                "compensation_history": ukg(seed, prefix + "compensation__history.html"),
            },
        ),
        "ukg_direct_deposit": (
            "ukg-ready",
            "direct_deposit",
            {"employee_id": "7770001"},
            lambda: ukg(seed, prefix + "employees__(aid)__direct-deposits.html"),
        ),
        "ukg_deduction": (
            "ukg-ready",
            "deduction",
            {"employee_id": "7770001"},
            lambda: ukg(seed, prefix + "employees__(aid)__deductions.html"),
        ),
        "ukg_payroll": (
            "ukg-ready",
            "payroll",
            {},
            lambda: {
                "payroll": ukg(seed, prefix + "payrolls__(id).html"),
                "pay_statements": [ukg(seed, prefix + "payrolls__(id)__pay-statements__(id).html")],
            },
        ),
    }
