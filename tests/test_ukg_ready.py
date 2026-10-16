"""UKG Ready adapter: company EINs, employee bundle, direct deposit, deduction setup, payroll."""

from datetime import date
from decimal import Decimal

import pytest

from normalization import service
from normalization.canonical.enums import AllocationType, DepositAccountType, Gender, Liability

D = Decimal


def run(entity, payload, ctx):
    return service.normalize("ukg-ready", entity, payload, ctx)


class TestCompany:
    def test_company_holds_eins_as_legal_entities(self, fixture, ukg_ctx):
        result = run("company", fixture("ukg_ready", "eins.json"), ukg_ctx)
        (co,) = result.records
        assert co.company_id == "CID1" and co.source_id == "CID1"
        assert [
            (e.entity_id, e.name, e.tax_id, e.currency_code, e.country) for e in co.legal_entities
        ] == [
            ("12", "Northwind Services Inc", "55-1234567", "USD", "USA"),
            ("13", "Northwind Canada Ltd", "55-7654321", "CAD", "CAN"),
        ]

    def test_missing_company_master_is_reported_not_invented(self, fixture, ukg_ctx):
        result = run("company", fixture("ukg_ready", "eins.json"), ukg_ctx)
        (co,) = result.records
        assert co.legal_name is None and co.status is None and co.address is None
        assert any(i.code == "company_master_unavailable" for i in result.issues)

    def test_company_id_context_required(self, fixture):
        result = run("company", fixture("ukg_ready", "eins.json"), {"tenant_id": "t"})
        assert result.issues[0].code == "missing_context"


class TestEmployee:
    @pytest.fixture
    def emp(self, fixture, ukg_ctx):
        result = run("employee", fixture("ukg_ready", "employee_bundle.json"), ukg_ctx)
        (e,) = result.records
        return e

    def test_profile_demographics_and_pay_info_are_merged(self, emp):
        assert emp.source_id == "7770001" and emp.employee_number == "EMP-204"
        assert (emp.first_name, emp.middle_name, emp.last_name) == ("Maria", None, "Diaz")
        assert emp.preferred_name == "Mari" and emp.name_prefix == "Ms"
        assert emp.gender is Gender.FEMALE  # from the demographics endpoint
        assert emp.job_code == "55" and emp.employee_type_raw == "3"
        assert emp.manager_id == "7770000"

    def test_dates(self, emp):
        assert emp.birth_date == date(1985, 7, 30)
        assert (emp.hire_date, emp.rehire_date, emp.start_date) == (
            date(2018, 3, 12),
            date(2022, 8, 1),
            date(2018, 3, 19),
        )
        assert emp.termination_date is None and emp.original_hire_date is None

    def test_addresses_and_phones(self, emp):
        assert emp.address.line1 == "44 Lake Dr" and emp.address.country == "USA"
        assert emp.mailing_address.line1 == "PO Box 9"
        assert emp.mobile_phone == "2085550111" and emp.home_phone is None
        assert emp.email == "maria.diaz@northwind.example.test"

    def test_latest_compensation_wins_and_older_is_preserved(self, emp):
        c = emp.compensation
        assert c.hourly_rate == D("31.25") and c.annual_salary is None
        assert c.effective_date == date(2024, 7, 1) and c.currency == "USD"
        history = emp.extras["compensation_history"]
        assert any(h.get("amount") == D("52000.00") for h in history)

    def test_ssn_and_bank_number_are_not_kept(self, emp):
        dumped = emp.model_dump_json()
        assert emp.ssn_last4 == "3333" and "900-44-3333" not in dumped
        assert "$.employee.social_security" in emp.provenance.redacted_paths
        assert "$.employee.national_id_numbers[0].value" in emp.provenance.redacted_paths
        # pay_info.bank_account_number is a bank account: it must not leak through extras
        assert "000999888777" not in dumped
        assert "$.pay_info.bank_account_number" in emp.provenance.redacted_paths

    def test_cost_centers_and_unmapped_parts(self, emp):
        assert [(o.kind, o.code) for o in emp.org_assignments] == [
            ("cost_center_1", "301"),
            ("cost_center_2", "402"),
            ("pay_grade", "9"),
        ]
        assert emp.extras["demographics"]["citizenship"] == "US_CITIZEN"
        assert emp.extras["external_id"] == "HRIS-204"
        assert emp.secondary_email == "mdiaz@example.test"
        assert emp.employment_status is None and emp.employment_status_raw == "ACTIVE"

    def test_profile_only_response_without_bundle(self, fixture, ukg_ctx):
        profile = fixture("ukg_ready", "employee_bundle.json")["employee"]
        result = run("employee", {"employees": [profile]}, ukg_ctx)
        assert len(result.records) == 1 and result.records[0].compensation is None

    def test_employee_summary_list(self, ukg_ctx):
        payload = {
            "employees": [
                {
                    "id": 1,
                    "employee_id": "A1",
                    "first_name": "A",
                    "last_name": "B",
                    "dates": {"hired": "2020-01-01", "terminated": "2021-02-02"},
                    "status": "TERMINATED",
                }
            ]
        }
        (e,) = run("employee", payload, ukg_ctx).records
        assert e.termination_date == date(2021, 2, 2) and e.hire_date == date(2020, 1, 1)


class TestDirectDeposit:
    @pytest.fixture
    def deposits(self, fixture, ukg_ctx):
        return run("direct_deposit", fixture("ukg_ready", "direct_deposits.json"), ukg_ctx).records

    def test_calc_methods(self, deposits):
        flat, gross, full, reserve = deposits
        assert flat.allocation_type is AllocationType.FIXED_AMOUNT and flat.amount == D("250.00")
        assert gross.allocation_type is AllocationType.PERCENTAGE and gross.percentage == D("10")
        assert gross.percentage_basis_raw == "GROSS_EARNING"
        assert (
            full.allocation_type is None and full.allocation_raw == "FULL"
        )  # meaning undocumented
        assert reserve.instruction_type_raw == "RESERVE_LIVE_CHECK"
        assert reserve.percentage == D("0")  # explicit zero kept

    def test_accounts_dates_and_status(self, deposits):
        flat, gross, full, _ = deposits
        assert flat.account_type is DepositAccountType.CHECKING
        assert gross.account_type is DepositAccountType.SAVINGS
        assert flat.active is True and full.active is False
        assert flat.effective_from == date(2023, 1, 1) and flat.effective_to is None
        assert flat.prenote_status_raw == "COMPLETE_OK" and flat.frequency_raw == "EVERY_PAY"
        assert flat.sequence == 1 and flat.currency == "USD"
        assert flat.extras["year_to_date"] == D("1250.00")

    def test_short_account_numbers_are_not_revealed(self, deposits):
        assert deposits[0].account_number_masked == "****2233"
        assert deposits[2].account_number_masked == "****"  # "7788": too short to show any digits
        assert deposits[3].account_number_masked is None  # no account at all

    def test_account_numbers_never_leak(self, deposits):
        assert "4400112233" not in " ".join(d.model_dump_json() for d in deposits)

    def test_employee_comes_from_context_or_body(self, fixture, ukg_ctx):
        payload = fixture("ukg_ready", "direct_deposits.json")
        payload["deposits"][0]["employee"] = {"account_id": 999}
        recs = run("direct_deposit", payload, ukg_ctx).records
        assert recs[0].employee_id == "999" and recs[1].employee_id == "7770001"

    def test_missing_employee_is_an_error(self, fixture):
        result = run(
            "direct_deposit",
            fixture("ukg_ready", "direct_deposits.json"),
            {"tenant_id": "t", "company_id": "c"},
        )
        assert not result.ok and result.records == []


class TestDeductionSetup:
    def test_setup_fields(self, fixture, ukg_ctx):
        result = run("deduction", fixture("ukg_ready", "deductions.json"), ukg_ctx)
        med, ret = result.records
        assert (med.deduction_code, med.name, med.sequence) == ("MED", "Medical", 1)
        assert med.amount == D("92.5") and med.percentage is None
        assert med.calc_method_raw == "FLAT_AMOUNT"
        assert med.annual_max_amount == D("2400") and med.employer_annual_max_amount == D("4800")
        assert med.employee_frequency_raw == "EVERY_PAY"
        assert (med.payee_code, med.payee_name) == ("21", "Acme Health")
        assert med.start_date == date(2024, 1, 1) and med.stop_date is None
        assert med.memo == "Open enrollment 2024"
        assert med.extras["cost_centers"][0]["value"]["display_name"] == "Ops"
        assert ret.percentage == D("4") and ret.amount is None
        assert ret.calc_method_raw == "PERCENT_OF_GROSS"  # kept raw as given

    def test_needs_employee_context(self, fixture):
        result = run(
            "deduction",
            fixture("ukg_ready", "deductions.json"),
            {"tenant_id": "t", "company_id": "c"},
        )
        assert result.issues[0].code == "missing_context"


class TestPayroll:
    @pytest.fixture
    def records(self, fixture, ukg_ctx):
        return run("payroll", fixture("ukg_ready", "payroll.json"), ukg_ctx).records

    @pytest.fixture
    def check(self, records):
        return next(r for r in records if r.record_type == "paycheck")

    def test_run(self, records):
        run_rec = next(r for r in records if r.record_type == "payroll_run")
        assert run_rec.source_id == "880001" and run_rec.name == "Semi-monthly 2025-02-A"
        assert run_rec.pay_date == date(2025, 2, 20)
        assert (run_rec.period_start, run_rec.period_end) == (date(2025, 2, 1), date(2025, 2, 15))
        assert run_rec.direct_deposit_date == date(2025, 2, 19)
        assert run_rec.status_raw == "FINALIZED" and run_rec.ein_id == "12"
        assert run_rec.extras["date_finalized"] == "2025-02-18"

    def test_paycheck_links(self, check):
        assert check.run_id == "880001" and check.employee_id == "7770001"
        assert check.pay_date == date(2025, 2, 20) and check.period_start == date(2025, 2, 1)

    def test_earnings(self, check):
        reg, ot, hol = check.earnings
        assert (reg.code, reg.rate, reg.hours, reg.amount) == (
            "REG",
            D("31.25"),
            D("86"),
            D("2687.50"),
        )
        assert (ot.code, ot.rate, ot.amount) == ("OT", D("46.875"), D("187.50"))
        assert hol.amount == D("0") and hol.code == "HOL"  # falls back to external_id

    def test_tax_ee_and_er_amounts_become_separate_lines(self, check):
        got = [(t.code, t.liability, t.amount, t.taxable_wage) for t in check.taxes]
        assert got == [
            ("FICA-SS", Liability.EMPLOYEE, D("166.43"), D("2875.00")),
            ("FICA-MED", Liability.EMPLOYEE, D("40.19"), D("2875.00")),
            ("FICA-MED", Liability.EMPLOYER, D("40.19"), D("2875.00")),
            ("SUTA", Liability.EMPLOYER, D("17.25"), D("2875.00")),
        ]

    def test_employer_and_employee_deduction_amounts(self, check):
        med, ret = check.deductions
        assert (med.amount, med.employer_amount) == (D("92.50"), D("185.00"))
        assert (ret.amount, ret.employer_amount) == (D("0"), D("0"))

    def test_gross_is_derived_and_flagged_net_is_not_invented(self, check):
        assert check.gross_pay == D("2875.00")
        assert "gross_pay" in check.provenance.derived_fields
        assert check.net_pay is None and check.check_amount is None
        assert check.benefits == []  # UKG documents no benefit lines on pay statements

    def test_undocumented_sections_are_preserved(self, check):
        assert check.extras["messages"][0]["message"]["text"] == "Check totals reconciled"
        # `payments` is a documented array: its amount and type are typed lines now; the rest of each entry stays in extras
        assert [(p.amount, p.description) for p in check.payments] == [(D("2300.00"), "PAYMENT")]
        assert check.extras["payments"][0] == {"id": 31}
        assert (
            check.net_pay is None
        )  # the meaning of a payment is undocumented: no net pay is derived from it

    def test_statement_summary_without_lines(self, records):
        summary = next(r for r in records if r.source_id == "990002")
        assert summary.earnings == [] and summary.gross_pay is None  # nothing to derive from
        assert summary.status_raw == "CLOSED"

    def test_runs_only_listing(self, ukg_ctx):
        payload = {"payrolls": [{"id": 1, "pay_date": "2025-01-01", "status": "NEW"}]}
        (run_rec,) = run("payroll", payload, ukg_ctx).records
        assert run_rec.record_type == "payroll_run" and run_rec.status_raw == "NEW"

    def test_single_statement_uses_payroll_id_from_context(self, fixture, ukg_ctx):
        stmt = fixture("ukg_ready", "payroll.json")["pay_statements"][0]
        (p,) = run("payroll", stmt, {**ukg_ctx, "payroll_id": "880001"}).records
        assert p.run_id == "880001"
        result = run("payroll", stmt, ukg_ctx)
        assert any(i.code == "no_run_link" for i in result.issues)
