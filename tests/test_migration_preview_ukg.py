"""UKG Ready migration preview (dry run): mapping, readiness, safety, determinism, API.

Source data is independent of the fixtures used elsewhere: canonical employees are produced by the real adapters from
NEW synthetic payloads, or built directly where an edge needs it.
"""

import copy
import json
import socket
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from normalization import service
from normalization.api.app import create_app
from normalization.canonical.models import Address, Employee
from normalization.migration import MigrationRequest, preview_ukg_ready, schema_check

DEST = {"company_id": "4410"}
SSN, ACCT = "907-31-4417", "4455667788"


def employee(**kw) -> Employee:
    base = {"provider": "prismhr", "tenant_id": "srcT", "company_id": "NOVA77", "source_id": "N-3001",
            "first_name": "Priya", "last_name": "Nair"}  # fmt: skip
    return Employee(**{**base, **kw})


def preview(emp=None, op=None, dest=None, **kw):
    return preview_ukg_ready(
        MigrationRequest(
            employee=emp or employee(**kw), destination=dest or DEST, operator=op or {}
        )
    )


def fields_by_dest(p):
    return {f.destination: f for f in p.fields}


def issue_codes(p, severity=None):
    return {i.code for i in p.issues if severity is None or i.severity == severity}


FULL = dict(  # noqa: C408
    middle_name="S", preferred_name="Pri", name_suffix="Jr", name_prefix="Ms", email="p@example.test",
    secondary_email="p2@example.test", mobile_phone="5205550101", home_phone="5205550102", work_phone="5205550103",
    preferred_phone="mobile", birth_date=date(1991, 3, 22), hire_date=date(2022, 5, 2), start_date=date(2022, 5, 9),
    rehire_date=date(2024, 1, 3), peo_start_date=date(2022, 5, 2), seniority_date=date(2021, 1, 4),
    address=Address(line1="14 Mesa Lane", line2="Unit 2", city="Tucson", state="AZ", postal_code="85719",
                    country="USA", house_number="14"),
)  # fmt: skip


# --- mapping against the documented schema ----------------------------------------------------------------------
def test_a_full_employee_maps_every_documented_field_and_passes_the_documented_schema():
    p = preview(op={"username": "pnair"}, **FULL)
    assert p.readiness == "READY", p.issues
    body = p.request_body
    assert (
        body["username"] == "pnair"
        and body["first_name"] == "Priya"
        and body["last_name"] == "Nair"
    )
    assert (body["middle_name"], body["nickname"], body["suffix"], body["salutation"]) == (
        "S",
        "Pri",
        "Jr",
        "Ms",
    )
    assert (body["primary_email"], body["secondary_email"]) == ("p@example.test", "p2@example.test")
    assert body["phones"] == {"cell_phone": "5205550101", "home_phone": "5205550102", "work_phone": "5205550103",
                              "preferred_phone": "CELL"}  # fmt: skip
    assert body["dates"] == {"hired": "2022-05-02", "started": "2022-05-09", "birthday": "1991-03-22",
                             "re_hired": "2024-01-03", "peo_hired": "2022-05-02", "seniority": "2021-01-04"}  # fmt: skip
    assert body["address"] == {"country": "USA", "address_line_1": "14 Mesa Lane", "address_line_2": "Unit 2",
                               "number": "14", "city": "Tucson", "state_code": "AZ", "zip": "85719"}  # fmt: skip
    schema = schema_check.employee_create_schema()
    assert (
        schema_check.validate(body, schema, schema) == []
    )  # the contract itself, not our opinion of it


def test_the_contract_reference_and_endpoint_use_the_destination_company_not_the_source_company():
    p = preview(op={"username": "u"}, dest={"company_id": "99887"})
    assert p.contract.method == "POST" and p.contract.endpoint == "/v2/companies/99887/employees"
    assert "NOVA77" not in p.contract.endpoint and p.destination_company_id == "99887"
    assert p.source == {
        "provider": "prismhr",
        "tenant_id": "srcT",
        "company_id": "NOVA77",
        "source_id": "N-3001",
    }
    assert (
        "reference-schemas.json" in p.contract.schema_file
        and "not a complete endpoint contract" in p.contract.caveat
    )


def test_phone_codes_follow_the_documented_enum_and_unknown_ones_are_flagged():
    for canonical, code in (
        ("home", "HOME"),
        ("mobile", "CELL"),
        ("work", "WORK"),
        ("none", "NONE"),
    ):
        p = preview(op={"username": "u"}, mobile_phone="1", preferred_phone=canonical)
        assert p.request_body["phones"]["preferred_phone"] == code
    p = preview(op={"username": "u"}, mobile_phone="1", preferred_phone="carrier-pigeon")
    assert "preferred_phone" not in p.request_body["phones"]
    assert "unknown_code" in issue_codes(p, "review") and p.readiness == "NEEDS_REVIEW"


def test_the_preferred_phone_alone_is_not_sent_without_a_phone_number():
    p = preview(op={"username": "u"}, preferred_phone="mobile")
    assert "phones" not in p.request_body


# --- required fields and readiness --------------------------------------------------------------------------------
def test_a_missing_username_is_blocked_even_when_everything_else_is_present():
    p = preview(**FULL)
    assert p.readiness == "BLOCKED" and "missing_username" in issue_codes(p, "blocking")
    assert not {r.field: r.satisfied for r in p.requirements}["username"]
    assert "username" not in p.request_body
    assert fields_by_dest(p)["username"].status == "missing_required"


def test_a_source_username_is_not_assumed_valid_in_the_destination():
    p = preview(username="srcuser")
    assert p.readiness == "BLOCKED" and "username" not in p.request_body
    assert "not assumed" in next(i.message for i in p.issues if i.code == "missing_username")
    assert "srcuser" not in json.dumps(p.model_dump(mode="json"))


@pytest.mark.parametrize("name", ["first_name", "last_name"])
def test_missing_required_names_block(name):
    p = preview(op={"username": "u"}, **{name: None})
    assert p.readiness == "BLOCKED" and f"missing_{name}" in issue_codes(p, "blocking")
    assert name not in p.request_body


def test_a_blank_operator_username_counts_as_missing():
    for blank in ("", "   "):
        assert preview(op={"username": blank}).readiness == "BLOCKED"


def test_an_operator_username_with_control_characters_is_refused():
    p = preview(op={"username": "bad\nname"})
    assert (
        p.readiness == "BLOCKED"
        and "username" not in p.request_body
        and "unsafe_value" in issue_codes(p)
    )


def test_ready_requires_nothing_more_than_the_three_documented_required_fields():
    p = preview(op={"username": "u"})
    assert p.readiness == "READY" and set(p.request_body) == {"username", "first_name", "last_name"}
    assert [r.field for r in p.requirements] == ["username", "first_name", "last_name"] and all(
        r.satisfied for r in p.requirements
    )


def test_the_meaning_of_ready_and_the_dry_run_notice_are_always_stated():
    p = preview(op={"username": "u"})
    assert (
        p.dry_run is True and p.notice.startswith("DRY RUN") and "No data has been sent" in p.notice
    )
    assert "does not mean UKG Ready will accept" in p.readiness_meaning


# --- identifiers ----------------------------------------------------------------------------------------------------
def test_source_ids_are_not_sent_without_explicit_confirmation():
    p = preview(op={"username": "u"}, employee_number="3001")
    assert (
        "employee_id" not in p.request_body
        and "external_id" not in p.request_body
        and "id" not in p.request_body
    )
    assert any(
        n.source == "employee_number" and n.reason == "not_confirmed" for n in p.not_migrated
    )


def test_confirmed_source_ids_go_only_to_the_documented_fields_and_are_marked_operator_supplied():
    p = preview(
        op={
            "username": "u",
            "use_source_employee_number": True,
            "use_source_id_as_external_id": True,
        },
        employee_number="3001",
    )
    assert p.request_body["employee_id"] == "3001" and p.request_body["external_id"] == "N-3001"
    assert "id" not in p.request_body  # the destination assigns its own id
    assert fields_by_dest(p)["employee_id"].status == "operator_supplied"


def test_an_employee_number_longer_than_the_documented_maximum_is_left_out_and_reviewed():
    p = preview(op={"username": "u", "use_source_employee_number": True}, employee_number="N" * 21)
    assert "employee_id" not in p.request_body and p.readiness == "NEEDS_REVIEW"
    assert "schema_violation" in issue_codes(p, "review")


def test_the_destination_ein_is_operator_supplied_and_validated():
    p = preview(op={"username": "u"}, dest={"company_id": "1", "ein_id": 12})
    assert (
        p.request_body["ein"] == {"id": 12}
        and fields_by_dest(p)["ein.id"].status == "operator_supplied"
    )


def test_source_manager_ids_are_never_used_as_destination_account_ids():
    p = preview(op={"username": "u"}, manager_id="N-3000")
    assert "managers" not in p.request_body
    assert any(n.source == "manager_id" and n.reason == "uncertain" for n in p.not_migrated)


# --- addresses, dates, unsafe and unknown values -----------------------------------------------------------------------
def test_an_address_without_a_usable_country_is_left_out_until_the_operator_supplies_one():
    addr = Address(
        line1="1 Main", city="Tucson", state="AZ", postal_code="85701", country="US"
    )  # alpha-2: not converted
    p = preview(op={"username": "u"}, address=addr)
    assert "address" not in p.request_body and p.readiness == "NEEDS_REVIEW"
    assert {"country_not_alpha3", "address_country_required"} <= issue_codes(p, "review")
    ok = preview(op={"username": "u", "address_country": "usa"}, address=addr)
    assert ok.readiness == "READY" and ok.request_body["address"]["country"] == "USA"
    assert fields_by_dest(ok)["address.country"].status == "operator_supplied"


def test_an_operator_country_that_is_not_alpha3_is_rejected_by_the_documented_pattern():
    p = preview(op={"username": "u", "address_country": "USAA"}, address=Address(line1="1 Main"))
    assert "address" not in p.request_body and p.readiness == "NEEDS_REVIEW"


def test_over_long_address_parts_are_left_out_not_truncated():
    p = preview(
        op={"username": "u"}, address=Address(line1="x" * 129, city="Tucson", country="USA")
    )
    assert (
        "address_line_1" not in p.request_body["address"]
        and p.request_body["address"]["city"] == "Tucson"
    )
    assert p.readiness == "NEEDS_REVIEW"


def test_a_mailing_address_sets_the_documented_flag():
    p = preview(op={"username": "u"}, mailing_address=Address(line1="PO Box 1", country="USA"))
    assert (
        p.request_body["use_separate_mailing_address"] is True
        and p.request_body["mailing_address"]["address_line_1"] == "PO Box 1"
    )


def test_dates_outside_the_documented_range_are_left_out_and_reviewed():
    p = preview(op={"username": "u"}, hire_date=date(1850, 1, 1), birth_date=date(2080, 1, 1))
    assert (
        "dates" not in p.request_body
        and p.readiness == "NEEDS_REVIEW"
        and "date_out_of_range" in issue_codes(p)
    )
    edge = preview(op={"username": "u"}, hire_date=date(1900, 12, 31), birth_date=date(2079, 6, 5))
    assert (
        edge.request_body["dates"] == {"hired": "1900-12-31", "birthday": "2079-06-05"}
        and edge.readiness == "READY"
    )


def test_termination_style_dates_are_reported_not_copied():
    p = preview(
        op={"username": "u"}, termination_date=date(2025, 11, 14), retirement_date=date(2026, 1, 1)
    )
    assert "dates" not in p.request_body
    assert {n.source for n in p.not_migrated} >= {"termination_date", "retirement_date"}


def test_unknown_source_codes_need_review_and_are_not_guessed():
    p = preview(op={"username": "u"}, gender_raw="X", employment_status_raw="ZZZ")
    assert p.readiness == "NEEDS_REVIEW" and "unknown_code" in issue_codes(p, "review")
    assert "gender" not in json.dumps(p.request_body)


def test_a_known_gender_is_reported_as_a_separate_documented_call_not_put_in_the_body():
    p = preview(op={"username": "u"}, gender="female", gender_raw="F")
    assert "gender" not in p.request_body
    assert any(n.source == "gender" and n.reason == "separate_endpoint" for n in p.not_migrated)


def test_text_with_control_characters_or_an_ssn_format_is_withheld_and_reviewed():
    p = preview(op={"username": "u"}, middle_name="a\x07b", preferred_name=f"call {SSN}")
    assert "middle_name" not in p.request_body and "nickname" not in p.request_body
    assert {"unsafe_value", "sensitive_value"} <= issue_codes(
        p, "review"
    ) and p.readiness == "NEEDS_REVIEW"
    assert SSN not in json.dumps(p.model_dump(mode="json"))


def test_an_unlisted_personal_email_is_not_chosen_as_primary():
    p = preview(op={"username": "u"}, personal_email="a@example.test", work_email="b@example.test")
    assert "primary_email" not in p.request_body
    assert {n.source for n in p.not_migrated} >= {"personal_email", "work_email"}


# --- nothing sensitive, nothing dropped silently ---------------------------------------------------------------------
def test_sensitive_fields_never_appear_and_are_reported_as_withheld():
    from normalization.canonical.models import Compensation

    emp = employee(
        ssn_last4="4417",
        compensation=Compensation(annual_salary=Decimal("87250.55"), pay_type_raw="S"),
        **{"mobile_phone": "1"},
    )
    p = preview(emp=emp, op={"username": "u"})
    text = json.dumps(p.model_dump(mode="json"))
    assert "4417" not in text and "87250" not in text
    sensitive = {n.source for n in p.not_migrated if n.reason == "sensitive"}
    assert {"ssn_last4", "compensation"} <= sensitive


def test_every_populated_canonical_field_is_either_mapped_or_reported():
    emp = employee(
        job_code="FAB3", flsa_exempt=True, department_code="WELD", extras={"zz": 1}, **FULL
    )
    p = preview(emp=emp, op={"username": "u"})
    mapped_sources = {f.source for f in p.fields if f.source}
    reported = {n.source for n in p.not_migrated}
    populated = {k for k, v in emp.model_dump().items()
                 if v not in (None, "", [], {}, False) and k not in ("provider", "tenant_id", "company_id", "source_id", "record_type", "provenance")}  # fmt: skip
    leaks = {
        k
        for k in populated
        if k not in reported
        and not any(s and (s == k or s.startswith(k + ".")) for s in mapped_sources)
    }
    assert leaks == set(), leaks
    assert {"job_code", "flsa_exempt", "department_code", "extras"} <= reported


# --- purity ----------------------------------------------------------------------------------------------------------
def test_the_preview_is_deterministic_and_does_not_mutate_the_canonical_record():
    emp = employee(**FULL)
    before = emp.model_dump_json()
    req = MigrationRequest(employee=emp, destination=DEST, operator={"username": "u"})
    first, second = preview_ukg_ready(req), preview_ukg_ready(req)
    assert first.model_dump_json() == second.model_dump_json()
    assert emp.model_dump_json() == before


def test_two_destinations_and_two_source_tenants_never_mix():
    a = preview(
        emp=employee(tenant_id="tenantA", **FULL), op={"username": "u"}, dest={"company_id": "111"}
    )
    b = preview(
        emp=employee(tenant_id="tenantB", **FULL), op={"username": "u"}, dest={"company_id": "222"}
    )
    assert a.request_body == b.request_body  # the body is the same data...
    assert a.contract.endpoint.endswith("/111/employees") and b.contract.endpoint.endswith(
        "/222/employees"
    )
    assert a.source["tenant_id"] == "tenantA" and b.source["tenant_id"] == "tenantB"
    assert "tenantA" not in json.dumps(b.model_dump(mode="json")) and "111" not in json.dumps(
        b.model_dump(mode="json")
    )


@pytest.mark.parametrize("provider", ["prismhr", "isolved", "ukg-ready"])
def test_real_adapter_output_from_every_source_provider_previews(provider):
    payloads = {
        "prismhr": ({"errorCode": "0", "employee": [{"id": "Z-9", "firstName": "Lena", "lastName": "Ortiz",
                     "compensation": {"ssn": SSN}, "contactInformation": {"emailAddress": "l@example.test"}}]}, {"company_id": "C1"}),
        "isolved": ({"results": [{"id": 5, "employeeNumber": "E5", "nameAddress": {"firstName": "Lena", "lastName": "Ortiz"},
                     "ssn": SSN}]}, {"company_id": "9"}),
        "ukg-ready": ({"employee": {"id": 7, "first_name": "Lena", "last_name": "Ortiz", "social_security": SSN,
                       "username": "lortiz"}}, {"company_id": "3"}),
    }  # fmt: skip
    payload, ctx = payloads[provider]
    res = service.normalize(provider, "employee", payload, {"tenant_id": "t", **ctx})
    p = preview(emp=res.records[0], op={"username": "lortiz"})
    assert p.readiness == "READY" and p.request_body["first_name"] == "Lena"
    assert SSN not in json.dumps(p.model_dump(mode="json")) and "907314417" not in json.dumps(
        p.model_dump(mode="json")
    )
    assert p.source["provider"] == provider


# --- no network, no database --------------------------------------------------------------------------------------------
def test_no_network_is_touched(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    p = preview(op={"username": "u"}, **FULL)
    assert p.readiness == "READY"
    client = TestClient(create_app())
    assert (
        client.post("/migrate/preview/ukg-ready", content=_body(employee(**FULL))).status_code
        == 200
    )


def test_the_preview_modules_import_nothing_that_can_reach_the_network_or_the_database():
    import pathlib

    src = "\n".join(p.read_text() for p in pathlib.Path(schema_check.__file__).parent.glob("*.py"))
    for forbidden in (
        "httpx",
        "requests",
        "urllib",
        "socket",
        "sqlalchemy",
        "psycopg",
        "persistence",
        "settings",
    ):
        assert forbidden not in src, forbidden


# --- API ----------------------------------------------------------------------------------------------------------------------
def _body(emp=None, dest=None, op=None) -> str:
    return json.dumps({"employee": (emp or employee()).model_dump(mode="json"), "destination": dest or DEST,
                       "operator": op if op is not None else {"username": "u"}}, default=str)  # fmt: skip


@pytest.fixture
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


def test_the_route_returns_the_full_preview(client):
    r = client.post("/migrate/preview/ukg-ready", content=_body(employee(**FULL)))
    assert r.status_code == 200
    j = r.json()
    assert j["dry_run"] is True and j["readiness"] == "READY" and j["notice"].startswith("DRY RUN")
    assert {
        "request_body",
        "fields",
        "requirements",
        "issues",
        "not_migrated",
        "contract",
        "source",
    } <= set(j)
    assert j["request_body"]["phones"]["cell_phone"] == "5205550101"


def test_the_route_reports_blocked_and_still_answers_200(client):
    j = client.post("/migrate/preview/ukg-ready", content=_body(op={})).json()
    assert j["readiness"] == "BLOCKED" and any(i["code"] == "missing_username" for i in j["issues"])


def test_the_route_needs_no_database_and_writes_nothing(client, monkeypatch):
    from normalization.api import app as app_module

    def forbidden(*a, **k):
        raise AssertionError("database access attempted")

    monkeypatch.setattr(app_module, "make_engine", forbidden)
    monkeypatch.setattr(app_module, "transaction", forbidden)
    assert client.post("/migrate/preview/ukg-ready", content=_body()).status_code == 200


@pytest.mark.parametrize("dest", [{}, {"company_id": ""}, {"company_id": "a b"}, {"company_id": "../x"}, {"company_id": "x" * 41},
                                  {"company_id": "1", "ein_id": -1}, {"company_id": "1", "ein_id": "12"}, {"company_id": "1", "extra": 1}, None])  # fmt: skip
def test_an_invalid_destination_context_is_a_422_that_does_not_echo_the_value(client, dest):
    body = json.loads(_body())
    body["destination"] = dest
    r = client.post("/migrate/preview/ukg-ready", content=json.dumps(body))
    assert r.status_code == 422
    for value in ("a b", "../x"):
        assert value not in r.text


def test_other_destinations_are_not_supported(client):
    for name in ("prismhr", "isolved", "nope"):
        r = client.post(f"/migrate/preview/{name}", content=_body())
        assert r.status_code == 422 and "ukg-ready" in r.text


@pytest.mark.parametrize("raw", [b"{bad", b"", b"[]", b'{"employee": 5, "destination": {"company_id": "1"}}',
                                 b'{"employee": {}, "destination": {"company_id": "1"}}'])  # fmt: skip
def test_malformed_requests_are_400_or_422_never_500(client, raw):
    r = client.post("/migrate/preview/ukg-ready", content=raw)
    assert r.status_code in (400, 422), (raw, r.status_code)


def test_invalid_unicode_and_oversized_bodies_are_refused(client):
    body = json.loads(_body())
    body["operator"] = {"username": "a\u0000b"}
    assert client.post("/migrate/preview/ukg-ready", content=json.dumps(body)).status_code == 422
    raw = b'{"employee": {"x": "' + b"x" * (21 * 1024 * 1024) + b'"}}'
    assert client.post("/migrate/preview/ukg-ready", content=raw).status_code == 413


def test_unsupported_operator_fields_are_rejected_not_ignored(client):
    r = client.post(
        "/migrate/preview/ukg-ready",
        content=_body(op={"username": "u", "password": "hunter2hunter2"}),
    )
    assert r.status_code == 422 and "hunter2hunter2" not in r.text


def test_the_openapi_description_does_not_claim_the_request_is_accepted(client):
    doc = json.dumps(client.get("/openapi.json").json())
    assert "/migrate/preview/{destination}" in doc and "not that UKG accepts it" in doc.replace(
        "\\n", " "
    ).replace("  ", " ")


def test_existing_routes_are_unchanged(client):
    paths = set(client.get("/openapi.json").json()["paths"])
    assert {
        "/health",
        "/stats",
        "/normalize/{provider}/{entity_type}",
        "/ingest/{provider}/{entity_type}",
        "/detect/{provider}",
        "/convert/{provider}",
    } <= paths


def test_request_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        MigrationRequest(
            employee=employee(), destination=DEST, operator={"username": "u"}, send=True
        )
    with pytest.raises(ValidationError):
        copy.copy(MigrationRequest).model_validate({"employee": {}, "destination": DEST})
