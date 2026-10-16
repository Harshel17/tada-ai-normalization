"""PrismHR adapter: company, employee, direct deposit, deduction setup, payroll."""

from datetime import date
from decimal import Decimal

import pytest

from normalization import service
from normalization.canonical.enums import (
    AllocationType,
    CompanyStatus,
    DeductionStatus,
    DepositAccountType,
    DepositStatus,
    EmploymentStatus,
    Gender,
    Liability,
    PaycheckType,
    PayType,
)

D = Decimal


def run(entity, payload, ctx):
    return service.normalize("prismhr", entity, payload, ctx)


@pytest.fixture
def employees(fixture, prism_ctx):
    result = run("employee", fixture("prismhr", "employee_get_employee.json"), prism_ctx)
    return {e.source_id: e for e in result.records}


class TestCompany:
    def test_maps_documented_fields(self, fixture, prism_ctx):
        result = run("company", fixture("prismhr", "client_master.json"), prism_ctx)
        (co,) = result.records
        assert (co.company_id, co.source_id, co.tenant_id) == ("ACME", "ACME", "tenant-prism")
        assert co.legal_name == "Acme Widgets, Inc." and co.display_name == "Acme Widgets"
        assert co.status is CompanyStatus.ACTIVE and co.status_raw == "ACT"
        assert co.service_type_raw == "PEO"
        assert co.federal_ein == "12-3456789"
        assert co.first_payroll_date == date(2020, 2, 7)
        assert co.address.postal_code == "78701-4321" and co.address.line2 is None  # "" -> null

    def test_unmapped_source_fields_are_preserved(self, fixture, prism_ctx):
        (co,) = run("company", fixture("prismhr", "client_master.json"), prism_ctx).records
        assert co.extras["cafeteriaPlan"] == "SEC125"
        assert co.extras["payCode"] == [{"payCodeId": "REG", "printOnPayStub": "1"}]
        assert "legalName" not in co.extras  # mapped fields are not duplicated

    def test_client_id_falls_back_to_context(self, prism_ctx):
        (co,) = run("company", {"clientMaster": {"legalName": "X"}}, prism_ctx).records
        assert co.source_id == "ACME"


class TestEmployee:
    def test_core_mapping(self, employees):
        e = employees["E1001"]
        assert (e.first_name, e.middle_name, e.last_name) == ("Jordan", "Q", "Rivera")
        assert e.employee_number == "1001"
        assert e.birth_date == date(1988, 4, 17)
        assert e.gender is Gender.FEMALE and e.gender_raw == "F"
        assert e.email == "jordan.rivera@example.test"
        assert e.work_email == "jrivera@acme.example.test"
        assert e.address.postal_code == "78701-1234" and e.address.county == "Travis"
        assert e.employment_status is EmploymentStatus.ACTIVE
        assert e.employment_status_raw == "ACT"  # client-defined status id is kept apart
        assert e.hire_date == date(2021, 3, 1) and e.original_hire_date == date(2019, 6, 3)
        assert e.manager_id == "E0900" and e.department_code == "PLAT"
        assert e.company_id == "ACME"

    def test_compensation_is_setup_not_paycheck_pay(self, employees):
        comp = employees["E1001"].compensation
        assert comp.pay_type is PayType.SALARY
        assert comp.pay_period_amount == D("3846.15")
        assert comp.hourly_rate == D("0")  # zero is kept, not turned into null
        assert str(comp.annual_salary) == "100000.00"
        assert str(comp.standard_hours_per_pay_period) == "80.00"
        assert comp.effective_date == date(2024, 1, 1)

    def test_ssn_is_reduced_to_last_four_and_redacted(self, employees):
        e = employees["E1001"]
        assert e.ssn_last4 == "6789"
        assert "123-45-6789" not in e.model_dump_json()
        assert e.provenance.redacted_paths == ["$.employee[0].compensation.ssn"]

    def test_unknown_fields_survive_in_extras(self, employees):
        extras = employees["E1001"].extras
        assert extras["person"]["personChecksum"] == "chk-person-1"
        assert extras["client"]["clientChecksum"] == "chk-client-1"
        assert extras["person"]["ethnicCode"] == "2"  # protected demographics stay in extras
        # fields promoted to canonical attributes are not duplicated in extras
        assert "fedFilingStatus" not in extras.get("compensation", {})
        assert "benefitGroup" not in extras["client"]
        # sections owned by other entity types are not reported as unmapped, but the
        # employee-level switches next to the account rows are kept
        assert extras["directDeposit"] == {"achStatus": "A", "voucherType": "RSV"}
        assert "scheduledDeduction" not in extras

    def test_terminated_employee_with_null_and_empty_fields(self, employees):
        e = employees["E1002"]
        assert e.employment_status is EmploymentStatus.TERMINATED
        assert e.birth_date is None and e.email is None
        assert e.termination_reason_raw == "RESIGN"
        assert e.flsa_exempt is False  # False is not null
        assert e.compensation.pay_type is PayType.HOURLY
        assert e.compensation.hourly_rate == D("22.75") and e.compensation.pay_period_amount == 0

    def test_undocumented_gender_code_stays_raw(self, fixture, prism_ctx):
        result = run("employee", fixture("prismhr", "employee_get_employee.json"), prism_ctx)
        casey = next(r for r in result.records if r.source_id == "E1002")
        assert casey.gender is None and casey.gender_raw == "X"
        assert any(i.code == "unmapped_code" for i in result.issues)

    def test_company_context_is_required(self, fixture):
        result = run(
            "employee", fixture("prismhr", "employee_get_employee.json"), {"tenant_id": "t"}
        )
        assert not result.ok and result.issues[0].code == "missing_context"
        assert result.records == []


class TestDirectDeposit:
    @pytest.fixture
    def deposits(self, fixture, prism_ctx):
        result = run("direct_deposit", fixture("prismhr", "employee_get_employee.json"), prism_ctx)
        return result.records

    def test_multiple_allocations_for_one_employee(self, deposits):
        assert len(deposits) == 3
        assert {d.employee_id for d in deposits} == {"E1001"}  # E1002 has an empty list
        fixed, pct, rest = deposits
        assert fixed.allocation_type is AllocationType.FIXED_AMOUNT and fixed.amount == D("500.00")
        assert fixed.account_type is DepositAccountType.CHECKING
        assert pct.allocation_type is AllocationType.PERCENTAGE
        assert pct.percentage == D("25.5") and pct.amount is None and pct.cap_amount == D("1000.00")
        assert rest.allocation_type is AllocationType.REMAINING_BALANCE
        assert rest.amount is None and rest.percentage is None

    def test_status_and_voucher_types(self, deposits):
        assert all(d.status is DepositStatus.ACTIVE and d.active is True for d in deposits)
        assert deposits[0].voucher_types_raw == "RSV"  # global voucherType
        assert deposits[2].voucher_types_raw == "R"  # per-account override wins

    def test_account_number_is_never_stored(self, deposits):
        dumped = " ".join(d.model_dump_json() for d in deposits)
        assert "000123456789" not in dumped and "998877665544" not in dumped
        assert deposits[0].account_number_masked == "****6789"
        assert deposits[0].routing_number == "111000025"
        assert deposits[0].provenance.redacted_paths

    def test_same_account_with_different_rules_gets_distinct_ids(self, deposits):
        assert len({d.source_id for d in deposits}) == 3

    def test_employee_level_ach_switch_is_kept(self, deposits):
        assert deposits[0].extras["directDeposit.achStatus"] == "A"

    def test_unknown_method_is_not_guessed(self, fixture, prism_ctx):
        payload = fixture("prismhr", "employee_get_employee.json")
        payload["employee"][0]["directDeposit"]["achVoucher"][0]["method"] = "ZZ"
        result = run("direct_deposit", payload, prism_ctx)
        first = result.records[0]
        assert first.allocation_type is None and first.allocation_raw == "ZZ"
        assert first.amount is None  # not guessed to be a fixed amount


class TestDeductionSetup:
    def test_recurring_deductions_percent_vs_amount(self, fixture, prism_ctx):
        ctx = {**prism_ctx, "employee_id": "E1001"}
        result = run("deduction", fixture("prismhr", "deductions_get_deductions.json"), ctx)
        k401, garn, gym = result.records
        assert k401.setup_kind == "recurring" and k401.employee_id == "E1001"
        assert k401.percentage == D("6.00") and k401.amount is None  # usePercent=true
        assert k401.category_raw == "K"  # kept raw: PrismHR lets clients define these
        assert k401.max_amount == D("23000.00") and k401.max_basis_raw == "A"
        assert k401.per_period_limit == D("500.00")
        assert k401.employer_match_method_raw == "P" and k401.employer_match_amount == D("3.00")
        assert k401.stop_date is None  # "" -> null
        assert garn.amount == D("150.25") and garn.percentage is None
        assert garn.loan_or_garnishment_id == "CS-77" and garn.payee_code == "COURT1"
        assert garn.tracks_arrears is True and garn.mandatory is True
        assert garn.extras["glOverrideAcct"] == "2100"
        assert gym.amount == D("0")

    def test_recurring_requires_employee_id(self, fixture, prism_ctx):
        result = run("deduction", fixture("prismhr", "deductions_get_deductions.json"), prism_ctx)
        assert result.issues[0].code == "missing_context"

    def test_scheduled_deductions_from_employee(self, fixture, prism_ctx):
        result = run("deduction", fixture("prismhr", "employee_get_employee.json"), prism_ctx)
        park, adv = result.records
        assert park.setup_kind == "scheduled" and park.employee_id == "E1001"
        assert park.status is DeductionStatus.ACTIVE and park.name == "Parking"
        assert park.amount == D("25.50") and park.start_date == date(2024, 2, 1)
        assert park.stop_date is None and park.payee_code == "CITY"
        assert adv.status is DeductionStatus.ONE_TIME and adv.amount == D("0")

    def test_setup_is_not_a_paycheck_line(self, fixture, prism_ctx):
        result = run("deduction", fixture("prismhr", "employee_get_employee.json"), prism_ctx)
        assert {r.record_type for r in result.records} == {"deduction"}


class TestPayroll:
    @pytest.fixture
    def result(self, fixture, prism_ctx):
        return run("payroll", fixture("prismhr", "payroll_vouchers.json"), prism_ctx)

    @pytest.fixture
    def checks(self, result):
        return {p.source_id: p for p in result.records if p.record_type == "paycheck"}

    def test_run_is_derived_from_batch_and_flagged(self, result):
        (payroll_run,) = [r for r in result.records if r.record_type == "payroll_run"]
        assert payroll_run.source_id == "B-2025-02"
        assert payroll_run.pay_date == date(2025, 1, 17)
        assert (payroll_run.period_start, payroll_run.period_end) == (
            date(2025, 1, 4),
            date(2025, 1, 17),
        )
        assert "pay_date" in payroll_run.provenance.derived_fields
        assert any(i.code == "runs_derived" for i in result.issues)

    def test_totals_and_decimal_precision(self, checks):
        p = checks["V-5001"]
        assert p.paycheck_type is PaycheckType.REGULAR and p.paycheck_type_raw == "R"
        assert (p.gross_pay, p.net_pay, p.check_amount) == (
            D("3846.15"),
            D("2410.37"),
            D("2410.37"),
        )
        assert p.pay_date == date(2025, 1, 17) and p.period_start == date(2025, 1, 4)
        assert p.run_id == "B-2025-02" and p.employee_id == "E1001"
        assert p.earnings[0].rate == D("48.0769")  # 4 decimals preserved
        assert str(p.earnings[0].hours) == "80.00"

    def test_earnings_taxes_deductions_benefits(self, checks):
        p = checks["V-5001"]
        assert [(e.code, e.amount) for e in p.earnings] == [("REG", D("3846.15"))]
        ee = [t for t in p.taxes if t.liability is Liability.EMPLOYEE]
        er = [t for t in p.taxes if t.liability is Liability.EMPLOYER]
        assert [(t.code, t.amount) for t in ee] == [
            ("FIT", D("512.30")),
            ("FICA", D("238.46")),
            ("MED", D("55.77")),
        ]
        assert [(t.code, t.amount, t.rate) for t in er] == [("FICA", D("238.46"), D("0.062"))]
        assert [(d.code, d.amount, d.category_raw) for d in p.deductions[:2]] == [
            ("401K", D("230.77"), "K"),
            ("MED125", D("98.50"), "C"),
        ]
        by_kind = {(b.source_kind, b.code): b for b in p.benefits}
        assert by_kind[("benefit", None)].employee_contribution == D("98.50")
        assert by_kind[("employerContributions", None)].employer_contribution == D("421.50")
        assert by_kind[("retirement", "employerMatchingContributions")].employer_contribution == D(
            "115.38"
        )

    def test_zero_amounts_are_kept(self, checks):
        p = checks["V-5001"]
        zero_other = next(d for d in p.deductions if d.source_kind == "otherDeduction")
        assert zero_other.amount == D("0")
        roth = next(b for b in p.benefits if b.code == "rothContributionsPostTax")
        assert roth.employee_contribution == D("0")
        assert checks["V-5002"].net_pay == D("0")

    def test_other_earnings_become_earning_lines(self, checks):
        p = checks["V-5002"]
        assert p.paycheck_type is PaycheckType.SUPPLEMENTAL
        assert [(e.code, e.source_kind, e.amount) for e in p.earnings] == [
            ("REG", "earning", D("450.00")),
            ("BONUS", "otherEarningsDetail", D("50.00")),
        ]

    def test_actual_payments_are_masked(self, checks):
        pay = checks["V-5001"].payments
        assert [(x.amount, x.account_masked) for x in pay] == [
            (D("500.00"), "****6789"),
            (D("1910.37"), "****5544"),
        ]
        assert "000123456789" not in checks["V-5001"].model_dump_json()

    def test_pay_date_and_pay_period_are_separate_fields(self, checks):
        p = checks["V-5001"]
        # fixture: the period ended on the pay date; they are still distinct source fields
        assert (p.pay_date, p.period_start, p.period_end) == (
            date(2025, 1, 17),
            date(2025, 1, 4),
            date(2025, 1, 17),
        )

    def test_unmodelled_voucher_sections_are_preserved(self, checks):
        extras = checks["V-5001"].extras
        assert extras["glAcctDistribution"]["glTotalDebit"] == D("3846.15")
        assert extras["wcInformation"] == {"wcPolicy": "WC-1"}

    def test_earnings_that_do_not_add_up_are_flagged(self, fixture, prism_ctx):
        payload = fixture("prismhr", "payroll_vouchers.json")
        payload["payrollVouchers"][0]["earnAmount"] = D("1.00")
        result = run("payroll", payload, prism_ctx)
        assert any(i.code == "earnings_total_mismatch" for i in result.issues)

    def test_voucher_without_ids_is_an_error_not_a_crash(self, prism_ctx):
        result = run("payroll", {"payrollVouchers": [{"netPay": 1}]}, prism_ctx)
        assert not result.ok and result.records == []
