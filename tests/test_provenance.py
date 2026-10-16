"""Provenance: the trace names the source fields that actually produced each canonical value."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from normalization import service
from normalization.adapters.base import Collector
from normalization.canonical.enums import Provider
from support.scenarios import CASES

FIX = Path(__file__).parent / "fixtures"
SECRETS = (
    "123-45-6789", "987-65-4321", "900-44-3333", "000123456789", "998877665544",
    "5550001111", "4400112233", "000999888777",
)  # fmt: skip


def load(rel):
    return json.loads((FIX / rel).read_text(), parse_float=Decimal)


def run(provider, entity, payload, trace=True, **ctx):
    return service.normalize(provider, entity, payload, {"tenant_id": "t", **ctx}, trace=trace)


def by_dest(result, i=0):
    return {t["dest"]: t for t in result.traces[i]}


def paths(step):
    return [s["path"] for s in step["sources"]]


PRISM = lambda: load("prismhr/employee_get_employee.json")  # noqa: E731
ISOLVED = lambda: load("isolved/employees.json")  # noqa: E731
UKG = lambda: load("ukg_ready/employee_bundle.json")  # noqa: E731

EXPECTED = {
    ("prismhr", "employee"): {
        "first_name": "$.employee[0].firstName",
        "employee_number": "$.employee[0].client.clientEmployeeId",
        "hire_date": "$.employee[0].client.lastHireDate",
        "original_hire_date": "$.employee[0].client.firstHireDate",
        "job_code": "$.employee[0].client.jobCode",
        "compensation.annual_salary": "$.employee[0].compensation.annualPay",
        "employment_status_raw": "$.employee[0].client.employeeStatus",
    },
    ("isolved", "employee"): {
        "first_name": "$.results[0].nameAddress.firstName",
        "hire_date": "$.results[0].hireDate",
        "job_title": "$.results[0].jobTitle",
        "compensation.annual_salary": "$.results[0].annualSalary",
    },
    ("ukg-ready", "employee"): {
        "first_name": "$.employee.first_name",
        "hire_date": "$.employee.dates.hired",
        "employee_number": "$.employee.employee_id",
        "compensation.effective_date": "$.compensation_history.compensations[1].effective_from",
    },
}


@pytest.mark.parametrize(
    ("key", "payload", "ctx"),
    [
        (("prismhr", "employee"), PRISM, {"company_id": "ACME"}),
        (("isolved", "employee"), ISOLVED, {"company_id": "4501"}),
        (("ukg-ready", "employee"), UKG, {"company_id": "CID1"}),
    ],
    ids=["prismhr", "isolved", "ukg"],
)
def test_exact_source_paths_for_representative_employee_fields(key, payload, ctx):
    steps = by_dest(run(*key, payload(), **ctx))
    for dest, path in EXPECTED[key].items():
        assert paths(steps[dest]) == [path], dest


@pytest.mark.parametrize("provider", ["prismhr", "isolved", "ukg-ready"])
def test_trace_is_opt_in_and_never_changes_the_records(provider):
    payload, entity, ctx = {
        "prismhr": (PRISM, "employee", {"company_id": "ACME"}),
        "isolved": (ISOLVED, "employee", {"company_id": "4501"}),
        "ukg-ready": (UKG, "employee", {"company_id": "CID1"}),
    }[provider]
    off = run(provider, entity, payload(), trace=False, **ctx)
    on = run(provider, entity, payload(), trace=True, **ctx)
    assert off.traces == [] and on.traces
    assert off.model_dump_json() == on.model_dump_json()
    assert len(on.traces) == len(on.records)


def test_identical_values_in_several_fields_do_not_confuse_the_trace():
    doc = PRISM()
    client = doc["employee"][0]["client"]
    for key in ("lastHireDate", "firstHireDate", "jobStartDate", "statusDate", "seniorityDate"):
        client[key] = "2020-02-02"
    steps = by_dest(run("prismhr", "employee", doc, company_id="ACME"))
    # Each canonical date points at its OWN field, even though every value is equal.
    assert paths(steps["hire_date"]) == ["$.employee[0].client.lastHireDate"]
    assert paths(steps["original_hire_date"]) == ["$.employee[0].client.firstHireDate"]
    assert paths(steps["start_date"]) == ["$.employee[0].client.jobStartDate"]
    assert paths(steps["status_date"]) == ["$.employee[0].client.statusDate"]


def test_fallback_records_only_the_field_that_was_used():
    def pronoun_step(name_val, personal_val):
        doc = ISOLVED()
        e = doc["results"][0]
        e["nameAddress"]["preferredPronouns"] = name_val
        e["personal"]["preferredPronouns"] = personal_val
        steps = by_dest(run("isolved", "employee", doc, company_id="4501"))
        return steps.get("preferred_pronouns")

    both = pronoun_step("she/her", "they/them")  # conflict: the first block wins
    assert paths(both) == ["$.results[0].nameAddress.preferredPronouns"]
    only_second = pronoun_step(None, "they/them")
    assert paths(only_second) == ["$.results[0].personal.preferredPronouns"]
    assert only_second["value"] == "they/them"
    assert pronoun_step(None, None) is None
    assert pronoun_step("", "  ") is None


def test_missing_null_and_empty_fields_have_no_trace_and_do_not_crash():
    doc = PRISM()
    client = doc["employee"][0]["client"]
    client["jobCode"] = None
    client["manager"] = ""
    del client["homeDepartment"]
    del doc["employee"][0]["compensation"]["annualPay"]
    steps = by_dest(run("prismhr", "employee", doc, company_id="ACME"))
    for dest in ("job_code", "manager_id", "department_code", "compensation.annual_salary"):
        assert dest not in steps
    assert "hire_date" in steps


def test_zero_is_a_real_value_with_a_trace():
    steps = by_dest(run("prismhr", "employee", PRISM(), company_id="ACME"))
    assert steps["compensation.hourly_rate"]["value"] == "0"
    assert steps["compensation.hourly_rate"]["sources"][0]["raw"] in (0, "0")


def test_invalid_values_are_not_traced_because_they_never_reached_the_record():
    doc = PRISM()
    doc["employee"][0]["client"]["lastHireDate"] = "not-a-date"
    r = run("prismhr", "employee", doc, company_id="ACME")
    assert "hire_date" not in by_dest(r)
    assert any(i.code == "invalid_date" for i in r.issues)


def test_enum_conversion_names_the_table_and_keeps_the_raw_code():
    steps = by_dest(run("prismhr", "employee", PRISM(), company_id="ACME"))
    enum = steps["employment_status"]
    assert (enum["op"], enum["table"], enum["value"]) == ("enum", "EMPLOYMENT_STATUS", "active")
    assert paths(enum) == ["$.employee[0].client.statusClass"]
    assert steps["compensation.pay_type"]["table"] == "PAY_METHOD"


def test_undocumented_enum_code_has_no_enum_step_but_keeps_the_raw_value():
    doc = PRISM()
    doc["employee"][0]["client"]["statusClass"] = "ZZ"
    r = run("prismhr", "employee", doc, company_id="ACME")
    steps = by_dest(r)
    assert "employment_status" not in steps
    assert steps["employment_status_raw"]["value"] == "ACT"
    assert any(i.code == "unmapped_code" for i in r.issues)


def test_derived_value_names_its_source_and_the_rule():
    steps = by_dest(run("ukg-ready", "employee", UKG(), company_id="CID1"))
    d = steps["compensation.standard_hours_per_day"]
    assert d["op"] == "derive" and paths(d) == ["$.pay_info.standard_work_day"]
    assert "3,600,000" in d["note"]


def test_ukg_amount_is_routed_by_period_and_only_the_used_field_is_listed():
    doc = UKG()
    rows = doc["compensation_history"]["compensations"]
    latest = max(rows, key=lambda r: r["effective_from"])
    latest["amount_period"], latest["amount"], latest["hourly_pay"] = "YEAR", "90000.00", None
    steps = by_dest(run("ukg-ready", "employee", doc, company_id="CID1"))
    assert paths(steps["compensation.annual_salary"])[0].endswith(".amount")
    assert steps["compensation.annual_salary"]["note"] == "amount_period is YEAR"
    assert "compensation.hourly_rate" not in steps


def test_combined_values_list_every_contributing_source():
    c = Collector(Provider.PRISMHR, trace=True)
    from normalization.canonical.models import Employee

    s = c.src({"a": "Ann", "b": "Lee", "unused": "X"}, "$.e")
    s.text("unused")  # read but not used: must not appear
    c.trace_combine("first_name", [(s, "a"), (s, "b")], "combine", "first + last")
    emp = c.build(
        Employee,
        s,
        "x",
        provider=Provider.PRISMHR,
        tenant_id="t",
        company_id="c",
        source_id="1",
        first_name="Ann Lee",
    )
    step = c.traces_for([emp])[0][0]
    assert [x["path"] for x in step["sources"]] == ["$.e.a", "$.e.b"]
    assert step["value"] == "Ann Lee"


def test_nested_employee_records_keep_their_own_source_paths():
    r = run("prismhr", "employee", PRISM(), company_id="ACME")
    assert len(r.traces) == 2
    assert paths(by_dest(r, 0)["first_name"]) == ["$.employee[0].firstName"]
    assert paths(by_dest(r, 1)["first_name"]) == ["$.employee[1].firstName"]
    for i in (0, 1):
        assert all(f"employee[{i}]" in p for t in r.traces[i] for p in paths(t))


def test_sensitive_values_never_appear_in_any_trace():
    for case in CASES:
        payload = load(f"{case.folder}/{case.file}")
        r = run(case.provider, case.entity, payload, **case.context)
        text = json.dumps(r.traces, default=str)
        for secret in SECRETS:
            assert secret not in text, (case.id, secret)


def test_ssn_trace_is_withheld_but_last4_is_shown():
    steps = by_dest(run("prismhr", "employee", PRISM(), company_id="ACME"))
    s = steps["ssn_last4"]
    assert s["op"] == "redact" and s["value"] == "6789"
    assert [x["raw"] for x in s["sources"]] == ["[withheld]"]


def test_unrelated_sensitive_extras_are_not_in_traces():
    doc = PRISM()
    doc["employee"][0]["client"]["ssnUnmasked"] = "955-66-7777"
    text = json.dumps(run("prismhr", "employee", doc, company_id="ACME").traces)
    assert "955-66-7777" not in text


def test_every_traced_destination_exists_in_the_record():
    for provider, payload, ctx in (
        ("prismhr", PRISM(), {"company_id": "ACME"}),
        ("isolved", ISOLVED(), {"company_id": "4501"}),
        ("ukg-ready", UKG(), {"company_id": "CID1"}),
    ):
        r = run(provider, "employee", payload, **ctx)
        for rec, steps in zip(r.records, r.traces, strict=True):
            dump = rec.model_dump(mode="json")
            for t in steps:
                node = dump
                for part in t["dest"].split("."):
                    assert part in node, (provider, t["dest"])
                    node = node[part]
                assert node not in (None, "", [], {})


def test_http_convert_returns_traces_only_when_asked():
    from fastapi.testclient import TestClient

    from normalization.api.app import create_app

    client = TestClient(create_app())
    body = {
        "files": [{"name": "e.json", "content": copy.deepcopy(UKG())}],
        "context": {"company_id": "CID1"},
    }
    off = client.post("/convert/ukg-ready", content=json.dumps(body, default=str)).json()
    on = client.post(
        "/convert/ukg-ready", content=json.dumps({**body, "trace": True}, default=str)
    ).json()
    assert off["results"][0]["traces"] == []
    assert on["results"][0]["traces"] and "social_security" in json.dumps(on)
    assert "900-44-3333" not in json.dumps(on)
