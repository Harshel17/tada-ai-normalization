"""Regression tests for differences found against the LIVE isolved demo API.

Fixtures in tests/fixtures/live_shapes/isolved/ are sanitized structural copies of live responses
(real key names and nesting, synthetic values). Findings covered (DECISIONS.md section 12):
  * employee checks arrive in the paged envelope {"results": [...], "totalItems": ...};
  * an employee without checks returns an empty page;
  * the live provider returns direct-deposit account numbers UNMASKED, so none may reach any output;
  * a live check carries only payrollRunNumber / payrollRunType, so the run relationship is unresolved.
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

LIVE = Path(__file__).parent / "fixtures" / "live_shapes" / "isolved"
FIXED = Path(__file__).parent / "fixtures" / "isolved"
CTX = {"tenant_id": "t", "company_id": "4501", "parent_company_id": "10"}
ACCOUNTS = ("7766554433", "99112233")


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


def everything(res) -> str:
    """Every serialized form of a result: records (with extras and provenance), traces and issues."""
    return " ".join(
        [res.model_dump_json(), json.dumps(res.traces, default=str), repr(res), str(res.issues)]
    )


# --- paged checks ------------------------------------------------------------------------------------
def test_paged_checks_normalize_without_rewrapping_and_match_the_bare_list_result():
    paged = load(LIVE / "checks_paged.json")
    a = service.normalize("isolved", "payroll", paged, CTX)
    b = service.normalize("isolved", "payroll", paged["results"], CTX)
    assert a.ok and a.counts() == {"paycheck": 2}
    # identical except that provenance honestly records where each check was found
    assert a.model_dump_json().replace("$.results[", "$[") == b.model_dump_json()


def test_existing_formats_still_work():
    fixture = load(FIXED / "payroll.json")
    assert service.normalize("isolved", "payroll", fixture, CTX).counts() == {
        "payroll_run": 1,
        "paycheck": 1,
    }
    assert service.normalize("isolved", "payroll", fixture["checks"], CTX).counts() == {
        "paycheck": 1
    }
    nested = {"payroll_run": fixture["payroll_run"], "checks": load(LIVE / "checks_paged.json")}
    res = service.normalize("isolved", "payroll", nested, CTX)
    assert res.ok and res.counts() == {
        "payroll_run": 1,
        "paycheck": 2,
    }  # a paged envelope inside our wrapper


def test_a_dict_that_is_one_check_is_still_one_check():
    one = load(FIXED / "payroll.json")["checks"][0]
    assert service.normalize("isolved", "payroll", one, CTX).counts() == {"paycheck": 1}


def test_live_only_check_fields_are_kept_and_nothing_is_lost():
    res = service.normalize("isolved", "payroll", load(LIVE / "checks_paged.json"), CTX)
    assert not [i for i in res.issues if i.severity.value == "error"]
    extras = {k for r in res.records for k in r.extras}
    assert {"checkSequence", "taxCategories", "vertexWorkGeoCode", "reissueCheckNumber"} <= extras


def test_detection_recognises_the_paged_envelope():
    d = detect(Provider.ISOLVED, load(LIVE / "checks_paged.json"))
    assert d.status == "ok"
    u = d.units[0]
    assert u.entity.value == "payroll" and u.keys == ["results"] and u.counts == {"paycheck": 2}
    assert u.requires == ["company_id"]
    # the other paged shapes are unchanged
    emp = detect(Provider.ISOLVED, load(FIXED / "employees.json"))
    assert emp.units[0].entity.value == "employee"


def test_convert_endpoint_handles_the_live_checks_file_directly():
    out = convert_files(
        Provider.ISOLVED,
        [FileIn(name="c.json", content=load(LIVE / "checks_paged.json"))],
        {"tenant_id": "t", "company_id": "4501"},
    )
    r = out.results[0]
    assert (
        r.status == "converted"
        and r.counts == {"paycheck": 2}
        and not [i for i in r.issues if i["severity"] == "error"]
    )


def test_an_empty_page_is_empty_not_unsupported_and_creates_nothing():
    doc = load(LIVE / "checks_paged_empty.json")
    d = detect(Provider.ISOLVED, doc)
    assert d.status == "empty" and "empty page" in (d.message or "")
    res = service.normalize("isolved", "payroll", doc, CTX)
    assert res.ok and res.records == [] and [i.code for i in res.issues] == ["no_records"]


def test_only_a_page_with_no_records_and_only_paging_keys_is_empty():
    assert detect(Provider.ISOLVED, {"results": [], "somethingElse": 1}).status != "empty"
    assert detect(Provider.ISOLVED, {"results": [{"x": 1}], "totalItems": 1}).status != "empty"


# --- the paycheck -> run relationship is NOT invented -------------------------------------------------
def test_paychecks_from_live_checks_are_not_linked_to_any_run():
    res = service.normalize("isolved", "payroll", load(LIVE / "checks_paged.json"), CTX)
    assert res.counts() == {"paycheck": 2}  # no payroll_run record is fabricated
    for r in res.records:
        assert r.run_id is None
        assert (
            r.extras["payrollRunNumber"] is not None and "payrollRunType" in r.extras
        )  # preserved
    assert not any("run" in i.code for i in res.issues)


def test_a_paycheck_is_linked_only_when_the_caller_supplies_the_run():
    fixture = load(FIXED / "payroll.json")
    res = service.normalize("isolved", "payroll", fixture, CTX)
    check = next(r for r in res.records if r.record_type == "paycheck")
    assert check.run_id == str(fixture["payroll_run"]["id"])


# --- unmasked live account numbers must never leave the normalizer --------------------------------------------
@pytest.mark.parametrize("trace", [False, True])
def test_unmasked_deposit_account_numbers_appear_in_no_serialized_output(trace, caplog):
    caplog.set_level(logging.DEBUG)
    res = service.normalize(
        "isolved",
        "direct_deposit",
        load(LIVE / "direct_deposits_unmasked.json"),
        {**CTX, "employee_id": "90001"},
        trace=trace,
    )
    assert res.ok and res.counts() == {"direct_deposit": 2}
    text = everything(res) + caplog.text
    for account in ACCOUNTS:
        assert account not in text
    rec = res.records[0].model_dump(mode="json")
    assert rec["account_number_masked"] == "****4433" and rec["routing_number"] == "021000021"
    assert "accountNumber" not in json.dumps(rec["extras"])
    assert rec["provenance"]["redacted_paths"]  # the withheld paths are listed, the values are not


def test_account_numbers_are_hidden_when_deposits_are_embedded_in_employees():
    emp = load(FIXED / "employees.json")
    emp["results"][0]["directDeposits"] = load(LIVE / "direct_deposits_unmasked.json")
    for entity in ("employee", "direct_deposit"):
        res = service.normalize("isolved", entity, emp, CTX, trace=True)
        for account in ACCOUNTS:
            assert account not in everything(res)


def test_account_numbers_are_hidden_in_every_http_response():
    client = TestClient(create_app())
    deposits = load(LIVE / "direct_deposits_unmasked.json")
    texts = []
    r = client.post(
        "/convert/isolved",
        content=json.dumps(
            {
                "files": [{"name": "d.json", "content": deposits}],
                "context": {"company_id": "4501", "employee_id": "90001"},
                "trace": True,
            },
            default=str,
        ),
    )
    texts.append(r.text)
    r = client.post(
        "/normalize/isolved/direct_deposit",
        content=json.dumps(
            {
                "context": {"tenant_id": "t", "company_id": "4501", "employee_id": "90001"},
                "payload": deposits,
            },
            default=str,
        ),
    )
    texts.append(r.text)
    r = client.post(
        "/detect/isolved",
        content=json.dumps({"files": [{"name": "d.json", "content": deposits}]}, default=str),
    )
    texts.append(r.text)
    for text in texts:
        for account in ACCOUNTS:
            assert account not in text


def test_a_masked_ssn_on_a_check_is_not_copied_into_the_output():
    res = service.normalize("isolved", "payroll", load(LIVE / "checks_paged.json"), CTX, trace=True)
    assert "XXX-XX-4321" not in everything(res)
