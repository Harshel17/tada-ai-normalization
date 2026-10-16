"""PrismHR adapter (OpenAPI 2025.09).

Source shapes handled (the JSON body of each documented response):
  company         GET /clientMaster/v1/getClientMaster      {"clientMaster": {...}}
  employee        GET /employee/v1/getEmployee              {"employee": [{...}]}
  direct_deposit  same getEmployee response -> employee.directDeposit.achVoucher[]
  deduction       GET /deductions/v1/getDeductions
                  {"employeeDeductions": {"deduction": []}}, or getEmployee ->
                  employee.scheduledDeduction[]
  payroll         GET /payroll/v1/getPayrollVouchers*       {"payrollVouchers": [{...}]}
                  (the live demo API returns the list under "payrollVoucher": both are accepted)

PrismHR responses never repeat the client id, so context.company_id (clientId) is required.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..canonical.enums import (
    AllocationType,
    CompanyStatus,
    DeductionStatus,
    DepositAccountType,
    DepositStatus,
    EmploymentStatus,
    EntityType,
    Gender,
    Liability,
    PaycheckType,
    PayType,
    Provider,
)
from ..canonical.models import (
    Address,
    AlternateRate,
    BenefitContributionLine,
    BenefitProfile,
    CafeteriaContribution,
    Company,
    Compensation,
    DeductionLine,
    DeductionSetup,
    DirectDepositInstruction,
    EarningLine,
    EmergencyContact,
    Employee,
    LaborAllocation,
    NormalizationResult,
    OrgAssignment,
    Paycheck,
    PaycheckPayment,
    PayrollRun,
    TaxLine,
    TaxWithholding,
)
from ..errors import NormalizationError
from .base import (
    Collector,
    SourceContext,
    Src,
    as_list,
    hash_account,
    last4,
    mask_account,
    none_if_empty,
    require,
    sid,
    ssn_last4,
)

PROVIDER = Provider.PRISMHR

# --- documented code tables (lookup-reference.json / OpenAPI field descriptions) -------------
COMPANY_STATUS = {
    "A": CompanyStatus.ACTIVE,
    "P": CompanyStatus.PENDING,
    "I": CompanyStatus.INACTIVE,
    "C": CompanyStatus.CANCELLING,
    "T": CompanyStatus.TERMINATED,
}
EMPLOYMENT_STATUS = {
    "A": EmploymentStatus.ACTIVE,
    "L": EmploymentStatus.ON_LEAVE,
    "T": EmploymentStatus.TERMINATED,
}
GENDER = {"M": Gender.MALE, "F": Gender.FEMALE}  # 'X' and 'D' are listed but not defined
PAY_METHOD = {
    "S": PayType.SALARY,
    "H": PayType.HOURLY,
    "C": PayType.COMMISSION,
    "D": PayType.DRIVER,
}
ACCOUNT_TYPE = {
    "C": DepositAccountType.CHECKING,
    "S": DepositAccountType.SAVINGS,
    "IP": DepositAccountType.INSTANT_PAY,
}
ACH_STATUS = {"P": DepositStatus.PRENOTE, "D": DepositStatus.ACTIVE, "I": DepositStatus.INACTIVE}
SCHEDULED_DEDUCTION_STATUS = {
    "A": DeductionStatus.ACTIVE,
    "I": DeductionStatus.INACTIVE,
    "O": DeductionStatus.ONE_TIME,
}
VOUCHER_TYPE = {
    "R": PaycheckType.REGULAR,
    "V": PaycheckType.VACATION,
    "S": PaycheckType.SUPPLEMENTAL,
    "A": PaycheckType.ARREARS,
    "J": PaycheckType.ADJUSTMENT,
}


def normalize(
    entity: str, response: Any, reference: dict | None = None, context: Any = None
) -> NormalizationResult:
    ctx = SourceContext.model_validate(context) if context is not None else None
    if ctx is None:
        raise NormalizationError("missing_context", "context with tenant_id is required")
    handler = HANDLERS.get(EntityType(entity))
    if handler is None:
        raise NormalizationError("unsupported_entity", f"PrismHR has no handler for {entity}")
    c = Collector(PROVIDER, trace=ctx.trace)
    records = [r for r in handler(response, ctx, c) if r]
    return NormalizationResult(
        provider=PROVIDER,
        entity_type=entity,
        records=records,
        issues=c.issues,
        traces=c.traces_for(records) if ctx.trace else [],
    )


# --- helpers ---------------------------------------------------------------------------------
def _objects(c: Collector, payload: Any, wrapper: str) -> list[Src]:
    """Each object under `wrapper` (or the payload itself) as a tracked Src."""
    if not isinstance(payload, (dict, list)):
        raise NormalizationError("invalid_payload", "payload must be a JSON object or array")
    if isinstance(payload, dict):
        _note_provider_error(c, payload)
    out = []
    for path, obj in as_list(payload, wrapper):
        if isinstance(obj, dict):
            out.append(c.src(obj, path))
        else:
            c.error("invalid_item", f"expected an object at {path}")
    return out


def _note_provider_error(c: Collector, payload: dict) -> None:
    """errorCode/errorMessage appear on every PrismHR response; their value semantics are not
    documented, so we only flag a non-empty message instead of guessing what a code means."""
    if payload.get("errorMessage"):
        c.warn("provider_error_message", "response carries a non-empty errorMessage")


def _address(
    s: Src,
    line1="addressLine1",
    line2="addressLine2",
    city="city",
    state="state",
    zipcode="zipcode",
    suffix="zipSuffix",
    county=None,
    dest: str | None = None,
) -> Address | None:
    """`dest` (e.g. "address") names the canonical field so reads are traced (employees: unset)."""

    def to(name: str) -> str | None:
        return f"{dest}.{name}" if dest else None

    postal = s.text(zipcode)
    plus4 = s.text(suffix) if suffix else None
    addr = Address(
        line1=s.text(line1, to=to("line1")),
        line2=s.text(line2, to=to("line2")),
        city=s.text(city, to=to("city")),
        state=s.text(state, to=to("state")),
        postal_code=f"{postal}-{plus4}" if postal and plus4 else postal,
        county=s.text(county, to=to("county")) if county else None,
    )
    if dest and postal:
        if plus4:
            s.collector.trace_combine(
                to("postal_code") or "", [(s, zipcode), (s, suffix)], "combine", "ZIP code + ZIP+4"
            )
        else:
            s.collector.trace_read(s, zipcode, "copy", to("postal_code"))
    return addr if addr.model_dump(exclude_none=True) else None


# --- company ---------------------------------------------------------------------------------
def company(payload: Any, ctx: SourceContext, c: Collector) -> list[Company | None]:
    out = []
    for s in _objects(c, payload, "clientMaster"):
        cm = s
        client_id = sid(cm.get("id")) or ctx.company_id
        c.focus(client_id)
        if not client_id:
            c.error("missing_id", "clientMaster.id is missing and no context.company_id given")
            continue
        status_class = sid(cm.peek("statusClass"))
        status = COMPANY_STATUS.get(status_class or "")
        if status:
            cm.consume("statusClass")  # the enum encodes it; an unmapped class stays in extras
        elif status_class:
            c.unmapped_code("statusClass", status_class, cm.path)
        addr = _address(cm, zipcode="zipCode", state="stateCode", dest="address")
        if status:
            c.trace_enum(cm, "statusClass", "status", "COMPANY_STATUS")
        if sid(cm.get("id")):
            c.trace_read(cm, "id", "copy", "source_id")
        else:
            c.trace_context("source_id", "company_id", ctx.company_id, cm)
        rec = c.build(
            Company,
            provider=PROVIDER,
            tenant_id=ctx.tenant_id,
            company_id=client_id,
            source_id=client_id,
            legal_name=cm.text("legalName", to="legal_name"),
            display_name=cm.text("clientName", to="display_name"),
            status=status,
            status_raw=cm.text(
                "status", to="status_raw"
            ),  # client-defined code that rolls up into the class
            status_date=cm.day("statusDate", to="status_date"),
            service_type_raw=cm.text("serviceType", to="service_type_raw"),
            federal_ein=cm.text("federalIdNumber", to="federal_ein"),
            naics_code=cm.text("naicsCode", to="naics_code"),
            sic_code=cm.text("sicCode", to="sic_code"),
            address=addr,
            phone=cm.text("mainPhone", to="phone"),
            email=cm.text("emailAddress", to="email"),
            website=cm.text("websiteUrl", to="website"),
            first_payroll_date=cm.day("payrollDate", to="first_payroll_date"),
            src=cm,
            endpoint="GET /clientMaster/v1/getClientMaster",
        )
        out.append(rec)
    return out


# --- employee --------------------------------------------------------------------------------
def employee(payload: Any, ctx: SourceContext, c: Collector) -> list[Employee | None]:
    require(ctx, "company_id")
    out = []
    for s in _objects(c, payload, "employee"):
        emp_id = sid(s.get("id"))
        person, contact = s.sub("person"), s.sub("contactInformation")
        client, comp = s.sub("client"), s.sub("compensation")
        c.focus(emp_id)
        if not emp_id:
            c.error("missing_id", "employee.id is missing", s.path)
            continue
        # Sections owned by other entity types are not "unmapped"; they are mapped elsewhere.
        # Account rows are the direct_deposit entity's; the switches beside them stay in extras.
        s.sub("directDeposit").consume("achVoucher")
        s.consume("scheduledDeduction")

        status_class = sid(client.peek("statusClass"))
        if status_class in EMPLOYMENT_STATUS:
            client.consume("statusClass")  # unmapped classes stay in extras
        gender_raw = person.text("gender", to="gender_raw")
        if status_class in EMPLOYMENT_STATUS:
            c.trace_enum(client, "statusClass", "employment_status", "EMPLOYMENT_STATUS")
        if gender_raw in GENDER:
            c.trace_enum(person, "gender", "gender", "GENDER")
        ssn = comp.secret("ssn", to="ssn_last4")

        # reading order matters: everything is read before `c.build` takes the leftovers
        org = []
        for kind, key in (
            ("work_shift", "workShift"),
            ("project_cost_center", "projCostCenter"),
            ("work_group", "workGroupCode"),
            ("sub_cost_center", "subCostCenter"),
        ):
            code = client.text(key)
            if code:
                org.append(OrgAssignment(kind=kind, code=code))

        rec = c.build(
            Employee,
            provider=PROVIDER,
            tenant_id=ctx.tenant_id,
            company_id=ctx.company_id,
            source_id=emp_id,
            employee_number=client.text("clientEmployeeId", to="employee_number"),
            first_name=s.text("firstName", to="first_name"),
            middle_name=s.text("middleInitial", to="middle_name"),
            last_name=s.text("lastName", to="last_name"),
            preferred_name=s.text("preferredName", to="preferred_name"),
            preferred_pronouns=client.text("preferredPronoun"),
            preferred_language=person.text("preferredLanguage"),
            birth_date=person.day("birthDate", to="birth_date"),
            deceased_date=person.day("deceasedDate"),
            gender=GENDER.get(gender_raw or ""),
            gender_raw=gender_raw,
            marital_status_raw=person.text("maritalStatus"),
            ssn_last4=ssn_last4(ssn),
            email=contact.text("emailAddress", to="email"),
            work_email=client.text("workEmail", to="work_email"),
            home_phone=contact.text("homePhone"),
            mobile_phone=contact.text("mobilePhone"),
            work_phone=client.text("workPhone"),
            work_phone_extension=client.text("workPhoneExt", to="work_phone_extension"),
            emergency_contacts=[
                EmergencyContact(
                    name=e.text("contactName"),
                    relationship=e.text("contactRelationship"),
                    contact_type_raw=e.text("contactType"),
                    contact_info=e.text("contactInfo"),
                )
                for e in person.items("emergencyContact")
            ],
            address=_address(contact, county="county"),
            # client.addressLine1.. is documented as the "alternate mailing address"
            mailing_address=_address(client, state="state", zipcode="zip"),
            tax_form_address=_address(  # Form W-2 mailing address
                person, "w2AddressLine1", "w2AddressLine2", "w2City", "w2State", "w2PostalCode", ""
            ),
            tax_geo_code=person.text("geoCode"),
            school_district_code=contact.text("schoolDistrict"),
            employment_status=EMPLOYMENT_STATUS.get(status_class or ""),
            employment_status_raw=client.text("employeeStatus", to="employment_status_raw"),
            status_date=client.day("statusDate", to="status_date"),
            start_date=client.day("jobStartDate", to="start_date"),
            hire_date=client.day("lastHireDate", to="hire_date"),
            original_hire_date=client.day("firstHireDate", to="original_hire_date"),
            seniority_date=client.day("seniorityDate", to="seniority_date"),
            termination_reason_raw=client.text("termReasonCode", to="termination_reason_raw"),
            termination_explanation=client.text("termExplanation", to="termination_explanation"),
            last_worked_date=comp.day("lastWorkedDate", to="last_worked_date"),
            leave_return_date=client.day("returnDate", to="leave_return_date"),
            eligible_for_rehire=client.boolean(
                "rehireOk", to="eligible_for_rehire"
            ),  # Y / N / "" (no preference)
            peo_start_date=person.day("peoStartDate"),
            job_code=client.text("jobCode", to="job_code"),
            job_effective_date=client.day("lastJobChangeDate", to="job_effective_date"),
            employee_type_raw=client.text("employeeType", to="employee_type_raw"),
            employee_type_effective_date=client.day("typeDate", to="employee_type_effective_date"),
            flsa_exempt=client.boolean("flsaExempt", to="flsa_exempt"),
            is_contractor=client.boolean("employee1099", to="is_contractor"),
            is_officer=client.boolean("officer", to="is_officer"),
            is_business_owner=client.boolean("businessOwner"),
            union_code_raw=client.text("unionCode"),
            union_start_date=client.day("unionDate", to="union_start_date"),
            timeclock_id=client.text("clockNum"),
            manager_id=client.text("manager", to="manager_id"),
            work_location_code=client.text("homeLocation", to="work_location_code"),
            division_code=client.text("homeDivision", to="division_code"),
            department_code=client.text("homeDepartment", to="department_code"),
            org_assignments=org,
            labor_allocations=[
                LaborAllocation(
                    percent=a.dec("percent"),
                    location_code=a.text("locationCode"),
                    division_code=a.text("division"),
                    department_code=a.text("deptCode"),
                    job_code=a.text("job"),
                    project_code=a.text("project"),
                )
                for a in comp.items("payAllocation")
            ],
            compensation=_compensation(comp, c),
            tax_withholdings=_tax_withholdings(comp),
            fica_exempt=comp.boolean("ficaExempt"),
            non_resident_alien=comp.boolean("nonResAlien"),
            benefits=_benefits_profile(s, client, comp),
            src=s,
            endpoint="GET /employee/v1/getEmployee",
        )
        if status_class and status_class not in EMPLOYMENT_STATUS:
            c.unmapped_code("client.statusClass", status_class, s.path)
        if gender_raw and gender_raw not in GENDER:
            c.unmapped_code("person.gender", gender_raw, s.path)
        out.append(rec)
    return out


def _compensation(comp: Src, c: Collector) -> Compensation | None:
    if not comp.keys():
        return None
    method = comp.text("payMethod", to="compensation.pay_type_raw")
    if method in PAY_METHOD:
        c.trace_enum(comp, "payMethod", "compensation.pay_type", "PAY_METHOD")
    if method and method not in PAY_METHOD:
        c.unmapped_code("compensation.payMethod", method, comp.path)
    return Compensation(
        pay_type=PAY_METHOD.get(method or ""),
        pay_type_raw=method,
        # Each rate is copied as-is; hourlyPay may legitimately be 0 for a salaried employee.
        hourly_rate=comp.dec("hourlyPay", to="compensation.hourly_rate"),
        pay_period_amount=comp.dec(
            "salary", to="compensation.pay_period_amount"
        ),  # "rate associated with the pay period" (payMethod S)
        annual_salary=comp.dec("annualPay", to="compensation.annual_salary"),
        standard_hours_per_pay_period=comp.dec(
            "standardHours", to="compensation.standard_hours_per_pay_period"
        ),
        pay_group_raw=comp.text("payGroup", to="compensation.pay_group_raw"),
        pay_frequency_raw=comp.text(
            "payPeriod", to="compensation.pay_frequency_raw"
        ),  # "pay period associated with the payGroup"
        effective_date=comp.day("effectiveDate", to="compensation.effective_date"),
        last_change_date=comp.day("payChangeLastDate", to="compensation.last_change_date"),
        last_change_amount=comp.dec("lastChangeAmt", to="compensation.last_change_amount"),
        last_change_percent=comp.dec("lastChangePct", to="compensation.last_change_percent"),
        per_diem_amount=comp.dec("perDiemPay", to="compensation.per_diem_amount"),
        last_pay_date=comp.day("lastPayDate", to="compensation.last_pay_date"),
        next_review_date=comp.day("compReviewNextDate", to="compensation.next_review_date"),
        compa_ratio=comp.dec("compaRatio", to="compensation.compa_ratio"),
        last_performance_review_date=comp.day(
            "perfReviewLastDate", to="compensation.last_performance_review_date"
        ),
        last_performance_review_rating_raw=comp.text(
            "perfReviewLastRating", to="compensation.last_performance_review_rating_raw"
        ),
        last_performance_review_job_title=comp.text(
            "perfReviewLastTitle", to="compensation.last_performance_review_job_title"
        ),
        next_performance_review_date=comp.day(
            "perfReviewNextDate", to="compensation.next_performance_review_date"
        ),
        alternate_rates=[
            AlternateRate(name=key, amount=amount)
            for key in ("payRate2", "payRate3", "payRate4")
            if (amount := comp.dec(key)) is not None
        ],
    )


def _tax_withholdings(comp: Src) -> list[TaxWithholding]:
    """Federal withholding sits flat on `compensation`; state and local are arrays."""
    out = []
    federal = TaxWithholding(
        jurisdiction="federal",
        filing_status_raw=comp.text("fedFilingStatus"),
        primary_allowances=comp.integer("fedWithholdAllowance"),
        dependents_amount=comp.dec("fedDependents"),
        other_income_amount=comp.dec("fedOtherIncome"),
        deductions_amount=comp.dec("fedDeductions"),
        override_type_raw=comp.text("fedOverrideType"),
        override_amount=comp.dec("fedOverrideAmt"),
        alternate_calculation_raw=comp.text("fedTaxCalc"),
        multiple_jobs=comp.boolean("fedMultipleJobs"),
        form_filed=comp.boolean("fedW4Filed"),
        form_year=comp.text("fedW4Year"),
    )
    if federal.model_dump(exclude_none=True, exclude={"jurisdiction"}):
        out.append(federal)
    for st in comp.items("stateTax"):
        out.append(
            TaxWithholding(
                jurisdiction="state",
                area=st.text("stateCode"),
                filing_status_raw=st.text("filingStatus"),
                primary_allowances=st.integer("primaryAllowance"),
                secondary_allowances=st.integer("secondaryAllowance"),
                exemption_amount=st.dec("exemptAmount"),
                supplemental_exemption_amount=st.dec("supplExemptAmount"),
                override_type_raw=st.text("overrideType"),
                override_amount=st.dec("overrideAmount"),
                alternate_calculation_raw=st.text("alternateCalc"),
                certificate_filed=st.boolean("nonResCertFiled"),
                form_filed=st.boolean("stateW4Filed"),
            )
        )
    for loc in comp.items("localTax"):
        out.append(
            TaxWithholding(
                jurisdiction="local",
                area=loc.text("authId"),
                filing_status_raw=loc.text("filingStatus"),
                primary_allowances=loc.integer("primaryAllowance"),
                certificate_filed=loc.boolean("nonResCert"),
                additional_amount=loc.dec("addlWithheld"),
            )
        )
    return out


def _benefits_profile(s: Src, client: Src, comp: Src) -> BenefitProfile | None:
    progress = s.sub("enrollmentInProgress")
    return none_if_empty(
        BenefitProfile(
            benefit_group=client.text("benefitGroup"),
            retirement_benefit_group=client.text("retirementBenefitGroup"),
            enrollment_completed=s.boolean("benefitEnrollmentCompleted"),
            enrollment_in_progress=progress.boolean("enrollmentInProgress"),
            enrollment_type_raw=progress.text("enrollmentType"),
            cafeteria_contributions=[
                CafeteriaContribution(
                    amount=p.dec("planAmt"), effective_date=p.day("planEffectiveDate")
                )
                for p in client.items("cafeteriaPlan")
            ],
            benefit_salary=comp.dec("benefitSalary"),
            court_ordered_medical=client.boolean("courtOrderMedical"),
            paid_through_date=comp.day("paidThruDate", to="benefits.paid_through_date"),
        )
    )


# --- direct deposit ----------------------------------------------------------------------------
def direct_deposit(
    payload: Any, ctx: SourceContext, c: Collector
) -> list[DirectDepositInstruction | None]:
    require(ctx, "company_id")
    out = []
    for s in _objects(c, payload, "employee"):
        emp_id = sid(s.get("id"))
        c.focus(emp_id)
        if not emp_id:
            c.error("missing_id", "employee.id is missing", s.path)
            continue
        dd = s.sub("directDeposit")
        global_voucher_types = dd.text("voucherType")
        employee_ach_status = dd.text("achStatus")
        seen: defaultdict[str, int] = defaultdict(int)
        for position, v in enumerate(dd.items("achVoucher")):
            routing = v.text("transitNum", to="routing_number")
            account = v.secret("accountNum", to="account_number_masked")
            acct_type_raw = v.text("accountType", to="account_type_raw")
            method = v.text("method", to="allocation_raw")
            status_raw = v.text("status", to="status_raw")
            amount = v.dec("amount")
            cap = v.dec("limit", to="cap_amount")
            override = v.text("voucherTypeOverride", to="voucher_types_raw")

            alloc, fixed, pct, skip = _allocation(method, amount, c, v.path)
            if alloc is None:
                v.keep("amount")  # unknown method: do not guess; the value stays in extras
            digest = hash_account(ctx.tenant_id, routing, account)  # internal, not part of the id
            # PrismHR gives a voucher no id. The identity uses only values the record already shows
            # (routing, last 4 digits, type, method, override): it reveals nothing extra and does
            # not change when ACCOUNT_HASH_SALT is rotated. Exact duplicates get `#2`, `#3`.
            key = (
                f"{routing or 'norouting'}-{last4(account) or 'noacct'}-"
                f"{acct_type_raw or ''}-{method or ''}-{override or ''}"
            )
            seen[key] += 1
            source_id = key if seen[key] == 1 else f"{key}#{seen[key]}"
            status = ACH_STATUS.get(status_raw or "")
            bank_name = v.text("bankName", to="bank_name")
            # --- provenance for values that are decided, not simply copied ---
            if ACCOUNT_TYPE.get(acct_type_raw or ""):
                c.trace_enum(v, "accountType", "account_type", "ACCOUNT_TYPE")
            if alloc is not None:
                c.trace_enum(v, "method", "allocation_type", "ACH_METHOD")
            if fixed is not None:
                c.trace_combine(
                    "amount", [(v, "amount")], "route", f"method {method}: a fixed amount"
                )
            if pct is not None:
                c.trace_combine(
                    "percentage", [(v, "amount")], "route", f"method {method}: a percentage"
                )
            if skip is not None:
                c.trace_combine(
                    "skip_if_insufficient",
                    [(v, "method")],
                    "derive",
                    "method FS skips the deposit when the full amount cannot be taken",
                )
            if status:
                c.trace_enum(v, "status", "status", "ACH_STATUS")
                c.trace_combine(
                    "active",
                    [(v, "status")],
                    "derive",
                    "true for an active voucher, false for an inactive one",
                )
            if not override and global_voucher_types:
                c.trace_combine(
                    "voucher_types_raw",
                    [(dd, "voucherType")],
                    "copy",
                    "the employee-level default (this account has no override)",
                    anchor=v,
                )
            c.trace_read(s, "id", "copy", "employee_id", anchor=v)
            c.trace_sources(
                "source_position",
                "derive",
                [(v.path, None)],
                v,
                "position of this voucher in the employee's achVoucher list (0-based)",
            )
            c.trace_combine(
                "source_id",
                [
                    (v, "transitNum"),
                    (v, "accountNum"),
                    (v, "accountType"),
                    (v, "method"),
                    (v, "voucherTypeOverride"),
                ],
                "derive",
                "routing + last 4 account digits + type + method + override (never the hash)",
            )
            out.append(
                c.build(
                    DirectDepositInstruction,
                    provider=PROVIDER,
                    tenant_id=ctx.tenant_id,
                    company_id=ctx.company_id,
                    source_id=source_id,
                    employee_id=emp_id,
                    source_position=position,
                    account_type=ACCOUNT_TYPE.get(acct_type_raw or ""),
                    account_type_raw=acct_type_raw,
                    routing_number=routing,
                    account_number_masked=mask_account(account),
                    account_number_hash=digest,
                    bank_name=bank_name,
                    allocation_type=alloc,
                    allocation_raw=method,
                    amount=fixed,
                    percentage=pct,
                    cap_amount=cap,
                    skip_if_insufficient=skip,
                    status=status,
                    status_raw=status_raw,
                    active={DepositStatus.ACTIVE: True, DepositStatus.INACTIVE: False}.get(status),
                    voucher_types_raw=override or global_voucher_types,
                    # employee-level switch; 'I' would disable every account listed under it
                    extras={"directDeposit.achStatus": employee_ach_status}
                    if employee_ach_status
                    else {},
                    src=v,
                    endpoint="GET /employee/v1/getEmployee",
                )
            )
    return out


def _allocation(method, amount, c, path):
    """PrismHR achVoucher.method -> (allocation_type, fixed_amount, percentage, skip_if_short)."""
    if method == "F":
        return AllocationType.FIXED_AMOUNT, amount, None, False
    if method == "FS":  # fixed, skip if the full amount cannot be taken
        return AllocationType.FIXED_AMOUNT, amount, None, True
    if method in ("P", "PN"):  # PN = percentage of net after fixed deposits
        return AllocationType.PERCENTAGE, None, amount, None
    if method == "B":  # remaining balance; amount must not be specified
        return AllocationType.REMAINING_BALANCE, None, None, None
    c.unmapped_code("achVoucher.method", method, path)
    return None, None, None, None


# --- deductions --------------------------------------------------------------------------------
def deduction(payload: Any, ctx: SourceContext, c: Collector) -> list[DeductionSetup | None]:
    require(ctx, "company_id")
    if isinstance(payload, dict) and "employeeDeductions" in payload:
        return _recurring_deductions(payload, ctx, c)
    return _scheduled_deductions(payload, ctx, c)


def _recurring_deductions(
    payload: dict, ctx: SourceContext, c: Collector
) -> list[DeductionSetup | None]:
    require(ctx, "employee_id")  # getDeductions is called per employee and does not echo the id
    _note_provider_error(c, payload)
    wrapper = c.src(payload["employeeDeductions"] or {}, "$.employeeDeductions")
    out = []
    seen: defaultdict[str, int] = defaultdict(int)
    for d in wrapper.items("deduction"):
        code = d.text("deductionCode", to="deduction_code")
        loan = d.text("loanOrGarnishId", to="loan_or_garnishment_id")
        start = d.day("startDate", to="start_date")
        c.focus(code)
        key = (
            "|".join(p for p in (code, loan, start.isoformat() if start else None) if p)
            or "unknown"
        )
        seen[key] += 1
        source_id = key if seen[key] == 1 else f"{key}#{seen[key]}"
        use_percent = d.boolean("usePercent")
        value = d.dec("amount")
        match_method = d.text("matchMethod", to="employer_match_method_raw")
        if value is not None:
            c.trace_combine(
                "percentage" if use_percent else "amount",
                [(d, "amount"), (d, "usePercent")],
                "route",
                "usePercent is true, so the value is a percent"
                if use_percent
                else "usePercent is not true, so the value is a dollar amount",
            )
        c.trace_assigned("setup_kind", d, "this response lists the employee's recurring deductions")
        c.trace_context("employee_id", "employee_id", ctx.employee_id, d)
        c.trace_combine(
            "source_id",
            [(d, "deductionCode"), (d, "loanOrGarnishId"), (d, "startDate")],
            "derive",
            "deduction code | loan id | start date (a stable id; exact duplicates get #2)",
        )
        out.append(
            c.build(
                DeductionSetup,
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=source_id,
                employee_id=ctx.employee_id,
                setup_kind="recurring",
                deduction_code=code,
                name=d.text("description", to="name"),
                category_raw=d.text("deductionType", to="category_raw"),
                calc_method_raw=d.text("deductionMethod", to="calc_method_raw"),
                # usePercent decides whether `amount` is a dollar value or a percent.
                amount=None if use_percent else value,
                percentage=value if use_percent else None,
                per_period_limit=d.dec("limit", to="per_period_limit"),
                max_amount=d.dec("maximum", to="max_amount"),
                max_basis_raw=d.text("basis", to="max_basis_raw"),
                employee_frequency_raw=d.text("period", to="employee_frequency_raw"),
                employer_match_method_raw=match_method,
                employer_match_amount=d.dec("matchAmount", to="employer_match_amount"),
                start_date=start,
                stop_date=d.day("stopDate", to="stop_date"),
                loan_or_garnishment_id=loan,
                benefit_plan_id=d.text("benefitPlanId", to="benefit_plan_id"),
                payee_code=d.text("cutbackPayeeCode", to="payee_code"),
                tracks_arrears=d.boolean("trackArrears", to="tracks_arrears"),
                mandatory=d.boolean("mandatory", to="mandatory"),
                src=d,
                endpoint="GET /deductions/v1/getDeductions",
            )
        )
    return out


def _scheduled_deductions(
    payload: Any, ctx: SourceContext, c: Collector
) -> list[DeductionSetup | None]:
    out = []
    for s in _objects(c, payload, "employee"):
        emp_id = sid(s.get("id"))
        c.focus(emp_id)
        if not emp_id:
            c.error("missing_id", "employee.id is missing", s.path)
            continue
        seen: defaultdict[str, int] = defaultdict(int)
        for d in s.items("scheduledDeduction"):
            code = d.text("deductionCode", to="deduction_code")
            start = d.day("startDate", to="start_date")
            status_raw = d.text("status", to="status_raw")
            key = (
                "|".join(p for p in (code, start.isoformat() if start else None) if p) or "unknown"
            )
            seen[key] += 1
            source_id = key if seen[key] == 1 else f"{key}#{seen[key]}"
            if SCHEDULED_DEDUCTION_STATUS.get(status_raw or ""):
                c.trace_enum(d, "status", "status", "SCHEDULED_DEDUCTION_STATUS")
            c.trace_assigned(
                "setup_kind", d, "this is one of the employee's scheduledDeduction rows"
            )
            c.trace_read(s, "id", "copy", "employee_id", anchor=d)
            c.trace_combine(
                "source_id",
                [(d, "deductionCode"), (d, "startDate")],
                "derive",
                "deduction code | start date (a stable id; exact duplicates get #2)",
            )
            out.append(
                c.build(
                    DeductionSetup,
                    provider=PROVIDER,
                    tenant_id=ctx.tenant_id,
                    company_id=ctx.company_id,
                    source_id=source_id,
                    employee_id=emp_id,
                    setup_kind="scheduled",
                    deduction_code=code,
                    name=d.text("checkStubDescription", to="name"),
                    status=SCHEDULED_DEDUCTION_STATUS.get(status_raw or ""),
                    status_raw=status_raw,
                    amount=d.dec("amount", to="amount"),
                    start_date=start,
                    stop_date=d.day("stopDate", to="stop_date"),
                    payee_code=d.text("cutbackPayeeCode", to="payee_code"),
                    src=d,
                    endpoint="GET /employee/v1/getEmployee",
                )
            )
    return out


# --- payroll -----------------------------------------------------------------------------------
def voucher_wrapper(payload: Any) -> str:
    """The contract names the list `payrollVouchers`; the live demo API returns `payrollVoucher`.
    Both are accepted (the documented plural wins if both are present)."""
    if (
        isinstance(payload, dict)
        and "payrollVouchers" not in payload
        and "payrollVoucher" in payload
    ):
        return "payrollVoucher"
    return "payrollVouchers"


def payroll(payload: Any, ctx: SourceContext, c: Collector) -> list[PayrollRun | Paycheck | None]:
    require(ctx, "company_id")
    paychecks: list[Paycheck | None] = []
    for v in _objects(c, payload, voucher_wrapper(payload)):
        paychecks.append(_paycheck(v, ctx, c))
    return [*_runs_from(paychecks, ctx, c), *paychecks]


def _paycheck(v: Src, ctx: SourceContext, c: Collector) -> Paycheck | None:
    voucher_id = sid(v.get("voucherId"))
    c.focus(voucher_id)
    emp_id = sid(v.get("employeeId"))
    if not voucher_id or not emp_id:
        c.error("missing_id", "voucher needs voucherId and employeeId", v.path)
        return None
    type_raw = v.text("type", to="paycheck_type_raw")
    check_number = v.text("checkNumber", to="check_number")
    check_num_alt = sid(v.peek("checkNum"))  # documented second name for the same value
    if check_num_alt == check_number:
        v.consume("checkNum")
    elif check_num_alt:  # differs: keep it in extras and say so
        c.warn("conflicting_fields", "checkNumber and checkNum differ", v.path)
    if VOUCHER_TYPE.get(type_raw or ""):
        c.trace_enum(v, "type", "paycheck_type", "VOUCHER_TYPE")
    if not check_number and check_num_alt:
        c.trace_combine(
            "check_number",
            [(v, "checkNum")],
            "copy",
            "checkNumber was empty; checkNum is the documented second name",
        )
    c.trace_read(v, "voucherId", "copy", "source_id")
    c.trace_read(v, "employeeId", "copy", "employee_id")
    earnings = _earnings(v, c)
    _check_earnings_total(v, earnings, c)
    return c.build(
        Paycheck,
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=voucher_id,
        run_id=v.text("batchId", to="run_id"),
        employee_id=emp_id,
        check_number=check_number or check_num_alt,
        paycheck_type=VOUCHER_TYPE.get(type_raw or ""),
        paycheck_type_raw=type_raw,
        status_raw=v.text("status", to="status_raw"),
        pay_date=v.day("payDate", to="pay_date"),
        period_start=v.day("payPeriodStart", to="period_start"),
        period_end=v.day("payPeriodEnd", to="period_end"),
        void_date=v.day("voidDate", to="void_date"),
        pay_group_raw=v.text("payGroup", to="pay_group_raw"),
        # totalEarnings is documented as "total earnings during the pay period" -> gross pay.
        gross_pay=v.dec("totalEarnings", to="gross_pay"),
        net_pay=v.dec("netPay", to="net_pay"),
        check_amount=v.dec("checkAmt", to="check_amount"),
        earnings=earnings,
        taxes=_taxes(v),
        deductions=_deductions(v),
        benefits=_benefits(v),
        payments=_payments(v),
        src=v,
        endpoint="GET /payroll/v1/getPayrollVouchers",
    )


def _line_extras(s: Src) -> dict[str, Any]:
    """Unread keys of a line item. Always pass it as the LAST constructor argument, so every
    other field has been read before leftovers are computed."""
    return s.leftover()


def _earnings(v: Src, c: Collector) -> list[EarningLine]:
    lines = []
    for e in v.items("earning"):
        lines.append(
            EarningLine(
                line_no=len(lines) + 1,
                source_kind="earning",
                code=e.text("payCode"),
                description=e.text("payClassDescription"),
                hours=e.dec("hoursWorked"),
                units=e.dec("hoursUnits"),  # "hours or units paid"
                rate=e.dec("unitRate"),
                amount=e.dec("payAmount"),
                work_date_from=e.day("chargeDate"),
                extras=_line_extras(e),
            )
        )
    for o in v.items("otherEarningsDetail"):
        lines.append(
            EarningLine(
                line_no=len(lines) + 1,
                source_kind="otherEarningsDetail",
                code=o.text("otherEarnCode"),
                description=o.text("otherEarnsDesc"),
                amount=o.dec("otherEarnAmount"),
                extras=_line_extras(o),
            )
        )
    return lines


def sid_dec(v: Src) -> Decimal | None:
    """earnAmount for the consistency check; it is not a canonical field, so it stays in extras."""
    raw = v.peek("earnAmount")
    try:
        return None if raw is None or raw == "" else Decimal(str(raw))
    except ArithmeticError:
        return None


def _check_earnings_total(v: Src, lines: list[EarningLine], c: Collector) -> None:
    """payAmount is described as 'pay per hour or unit', which reads like a rate. If the time
    sheet total (earnAmount) equals the sum of payAmount we know it is the line amount."""
    total = sid_dec(v)
    amounts = [ln.amount for ln in lines if ln.source_kind == "earning" and ln.amount is not None]
    if total is not None and amounts and sum(amounts, Decimal(0)) != total:
        c.warn(
            "earnings_total_mismatch",
            "sum of earning[].payAmount differs from earnAmount; payAmount may be a rate",
            v.path,
        )


def _taxes(v: Src) -> list[TaxLine]:
    lines = []
    for t in v.items("employeeTax"):
        lines.append(
            TaxLine(
                line_no=len(lines) + 1,
                source_kind="employeeTax",
                liability=Liability.EMPLOYEE,
                code=t.text("empTaxDeductCode"),
                description=t.text("empTaxDeductCodeDesc"),
                amount=t.dec("empTaxAmount"),
                taxable_wage=t.dec("empTaxableAmount"),
                extras=_line_extras(t),
            )
        )
    for t in v.items("companyTax"):
        lines.append(
            TaxLine(
                line_no=len(lines) + 1,
                source_kind="companyTax",
                liability=Liability.EMPLOYER,
                code=t.text("comTaxDeductCode"),
                description=t.text("comTaxDeductCodeDesc"),
                amount=t.dec("comTaxAmount"),
                taxable_wage=t.dec("comTaxableAmount"),
                rate=t.dec("comTaxRate"),
                extras=_line_extras(t),
            )
        )
    return lines


def _deductions(v: Src) -> list[DeductionLine]:
    lines = []
    for d in v.items("deduction"):
        lines.append(
            DeductionLine(
                line_no=len(lines) + 1,
                source_kind="deduction",
                code=d.text("deductCode"),
                description=d.text("deductDescription"),
                category_raw=d.text("deductType"),
                amount=d.dec("deductAmount"),
                plan_id=d.text("planId"),
                extras=_line_extras(d),
            )
        )
    for d in v.items("otherDeduction"):
        lines.append(
            DeductionLine(
                line_no=len(lines) + 1,
                source_kind="otherDeduction",
                code=d.text("otherDeductCode"),
                description=d.text("otherDeductDescription"),
                amount=d.dec("otherDeductAmount"),
                extras=_line_extras(d),
            )
        )
    return lines


RETIREMENT_EMPLOYEE = (
    "electiveContributionsPreTax",
    "catchUpContributionsPreTax",
    "rothContributionsPostTax",
    "rothCatchUpContributionsPostTax",
    "employeeContributionsPostTax",
)
RETIREMENT_EMPLOYER = (
    "employerMatchingContributions",
    "employerNonElectiveContributions",
    "employerSafeHarborMatchType1",
    "employerSafeHarborMatchType2",
    "peoMatchingContribution",
)


def _benefits(v: Src) -> list[BenefitContributionLine]:
    """benefit[] overlaps with deduction[] (same plan money seen from the benefits side), so
    these lines are informational and must not be added to deduction totals."""
    lines = []
    for b in v.items("benefit"):
        lines.append(
            BenefitContributionLine(
                line_no=len(lines) + 1,
                source_kind="benefit",
                plan_id=b.text("planId"),
                premium_amount=b.dec("premiumAmt"),
                employee_contribution=b.dec("empContrib"),
                employee_deducted_amount=b.dec("empDeduct"),
                extras=_line_extras(b),
            )
        )
    for b in v.items("employerContributions"):
        lines.append(
            BenefitContributionLine(
                line_no=len(lines) + 1,
                source_kind="employerContributions",
                plan_id=b.text("code"),
                description=b.text("description"),
                employer_contribution=b.dec("amount"),
                extras=_line_extras(b),
            )
        )
    for b in v.items("fsaMatch"):
        lines.append(
            BenefitContributionLine(
                line_no=len(lines) + 1,
                source_kind="fsaMatch",
                code=b.text("code"),
                description=b.text("description"),
                employer_contribution=b.dec("matchAmt"),
                extras=_line_extras(b),
            )
        )
    r = v.sub("retirement")
    plan = r.text("planId")
    for key in RETIREMENT_EMPLOYEE:
        amount = r.dec(key)
        if amount is not None:
            lines.append(
                BenefitContributionLine(
                    line_no=len(lines) + 1,
                    source_kind="retirement",
                    code=key,
                    plan_id=plan,
                    employee_contribution=amount,
                )
            )
    for key in RETIREMENT_EMPLOYER:
        amount = r.dec(key)
        if amount is not None:
            lines.append(
                BenefitContributionLine(
                    line_no=len(lines) + 1,
                    source_kind="retirement",
                    code=key,
                    plan_id=plan,
                    employer_contribution=amount,
                )
            )
    return lines


def _payments(v: Src) -> list[PaycheckPayment]:
    out = []
    for d in v.items("directDeposit"):
        out.append(
            PaycheckPayment(
                line_no=len(out) + 1,
                amount=d.dec("amount"),
                routing_number=d.text("transitNum"),
                account_masked=mask_account(d.secret("accountNum")),
                status_raw=d.text("status"),
            )
        )
    return out


def _runs_from(
    paychecks: list[Paycheck | None], ctx: SourceContext, c: Collector
) -> list[PayrollRun | None]:
    """PrismHR has no "run" object on the voucher endpoints, only batchId. We derive one run per
    batch and only keep a date when every voucher in the batch agrees on it."""
    by_batch: defaultdict[str, list[Paycheck]] = defaultdict(list)
    for p in paychecks:
        if p and p.run_id:
            by_batch[p.run_id].append(p)
    runs = []
    voucher_keys = {
        "pay_date": "payDate",
        "period_start": "payPeriodStart",
        "period_end": "payPeriodEnd",
    }
    for batch_id, checks in by_batch.items():
        c.focus(batch_id)

        def agreed(attr: str, checks=checks):
            values = {getattr(p, attr) for p in checks if getattr(p, attr) is not None}
            if len(values) > 1:
                c.warn("run_dates_inconsistent", f"vouchers in the batch disagree on {attr}")
            return values.pop() if len(values) == 1 else None

        run = c.build(
            PayrollRun,
            None,
            "GET /payroll/v1/getPayrollVouchers",
            derived=["pay_date", "period_start", "period_end"],
            provider=PROVIDER,
            tenant_id=ctx.tenant_id,
            company_id=ctx.company_id,
            source_id=batch_id,
            pay_date=agreed("pay_date"),
            period_start=agreed("period_start"),
            period_end=agreed("period_end"),
        )
        runs.append(run)
        _trace_derived_run(c, run, batch_id, checks, voucher_keys)
    if runs:
        c.info("runs_derived", "PayrollRun records were derived from voucher batchId values")
    return runs


def _trace_derived_run(
    c: Collector,
    run: PayrollRun | None,
    batch_id: str,
    checks: list[Paycheck],
    keys: dict[str, str],
) -> None:
    """Provenance for a run PrismHR never sent: each date is derived from the vouchers' own fields.
    Every step names real voucher paths (at most three) and how many vouchers agreed."""
    if not (c.tracing and run is not None):
        return
    anchor = f"$derived-run:{batch_id}"
    n = len(checks)
    for attr, key in keys.items():
        shown = [
            (f"{p.provenance.source_path}.{key}", getattr(p, attr).isoformat())
            for p in checks
            if getattr(p, attr) is not None
        ]
        if getattr(run, attr) is not None and shown:
            more = f" (first 3 of {len(shown)} shown)" if len(shown) > 3 else ""
            c.trace_sources(
                attr, "derive", shown[:3], anchor,
                f"derived, not copied: all {n} voucher(s) of batch {batch_id} carry this date, "
                f"and a run date is kept only when they agree{more}",
            )  # fmt: skip
    ids = [(f"{p.provenance.source_path}.batchId", batch_id) for p in checks]
    c.trace_sources(
        "source_id", "derive", ids[:3], anchor,
        f"the run id is the batchId shared by {n} voucher(s); PrismHR sends no run object",
    )  # fmt: skip
    c.finish_trace(run, (anchor,))


HANDLERS = {
    EntityType.COMPANY: company,
    EntityType.EMPLOYEE: employee,
    EntityType.DIRECT_DEPOSIT: direct_deposit,
    EntityType.DEDUCTION: deduction,
    EntityType.PAYROLL: payroll,
}
