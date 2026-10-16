"""Deterministic payroll integrity checks: four statuses, no false alarms, no unsupported assumptions."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from normalization.api.app import create_app
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files
from normalization.integrity import evaluate
from support.scenarios import CASES

FIX = Path(__file__).parent / "fixtures"
SECRETS = (
    "123-45-6789",
    "987-65-4321",
    "900-44-3333",
    "000123456789",
    "998877665544",
    "7766554433",
    "55667788",
)


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


def check(report, id_):
    return next(c for c in report.checks if c.id == id_)


def rec(record_type, source_id, **fields):
    return {
        "record_type": record_type,
        "source_id": source_id,
        "provider": "prismhr",
        "tenant_id": "t",
        "company_id": "c",
        "provenance": {"derived_fields": []},
        **fields,
    }


def line(amount, **kw):
    return {"amount": amount, **kw}


# --- shape and statuses -------------------------------------------------------------------------------------
def test_every_check_has_one_of_four_statuses_and_the_summary_adds_up():
    report = evaluate([])
    assert {c.status for c in report.checks} <= {"passed", "warning", "failed", "not_evaluated"}
    assert sum(report.summary.values()) == len(report.checks) and set(report.summary) == {
        "passed",
        "warning",
        "failed",
        "not_evaluated",
    }
    assert [c.id for c in report.checks] == [
        "duplicate_ids", "paycheck_run_link", "employee_links", "period_order", "effective_dates", "employment_dates",
        "earnings_equal_gross", "payments_equal_net", "gross_to_net", "deposit_percentages", "deposit_remaining_balance",
    ]  # fmt: skip
    assert all(
        c.status == "not_evaluated" and c.reason for c in report.checks
    )  # nothing converted: nothing is claimed


def test_the_report_is_deterministic():
    records = [
        rec("paycheck", "P1", gross_pay="10", earnings=[line("10")]),
        rec("paycheck", "P2", gross_pay="10", earnings=[line("9")]),
    ]
    assert evaluate(records).model_dump_json() == evaluate(list(records)).model_dump_json()


def test_the_engine_never_modifies_the_records():
    records = [
        rec(
            "paycheck",
            "P1",
            gross_pay="10",
            earnings=[line("10")],
            payments=[line("10")],
            net_pay="10",
        )
    ]
    before = json.dumps(records, sort_keys=True)
    evaluate(records)
    assert json.dumps(records, sort_keys=True) == before


# --- identity -------------------------------------------------------------------------------------------------
def test_duplicate_ids_conflict_is_failed_identical_copy_is_a_warning_unique_is_passed():
    a = rec("employee", "E1", first_name="A")
    assert check(evaluate([a, rec("employee", "E2")]), "duplicate_ids").status == "passed"
    same = check(
        evaluate([a, {**a, "provenance": {"source_path": "$[1]"}}]), "duplicate_ids"
    )  # only provenance differs
    assert (same.status, same.flagged) == ("warning", 1) and "identical" in same.reason
    conflict = check(evaluate([a, rec("employee", "E1", first_name="B")]), "duplicate_ids")
    assert (conflict.status, conflict.flagged) == (
        "failed",
        1,
    ) and "different content" in conflict.examples[0].detail


def test_deposits_of_different_employees_may_share_a_source_id():
    d1, d2 = (
        rec("direct_deposit", "X", employee_id="E1"),
        rec("direct_deposit", "X", employee_id="E2"),
    )
    assert check(evaluate([d1, d2]), "duplicate_ids").status == "passed"
    assert check(evaluate([d1, {**d1, "bank_name": "other"}]), "duplicate_ids").status == "failed"


# --- relationships ----------------------------------------------------------------------------------------------
def test_paycheck_run_link_statuses():
    run, ok, bad, free = (
        rec("payroll_run", "R1"),
        rec("paycheck", "P1", run_id="R1"),
        rec("paycheck", "P2", run_id="R9"),
        rec("paycheck", "P3"),
    )
    assert check(evaluate([run, ok]), "paycheck_run_link").status == "passed"
    failed = check(evaluate([run, ok, bad]), "paycheck_run_link")
    assert (failed.status, failed.flagged) == ("failed", 1) and "R9" in failed.examples[0].detail
    assert check(evaluate([ok]), "paycheck_run_link").status == "not_evaluated"  # no run converted
    assert (
        check(evaluate([free]), "paycheck_run_link").status == "not_evaluated"
    )  # paychecks name no run
    note = check(evaluate([run, ok, free]), "paycheck_run_link")
    assert (
        note.status == "passed" and "name no run" in note.reason
    )  # the unlinked one is mentioned, not failed


def test_employee_links_warn_never_fail_and_need_employees():
    emp = rec("employee", "E1")
    child = rec("direct_deposit", "d", employee_id="E1")
    assert check(evaluate([emp, child]), "employee_links").status == "passed"
    warn = check(evaluate([emp, child, rec("paycheck", "p", employee_id="E7")]), "employee_links")
    assert (warn.status, warn.flagged) == ("warning", 1) and "E7" in warn.examples[0].record
    assert (
        check(evaluate([child]), "employee_links").status == "not_evaluated"
    )  # no employees converted


# --- dates --------------------------------------------------------------------------------------------------------
def test_period_order_failed_passed_and_not_evaluated():
    good, bad = (
        rec("paycheck", "P1", period_start="2025-01-01", period_end="2025-01-14"),
        rec("payroll_run", "R", period_start="2025-02-01", period_end="2025-01-01"),
    )
    assert check(evaluate([good]), "period_order").status == "passed"
    failed = check(evaluate([good, bad]), "period_order")
    assert (failed.status, failed.flagged, failed.evaluated) == ("failed", 1, 2)
    assert (
        check(evaluate([rec("paycheck", "P", period_start="2025-01-01")]), "period_order").status
        == "not_evaluated"
    )
    assert (
        check(
            evaluate([rec("paycheck", "P", period_start="junk", period_end="2025-01-01")]),
            "period_order",
        ).status
        == "not_evaluated"
    )


def test_effective_dates_use_each_records_own_fields_and_the_documented_sentinels_pass():
    dep = rec(
        "direct_deposit",
        "d",
        employee_id="E",
        effective_from="1900-12-31",
        effective_to="9999-12-31",
    )
    ded = rec("deduction", "x", employee_id="E", start_date="2024-01-01", stop_date="2024-06-01")
    assert check(evaluate([dep, ded]), "effective_dates").status == "passed"
    bad = rec("deduction", "y", employee_id="E", start_date="2024-06-01", stop_date="2024-01-01")
    assert check(evaluate([dep, bad]), "effective_dates").status == "failed"
    assert (
        check(
            evaluate([rec("deduction", "z", employee_id="E", start_date="2024-01-01")]),
            "effective_dates",
        ).status
        == "not_evaluated"
    )


def test_employment_dates_warn_because_a_rehire_can_look_like_a_termination_before_hire():
    assert (
        check(
            evaluate([rec("employee", "E", hire_date="2020-01-01", termination_date="2021-01-01")]),
            "employment_dates",
        ).status
        == "passed"
    )
    assert (
        check(
            evaluate([rec("employee", "E", hire_date="2022-01-01", termination_date="2021-01-01")]),
            "employment_dates",
        ).status
        == "warning"
    )
    assert (
        check(evaluate([rec("employee", "E", hire_date="2022-01-01")]), "employment_dates").status
        == "not_evaluated"
    )


# --- money --------------------------------------------------------------------------------------------------------
def test_earnings_equal_gross_passes_within_a_cent_and_warns_otherwise_never_fails():
    ok = rec("paycheck", "P1", gross_pay="100.00", earnings=[line("60.00"), line("40.004")])
    assert check(evaluate([ok]), "earnings_equal_gross").status == "passed"
    off = rec("paycheck", "P2", gross_pay="100.00", earnings=[line("99.50")])
    r = check(evaluate([ok, off]), "earnings_equal_gross")
    assert (r.status, r.flagged, r.evaluated) == (
        "warning",
        1,
        2,
    ) and "difference -0.50" in r.examples[0].detail
    assert all(c.status != "failed" for c in evaluate([ok, off]).checks)


def test_earnings_check_excludes_memo_lines_and_needs_every_amount():
    memo = rec("paycheck", "P1", gross_pay="100", earnings=[line("100"), line("5", is_memo=True)])
    assert check(evaluate([memo]), "earnings_equal_gross").status == "passed"
    partial = rec("paycheck", "P2", gross_pay="100", earnings=[line("100"), line(None)])
    r = check(evaluate([partial]), "earnings_equal_gross")
    assert r.status == "not_evaluated" and "no amount" in r.reason


def test_earnings_check_is_not_evaluated_when_gross_was_calculated_from_the_earnings():
    derived = rec(
        "paycheck",
        "P1",
        gross_pay="100",
        earnings=[line("100")],
        provenance={"derived_fields": ["gross_pay"]},
    )
    r = check(evaluate([derived]), "earnings_equal_gross")
    assert (
        r.status == "not_evaluated" and "prove nothing" in r.reason
    )  # a tautology is not evidence
    assert (
        check(
            evaluate([rec("paycheck", "P2", earnings=[line("1")])]), "earnings_equal_gross"
        ).status
        == "not_evaluated"
    )  # no gross


def test_payments_equal_net_statuses():
    ok = rec("paycheck", "P1", net_pay="70", payments=[line("50"), line("20")])
    assert check(evaluate([ok]), "payments_equal_net").status == "passed"
    off = rec("paycheck", "P2", net_pay="70", payments=[line("50")])
    r = check(evaluate([ok, off]), "payments_equal_net")
    assert (r.status, r.flagged) == ("warning", 1)
    for missing in (
        rec("paycheck", "P3", payments=[line("1")]),
        rec("paycheck", "P4", net_pay="1"),
        rec("paycheck", "P5", net_pay="1", payments=[line(None)]),
    ):
        assert check(evaluate([missing]), "payments_equal_net").status == "not_evaluated"


def test_the_unsupported_money_assumptions_are_not_implemented():
    """Gross - taxes - deductions = net and check amount = net gave false alarms on live data: never evaluated."""
    split = rec(
        "paycheck",
        "P",
        gross_pay="100",
        net_pay="60",
        check_amount="10",
        earnings=[line("100")],
        taxes=[{"amount": "5", "liability": "employee"}],
        deductions=[line("1")],
        payments=[line("50"), line("10")],
    )
    report = evaluate([split])
    bridge = check(report, "gross_to_net")
    assert (
        bridge.status == "not_evaluated"
        and "false alarms" in bridge.reason
        and bridge.evaluated == 0
        and bridge.examples == []
    )
    text = report.model_dump_json()
    assert "check_amount" not in text and "check amount" not in text.replace(
        "check amount = net pay", ""
    )
    assert all(
        c.status != "failed" for c in report.checks
    )  # a split paycheck with a small check is not a failure


def test_money_rules_run_only_for_providers_where_they_held_on_live_data_and_say_why_otherwise():
    for provider in ("isolved", "ukg-ready", None):
        p = rec(
            "paycheck",
            "P",
            provider=provider,
            gross_pay="100",
            net_pay="60",
            earnings=[line("99")],
            payments=[line("50")],
        )
        earn, pay = (
            check(evaluate([p]), "earnings_equal_gross"),
            check(evaluate([p]), "payments_equal_net"),
        )
        assert earn.status == pay.status == "not_evaluated" and earn.flagged == pay.flagged == 0
    iso = check(
        evaluate(
            [rec("paycheck", "P", provider="isolved", gross_pay="100", earnings=[line("99")])]
        ),
        "earnings_equal_gross",
    )
    assert "isolved" in iso.reason and "differed on every comparable check" in iso.reason
    assert (
        check(
            evaluate([rec("paycheck", "P", gross_pay="100", earnings=[line("99")])]),
            "earnings_equal_gross",
        ).status
        == "warning"
    )  # prismhr


# --- deposits -----------------------------------------------------------------------------------------------------
def dep(i, employee="E1", kind="percentage", pct=None, raw="P", **kw):
    return rec(
        "direct_deposit",
        i,
        employee_id=employee,
        allocation_type=kind,
        percentage=pct,
        allocation_raw=raw,
        **kw,
    )


def test_percentage_deposits_are_summed_per_employee_and_kind_active_only():
    assert (
        check(evaluate([dep("a", pct="60"), dep("b", pct="40")]), "deposit_percentages").status
        == "passed"
    )
    over = check(evaluate([dep("a", pct="60"), dep("b", pct="50")]), "deposit_percentages")
    assert (over.status, over.flagged) == ("warning", 1) and "110" in over.examples[0].detail
    # different kinds (PrismHR P vs PN) and different employees are NOT added together
    assert (
        check(
            evaluate(
                [
                    dep("a", pct="60", raw="P"),
                    dep("b", pct="60", raw="PN"),
                    dep("c", employee="E2", pct="60"),
                ]
            ),
            "deposit_percentages",
        ).status
        == "passed"
    )
    # inactive and prenote deposits are not allocations
    assert (
        check(
            evaluate(
                [
                    dep("a", pct="60"),
                    dep("b", pct="60", active=False),
                    dep("c", pct="60", status="prenote"),
                ]
            ),
            "deposit_percentages",
        ).status
        == "passed"
    )
    assert (
        check(evaluate([dep("a", pct="60"), dep("b", pct=None)]), "deposit_percentages").status
        == "not_evaluated"
    )
    assert (
        check(evaluate([dep("a", kind="fixed_amount")]), "deposit_percentages").status
        == "not_evaluated"
    )


def test_deposits_for_different_voucher_types_are_independent_allocations():
    """Live PrismHR: one employee has a remaining-balance deposit for regular (R) and one for supplemental (S) pay."""
    regular = dep("a", kind="remaining_balance", raw="B", voucher_types_raw="R")
    supplemental = dep("b", kind="remaining_balance", raw="B", voucher_types_raw="S")
    assert check(evaluate([regular, supplemental]), "deposit_remaining_balance").status == "passed"
    same = check(
        evaluate([regular, {**supplemental, "voucher_types_raw": "R"}]), "deposit_remaining_balance"
    )
    assert same.status == "warning" and "voucher type R" in same.examples[0].record
    pct = lambda i, v: dep(i, pct="70", voucher_types_raw=v)  # noqa: E731
    assert check(evaluate([pct("a", "R"), pct("b", "S")]), "deposit_percentages").status == "passed"
    assert (
        check(evaluate([pct("a", "R"), pct("b", "R")]), "deposit_percentages").status == "warning"
    )


def test_one_remaining_balance_deposit_per_employee():
    rb = lambda i, e="E1", **kw: dep(i, employee=e, kind="remaining_balance", **kw)  # noqa: E731
    assert (
        check(evaluate([rb("a"), dep("b", pct="10")]), "deposit_remaining_balance").status
        == "passed"
    )
    two = check(evaluate([rb("a"), rb("b")]), "deposit_remaining_balance")
    assert (two.status, two.flagged) == ("warning", 1)
    assert (
        check(evaluate([rb("a"), rb("b", e="E2")]), "deposit_remaining_balance").status == "passed"
    )
    assert (
        check(evaluate([rb("a"), rb("b", active=False)]), "deposit_remaining_balance").status
        == "passed"
    )
    assert check(evaluate([]), "deposit_remaining_balance").status == "not_evaluated"


# --- no false alarms on the repository's own data and on live-shaped data ---------------------------------------
def example_set(provider, folder, ctx):
    files = [FileIn(name=f.name, content=load(f)) for f in sorted((FIX / folder).glob("*.json"))]
    return convert_files(Provider(provider), files, {"tenant_id": "t", **ctx}, integrity=True)


EXAMPLE_SETS = [
    ("prismhr", "prismhr", {"company_id": "ACME", "employee_id": "E1001"}),
    ("isolved", "isolved", {"company_id": "4501", "employee_id": "90001"}),
    ("ukg-ready", "ukg_ready", {"company_id": "CID1", "employee_id": "7770001"}),
]


@pytest.mark.parametrize(
    ("provider", "folder", "ctx"), EXAMPLE_SETS, ids=[e[0] for e in EXAMPLE_SETS]
)
def test_a_provider_example_set_has_no_failed_check(provider, folder, ctx):
    report = example_set(provider, folder, ctx).integrity
    assert report is not None and report.summary["failed"] == 0
    assert report.summary["passed"] >= 5 and sum(report.summary.values()) == len(report.checks)


def test_real_findings_in_the_example_sets_are_reported_not_hidden():
    ukg = example_set("ukg-ready", "ukg_ready", EXAMPLE_SETS[2][2]).integrity
    links = check(ukg, "employee_links")
    assert (
        links.status == "warning" and "7770002" in links.examples[0].record
    )  # a statement names an unconverted employee
    assert (
        check(ukg, "earnings_equal_gross").status == "not_evaluated"
        and check(ukg, "payments_equal_net").status == "not_evaluated"
    )
    prism = example_set("prismhr", "prismhr", EXAMPLE_SETS[0][2]).integrity
    assert (
        check(prism, "paycheck_run_link").status,
        check(prism, "earnings_equal_gross").status,
    ) == ("passed", "passed")


@pytest.mark.parametrize("c", CASES, ids=lambda c: c.id)
def test_each_single_fixture_run_has_no_failed_check(c):
    out = convert_files(
        Provider(c.provider),
        [FileIn(name=c.file, content=load(FIX / c.folder / c.file))],
        {"tenant_id": "t", **c.context},
        integrity=True,
    )
    assert out.integrity.summary["failed"] == 0, [
        (k.id, k.reason) for k in out.integrity.checks if k.status == "failed"
    ]


def test_live_shaped_responses_do_not_produce_failures_or_money_assumptions():
    live = FIX / "live_shapes"
    out = convert_files(
        Provider.ISOLVED,
        [FileIn(name="c.json", content=load(live / "isolved" / "checks_paged.json"))],
        {"tenant_id": "t", "company_id": "4501"},
        integrity=True,
    )
    assert out.integrity.summary["failed"] == 0
    assert (
        check(out.integrity, "paycheck_run_link").status == "not_evaluated"
    )  # live checks name no run id
    prism = convert_files(
        Provider.PRISMHR,
        [FileIn(name="p.json", content=load(live / "prismhr" / "payroll_voucher_singular.json"))],
        {"tenant_id": "t", "company_id": "ACME"},
        integrity=True,
    )
    assert (
        prism.integrity.summary["failed"] == 0
        and check(prism.integrity, "gross_to_net").status == "not_evaluated"
    )


# --- privacy and API ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("provider", "folder", "ctx"), EXAMPLE_SETS, ids=[e[0] for e in EXAMPLE_SETS]
)
def test_the_report_contains_only_ids_dates_amounts_and_sentences(provider, folder, ctx):
    text = example_set(provider, folder, ctx).integrity.model_dump_json()
    for secret in SECRETS:
        assert secret not in text
    assert "account_number" not in text and "hash" not in text.lower() and "ssn" not in text.lower()
    assert set(json.loads(text)) == {"summary", "checks"}


def post(client, body):
    return client.post("/convert/prismhr", content=json.dumps(body, default=str))


def convert_body(**extra):
    files = [{"name": f.name, "content": load(f)} for f in sorted((FIX / "prismhr").glob("*.json"))]
    return {"files": files, "context": {"company_id": "ACME", "employee_id": "E1001"}, **extra}


def test_the_integrity_flag_is_opt_in_and_the_default_response_is_unchanged():
    client = TestClient(create_app())
    plain = post(client, convert_body()).json()
    assert "integrity" not in plain and set(plain) == {
        "provider",
        "files",
        "results",
        "suggestions",
    }
    on = post(client, convert_body(integrity=True)).json()
    assert on["integrity"]["summary"]["failed"] == 0 and len(on["integrity"]["checks"]) == 11
    for truthy_but_not_true in ("yes", 1, "true", None, [True]):
        assert "integrity" not in post(client, convert_body(integrity=truthy_but_not_true)).json()
    without_key = {k: v for k, v in on.items() if k != "integrity"}
    assert without_key == plain  # the flag adds a report and changes nothing else


def test_integrity_is_a_preview_and_works_together_with_trace():
    client = TestClient(create_app())
    both = post(client, convert_body(integrity=True, trace=True)).json()
    assert both["integrity"] and any(r["traces"] for r in both["results"])
    assert "stored" not in json.dumps(both)
