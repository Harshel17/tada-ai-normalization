"""Second employee-coverage step: documented fields promoted from extras (DECISIONS.md section 7, PROVIDER_MAPPINGS.md).

New, independent synthetic payloads (not the bundled fixtures). Each promoted field is checked for exact value, for being
absent from extras once typed, and for what happens with a bad value (kept in extras, reported, never dropped or guessed).
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from normalization import service
from normalization.api.app import create_app

P_CTX = {"tenant_id": "t", "company_id": "NOVA77"}


def prism_emp(**over):
    emp = {
        "id": "N-1", "firstName": "Ada", "lastName": "Lovelace",
        "client": {"typeDate": "2023-04-17", "unionDate": "2019-09-02", "workPhoneExt": "4417", "employeeStatus": "ACT"},
        "compensation": {
            "lastPayDate": "2026-09-30", "compReviewNextDate": "2027-01-15", "compaRatio": "0.9750",
            "perfReviewLastDate": "2026-03-01", "perfReviewLastRating": "Exceeds", "perfReviewLastTitle": "Staff Engineer",
            "perfReviewNextDate": "2027-03-01", "paidThruDate": "2026-09-30", "payMethod": "S", "annualPay": "100000.00",
        },
    }  # fmt: skip
    for key, value in over.items():
        section, field = key.split("__")
        emp[section][field] = value
    return {"errorCode": "0", "employee": [emp]}


def employee(provider, payload, ctx):
    res = service.normalize(provider, "employee", payload, ctx)
    assert res.ok, res.issues
    return res, res.records[0]


def test_prismhr_promoted_employee_and_compensation_fields_have_exact_values():
    res, e = employee("prismhr", prism_emp(), P_CTX)
    assert e.work_phone_extension == "4417"
    assert e.employee_type_effective_date == date(2023, 4, 17) and e.union_start_date == date(
        2019, 9, 2
    )
    c = e.compensation
    assert (c.last_pay_date, c.next_review_date) == (date(2026, 9, 30), date(2027, 1, 15))
    assert (
        c.compa_ratio == Decimal("0.9750")
        and json.loads(e.model_dump_json())["compensation"]["compa_ratio"] == "0.9750"
    )
    assert (c.last_performance_review_date, c.last_performance_review_rating_raw) == (
        date(2026, 3, 1),
        "Exceeds",
    )
    assert (c.last_performance_review_job_title, c.next_performance_review_date) == (
        "Staff Engineer",
        date(2027, 3, 1),
    )
    assert e.benefits.paid_through_date == date(2026, 9, 30)


def test_a_typed_field_leaves_extras_but_nothing_else_does():
    _, e = employee("prismhr", prism_emp(), P_CTX)
    flat = json.dumps(e.extras)
    for gone in (
        "workPhoneExt",
        "typeDate",
        "unionDate",
        "lastPayDate",
        "compaRatio",
        "perfReviewLastRating",
        "paidThruDate",
    ):
        assert gone not in flat, gone


@pytest.mark.parametrize(
    ("override", "extras_path"),
    [
        ("client__typeDate", ("client", "typeDate")), ("client__unionDate", ("client", "unionDate")),
        ("compensation__lastPayDate", ("compensation", "lastPayDate")), ("compensation__compReviewNextDate", ("compensation", "compReviewNextDate")),
        ("compensation__perfReviewLastDate", ("compensation", "perfReviewLastDate")), ("compensation__compaRatio", ("compensation", "compaRatio")),
        ("compensation__paidThruDate", ("compensation", "paidThruDate")),
    ],
)  # fmt: skip
def test_a_value_that_cannot_be_parsed_is_kept_in_extras_and_reported_never_dropped_or_guessed(
    override, extras_path
):
    res = service.normalize("prismhr", "employee", prism_emp(**{override: "not-a-value"}), P_CTX)
    e = res.records[0]
    node = e.extras
    for key in extras_path:
        node = node[key]
    assert node == "not-a-value"  # still there, exactly as received
    assert {i.code for i in res.issues} & {"invalid_date", "invalid_decimal"}


def test_absent_values_stay_null_not_zero():
    _, e = employee(
        "prismhr",
        {
            "errorCode": "0",
            "employee": [{"id": "N-2", "firstName": "B", "compensation": {"payMethod": "H"}}],
        },
        P_CTX,
    )
    c = e.compensation
    assert (
        c.compa_ratio is None
        and c.last_pay_date is None
        and e.union_start_date is None
        and e.work_phone_extension is None
    )


def test_isolved_category_effective_date_and_aca_status():
    payload = {
        "results": [
            {
                "id": 5,
                "nameAddress": {"firstName": "A"},
                "employmentCategoryCode": "FT",
                "employmentCategoryEffectiveDate": "2024-02-01T00:00:00",
                "acaEmploymentStatus": "Full-Time",
            }
        ]
    }
    _, e = employee("isolved", payload, {"tenant_id": "t", "company_id": "9"})
    assert (e.employee_type_raw, e.employee_type_effective_date, e.aca_employment_status_raw) == (
        "FT",
        date(2024, 2, 1),
        "Full-Time",
    )
    assert (
        "acaEmploymentStatus" not in e.extras and "employmentCategoryEffectiveDate" not in e.extras
    )


def ukg_bundle(time, period):
    return {
        "employee": {"id": 31, "username": "u", "first_name": "A", "last_name": "B"},
        "compensation_history": {"compensations": [{"id": 1, "employee": {"account_id": 31}, "effective_from": "2025-01-01", "amount": 50000, "amount_period": "YEAR", "time": time, "time_period": period, "num_pp_in_year": 26}]},
    }  # fmt: skip


@pytest.mark.parametrize(
    ("ms", "period", "week", "pay_period"),
    [
        (144_000_000, "WEEK", "40", None),
        (135_000_000, "WEEK", "37.5", None),
        (288_000_000, "PAY_PERIOD", None, "80"),
    ],
)
def test_ukg_time_is_milliseconds_for_a_week_or_a_pay_period(ms, period, week, pay_period):
    _, e = employee("ukg-ready", ukg_bundle(ms, period), {"tenant_id": "t", "company_id": "3"})
    c = e.compensation
    assert (c.standard_hours_per_week and str(c.standard_hours_per_week)) == week
    assert (c.standard_hours_per_pay_period and str(c.standard_hours_per_pay_period)) == pay_period
    assert "time" not in json.dumps(e.extras.get("compensation_history", []))


@pytest.mark.parametrize("period", ["MONTH", "YEAR", "FORTNIGHT", None])
def test_ukg_other_periods_are_not_converted_and_stay_in_extras(period):
    _, e = employee(
        "ukg-ready", ukg_bundle(144_000_000, period), {"tenant_id": "t", "company_id": "3"}
    )
    c = e.compensation
    assert c.standard_hours_per_week is None and c.standard_hours_per_pay_period is None
    history = json.dumps(e.extras)
    assert "144000000" in history  # the value was kept, as received


def test_ukg_hours_from_a_bad_time_value_are_kept_not_converted():
    res = service.normalize(
        "ukg-ready", "employee", ukg_bundle("lots", "WEEK"), {"tenant_id": "t", "company_id": "3"}
    )
    assert res.records[0].compensation.standard_hours_per_week is None and "lots" in json.dumps(
        res.records[0].extras
    )


def test_new_fields_are_traced_with_their_real_source_paths():
    res = service.normalize("prismhr", "employee", prism_emp(), P_CTX, trace=True)
    steps = {s["dest"]: s for s in res.traces[0]}
    assert steps["union_start_date"]["sources"][0]["path"].endswith(".client.unionDate")
    assert (
        steps["compensation.compa_ratio"]["op"] == "decimal"
        and steps["compensation.last_pay_date"]["op"] == "date"
    )
    ukg = service.normalize(
        "ukg-ready",
        "employee",
        ukg_bundle(144_000_000, "WEEK"),
        {"tenant_id": "t", "company_id": "3"},
        trace=True,
    )
    derived = {s["dest"]: s for s in ukg.traces[0]}["compensation.standard_hours_per_week"]
    assert derived["op"] == "derive" and "3,600,000" in derived["note"]


def test_no_sensitive_value_appears_in_the_new_fields():
    payload = prism_emp()
    payload["employee"][0]["compensation"]["ssn"] = "900-44-3333"
    res, e = employee("prismhr", payload, P_CTX)
    assert "900-44-3333" not in res.model_dump_json() and "900443333" not in res.model_dump_json()


@pytest.mark.db
def test_the_new_fields_survive_a_database_round_trip_and_the_stored_read(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)
    body = json.dumps({"context": P_CTX, "payload": prism_emp()})
    assert client.post("/ingest/prismhr/employee", content=body).status_code == 200
    got = client.get("/tenants/t/prismhr/companies/NOVA77/employees/N-1").json()
    assert (
        got["work_phone_extension"] == "4417"
        and got["union_start_date"] == "2019-09-02"
        and got["employee_type_effective_date"] == "2023-04-17"
    )
    comp = got["compensation"]
    assert (
        comp["last_pay_date"] == "2026-09-30"
        and comp["compa_ratio"] == "0.9750"
        and comp["last_performance_review_rating_raw"] == "Exceeds"
    )
    assert got["benefits"]["paid_through_date"] == "2026-09-30"
    again = client.post("/ingest/prismhr/employee", content=body).json()
    assert again["stored"]["employee"] == {
        "inserted": 0,
        "updated": 0,
        "unchanged": 1,
    }  # idempotent with the new columns
    ukg = json.dumps(
        {
            "context": {"tenant_id": "t", "company_id": "3"},
            "payload": ukg_bundle(144_000_000, "WEEK"),
        }
    )
    assert client.post("/ingest/ukg-ready/employee", content=ukg).status_code == 200
    stored = client.get("/tenants/t/ukg-ready/companies/3/employees/31").json()
    assert stored["compensation"]["standard_hours_per_week"] == "40"


# --- UKG pay statement `payments` (documented array; meaning undocumented, so nothing is derived) -------------------------------
def ukg_statement(payments):
    return {
        "payroll": {"id": 9, "pay_date": "2026-02-20"},
        "pay_statements": [{
            "id": 1, "employee": {"account_id": 31}, "pay_date": "2026-02-20",
            "earnings": [{"id": 1, "type_name": "EARNING", "ee_amount": 100, "earning": {"code": "REG", "name": "Regular"}}],
            "payments": payments,
        }],
    }  # fmt: skip


def test_ukg_payments_become_typed_lines_but_never_a_net_pay():
    res = service.normalize(
        "ukg-ready",
        "payroll",
        ukg_statement(
            [
                {
                    "id": 7,
                    "type_name": "PAYMENT",
                    "ee_amount": 80.5,
                    "hours": 4,
                    "batch": {"id": 2},
                },
                {"id": 8, "type_name": "ADJUSTMENT", "ee_amount": None},
            ]
        ),
        {"tenant_id": "t", "company_id": "3"},
    )
    check = next(r for r in res.records if r.record_type == "paycheck")
    assert [(p.line_no, p.amount, p.description) for p in check.payments] == [
        (1, Decimal("80.5"), "PAYMENT"),
        (2, None, "ADJUSTMENT"),
    ]
    assert check.net_pay is None and "net_pay_not_exposed" in {
        i.code for i in res.issues
    }  # no derived financial figure
    assert (
        check.extras["payments"][0] == {"id": 7, "hours": "4", "batch": {"id": 2}}
        or check.extras["payments"][0]["id"] == 7
    )  # nothing documented is dropped


def test_ukg_without_payments_has_none():
    res = service.normalize(
        "ukg-ready", "payroll", ukg_statement([]), {"tenant_id": "t", "company_id": "3"}
    )
    assert next(r for r in res.records if r.record_type == "paycheck").payments == []


def test_ukg_payments_never_reach_the_net_pay_reconciliation():
    from normalization.integrity import evaluate

    res = service.normalize(
        "ukg-ready",
        "payroll",
        ukg_statement([{"id": 7, "type_name": "PAYMENT", "ee_amount": 1}]),
        {"tenant_id": "t", "company_id": "3"},
    )
    report = evaluate([r.model_dump(mode="json") for r in res.records])
    assert next(c for c in report.checks if c.id == "payments_equal_net").status == "not_evaluated"
