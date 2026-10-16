"""Duplicate record identities inside one payload (DECISIONS.md section 7).

Conflicting repeats are an ERROR before anything is stored; identical repeats are a WARNING and are written once;
the same ids in different tenants, companies or under different employees are NOT duplicates; separate requests are
ordinary idempotent updates.
"""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from normalization import cli, service
from normalization.api.app import create_app
from normalization.duplicates import duplicate_issues, identity

CTX = {"tenant_id": "t", "company_id": "C1"}


def emp(i, first="A"):
    return {"id": i, "firstName": first, "lastName": "X"}


def prism(*employees):
    return {"errorCode": "0", "employee": list(employees)}


def codes(res):
    return sorted((i.severity.value, i.code) for i in res.issues if i.code.startswith("duplicate_"))


# --- every entity type ---------------------------------------------------------------------------------------------------
def test_conflicting_employees_are_an_error_and_name_the_id_not_the_values():
    res = service.normalize("prismhr", "employee", prism(emp("E1", "Alice"), emp("E1", "Bob")), CTX)
    assert not res.ok and codes(res) == [("error", "duplicate_record_id")]
    msg = next(i.message for i in res.issues if i.code == "duplicate_record_id")
    assert "'E1'" in msg and "Nothing was saved" in msg and "Alice" not in msg and "Bob" not in msg


def test_identical_employees_are_only_a_warning_and_stay_in_the_result():
    res = service.normalize("prismhr", "employee", prism(emp("E1"), emp("E1")), CTX)
    assert (
        res.ok
        and codes(res) == [("warning", "duplicate_record_identical")]
        and len(res.records) == 2
    )


def test_a_third_copy_is_still_one_finding_per_identity():
    res = service.normalize(
        "prismhr", "employee", prism(emp("E1", "A"), emp("E1", "B"), emp("E1", "C"), emp("E2")), CTX
    )
    assert (
        codes(res) == [("error", "duplicate_record_id")]
        and "3 employee records" in res.issues[-1].message
    )


def test_conflicting_direct_deposits_with_the_same_id_under_one_employee():
    def row(amount):
        return {
            "id": 301,
            "accountNumber": "4455667788",
            "routingNumber": "011000015",
            "accountType": "Checking",
            "amount": amount,
        }

    res = service.normalize(
        "isolved",
        "direct_deposit",
        [row(100), row(50)],
        {**CTX, "company_id": "4501", "employee_id": "90001"},
    )
    assert not res.ok and codes(res) == [("error", "duplicate_record_id")]
    assert "4455667788" not in json.dumps(
        [i.model_dump(mode="json") for i in res.issues]
    )  # no account data in the message


def test_prismhr_already_numbers_two_identical_ach_vouchers_so_they_are_not_confused():
    v = {
        "transitNum": "122105155",
        "accountNum": "4455667788",
        "accountType": "C",
        "method": "F",
        "amount": 100,
        "status": "D",
    }
    res = service.normalize(
        "prismhr",
        "direct_deposit",
        prism({**emp("E1"), "directDeposit": {"achVoucher": [v, {**v, "amount": 50}]}}),
        CTX,
    )
    assert res.ok and [r.source_id[-2:] for r in res.records] == ["F-", "#2"] and codes(res) == []


def test_the_same_deposit_id_under_two_employees_is_not_a_duplicate():
    v = {
        "transitNum": "122105155",
        "accountNum": "4455667788",
        "accountType": "C",
        "method": "F",
        "amount": 100,
        "status": "D",
    }
    payload = prism(
        {**emp("E1"), "directDeposit": {"achVoucher": [v]}},
        {**emp("E2"), "directDeposit": {"achVoucher": [v]}},
    )
    res = service.normalize("prismhr", "direct_deposit", payload, CTX)
    assert res.ok and len(res.records) == 2 and codes(res) == []


def test_conflicting_deductions():
    payload = [
        {
            "id": 7,
            "employeeId": 1,
            "payItemCode": "MED",
            "calcParams": [],
            "startDate": "2025-01-01",
        },
        {
            "id": 7,
            "employeeId": 1,
            "payItemCode": "DEN",
            "calcParams": [],
            "startDate": "2025-01-01",
        },
    ]
    res = service.normalize("isolved", "deduction", payload, {**CTX, "company_id": "4501"})
    assert codes(res) == [("error", "duplicate_record_id")]


def test_the_same_deduction_id_for_two_employees_is_legitimate():
    payload = [
        {"id": 7, "employeeId": 1, "payItemCode": "MED", "calcParams": []},
        {"id": 7, "employeeId": 2, "payItemCode": "MED", "calcParams": []},
    ]
    res = service.normalize("isolved", "deduction", payload, {**CTX, "company_id": "4501"})
    assert res.ok and codes(res) == [] and len(res.records) == 2


def test_conflicting_paychecks_and_identical_paychecks():
    def v(net):
        return {
            "voucherId": "V1",
            "batchId": "B1",
            "employeeId": "E1",
            "payDate": "2025-01-01",
            "netPay": net,
            "totalEarnings": 10,
        }

    same = service.normalize(
        "prismhr", "payroll", {"errorCode": "0", "payrollVouchers": [v(5), v(5)]}, CTX
    )
    assert same.ok and codes(same) == [("warning", "duplicate_record_identical")]
    differ = service.normalize(
        "prismhr", "payroll", {"errorCode": "0", "payrollVouchers": [v(5), v(6)]}, CTX
    )
    assert codes(differ) == [("error", "duplicate_record_id")]


def test_conflicting_isolved_checks_in_one_bundle():
    def chk(gross):
        return {"id": 5, "employeeId": 1, "grossPay": gross, "netPay": 1, "checkDate": "2025-01-01"}

    payload = {"payroll_run": {"id": 9, "payDate": "2025-01-01"}, "checks": [chk(10), chk(11)]}
    assert codes(
        service.normalize("isolved", "payroll", payload, {**CTX, "company_id": "4501"})
    ) == [("error", "duplicate_record_id")]


def test_two_runs_derived_from_one_batch_never_collide():
    vs = [
        {
            "voucherId": f"V{i}",
            "batchId": "B1",
            "employeeId": f"E{i}",
            "payDate": "2025-01-01",
            "netPay": 1,
        }
        for i in (1, 2, 3)
    ]
    res = service.normalize("prismhr", "payroll", {"errorCode": "0", "payrollVouchers": vs}, CTX)
    assert (
        res.ok
        and [r.record_type for r in res.records].count("payroll_run") == 1
        and codes(res) == []
    )


def test_ukg_employees_in_one_list():
    e = {"id": 1, "username": "a", "first_name": "A", "last_name": "X"}
    res = service.normalize(
        "ukg-ready",
        "employee",
        {"employees": [e, {**e, "first_name": "B"}]},
        {**CTX, "company_id": "3"},
    )
    assert codes(res) == [("error", "duplicate_record_id")]


# --- scope: tenant and company are part of the identity -----------------------------------------------------------------------
def test_the_same_local_id_in_different_companies_or_tenants_is_not_a_duplicate():
    a = service.normalize("prismhr", "employee", prism(emp("E1", "A")), CTX).records
    b = service.normalize(
        "prismhr", "employee", prism(emp("E1", "B")), {**CTX, "company_id": "C2"}
    ).records
    c = service.normalize(
        "prismhr", "employee", prism(emp("E1", "C")), {**CTX, "tenant_id": "other"}
    ).records
    assert len({identity(r) for r in (*a, *b, *c)}) == 3
    assert duplicate_issues([*a, *b, *c]) == []


def test_a_company_has_no_local_id_so_two_company_records_for_one_scope_conflict():
    one = service.normalize(
        "prismhr",
        "company",
        {"errorCode": "0", "clientMaster": {"id": "C1", "clientName": "A"}},
        CTX,
    ).records
    two = service.normalize(
        "prismhr",
        "company",
        {"errorCode": "0", "clientMaster": {"id": "C1", "clientName": "B"}},
        CTX,
    ).records
    assert [i.code for i in duplicate_issues([*one, *two])] == ["duplicate_record_id"]


def test_legitimate_data_in_the_bundled_fixtures_has_no_duplicates():
    from decimal import Decimal
    from pathlib import Path

    fix = Path(__file__).parent / "fixtures"
    for provider, folder, name, entity, ctx in [
        ("prismhr", "prismhr", "employee_get_employee.json", "employee", {"company_id": "ACME"}),
        (
            "prismhr",
            "prismhr",
            "employee_get_employee.json",
            "direct_deposit",
            {"company_id": "ACME"},
        ),
        ("prismhr", "prismhr", "payroll_vouchers.json", "payroll", {"company_id": "ACME"}),
        ("isolved", "isolved", "employees.json", "direct_deposit", {"company_id": "4501"}),
        ("isolved", "isolved", "payroll.json", "payroll", {"company_id": "4501"}),
        ("ukg-ready", "ukg_ready", "payroll.json", "payroll", {"company_id": "CID1"}),
    ]:
        res = service.normalize(
            provider,
            entity,
            json.loads((fix / folder / name).read_text(), parse_float=Decimal),
            {"tenant_id": "t", **ctx},
        )
        assert codes(res) == [], (provider, name, entity)


# --- one policy on every path ------------------------------------------------------------------------------------------------------
BAD = prism(emp("E1", "Alice"), emp("E1", "Bob"))


def test_the_api_rejects_a_conflicting_payload_on_normalize_and_ingest_without_touching_a_database():
    client = TestClient(create_app(), raise_server_exceptions=False)
    body = json.dumps({"context": CTX, "payload": BAD})
    r = client.post("/normalize/prismhr/employee", content=body)
    assert (
        r.status_code == 422
        and [i["code"] for i in r.json()["issues"]] == ["duplicate_record_id"]
        and r.json()["ok"] is False
    )


def test_the_cli_fails_with_the_same_error(tmp_path):
    src, out = tmp_path / "in.json", tmp_path / "out.json"
    src.write_text(json.dumps(BAD))
    code = cli.main(
        [
            "normalize",
            "--provider",
            "prismhr",
            "--entity",
            "employee",
            "--input",
            str(src),
            "--tenant-id",
            "t",
            "--company-id",
            "C1",
            "--output",
            str(out),
        ]
    )
    assert code == cli.EXIT_DATA_ERRORS
    assert json.loads(out.read_text())["ok"] is False


def test_convert_marks_the_section_as_failed_for_the_browser_path():
    from normalization.canonical.enums import Provider
    from normalization.convert import FileIn, convert_files

    out = convert_files(
        Provider.PRISMHR,
        [FileIn(name="e.json", content=BAD, entity="employee")],
        {"tenant_id": "t", "company_id": "C1"},
        integrity=True,
    )
    unit = next(u for u in out.results if u.entity == "employee")
    assert unit.status == "failed" and any(i["code"] == "duplicate_record_id" for i in unit.issues)


# --- persistence ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.db
def test_a_rejected_batch_writes_nothing_and_keeps_what_was_already_stored(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)

    def ingest(payload, ctx=CTX):
        return client.post(
            "/ingest/prismhr/employee", content=json.dumps({"context": ctx, "payload": payload})
        )

    assert ingest(prism(emp("E0", "Existing"))).status_code == 200
    bad = ingest(prism(emp("E5", "Keep?"), emp("E1", "Alice"), emp("E1", "Bob")))
    assert (
        bad.status_code == 422 and bad.json()["stored"] == {} and "duplicate_record_id" in bad.text
    )
    with db.connect() as conn:
        names = sorted(r[0] for r in conn.execute(text("select first_name from employees")))
    assert names == ["Existing"]  # not even the innocent E5 was written: all or nothing


@pytest.mark.db
def test_an_identical_repeat_is_written_once_and_re_ingesting_is_still_idempotent(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)
    body = json.dumps({"context": CTX, "payload": prism(emp("E1"), emp("E1"), emp("E2"))})
    first = client.post("/ingest/prismhr/employee", content=body).json()
    assert first["stored"]["employee"] == {"inserted": 2, "updated": 0, "unchanged": 1}
    assert [i["code"] for i in first["issues"]] == ["duplicate_record_identical"]
    again = client.post("/ingest/prismhr/employee", content=body).json()
    assert again["stored"]["employee"] == {"inserted": 0, "updated": 0, "unchanged": 3}
    with db.connect() as conn:
        row = conn.execute(text("select count(*), max(version) from employees")).one()
    assert tuple(row) == (2, 1)  # two rows, never versioned by the repeat


@pytest.mark.db
def test_the_same_ids_in_other_tenants_and_companies_store_side_by_side(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)
    for tenant, company in (("t", "C1"), ("t", "C2"), ("u", "C1")):
        r = client.post(
            "/ingest/prismhr/employee",
            content=json.dumps(
                {
                    "context": {"tenant_id": tenant, "company_id": company},
                    "payload": prism(emp("E1", tenant + company)),
                }
            ),
        )
        assert r.status_code == 200, r.text
    with db.connect() as conn:
        assert (
            conn.execute(text("select count(*) from employees where source_id = 'E1'")).scalar()
            == 3
        )
