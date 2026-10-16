"""PostgreSQL tables for the canonical model (SQLAlchemy 2.x declarative).

Identity rule: every record is unique on (provider, tenant_id, company_id, source_id), so two
providers, two tenants, or two companies can never overwrite one another.

Relational columns hold every canonical field; JSONB holds only what is provider-specific
(`extras`, `provenance`) or naturally nested and not queried (addresses, org assignments).
Numeric columns are unconstrained NUMERIC so Decimal values keep their exact precision.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..canonical.enums import (
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
)


class Base(DeclarativeBase):
    pass


def enum_check(column: str, enum_cls: type, name: str) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({values})", name=name)


def _text() -> Mapped[str | None]:
    return mapped_column(Text, nullable=True)


def _num() -> Mapped[Decimal | None]:
    return mapped_column(Numeric, nullable=True)


def _day() -> Mapped[date | None]:
    return mapped_column(Date, nullable=True)


def _json_obj() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, nullable=False, server_default="{}")


class RecordColumns:
    """Columns every top-level record table shares."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    extras: Mapped[dict[str, Any]] = _json_obj()
    provenance: Mapped[dict[str, Any]] = _json_obj()
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # sha256 of the canonical JSON
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # +1 per real change
    first_ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def provider_check(table: str) -> CheckConstraint:
    return enum_check("provider", Provider, f"ck_{table}_provider")


class CompanyRow(RecordColumns, Base):
    __tablename__ = "companies"
    parent_company_id: Mapped[str | None] = _text()
    parent_company_name: Mapped[str | None] = _text()
    legal_name: Mapped[str | None] = _text()
    display_name: Mapped[str | None] = _text()
    status: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    status_date: Mapped[date | None] = _day()
    service_type_raw: Mapped[str | None] = _text()
    federal_ein: Mapped[str | None] = _text()
    naics_code: Mapped[str | None] = _text()
    sic_code: Mapped[str | None] = _text()
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    phone: Mapped[str | None] = _text()
    email: Mapped[str | None] = _text()
    website: Mapped[str | None] = _text()
    first_payroll_date: Mapped[date | None] = _day()
    legal_entities: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    __table_args__ = (
        UniqueConstraint("provider", "tenant_id", "company_id", name="uq_companies_identity"),
        provider_check("companies"),
        enum_check("status", CompanyStatus, "ck_companies_status"),
    )


class EmployeeRow(RecordColumns, Base):
    __tablename__ = "employees"
    # Resolved link to the company row; NULL until that company has been ingested.
    company_pk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True
    )
    employee_number: Mapped[str | None] = _text()
    username: Mapped[str | None] = _text()
    first_name: Mapped[str | None] = _text()
    middle_name: Mapped[str | None] = _text()
    last_name: Mapped[str | None] = _text()
    preferred_name: Mapped[str | None] = _text()
    name_prefix: Mapped[str | None] = _text()
    name_suffix: Mapped[str | None] = _text()
    preferred_pronouns: Mapped[str | None] = _text()
    preferred_language: Mapped[str | None] = _text()
    birth_date: Mapped[date | None] = _day()
    deceased_date: Mapped[date | None] = _day()
    gender: Mapped[str | None] = _text()
    gender_raw: Mapped[str | None] = _text()
    marital_status_raw: Mapped[str | None] = _text()
    ssn_last4: Mapped[str | None] = _text()
    email: Mapped[str | None] = _text()
    work_email: Mapped[str | None] = _text()
    personal_email: Mapped[str | None] = _text()
    secondary_email: Mapped[str | None] = _text()
    home_phone: Mapped[str | None] = _text()
    mobile_phone: Mapped[str | None] = _text()
    work_phone: Mapped[str | None] = _text()
    work_phone_extension: Mapped[str | None] = _text()
    fax: Mapped[str | None] = _text()
    preferred_phone: Mapped[str | None] = _text()
    emergency_contacts: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    mailing_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tax_form_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    tax_geo_code: Mapped[str | None] = _text()
    school_district_code: Mapped[str | None] = _text()
    employment_status: Mapped[str | None] = _text()
    employment_status_raw: Mapped[str | None] = _text()
    aca_employment_status_raw: Mapped[str | None] = _text()
    status_date: Mapped[date | None] = _day()
    start_date: Mapped[date | None] = _day()
    hire_date: Mapped[date | None] = _day()
    original_hire_date: Mapped[date | None] = _day()
    rehire_date: Mapped[date | None] = _day()
    termination_date: Mapped[date | None] = _day()
    termination_reason_raw: Mapped[str | None] = _text()
    termination_type_raw: Mapped[str | None] = _text()
    termination_explanation: Mapped[str | None] = _text()
    last_worked_date: Mapped[date | None] = _day()
    leave_return_date: Mapped[date | None] = _day()
    eligible_for_rehire: Mapped[bool | None] = mapped_column(nullable=True)
    seniority_date: Mapped[date | None] = _day()
    peo_start_date: Mapped[date | None] = _day()
    retirement_date: Mapped[date | None] = _day()
    job_code: Mapped[str | None] = _text()
    job_title: Mapped[str | None] = _text()
    job_group: Mapped[str | None] = _text()
    job_effective_date: Mapped[date | None] = _day()
    soc_code: Mapped[str | None] = _text()
    eeo_job_category_raw: Mapped[str | None] = _text()
    employee_type_raw: Mapped[str | None] = _text()
    employee_type_effective_date: Mapped[date | None] = _day()
    full_time_equivalent: Mapped[Decimal | None] = _num()
    flsa_exempt: Mapped[bool | None] = mapped_column(nullable=True)
    is_contractor: Mapped[bool | None] = mapped_column(nullable=True)
    is_officer: Mapped[bool | None] = mapped_column(nullable=True)
    is_business_owner: Mapped[bool | None] = mapped_column(nullable=True)
    is_manager: Mapped[bool | None] = mapped_column(nullable=True)
    is_supervisor: Mapped[bool | None] = mapped_column(nullable=True)
    is_remote_worker: Mapped[bool | None] = mapped_column(nullable=True)
    is_seasonal: Mapped[bool | None] = mapped_column(nullable=True)
    is_union_member: Mapped[bool | None] = mapped_column(nullable=True)
    union_code_raw: Mapped[str | None] = _text()
    union_start_date: Mapped[date | None] = _day()
    workers_comp_code: Mapped[str | None] = _text()
    timeclock_id: Mapped[str | None] = _text()
    manager_id: Mapped[str | None] = _text()
    supervisor_id: Mapped[str | None] = _text()
    work_location_code: Mapped[str | None] = _text()
    division_code: Mapped[str | None] = _text()
    department_code: Mapped[str | None] = _text()
    org_assignments: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    labor_allocations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    # Withholding elections and benefit set-up: nested, read as a unit, so JSONB.
    tax_withholdings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    fica_exempt: Mapped[bool | None] = mapped_column(nullable=True)
    non_resident_alien: Mapped[bool | None] = mapped_column(nullable=True)
    benefits: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Compensation setup (flattened). comp_present distinguishes "no data" from "all null".
    comp_present: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    comp_pay_type: Mapped[str | None] = _text()
    comp_pay_type_raw: Mapped[str | None] = _text()
    comp_hourly_rate: Mapped[Decimal | None] = _num()
    comp_pay_period_amount: Mapped[Decimal | None] = _num()
    comp_annual_salary: Mapped[Decimal | None] = _num()
    comp_other_rate: Mapped[Decimal | None] = _num()
    comp_other_rate_period: Mapped[str | None] = _text()
    comp_standard_hours_per_pay_period: Mapped[Decimal | None] = _num()
    comp_standard_hours_per_day: Mapped[Decimal | None] = _num()
    comp_pay_group_raw: Mapped[str | None] = _text()
    comp_pay_frequency_raw: Mapped[str | None] = _text()
    comp_periods_per_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comp_effective_date: Mapped[date | None] = _day()
    comp_last_change_date: Mapped[date | None] = _day()
    comp_last_change_amount: Mapped[Decimal | None] = _num()
    comp_last_change_percent: Mapped[Decimal | None] = _num()
    comp_per_diem_amount: Mapped[Decimal | None] = _num()
    comp_alternate_rates: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    comp_currency: Mapped[str | None] = _text()
    comp_standard_hours_per_week: Mapped[Decimal | None] = _num()
    comp_last_pay_date: Mapped[date | None] = _day()
    comp_next_review_date: Mapped[date | None] = _day()
    comp_compa_ratio: Mapped[Decimal | None] = _num()
    comp_last_performance_review_date: Mapped[date | None] = _day()
    comp_last_performance_review_rating_raw: Mapped[str | None] = _text()
    comp_last_performance_review_job_title: Mapped[str | None] = _text()
    comp_next_performance_review_date: Mapped[date | None] = _day()

    __table_args__ = (
        UniqueConstraint(
            "provider", "tenant_id", "company_id", "source_id", name="uq_employees_identity"
        ),
        Index("ix_employees_company_pk", "company_pk"),
        Index("ix_employees_last_name", "provider", "tenant_id", "last_name"),
        provider_check("employees"),
        enum_check("gender", Gender, "ck_employees_gender"),
        enum_check("employment_status", EmploymentStatus, "ck_employees_status"),
        enum_check("comp_pay_type", PayType, "ck_employees_pay_type"),
        enum_check("comp_other_rate_period", RatePeriod, "ck_employees_rate_period"),
    )


class DirectDepositRow(RecordColumns, Base):
    __tablename__ = "direct_deposit_instructions"
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    employee_pk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str | None] = _text()
    instruction_type_raw: Mapped[str | None] = _text()
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_type: Mapped[str | None] = _text()
    account_type_raw: Mapped[str | None] = _text()
    routing_number: Mapped[str | None] = _text()
    account_number_masked: Mapped[str | None] = _text()
    account_number_hash: Mapped[str | None] = _text()
    bank_name: Mapped[str | None] = _text()
    allocation_type: Mapped[str | None] = _text()
    allocation_raw: Mapped[str | None] = _text()
    amount: Mapped[Decimal | None] = _num()
    percentage: Mapped[Decimal | None] = _num()
    percentage_basis_raw: Mapped[str | None] = _text()
    cap_amount: Mapped[Decimal | None] = _num()
    skip_if_insufficient: Mapped[bool | None] = mapped_column(nullable=True)
    status: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    active: Mapped[bool | None] = mapped_column(nullable=True)
    effective_from: Mapped[date | None] = _day()
    effective_to: Mapped[date | None] = _day()
    prenote_status_raw: Mapped[str | None] = _text()
    frequency_raw: Mapped[str | None] = _text()
    voucher_types_raw: Mapped[str | None] = _text()
    currency: Mapped[str | None] = _text()

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "tenant_id",
            "company_id",
            "employee_id",
            "source_id",
            name="uq_direct_deposits_identity",
        ),
        Index("ix_direct_deposits_employee", "provider", "tenant_id", "company_id", "employee_id"),
        Index("ix_direct_deposits_employee_pk", "employee_pk"),
        provider_check("direct_deposit_instructions"),
        enum_check("account_type", DepositAccountType, "ck_direct_deposits_account_type"),
        enum_check("allocation_type", AllocationType, "ck_direct_deposits_allocation"),
        enum_check("status", DepositStatus, "ck_direct_deposits_status"),
    )


class DeductionSetupRow(RecordColumns, Base):
    __tablename__ = "deduction_setups"
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    employee_pk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    setup_kind: Mapped[str] = mapped_column(Text, nullable=False)
    deduction_code: Mapped[str | None] = _text()
    name: Mapped[str | None] = _text()
    category_raw: Mapped[str | None] = _text()
    status: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calc_method_raw: Mapped[str | None] = _text()
    amount: Mapped[Decimal | None] = _num()
    percentage: Mapped[Decimal | None] = _num()
    calc_params: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    per_period_limit: Mapped[Decimal | None] = _num()
    max_amount: Mapped[Decimal | None] = _num()
    max_basis_raw: Mapped[str | None] = _text()
    annual_max_amount: Mapped[Decimal | None] = _num()
    employer_annual_max_amount: Mapped[Decimal | None] = _num()
    employee_frequency_raw: Mapped[str | None] = _text()
    employer_frequency_raw: Mapped[str | None] = _text()
    employer_match_method_raw: Mapped[str | None] = _text()
    employer_match_amount: Mapped[Decimal | None] = _num()
    start_date: Mapped[date | None] = _day()
    stop_date: Mapped[date | None] = _day()
    loan_or_garnishment_id: Mapped[str | None] = _text()
    benefit_plan_id: Mapped[str | None] = _text()
    payee_code: Mapped[str | None] = _text()
    payee_name: Mapped[str | None] = _text()
    reference_number: Mapped[str | None] = _text()
    memo: Mapped[str | None] = _text()
    tracks_arrears: Mapped[bool | None] = mapped_column(nullable=True)
    mandatory: Mapped[bool | None] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "tenant_id",
            "company_id",
            "employee_id",
            "source_id",
            name="uq_deduction_setups_identity",
        ),
        Index("ix_deduction_setups_employee", "provider", "tenant_id", "company_id", "employee_id"),
        Index("ix_deduction_setups_employee_pk", "employee_pk"),
        provider_check("deduction_setups"),
        enum_check("status", DeductionStatus, "ck_deduction_setups_status"),
        CheckConstraint(
            "setup_kind IN ('recurring', 'scheduled', 'configured')",
            name="ck_deduction_setups_kind",
        ),
    )


class PayrollRunRow(RecordColumns, Base):
    __tablename__ = "payroll_runs"
    name: Mapped[str | None] = _text()
    pay_date: Mapped[date | None] = _day()
    period_start: Mapped[date | None] = _day()
    period_end: Mapped[date | None] = _day()
    scheduled_pay_date: Mapped[date | None] = _day()
    direct_deposit_date: Mapped[date | None] = _day()
    run_type_raw: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    run_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ein_id: Mapped[str | None] = _text()

    __table_args__ = (
        UniqueConstraint(
            "provider", "tenant_id", "company_id", "source_id", name="uq_runs_identity"
        ),
        Index("ix_payroll_runs_pay_date", "provider", "tenant_id", "company_id", "pay_date"),
        provider_check("payroll_runs"),
    )


class PaycheckRow(RecordColumns, Base):
    __tablename__ = "paychecks"
    run_id: Mapped[str | None] = _text()  # natural key of the run, as sent by the provider
    run_pk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("payroll_runs.id", ondelete="SET NULL"), nullable=True
    )
    employee_id: Mapped[str] = mapped_column(Text, nullable=False)
    employee_pk: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True
    )
    check_number: Mapped[str | None] = _text()
    paycheck_type: Mapped[str | None] = _text()
    paycheck_type_raw: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    pay_date: Mapped[date | None] = _day()
    period_start: Mapped[date | None] = _day()
    period_end: Mapped[date | None] = _day()
    void_date: Mapped[date | None] = _day()
    pay_group_raw: Mapped[str | None] = _text()
    gross_pay: Mapped[Decimal | None] = _num()
    net_pay: Mapped[Decimal | None] = _num()
    check_amount: Mapped[Decimal | None] = _num()
    currency: Mapped[str | None] = _text()

    __table_args__ = (
        UniqueConstraint(
            "provider", "tenant_id", "company_id", "source_id", name="uq_paychecks_identity"
        ),
        Index("ix_paychecks_employee", "provider", "tenant_id", "company_id", "employee_id"),
        Index("ix_paychecks_pay_date", "provider", "tenant_id", "company_id", "pay_date"),
        Index("ix_paychecks_run_pk", "run_pk"),
        Index("ix_paychecks_employee_pk", "employee_pk"),
        provider_check("paychecks"),
        enum_check("paycheck_type", PaycheckType, "ck_paychecks_type"),
    )


class LineColumns:
    """Columns shared by the four paycheck line tables. Lines belong to exactly one paycheck."""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paycheck_pk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("paychecks.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source_kind: Mapped[str | None] = _text()
    code: Mapped[str | None] = _text()
    description: Mapped[str | None] = _text()
    extras: Mapped[dict[str, Any]] = _json_obj()


class EarningLineRow(LineColumns, Base):
    __tablename__ = "earning_lines"
    hours: Mapped[Decimal | None] = _num()
    units: Mapped[Decimal | None] = _num()
    rate: Mapped[Decimal | None] = _num()
    amount: Mapped[Decimal | None] = _num()
    is_memo: Mapped[bool | None] = mapped_column(nullable=True)
    work_date_from: Mapped[date | None] = _day()
    work_date_to: Mapped[date | None] = _day()
    accumulators: Mapped[dict[str, Any]] = _json_obj()
    __table_args__ = (
        UniqueConstraint("paycheck_pk", "line_no", name="uq_earning_lines_paycheck_line"),
    )


class TaxLineRow(LineColumns, Base):
    __tablename__ = "tax_lines"
    liability: Mapped[str | None] = _text()
    amount: Mapped[Decimal | None] = _num()
    taxable_wage: Mapped[Decimal | None] = _num()
    rate: Mapped[Decimal | None] = _num()
    accumulators: Mapped[dict[str, Any]] = _json_obj()
    __table_args__ = (
        UniqueConstraint("paycheck_pk", "line_no", name="uq_tax_lines_paycheck_line"),
        enum_check("liability", Liability, "ck_tax_lines_liability"),
    )


class DeductionLineRow(LineColumns, Base):
    __tablename__ = "deduction_lines"
    category_raw: Mapped[str | None] = _text()
    amount: Mapped[Decimal | None] = _num()
    employer_amount: Mapped[Decimal | None] = _num()
    plan_id: Mapped[str | None] = _text()
    accumulators: Mapped[dict[str, Any]] = _json_obj()
    __table_args__ = (
        UniqueConstraint("paycheck_pk", "line_no", name="uq_deduction_lines_paycheck_line"),
    )


class BenefitLineRow(LineColumns, Base):
    __tablename__ = "benefit_contribution_lines"
    plan_id: Mapped[str | None] = _text()
    employee_contribution: Mapped[Decimal | None] = _num()
    employee_deducted_amount: Mapped[Decimal | None] = _num()
    employer_contribution: Mapped[Decimal | None] = _num()
    premium_amount: Mapped[Decimal | None] = _num()
    accumulators: Mapped[dict[str, Any]] = _json_obj()
    __table_args__ = (
        UniqueConstraint("paycheck_pk", "line_no", name="uq_benefit_lines_paycheck_line"),
    )


class PaycheckPaymentRow(Base):
    __tablename__ = "paycheck_payments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    paycheck_pk: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("paychecks.id", ondelete="CASCADE"), nullable=False
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal | None] = _num()
    account_masked: Mapped[str | None] = _text()
    routing_number: Mapped[str | None] = _text()
    status_raw: Mapped[str | None] = _text()
    description: Mapped[str | None] = _text()
    __table_args__ = (UniqueConstraint("paycheck_pk", "line_no", name="uq_payments_paycheck_line"),)


class IngestRunRow(Base):
    """Audit trail: one row per ingest call, with what happened to each record type."""

    __tablename__ = "ingest_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    company_id: Mapped[str | None] = _text()
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    counts: Mapped[dict[str, Any]] = _json_obj()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        Index("ix_ingest_runs_scope", "provider", "tenant_id", "created_at"),
        provider_check("ingest_runs"),
    )
