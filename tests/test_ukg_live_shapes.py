"""Regression tests for differences found against the LIVE UKG Ready demo API.

Fixtures in tests/fixtures/live_shapes/ukg_ready/ are sanitized structural copies of live responses
(real keys and nesting, synthetic values). Findings covered (DECISIONS.md section 12):
  * a single pay statement carries `employee: {account_id}` and was mistaken for an employee bundle;
  * direct-deposit and pay-statement lists can legitimately be empty;
  * the live provider returns SSNs unmasked, so none may reach any output.
"""

import json
import logging
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from normalization import service
from normalization.api.app import create_app
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files
from normalization.detect import detect

LIVE = Path(__file__).parent / "fixtures" / "live_shapes" / "ukg_ready"
FIXED = Path(__file__).parent / "fixtures" / "ukg_ready"
CTX = {"tenant_id": "t", "company_id": "CID1"}
SSN = "912-34-6789"
ACCOUNTS = ("55667788", "3344556677")


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


def everything(res) -> str:
    return " ".join(
        [res.model_dump_json(), json.dumps(res.traces, default=str), repr(res), str(res.issues)]
    )


# --- single pay statement ---------------------------------------------------------------------------
def test_a_single_pay_statement_with_an_employee_reference_is_detected_as_payroll():
    d = detect(Provider.UKG_READY, load(LIVE / "pay_statement_single.json"))
    assert d.status == "ok"
    assert [(u.entity.value, u.counts) for u in d.units] == [("payroll", {"paycheck": 1})]
    assert d.units[0].requires == ["company_id"] and d.units[0].optional == ["payroll_id"]


def test_the_detected_pay_statement_converts_to_one_paycheck():
    doc = load(LIVE / "pay_statement_single.json")
    out = convert_files(
        Provider.UKG_READY,
        [FileIn(name="s.json", content=doc)],
        {"tenant_id": "t", "company_id": "CID1", "payroll_id": "9"},
    )
    r = out.results[0]
    assert r.status == "converted" and r.counts == {"paycheck": 1}
    assert not [i for i in r.issues if i["severity"] == "error"]


def test_a_reference_only_employee_object_is_not_an_employee_bundle():
    ref = {"employee": {"account_id": 1}, "pay_date": "2025-01-01", "earnings": [{"x": 1}]}
    assert [u.entity.value for u in detect(Provider.UKG_READY, ref).units] == ["payroll"]
    nothing = {"employee": {"account_id": 1}}  # a reference alone is not evidence of anything
    assert detect(Provider.UKG_READY, nothing).status == "unsupported"


def test_real_employee_bundles_and_profiles_are_still_employees():
    bundle = detect(Provider.UKG_READY, load(FIXED / "employee_bundle.json"))
    assert [u.entity.value for u in bundle.units] == ["employee"] and bundle.units[
        0
    ].strength == "strong"
    only_profile_in_bundle = {"employee": {"first_name": "Ann", "id": 1}}
    weak = detect(
        Provider.UKG_READY, only_profile_in_bundle
    )  # a name but no parts: weak, so it asks
    assert weak.status == "ambiguous" and [c.entity.value for c in weak.candidates] == ["employee"]
    parts_only = {"employee": {"id": 1}, "pay_info": {"x": 1}}
    assert detect(Provider.UKG_READY, parts_only).units[0].entity.value == "employee"


def test_statement_evidence_is_paycheck_specific_not_just_any_date():
    dated_only = {"employee": {"account_id": 1}, "pay_date": "2025-01-01", "status": "OPEN"}
    assert detect(Provider.UKG_READY, dated_only).status != "ok"
    lists_without_dates = {"earnings": [{"a": 1}], "taxes": [{"b": 1}]}
    assert detect(Provider.UKG_READY, lists_without_dates).status != "ok"
    empty_lines_but_two_lists = {"pay_date": "2025-01-01", "earnings": [], "taxes": []}
    assert [
        u.entity.value for u in detect(Provider.UKG_READY, empty_lines_but_two_lists).units
    ] == ["payroll"]


def test_a_payroll_run_or_a_deposit_is_not_mistaken_for_a_statement():
    run = load(FIXED / "payroll.json")["payroll"]
    assert not any(u.shape == "single pay statement" for u in detect(Provider.UKG_READY, run).units)
    dep = load(FIXED / "direct_deposits.json")["deposits"][0]
    assert [u.entity.value for u in detect(Provider.UKG_READY, dep).units] == ["direct_deposit"]


# --- empty lists ------------------------------------------------------------------------------------------
def test_an_empty_direct_deposit_response_is_a_valid_zero_record_response():
    doc = load(LIVE / "direct_deposits_empty.json")
    d = detect(Provider.UKG_READY, doc)
    assert d.status == "ok" and d.units[0].counts == {"direct_deposit": 0}
    assert d.units[0].requires == ["company_id", "employee_id"]
    res = service.normalize("ukg-ready", "direct_deposit", doc, {**CTX, "employee_id": "7770001"})
    assert res.ok and res.records == [] and [i.code for i in res.issues] == ["no_records"]


def test_an_empty_pay_statement_list_is_a_valid_zero_record_response():
    doc = load(LIVE / "pay_statements_empty.json")
    d = detect(Provider.UKG_READY, doc)
    assert (
        d.status == "ok"
        and d.units[0].counts == {"paycheck": 0}
        and d.units[0].entity.value == "payroll"
    )
    res = service.normalize("ukg-ready", "payroll", doc, CTX)
    assert res.ok and res.records == []
    out = convert_files(
        Provider.UKG_READY,
        [FileIn(name="p.json", content=doc)],
        {"tenant_id": "t", "company_id": "CID1"},
    )
    assert out.results[0].status == "converted" and out.results[0].records == []


def test_empty_responses_still_need_their_context():
    out = convert_files(
        Provider.UKG_READY,
        [FileIn(name="d.json", content=load(LIVE / "direct_deposits_empty.json"))],
        {"tenant_id": "t", "company_id": "CID1"},
    )
    assert (out.results[0].status, out.results[0].missing) == ("needs_context", ["employee_id"])


@pytest.mark.parametrize(
    "doc",
    [
        {"deposits": {}},
        {"deposits": None},
        {"deposits": "none"},
        {"deposits": [], "unexpected": 1},  # unrelated top-level key
        {"deposits": [1, 2]},
        {"deposits": [{}]},
        {"deposits": [{"unrelated": 1}]},
        {"pay_statements": {}},
        {"pay_statements": None},
        {"pay_statements": [], "unexpected": 1},
        {"pay_statements": [1]},
    ],
)
def test_malformed_or_unrelated_responses_are_still_rejected(doc):
    assert detect(Provider.UKG_READY, doc).status == "unsupported"


def test_non_empty_lists_are_unchanged():
    d = detect(Provider.UKG_READY, load(FIXED / "direct_deposits.json"))
    assert d.units[0].counts == {"direct_deposit": 4} and "zero records" not in " ".join(
        d.units[0].evidence
    )
    p = detect(Provider.UKG_READY, load(FIXED / "payroll.json"))
    assert p.units[0].counts == {"payroll_run": 1, "paycheck": 2}


# --- unmasked live values never reach an output -------------------------------------------------------------
def test_unmasked_ssns_appear_in_no_serialized_output(caplog):
    caplog.set_level(logging.DEBUG)
    doc = load(LIVE / "employee_bundle_unmasked.json")
    res = service.normalize("ukg-ready", "employee", doc, CTX, trace=True)
    assert res.ok and res.counts() == {"employee": 1}
    text = everything(res) + caplog.text
    assert SSN not in text and SSN.replace("-", "") not in text
    assert res.records[0].model_dump(mode="json")["ssn_last4"] == "6789"


def test_unmasked_deposit_account_numbers_appear_in_no_serialized_output(caplog):
    caplog.set_level(logging.DEBUG)
    doc = load(LIVE / "direct_deposits_unmasked.json")
    res = service.normalize(
        "ukg-ready", "direct_deposit", doc, {**CTX, "employee_id": "7770001"}, trace=True
    )
    assert res.ok and res.counts() == {"direct_deposit": 2}
    text = everything(res) + caplog.text
    for account in ACCOUNTS:
        assert account not in text


def test_nothing_sensitive_is_in_any_http_response():
    client = TestClient(create_app())
    bundle, deposits = (
        load(LIVE / "employee_bundle_unmasked.json"),
        load(LIVE / "direct_deposits_unmasked.json"),
    )
    texts = []
    for path, body in (
        (
            "/detect/ukg-ready",
            {
                "files": [
                    {"name": "e.json", "content": bundle},
                    {"name": "d.json", "content": deposits},
                ]
            },
        ),
        (
            "/convert/ukg-ready",
            {
                "files": [
                    {"name": "e.json", "content": bundle},
                    {"name": "d.json", "content": deposits},
                ],
                "context": {"company_id": "CID1", "employee_id": "7770001"},
                "trace": True,
            },
        ),
        (
            "/normalize/ukg-ready/employee",
            {"context": {"tenant_id": "t", "company_id": "CID1"}, "payload": bundle},
        ),
    ):
        texts.append(client.post(path, content=json.dumps(body, default=str)).text)
    joined = " ".join(texts)
    for secret in (SSN, SSN.replace("-", ""), *ACCOUNTS):
        assert secret not in joined
