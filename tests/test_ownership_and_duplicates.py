"""Duplicate wording (source occurrences vs logical records) and employee-ownership limits, on the REAL bundled fixtures.

Facts these tests pin down:
  * the isolved example holds deposit 301 twice: as a bare deposit list and embedded in employee 90001;
  * both are returned (source occurrences are preserved, nothing is silently deduplicated);
  * a bare list has no owner, so the Employee ID context decides it; an embedded record's owner always wins;
  * integrity can show that an owner EXISTS in the data, never that it is the RIGHT owner.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from normalization import service
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files, detect_files

FIX = Path(__file__).parent / "fixtures"
PROVIDERS = {
    "prismhr": Provider.PRISMHR,
    "isolved": Provider.ISOLVED,
    "ukg_ready": Provider.UKG_READY,
}


def load(folder, name):
    return json.loads((FIX / folder / name).read_text(), parse_float=Decimal)


def isolved_run(owner):
    files = [
        FileIn(name=n, content=load("isolved", n))
        for n in ("direct_deposits.json", "employees.json")
    ]
    return convert_files(
        Provider.ISOLVED,
        files,
        {"tenant_id": "t", "company_id": "4501", "employee_id": owner},
        integrity=True,
    )


def check(out, id_):
    return next(c for c in out.integrity.checks if c.id == id_)


def deposits(out):
    return {
        u.file: [(r["source_id"], r["employee_id"]) for r in u.records]
        for u in out.results
        if u.entity == "direct_deposit"
    }


# --- FIX 1: the duplicate warning ---------------------------------------------------------------------------------
def test_the_bundled_isolved_example_really_contains_deposit_301_twice_and_both_are_returned():
    out = isolved_run("90001")
    assert deposits(out) == {
        "direct_deposits.json": [("301", "90001"), ("302", "90001"), ("303", "90001")],
        "employees.json": [("301", "90001")],
    }  # source occurrences are preserved: nothing is silently deduplicated


def test_the_two_occurrences_differ_only_in_provenance():
    out = isolved_run("90001")
    bare = next(
        r
        for u in out.results
        if u.file == "direct_deposits.json"
        for r in u.records
        if r["source_id"] == "301"
    )
    embedded = next(
        r
        for u in out.results
        if u.file == "employees.json"
        if u.entity == "direct_deposit"
        for r in u.records
    )
    strip = lambda r: {k: v for k, v in r.items() if k != "provenance"}  # noqa: E731
    assert strip(bare) == strip(embedded)
    assert bare["provenance"]["source_path"] != embedded["provenance"]["source_path"]


def test_the_warning_stays_a_warning_and_explains_overlap_not_a_double_upload():
    c = check(isolved_run("90001"), "duplicate_ids")
    assert c.status == "warning" and c.flagged == 1  # detection logic and status unchanged
    reason = c.reason
    assert (
        "1 logical record was found in more than one place (2 source occurrences in all)" in reason
    )
    assert "several provider responses" in reason and "embedded inside another" in reason
    assert "does not mean a file was uploaded twice" in reason
    assert "probably" not in reason and "added twice" not in reason
    assert [e.detail for e in c.examples] == [
        "2 source occurrences of 1 logical record, with identical content"
    ]
    assert c.examples[0].record == "direct_deposit 301"


def test_a_real_conflict_is_still_failed_and_an_unrelated_set_still_passes():
    # same id, different content -> failed (unchanged logic)
    a = load("isolved", "direct_deposits.json")
    b = json.loads(json.dumps(a, default=str), parse_float=Decimal)
    b[0]["bankName"] = "Another Bank"
    files = [FileIn(name="a.json", content=a), FileIn(name="b.json", content=b)]
    out = convert_files(
        Provider.ISOLVED,
        files,
        {"tenant_id": "t", "company_id": "4501", "employee_id": "90001"},
        integrity=True,
    )
    assert check(out, "duplicate_ids").status == "failed"


# --- FIX 2: ownership -----------------------------------------------------------------------------------------------
def test_an_embedded_deposit_keeps_its_own_owner_whatever_the_context_says():
    out = isolved_run("90002")
    assert deposits(out)["employees.json"] == [("301", "90001")]  # never reassigned to 90002


def test_a_wrong_but_existing_owner_is_accepted_and_every_check_can_still_pass():
    """The limit of what the system can verify: 90002 exists, so nothing can say the bare list is not theirs."""
    right, wrong = isolved_run("90001"), isolved_run("90002")
    assert deposits(wrong)["direct_deposits.json"] == [
        ("301", "90002"),
        ("302", "90002"),
        ("303", "90002"),
    ]
    assert deposits(right)["direct_deposits.json"][0][1] == "90001"
    links = check(wrong, "employee_links")
    assert links.status == "passed"  # 90002 exists among the converted employees...
    assert "not that it is the right owner" in links.reason  # ...and the check says only that
    assert (
        check(wrong, "duplicate_ids").status == "passed"
    )  # the overlap even disappears: 301 now has two owners


def test_an_owner_that_does_not_exist_is_flagged_but_an_existing_wrong_one_is_not():
    out = isolved_run("99999")
    assert check(out, "employee_links").status in {"warning", "failed"}


def test_a_deduction_with_its_own_employee_id_keeps_it_and_an_unowned_one_takes_the_context():
    payload = [
        {
            "id": 1,
            "payItemCode": "MED",
            "employeeId": 90001,
            "calcParams": [],
            "startDate": "2025-01-01",
        },
        {"id": 2, "payItemCode": "DEN", "calcParams": [], "startDate": "2025-01-01"},
    ]
    res = service.normalize(
        "isolved",
        "deduction",
        payload,
        {"tenant_id": "t", "company_id": "4501", "employee_id": "90002"},
    )
    assert {r.source_id: r.employee_id for r in res.records} == {"1": "90001", "2": "90002"}


def test_prismhr_embedded_deposits_keep_their_owner_under_a_different_context_employee():
    res = service.normalize(
        "prismhr", "direct_deposit", load("prismhr", "employee_get_employee.json"),
        {"tenant_id": "t", "company_id": "ACME", "employee_id": "E1002"},
    )  # fmt: skip
    assert {d.employee_id for d in res.records} == {"E1001"}


def test_a_bare_deposit_list_without_a_context_owner_is_refused_not_guessed():
    out = convert_files(
        Provider.ISOLVED, [FileIn(name="direct_deposits.json", content=load("isolved", "direct_deposits.json"))],
        {"tenant_id": "t", "company_id": "4501"}, integrity=True,
    )  # fmt: skip
    unit = out.results[0]
    assert unit.status == "needs_context" and unit.missing == ["employee_id"] and unit.records == []


def test_a_custom_file_with_no_ownership_information_has_no_owner_suggestion():
    det = detect_files(
        Provider.ISOLVED,
        [FileIn(name="mine.json", content=load("isolved", "direct_deposits.json"))],
    )
    assert [
        s for s in det.suggestions if s.field == "employee_id"
    ] == []  # nothing in the file names an employee
    unit = det.files[0].detection.units[0]
    assert "employee_id" in unit.requires and any(
        "does not say which employee" in n for n in unit.notes
    )


def test_several_employees_in_other_files_are_only_candidates_never_chosen_for_you():
    files = [
        FileIn(name=n, content=load("isolved", n))
        for n in ("direct_deposits.json", "employees.json")
    ]
    det = detect_files(Provider.ISOLVED, files)
    assert [s.value for s in det.suggestions if s.field == "employee_id"] == ["90001", "90002"]
    out = convert_files(
        Provider.ISOLVED, files, {"tenant_id": "t", "company_id": "4501"}
    )  # no owner given
    assert (
        next(u for u in out.results if u.file == "direct_deposits.json").status == "needs_context"
    )


# --- the documented owners of the bundled examples ----------------------------------------------------------------------
OWNERS = json.loads((FIX / "example_owners.json").read_text())


def documented():
    return {k: v["employee_id"] for k, v in OWNERS.items() if not k.startswith("_")}


def needing_owner():
    """Bundled files that the REAL detection says need an employee id."""
    found = {}
    for folder, provider in PROVIDERS.items():
        files = [
            FileIn(name=f.name, content=load(folder, f.name))
            for f in sorted((FIX / folder).glob("*.json"))
        ]
        det = detect_files(provider, files)
        for f in det.files:
            if any("employee_id" in u.requires for u in f.detection.units):
                found[f"{folder}/{f.name}"] = [
                    s.value for s in det.suggestions if s.field == "employee_id"
                ]
    return found


def test_every_bundled_file_that_needs_an_owner_has_a_documented_one_and_nothing_else_is_listed():
    assert set(documented()) == set(needing_owner())


@pytest.mark.parametrize(("key", "candidates"), sorted(needing_owner().items()))
def test_each_documented_owner_exists_among_the_example_employees(key, candidates):
    assert documented()[key] in candidates, (key, candidates)


def test_the_documented_owners_agree_with_the_console_scenarios():
    scenarios = Path(__file__).parents[1] / "frontend/src/lib/scenarios.ts"
    if not scenarios.exists():
        pytest.skip("frontend/ is not part of this checkout (backend-only)")
    text = scenarios.read_text()
    for key in (
        "isolved/direct_deposits.json",
        "ukg_ready/direct_deposits.json",
        "ukg_ready/deductions.json",
    ):
        assert f"employee_id: '{documented()[key]}'" in text


def test_every_documented_example_owner_normalizes_to_that_owner():
    out = isolved_run(documented()["isolved/direct_deposits.json"])
    assert {o for _, o in deposits(out)["direct_deposits.json"]} == {"90001"}
    assert (
        check(out, "duplicate_ids").status == "warning"
    )  # the intended owner makes the overlap visible


# --- is E1001 really the PrismHR example's owner? What the source can and cannot establish ---------------------------------
def test_prismhr_example_deposits_are_embedded_in_E1001_so_their_owner_is_established_by_the_data():
    emp = load("prismhr", "employee_get_employee.json")["employee"]
    with_deposits = {
        e["id"]: len((e.get("directDeposit") or {}).get("achVoucher") or []) for e in emp
    }
    assert with_deposits == {"E1001": 3, "E1002": 0}
    res = service.normalize(
        "prismhr",
        "direct_deposit",
        load("prismhr", "employee_get_employee.json"),
        {"tenant_id": "t", "company_id": "ACME", "employee_id": "E1002"},
    )
    assert {d.employee_id for d in res.records} == {"E1001"} and len(
        res.records
    ) == 3  # even if the context says otherwise


def test_the_prismhr_deductions_file_cannot_establish_its_owner_so_the_documented_owner_is_a_convention_not_proof():
    doc = load("prismhr", "deductions_get_deductions.json")

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    assert not [
        k for k in keys(doc) if "mploy" in k.lower() and k != "employeeDeductions"
    ]  # no employee reference inside the file
    contract = json.loads(
        (
            Path(__file__).parents[1] / "contracts/prismhr/openapi/prismhr-2025.09.openapi.json"
        ).read_text()
    )
    op = contract["paths"]["/deductions/v1/getDeductions"]["get"]
    assert "employeeId" in [
        p["name"] for p in op["parameters"]
    ]  # the owner travels in the REQUEST...
    assert "employeeId" not in json.dumps(
        contract["components"]["schemas"]["DeductionResponse"]
    ).replace("employeeDeductions", "")  # ...not in the response
    entry = OWNERS["prismhr/deductions_get_deductions.json"]
    assert (
        entry["employee_id"] == "E1001"
        and "CONVENTION" in entry["evidence"]
        and "not stated by the file" in entry["evidence"]
    )


def test_the_project_uses_E1001_for_that_file_in_its_own_regression_case():
    cases = (Path(__file__).parent / "support" / "scenarios.py").read_text()
    block = cases[cases.index('"prismhr-deduction-recurring"') : cases.index('"prismhr-payroll"')]
    assert '"employee_id": "E1001"' in block and "deductions_get_deductions.json" in block
