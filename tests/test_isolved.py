"""isolved adapter: legal company, employee, direct deposit, deduction setup, payroll."""

from datetime import date
from decimal import Decimal

import pytest

from normalization import service
from normalization.canonical.enums import AllocationType, DepositAccountType, Gender, Liability

D = Decimal


def run(entity, payload, ctx):
    return service.normalize("isolved", entity, payload, ctx)


class TestCompany:
    def test_legal_company_with_client_as_parent(self, fixture):
        result = run("company", fixture("isolved", "legals.json"), {"tenant_id": "tenant-isolved"})
        (co,) = result.records
        assert (co.company_id, co.source_id) == ("4501", "4501")  # legal id
        assert co.parent_company_id == "77" and co.parent_company_name == "Bluebird Holdings"
        assert co.legal_name == "Bluebird Foods LLC" and co.display_name == "Bluebird Cafe"
        assert co.federal_ein == "98-7654321" and co.phone == "2075550123"
        assert co.address.line1 == "9 Harbor Rd" and co.address.line2 is None
        assert co.status is None and co.status_raw == "Active"  # status values undocumented
        assert co.extras["payGroups"] == [{"id": 11, "name": "Weekly"}]
        assert "links" not in co.extras


class TestEmployee:
    @pytest.fixture
    def employees(self, fixture, isolved_ctx):
        result = run("employee", fixture("isolved", "employees.json"), isolved_ctx)
        return {e.source_id: e for e in result.records}

    def test_nested_name_address_and_contact_blocks(self, employees):
        e = employees["90001"]
        assert (e.first_name, e.middle_name, e.last_name) == ("Alex", None, "Okafor")
        assert e.preferred_name == "Lex" and e.name_prefix == "Mx"
        assert e.address.line1 == "5 Pine St" and e.address.postal_code == "04101"
        assert e.mailing_address.line1 == "PO Box 12"
        assert e.email == "alex@bluebird.example.test"
        assert e.personal_email == "alex.personal@example.test"
        assert e.home_phone == "2075550140" and e.work_phone is None  # "" -> null
        assert e.company_id == "4501" and e.employee_number == "0042"

    def test_datetimes_become_dates(self, employees):
        e = employees["90001"]
        assert e.birth_date == date(1991, 11, 2)
        assert (e.hire_date, e.original_hire_date, e.rehire_date) == (
            date(2023, 5, 15),
            date(2020, 8, 1),
            date(2023, 5, 15),
        )

    def test_free_text_values_are_not_given_invented_enums(self, employees):
        e = employees["90001"]
        assert e.gender is Gender.FEMALE  # "Female" is self-describing
        assert e.employment_status is None and e.employment_status_raw == "Active"
        assert e.compensation.pay_type is None and e.compensation.pay_type_raw == "Hourly"

    def test_unrecognised_gender_label_stays_raw(self, employees):
        e = employees["90002"]
        assert e.gender is None and e.gender_raw == "Non-binary"

    def test_compensation_fields(self, employees):
        c = employees["90001"].compensation
        assert (c.hourly_rate, c.pay_period_amount, c.annual_salary) == (
            D("19.25"),
            D("770.00"),
            D("40040.00"),
        )
        assert c.standard_hours_per_pay_period == D("40.00")
        assert c.effective_date == date(2024, 6, 1)

    def test_zero_and_null_pay_are_different(self, employees):
        c = employees["90002"].compensation
        assert c.hourly_rate == D("0")
        assert c.pay_period_amount is None and c.annual_salary is None

    def test_organizations_labors_and_manager(self, employees):
        e = employees["90001"]
        assert e.manager_id == "90000"
        kinds = {(o.kind, o.code, o.description) for o in e.org_assignments}
        assert ("Department", "FOH", "Front of House") in kinds
        assert ("Cost Center", "CC-10", "Cafe") in kinds

    def test_terminated_employee(self, employees):
        e = employees["90002"]
        assert e.termination_date == date(2024, 12, 20)
        assert e.termination_reason_raw == "Relocation"
        assert e.termination_type_raw == "Voluntary"

    def test_ssn_removed_everywhere(self, employees):
        e = employees["90001"]
        assert e.ssn_last4 == "4321" and "987-65-4321" not in e.model_dump_json()
        assert employees["90002"].ssn_last4 is None  # empty SSN

    def test_embedded_direct_deposits_are_not_reported_as_unmapped(self, employees):
        assert "directDeposits" not in employees["90001"].extras

    def test_requires_legal_id_context(self, fixture):
        result = run("employee", fixture("isolved", "employees.json"), {"tenant_id": "t"})
        assert result.issues[0].code == "missing_context"


class TestDirectDeposit:
    @pytest.fixture
    def deposits(self, fixture, isolved_ctx):
        ctx = {**isolved_ctx, "employee_id": "90001"}
        return run("direct_deposit", fixture("isolved", "direct_deposits.json"), ctx).records

    def test_amount_percent_and_net_pay_allocations(self, deposits):
        fixed, pct, net = deposits
        assert fixed.allocation_type is AllocationType.FIXED_AMOUNT and fixed.amount == D("100.00")
        assert pct.allocation_type is AllocationType.PERCENTAGE and pct.percentage == D("15.5")
        assert net.allocation_type is AllocationType.REMAINING_BALANCE  # sequenceNumber 0
        assert net.sequence == 0 and "allocation_type" in net.provenance.derived_fields

    def test_account_types_and_unknown_type(self, deposits):
        assert deposits[0].account_type is DepositAccountType.CHECKING
        assert deposits[1].account_type is DepositAccountType.SAVINGS
        assert deposits[2].account_type is None and deposits[2].account_type_raw == "Money Market"

    def test_status_and_frequency_stay_raw(self, deposits):
        assert deposits[2].status is None and deposits[2].status_raw == "Pending Prenote"
        assert deposits[0].frequency_raw == "Every Pay"

    def test_identity_and_masking(self, deposits):
        assert [d.source_id for d in deposits] == ["301", "302", "303"]
        assert {d.employee_id for d in deposits} == {"90001"}
        assert deposits[0].account_number_masked == "****1111"
        assert "5550001111" not in deposits[0].model_dump_json()
        assert deposits[0].routing_number == "011000015"

    def test_deposits_embedded_in_employee_response(self, fixture, isolved_ctx):
        result = run("direct_deposit", fixture("isolved", "employees.json"), isolved_ctx)
        (dep,) = result.records
        assert dep.employee_id == "90001" and dep.source_id == "301"
        assert not any(i.severity.value == "warning" for i in result.issues)

    def test_bare_deposit_list_needs_employee_context(self, fixture, isolved_ctx):
        result = run("direct_deposit", fixture("isolved", "direct_deposits.json"), isolved_ctx)
        assert result.issues[0].code == "missing_context"


class TestDeductionSetup:
    def test_setup_with_calc_params(self, fixture, isolved_ctx):
        result = run("deduction", fixture("isolved", "deductions.json"), isolved_ctx)
        k401, union = result.records
        assert k401.employee_id == "90001" and k401.deduction_code == "401K"
        assert k401.setup_kind == "configured" and k401.reference_number == "REF-1"
        assert [(p.type, p.value) for p in k401.calc_params] == [
            ("Percent", D("5.0")),
            ("MaxPerPay", D("0")),
        ]
        # paramType meanings are undocumented, so nothing is promoted to amount/percentage
        assert k401.amount is None and k401.percentage is None
        assert k401.start_date == date(2024, 1, 1) and k401.stop_date is None
        assert union.stop_date == date(2024, 12, 31) and union.calc_params == []


class TestPayroll:
    @pytest.fixture
    def records(self, fixture, isolved_ctx):
        return run("payroll", fixture("isolved", "payroll.json"), isolved_ctx).records

    @pytest.fixture
    def check(self, records):
        return next(r for r in records if r.record_type == "paycheck")

    def test_run_and_check_dates_are_separate(self, records, check):
        run_rec = next(r for r in records if r.record_type == "payroll_run")
        assert run_rec.source_id == "600123" and run_rec.run_number == 1
        assert run_rec.run_type_raw == "Regular" and run_rec.status_raw == "Completed"
        assert run_rec.pay_date == date(2025, 2, 7)
        assert (run_rec.period_start, run_rec.period_end) == (date(2025, 1, 27), date(2025, 2, 2))
        assert check.run_id == "600123"
        assert check.pay_date == date(2025, 2, 7) and check.period_end == date(2025, 2, 2)

    def test_totals(self, check):
        assert (check.gross_pay, check.net_pay, check.check_amount) == (
            D("770.00"),
            D("604.15"),
            D("604.15"),
        )
        assert check.check_number == "10442" and check.employee_id == "90001"

    def test_earnings_with_memo_and_accumulators(self, check):
        reg, tips, memo = check.earnings
        assert (reg.code, reg.hours, reg.amount) == ("REG", D("36.00"), D("693.00"))
        assert reg.accumulators == {"current": D("693.00"), "ytd": D("3465.00")}
        assert tips.hours == D("0") and tips.amount == D("77.00")
        assert memo.is_memo is True and reg.is_memo is False
        assert reg.extras["labors"][0]["laborCode"] == "CC-10"  # nested repeated record kept

    def test_taxes_split_employee_employer_unknown(self, check):
        by_code = {t.code: t for t in check.taxes}
        assert by_code["FIT"].liability is Liability.EMPLOYEE and by_code["FIT"].amount == D(
            "61.20"
        )
        assert by_code["FUTA"].liability is Liability.EMPLOYER and by_code["FUTA"].amount == D("0")
        assert by_code["XYZ"].liability is None  # source did not say who pays
        assert by_code["FIT"].taxable_wage == D("735.00")

    def test_deductions_garnishments_and_benefits(self, check):
        assert [(d.source_kind, d.code, d.amount) for d in check.deductions] == [
            ("deductions", "401K", D("38.50")),
            ("garnishments", "GARN1", D("18.41")),
        ]
        (b,) = check.benefits
        assert b.employee_contribution == D("38.50") and b.employer_contribution == D("23.10")
        assert b.extras["ytdEmployerContribution"] == D("115.50")

    def test_actual_deposits_are_payments_not_instructions(self, check):
        assert [(p.amount, p.account_masked) for p in check.payments] == [
            (D("100.00"), "****1111"),
            (D("504.15"), "****3333"),
        ]

    def test_employee_snapshot_is_withheld_and_listed_as_redacted(self, check):
        dumped = check.model_dump_json()
        assert "987-65-4321" not in dumped and "5 Pine St" not in dumped
        assert "Okafor" not in dumped
        assert "$.checks[0].employeeSSN" in check.provenance.redacted_paths

    def test_unmodelled_sections_are_kept(self, check):
        assert check.extras["accruals"][0]["accrualName"] == "PTO"
        assert check.extras["employeeFederalFilingStatus"] == "Single"

    def test_single_check_without_run(self, fixture, isolved_ctx):
        one = fixture("isolved", "payroll.json")["checks"][0]
        result = run("payroll", one, isolved_ctx)
        (p,) = result.records
        assert p.run_id is None and p.source_id == "99000111"
