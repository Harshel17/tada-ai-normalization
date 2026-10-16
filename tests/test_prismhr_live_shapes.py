"""Regression tests for differences found against the LIVE PrismHR demo API.

The fixtures in tests/fixtures/live_shapes/prismhr/ are sanitized STRUCTURAL copies of live responses:
the key names and nesting seen live, with synthetic values (and already-masked account numbers).
Findings these cover (see DECISIONS.md section 12):
  * getPayrollVouchers returns the list under `payrollVoucher`, the contract says `payrollVouchers`;
  * getDeductions can legitimately return an empty list;
  * getEmployee without `options` returns every section name with null values.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from normalization import service
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files
from normalization.detect import detect

LIVE = Path(__file__).parent / "fixtures" / "live_shapes" / "prismhr"
FIXED = Path(__file__).parent / "fixtures" / "prismhr"
CTX = {"tenant_id": "t", "company_id": "ACME"}


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


# --- payroll: payrollVoucher (live) and payrollVouchers (contract) ----------------------------------
def test_singular_wrapper_normalizes_without_rewrapping_and_matches_the_plural_result():
    live = load(LIVE / "payroll_voucher_singular.json")
    plural = {
        **{k: v for k, v in live.items() if k != "payrollVoucher"},
        "payrollVouchers": live["payrollVoucher"],
    }
    a = service.normalize("prismhr", "payroll", live, CTX)
    b = service.normalize("prismhr", "payroll", plural, CTX)
    assert a.ok and a.counts() == {"payroll_run": 1, "paycheck": 2}
    # identical except that provenance honestly records the wrapper key each came from
    assert a.model_dump_json().replace("payrollVoucher[", "payrollVouchers[") == b.model_dump_json()


def test_live_only_fields_are_kept_as_extras_and_masked_accounts_never_become_data():
    res = service.normalize("prismhr", "payroll", load(LIVE / "payroll_voucher_singular.json"), CTX)
    assert not [i for i in res.issues if i.severity.value == "error"]
    extras = {k for r in res.records if r.record_type == "paycheck" for k in r.extras}
    assert {"isManualCheck", "hsaPeoMatch", "bankAccount", "checkAccountACH"} <= extras
    text = res.model_dump_json()
    assert "123-45-6789" not in text and "000123456789" not in text


def test_existing_plural_wrapper_still_works_and_wins_when_both_are_present():
    plural = load(FIXED / "payroll_vouchers.json")
    assert service.normalize("prismhr", "payroll", plural, CTX).counts() == {
        "payroll_run": 1,
        "paycheck": 2,
    }
    both = {**plural, "payrollVoucher": [{"voucherId": "ZZ", "employeeId": "E1"}]}
    res = service.normalize("prismhr", "payroll", both, CTX)
    assert res.counts() == {"payroll_run": 1, "paycheck": 2}  # the documented key is used


def test_detection_accepts_both_wrapper_spellings():
    live = detect(Provider.PRISMHR, load(LIVE / "payroll_voucher_singular.json"))
    old = detect(Provider.PRISMHR, load(FIXED / "payroll_vouchers.json"))
    for d, key in ((live, "payrollVoucher"), (old, "payrollVouchers")):
        assert d.status == "ok" and d.units[0].entity.value == "payroll"
        assert d.units[0].keys == [key] and d.units[0].counts == {"paycheck": 2}
        assert any(key in e for e in d.units[0].evidence)


def test_convert_endpoint_handles_the_live_payroll_file_directly():
    out = convert_files(
        Provider.PRISMHR,
        [FileIn(name="p.json", content=load(LIVE / "payroll_voucher_singular.json"))],
        {"tenant_id": "t", "company_id": "ACME"},
    )
    r = out.results[0]
    assert r.status == "converted" and r.counts == {"payroll_run": 1, "paycheck": 2}


# --- deductions: an empty list is a valid zero-record answer ------------------------------------------------
def test_empty_deduction_list_is_recognised_as_zero_records_not_unsupported():
    d = detect(Provider.PRISMHR, load(LIVE / "deductions_empty.json"))
    assert d.status == "ok"
    u = d.units[0]
    assert u.entity.value == "deduction" and u.counts == {"deduction": 0}
    assert u.requires == ["company_id", "employee_id"]
    assert any("zero records" in e for e in u.evidence)


def test_empty_deduction_list_creates_no_records_and_says_so():
    doc = load(LIVE / "deductions_empty.json")
    res = service.normalize("prismhr", "deduction", doc, {**CTX, "employee_id": "E1"})
    assert res.ok and res.records == [] and [i.code for i in res.issues] == ["no_records"]
    out = convert_files(
        Provider.PRISMHR,
        [FileIn(name="d.json", content=doc)],
        {"tenant_id": "t", "company_id": "ACME", "employee_id": "E1"},
    )
    assert out.results[0].status == "converted" and out.results[0].records == []


def test_empty_deduction_list_still_needs_the_employee_id():
    out = convert_files(
        Provider.PRISMHR,
        [FileIn(name="d.json", content=load(LIVE / "deductions_empty.json"))],
        {"tenant_id": "t", "company_id": "ACME"},
    )
    assert (out.results[0].status, out.results[0].missing) == ("needs_context", ["employee_id"])


@pytest.mark.parametrize(
    "doc",
    [
        {"employeeDeductions": {"deduction": [{"unrelated": 1}]}},  # rows without a deductionCode
        {"employeeDeductions": {"deduction": None}},
        {"employeeDeductions": {}},
        {"employeeDeductions": {"deduction": {}}},
    ],
)
def test_only_a_genuinely_empty_list_gets_the_new_treatment(doc):
    assert detect(Provider.PRISMHR, doc).status == "unsupported"


def test_nonempty_deductions_are_unchanged():
    d = detect(Provider.PRISMHR, load(FIXED / "deductions_get_deductions.json"))
    assert (
        d.units[0].counts == {"deduction": 3}
        and d.units[0].evidence[1] == "Items carry deductionCode"
    )


# --- employee: a person-only response (no `options`) -------------------------------------------------------------
def test_person_only_employee_is_detected_from_the_section_names_even_though_they_are_null():
    d = detect(Provider.PRISMHR, load(LIVE / "employee_person_only.json"))
    assert d.status == "ok"
    u = d.units[0]
    assert u.entity.value == "employee" and u.counts == {"employee": 1} and u.strength == "strong"
    assert any("section names present" in e for e in u.evidence)
    assert [x.entity.value for x in d.units] == [
        "employee"
    ]  # no deposits or deductions are invented


def test_person_only_employee_converts_with_only_what_it_contains():
    res = service.normalize("prismhr", "employee", load(LIVE / "employee_person_only.json"), CTX)
    assert res.ok and res.counts() == {"employee": 1}
    rec = res.records[0].model_dump(mode="json")
    assert (rec["first_name"], rec["last_name"], rec["source_id"]) == ("Pat", "Sample", "E9001")
    assert rec["hire_date"] is None and rec["compensation"] is None


def test_a_bare_name_is_still_ambiguous_and_two_section_names_are_not_enough():
    assert detect(Provider.PRISMHR, {"employee": [{"firstName": "Ann"}]}).status == "ambiguous"
    two = {"employee": [{"id": "E1", "firstName": "Ann", "person": None, "client": None}]}
    assert detect(Provider.PRISMHR, two).status == "ambiguous"
    no_id = {
        "employee": [
            {"firstName": "Ann", **{k: None for k in ("person", "client", "health", "hsa")}}
        ]
    }
    assert detect(Provider.PRISMHR, no_id).status == "ambiguous"


def test_other_providers_are_not_affected_by_the_prismhr_section_names():
    doc = {
        "employee": [
            {"id": "E1", "firstName": "Ann", "person": None, "client": None, "health": None}
        ]
    }
    for provider in (Provider.ISOLVED, Provider.UKG_READY):
        d = detect(provider, doc)
        assert not any("section names" in e for u in d.units for e in u.evidence)
