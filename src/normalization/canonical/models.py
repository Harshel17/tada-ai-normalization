"""Provider-independent canonical records.

Every record carries identity (provider + tenant + company + source id), the unmapped source
fields (`extras`) and provenance. Money is Decimal. Dates are date; instants are datetime.
Fields are nullable when a provider may not expose them: null means "not provided", never zero.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from .enums import (
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
    Provider,
    RatePeriod,
    Severity,
)


def _plain(value: Decimal) -> str:
    """Plain decimal text, never exponent notation (1E+3 -> "1000", 1E-7 -> "0.0000001").

    Exact: digits and scale are the value's own (100.00 keeps its zeros); nothing is rounded.
    """
    return format(value, "f")


# Every canonical amount, rate, percentage and hours value (exact Decimal; plain text in JSON).
Dec = Annotated[Decimal, PlainSerializer(_plain, return_type=str, when_used="json")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Provenance(Strict):
    """Where a record came from inside the provider payload."""

    source_endpoint: str | None = None  # documented endpoint, e.g. "GET /employee/v1/getEmployee"
    source_path: str | None = None  # JSON path of the source object inside the supplied payload
    redacted_paths: list[str] = Field(
        default_factory=list
    )  # sensitive fields deliberately not kept
    derived_fields: list[str] = Field(default_factory=list)  # canonical fields computed by us


class Address(Strict):
    line1: str | None = None
    line2: str | None = None
    line3: str | None = None
    house_number: str | None = None  # separate from line1 where the provider splits it (UKG)
    city: str | None = None
    county: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


class CanonicalRecord(Strict):
    provider: Provider
    tenant_id: str  # provider account / environment, supplied by the caller (never in payloads)
    company_id: str | None = None  # PrismHR clientId, isolved legalId, UKG cid
    source_id: str  # provider-native id (or a documented natural key when none exists)
    extras: dict[str, Any] = Field(default_factory=dict)  # source fields we did not map
    provenance: Provenance = Field(default_factory=Provenance)


class LegalEntity(Strict):
    entity_id: str
    name: str | None = None
    tax_id: str | None = None
    currency_code: str | None = None
    country: str | None = None


class Company(CanonicalRecord):
    record_type: Literal["company"] = "company"
    parent_company_id: str | None = None  # isolved client id that owns this legal company
    parent_company_name: str | None = None
    legal_name: str | None = None
    display_name: str | None = None  # DBA / client name
    status: CompanyStatus | None = None
    status_raw: str | None = None
    status_date: date | None = None
    service_type_raw: str | None = None
    federal_ein: str | None = None
    naics_code: str | None = None
    sic_code: str | None = None
    address: Address | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    first_payroll_date: date | None = None
    legal_entities: list[LegalEntity] = Field(default_factory=list)


class OrgAssignment(Strict):
    """A cost-center / organization / labor assignment we cannot place in a fixed column."""

    kind: str
    code: str | None = None
    description: str | None = None


class EmergencyContact(Strict):
    name: str | None = None
    relationship: str | None = None
    contact_type_raw: str | None = None  # PHONE, EMAIL, TEXT, ADDRESS, OTHER (PrismHR)
    contact_info: str | None = None


class LaborAllocation(Strict):
    """How an employee's labor cost is split across locations / departments / jobs (PrismHR)."""

    percent: Dec | None = None
    location_code: str | None = None
    division_code: str | None = None
    department_code: str | None = None
    job_code: str | None = None
    project_code: str | None = None


class AlternateRate(Strict):
    """A named extra pay rate (isolved payRates, PrismHR payRate2..4)."""

    name: str
    amount: Dec | None = None


class TaxWithholding(Strict):
    """An employee's withholding election for one tax jurisdiction (setup, not tax actually paid).

    PrismHR: federal fields on `compensation`, `stateTax[]`, `localTax[]`.
    isolved: `taxes.federal / residentState / nonResidentState / residentCity / nonResidentCity`.
    UKG Ready documents tax info on a separate endpoint that this project does not read.
    """

    jurisdiction: Literal["federal", "state", "local"]
    residency: Literal["resident", "non_resident"] | None = None  # only where the source says
    area: str | None = None  # state code, or locality authority id
    description: str | None = None
    tax_category_id: str | None = None
    filing_status_raw: str | None = None
    primary_allowances: int | None = None
    secondary_allowances: int | None = None
    dependents_amount: Dec | None = None
    other_income_amount: Dec | None = None
    deductions_amount: Dec | None = None
    exemption_amount: Dec | None = None
    supplemental_exemption_amount: Dec | None = None
    additional_amount: Dec | None = None
    additional_percent: Dec | None = None
    override_type_raw: str | None = None  # PrismHR A/F/B/P
    override_amount: Dec | None = None
    alternate_calculation_raw: str | None = None
    multiple_jobs: bool | None = None
    withholding_blocked: bool | None = None
    certificate_filed: bool | None = None  # non-resident / local certificate on file
    form_filed: bool | None = None
    form_year: str | None = None


class CafeteriaContribution(Strict):
    amount: Dec | None = None
    effective_date: date | None = None


class BenefitProfile(Strict):
    """Benefit set-up and eligibility for an employee (not premiums or deductions on a paycheck)."""

    benefit_group: str | None = None
    retirement_benefit_group: str | None = None
    medical_eligibility_raw: str | None = None
    dental_eligibility_raw: str | None = None
    dependent_benefits_eligibility_raw: str | None = None
    auto_enrollment_eligibility_raw: str | None = None
    enrollment_completed: bool | None = None
    enrollment_in_progress: bool | None = None
    enrollment_type_raw: str | None = None
    cafeteria_contributions: list[CafeteriaContribution] = Field(default_factory=list)
    benefit_salary: Dec | None = None
    court_ordered_medical: bool | None = None
    paid_through_date: date | None = (
        None  # benefit contributions have been deducted from pay up to this date
    )


class Compensation(Strict):
    """Pay *setup* for an employee. Not what any paycheck actually paid."""

    pay_type: PayType | None = None
    pay_type_raw: str | None = None
    hourly_rate: Dec | None = None
    pay_period_amount: Dec | None = None  # salary for one pay period
    annual_salary: Dec | None = None
    other_rate: Dec | None = None  # a rate the source gives in a unit not covered above
    other_rate_period: RatePeriod | None = None
    standard_hours_per_pay_period: Dec | None = None
    standard_hours_per_day: Dec | None = None
    pay_group_raw: str | None = None
    pay_frequency_raw: str | None = None
    periods_per_year: int | None = None
    effective_date: date | None = None
    last_change_date: date | None = None
    last_change_amount: Dec | None = None
    last_change_percent: Dec | None = None
    per_diem_amount: Dec | None = None
    alternate_rates: list[AlternateRate] = Field(default_factory=list)
    currency: str | None = None
    standard_hours_per_week: Dec | None = None
    last_pay_date: date | None = None  # the most recent day the employee received pay
    next_review_date: date | None = None  # next compensation review
    compa_ratio: Dec | None = (
        None  # the provider's "comparative ratio" of pay to its range, as given
    )
    last_performance_review_date: date | None = None
    last_performance_review_rating_raw: str | None = (
        None  # a rating scale is tenant data: kept as given
    )
    last_performance_review_job_title: str | None = None  # the job title held at that review
    next_performance_review_date: date | None = None


class Employee(CanonicalRecord):
    record_type: Literal["employee"] = "employee"
    employee_number: str | None = None
    username: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    preferred_name: str | None = None
    name_prefix: str | None = None
    name_suffix: str | None = None
    preferred_pronouns: str | None = None
    preferred_language: str | None = None
    birth_date: date | None = None
    deceased_date: date | None = None
    gender: Gender | None = None
    gender_raw: str | None = None
    marital_status_raw: str | None = None
    ssn_last4: str | None = None  # full SSN is never kept
    email: str | None = None
    work_email: str | None = None
    personal_email: str | None = None
    secondary_email: str | None = None
    home_phone: str | None = None
    mobile_phone: str | None = None
    work_phone: str | None = None
    work_phone_extension: str | None = None  # the extension at the work number
    fax: str | None = None
    preferred_phone: str | None = None  # home | mobile | work | none (UKG preferred_phone)
    emergency_contacts: list[EmergencyContact] = Field(default_factory=list)
    address: Address | None = None
    mailing_address: Address | None = None
    tax_form_address: Address | None = None  # address for W-2 / tax forms, where it differs
    tax_geo_code: str | None = None
    school_district_code: str | None = None

    employment_status: EmploymentStatus | None = None
    employment_status_raw: str | None = None
    aca_employment_status_raw: str | None = None  # the provider's ACA employment status, as given
    status_date: date | None = None
    start_date: date | None = None
    hire_date: date | None = None
    original_hire_date: date | None = None
    rehire_date: date | None = None
    termination_date: date | None = None
    termination_reason_raw: str | None = None
    termination_type_raw: str | None = None
    termination_explanation: str | None = None
    last_worked_date: date | None = None
    leave_return_date: date | None = None
    eligible_for_rehire: bool | None = None
    seniority_date: date | None = None
    peo_start_date: date | None = None
    retirement_date: date | None = None
    job_code: str | None = None
    job_title: str | None = None
    job_group: str | None = None
    job_effective_date: date | None = None
    soc_code: str | None = None
    eeo_job_category_raw: str | None = None
    employee_type_raw: str | None = None
    employee_type_effective_date: date | None = (
        None  # date the employment type / category took effect
    )
    full_time_equivalent: Dec | None = None
    flsa_exempt: bool | None = None
    is_contractor: bool | None = None  # PrismHR employee1099
    is_officer: bool | None = None
    is_business_owner: bool | None = None
    is_manager: bool | None = None
    is_supervisor: bool | None = None
    is_remote_worker: bool | None = None
    is_seasonal: bool | None = None
    is_union_member: bool | None = None
    union_code_raw: str | None = None
    union_start_date: date | None = None  # date the employee started with the labor union
    workers_comp_code: str | None = None
    timeclock_id: str | None = None
    manager_id: str | None = None
    supervisor_id: str | None = None
    work_location_code: str | None = None
    division_code: str | None = None
    department_code: str | None = None
    org_assignments: list[OrgAssignment] = Field(default_factory=list)
    labor_allocations: list[LaborAllocation] = Field(default_factory=list)
    compensation: Compensation | None = None
    tax_withholdings: list[TaxWithholding] = Field(default_factory=list)
    fica_exempt: bool | None = None
    non_resident_alien: bool | None = None
    benefits: BenefitProfile | None = None


class DirectDepositInstruction(CanonicalRecord):
    """Standing instruction for splitting pay. Not a payment that happened."""

    record_type: Literal["direct_deposit"] = "direct_deposit"
    employee_id: str
    name: str | None = None
    instruction_type_raw: str | None = None  # e.g. UKG RESERVE_LIVE_CHECK vs DIRECT_DEPOSIT
    sequence: int | None = None  # the provider's own ordering number, when it has one
    source_position: int | None = None  # 0-based position in the provider's list
    account_type: DepositAccountType | None = None
    account_type_raw: str | None = None
    routing_number: str | None = None
    account_number_masked: str | None = None  # e.g. "****1234"
    # Salted hash that recognises the same account without storing the number. INTERNAL: stored in
    # the database, but excluded from every serialization (API, CLI, repr, traces).
    account_number_hash: str | None = Field(default=None, exclude=True, repr=False)
    bank_name: str | None = None
    allocation_type: AllocationType | None = None
    allocation_raw: str | None = None
    amount: Dec | None = None
    percentage: Dec | None = None
    percentage_basis_raw: str | None = None
    cap_amount: Dec | None = None
    skip_if_insufficient: bool | None = None
    status: DepositStatus | None = None
    status_raw: str | None = None
    active: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    prenote_status_raw: str | None = None
    frequency_raw: str | None = None
    voucher_types_raw: str | None = None
    currency: str | None = None


class CalcParam(Strict):
    type: str | None = None
    value: Dec | None = None


class DeductionSetup(CanonicalRecord):
    """A deduction an employee is *configured* to have. Not a deduction taken on a paycheck."""

    record_type: Literal["deduction"] = "deduction"
    employee_id: str
    setup_kind: Literal["recurring", "scheduled", "configured"]
    deduction_code: str | None = None
    name: str | None = None
    category_raw: str | None = None
    status: DeductionStatus | None = None
    status_raw: str | None = None
    sequence: int | None = None
    calc_method_raw: str | None = None
    amount: Dec | None = None  # fixed amount per deduction
    percentage: Dec | None = None  # percent, when the source says the value is a percent
    calc_params: list[CalcParam] = Field(default_factory=list)
    per_period_limit: Dec | None = None
    max_amount: Dec | None = None
    max_basis_raw: str | None = None
    annual_max_amount: Dec | None = None
    employer_annual_max_amount: Dec | None = None
    employee_frequency_raw: str | None = None
    employer_frequency_raw: str | None = None
    employer_match_method_raw: str | None = None
    employer_match_amount: Dec | None = None
    start_date: date | None = None
    stop_date: date | None = None
    loan_or_garnishment_id: str | None = None
    benefit_plan_id: str | None = None
    payee_code: str | None = None
    payee_name: str | None = None
    reference_number: str | None = None
    memo: str | None = None
    tracks_arrears: bool | None = None
    mandatory: bool | None = None


Accumulators = dict[str, Dec]  # e.g. {"mtd": 10.00, "ytd": 120.00}; keys are period labels


class LineBase(Strict):
    line_no: int  # position within its category on the source paycheck
    source_kind: str | None = None  # which source array produced it, e.g. "otherDeduction"
    code: str | None = None
    description: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)


class EarningLine(LineBase):
    hours: Dec | None = None
    units: Dec | None = None
    rate: Dec | None = None
    amount: Dec | None = None
    is_memo: bool | None = None
    work_date_from: date | None = None
    work_date_to: date | None = None
    accumulators: Accumulators = Field(default_factory=dict)


class TaxLine(LineBase):
    liability: Liability | None = None  # who bears the tax; null when the source does not say
    amount: Dec | None = None
    taxable_wage: Dec | None = None
    rate: Dec | None = None
    accumulators: Accumulators = Field(default_factory=dict)


class DeductionLine(LineBase):
    """A deduction actually taken on this paycheck."""

    category_raw: str | None = None
    amount: Dec | None = None  # employee side
    employer_amount: Dec | None = None
    plan_id: str | None = None
    accumulators: Accumulators = Field(default_factory=dict)


class BenefitContributionLine(LineBase):
    plan_id: str | None = None
    employee_contribution: Dec | None = None
    employee_deducted_amount: Dec | None = None  # what actually came out of the paycheck
    employer_contribution: Dec | None = None
    premium_amount: Dec | None = None
    accumulators: Accumulators = Field(default_factory=dict)


class PaycheckPayment(Strict):
    """Money actually sent (e.g. an ACH on this check). Account is masked."""

    line_no: int
    amount: Dec | None = None
    account_masked: str | None = None
    routing_number: str | None = None
    status_raw: str | None = None
    description: str | None = None


class PayrollRun(CanonicalRecord):
    record_type: Literal["payroll_run"] = "payroll_run"
    name: str | None = None
    pay_date: date | None = None  # when employees are paid
    period_start: date | None = None  # the work period the pay covers; distinct from pay_date
    period_end: date | None = None
    scheduled_pay_date: date | None = None
    direct_deposit_date: date | None = None
    run_type_raw: str | None = None
    status_raw: str | None = None
    run_number: int | None = None
    ein_id: str | None = None


class Paycheck(CanonicalRecord):
    record_type: Literal["paycheck"] = "paycheck"
    run_id: str | None = None  # PayrollRun.source_id when the source tells us
    employee_id: str
    check_number: str | None = None
    paycheck_type: PaycheckType | None = None
    paycheck_type_raw: str | None = None
    status_raw: str | None = None
    pay_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None
    void_date: date | None = None
    pay_group_raw: str | None = None
    gross_pay: Dec | None = None
    net_pay: Dec | None = None
    check_amount: Dec | None = None
    currency: str | None = None
    earnings: list[EarningLine] = Field(default_factory=list)
    taxes: list[TaxLine] = Field(default_factory=list)
    deductions: list[DeductionLine] = Field(default_factory=list)
    benefits: list[BenefitContributionLine] = Field(default_factory=list)
    payments: list[PaycheckPayment] = Field(default_factory=list)


class Issue(Strict):
    """A structured problem or note found while normalizing. Never contains source values."""

    severity: Severity
    code: str
    message: str
    path: str | None = None
    source_id: str | None = None


Record = Annotated[
    Company | Employee | DirectDepositInstruction | DeductionSetup | PayrollRun | Paycheck,
    Field(discriminator="record_type"),
]


class NormalizationResult(Strict):
    provider: Provider
    entity_type: str
    records: list[Record] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    # Opt-in provenance, one list per record. Excluded from dumps: it only travels in API responses.
    traces: list[list[dict]] = Field(default_factory=list, exclude=True)

    @property
    def ok(self) -> bool:
        return not any(i.severity is Severity.ERROR for i in self.issues)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records:
            out[r.record_type] = out.get(r.record_type, 0) + 1
        return out
