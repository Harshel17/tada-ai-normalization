"""isolved adapter (Swagger 2.0, API v6 primary snapshot).

Source shapes handled (the JSON body of each documented response):
  company         GET /api/clients/{clientId}/legals            [ {legal company}, ... ]
  employee        GET /api/clients/{clientId}/legals/{legalId}/employees   {"results": [ ... ]}
  direct_deposit  GET .../employees/{employeeId}/directdeposits  [ ... ]  (or employees that embed
                  directDeposits)
  deduction       GET /api/corestream/clients/{c}/legals/{l}/employees/{e}/deductions  [ ... ]
  payroll         GET .../employees/{e}/checks/{id}  (one check, or a list of them), optionally
                  wrapped as {"payroll_run": <payrollRunHistory object>, "checks": [ ... ]}
                  (the live paged envelope {"results": [checks], ...} is also accepted)
                  (the wrapper is our convention for sending a run and its checks together)

isolved identifies a company by client (tenant-like) + legal company. A canonical company is the
LEGAL company: company_id = legalId, parent_company_id = clientId. Employee, deposit and check
bodies do not repeat those ids, so context.company_id (legalId) is required.
"""

from typing import Any

from ..canonical.enums import (
    AllocationType,
    DepositAccountType,
    EntityType,
    Gender,
    Liability,
    Provider,
)
from ..canonical.models import (
    Address,
    AlternateRate,
    BenefitContributionLine,
    CalcParam,
    Company,
    Compensation,
    DeductionLine,
    DeductionSetup,
    DirectDepositInstruction,
    EarningLine,
    Employee,
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
    mask_account,
    require,
    sid,
    ssn_last4,
)

PROVIDER = Provider.ISOLVED
DEPOSITS_ENDPOINT = (
    "GET /api/clients/{clientId}/legals/{legalId}/employees/{employeeId}/directdeposits"
)
DEDUCTIONS_ENDPOINT = (
    "GET /api/corestream/clients/{clientId}/legals/{legalId}/employees/{employeeId}/deductions"
)

# isolved gives descriptions, not codes: gender "Male"/"Female" are the only self-explanatory
# labels. Anything else stays raw. Matching is case-insensitive.
GENDER = {"male": Gender.MALE, "female": Gender.FEMALE}
ACCOUNT_TYPE = {"checking": DepositAccountType.CHECKING, "savings": DepositAccountType.SAVINGS}
PERIODS = ("current", "mtd", "ptd", "qtd", "ytd", "ftd")


def normalize(
    entity: str, response: Any, reference: dict | None = None, context: Any = None
) -> NormalizationResult:
    if context is None:
        raise NormalizationError("missing_context", "context with tenant_id is required")
    ctx = SourceContext.model_validate(context)
    handler = HANDLERS.get(EntityType(entity))
    if handler is None:
        raise NormalizationError("unsupported_entity", f"isolved has no handler for {entity}")
    c = Collector(PROVIDER, trace=ctx.trace)
    records = [r for r in handler(response, ctx, c) if r]
    return NormalizationResult(
        provider=PROVIDER,
        entity_type=entity,
        records=records,
        issues=c.issues,
        traces=c.traces_for(records) if ctx.trace else [],
    )


def _objects(c: Collector, payload: Any, wrapper: str = "results", prefix: str = "$") -> list[Src]:
    out = []
    for path, obj in as_list(payload, wrapper, prefix=prefix):
        if isinstance(obj, dict):
            out.append(c.src(obj, path))
        else:
            c.error("invalid_item", f"expected an object at {path}")
    return out


def _accumulators(s: Src, suffix: str) -> dict:
    """{'ytd': Decimal, ...} from fields like ytdDollars / ytdTaxableWages. Zero is kept."""
    out = {}
    for p in PERIODS:
        v = s.dec(f"{p}{suffix}")
        if v is not None:
            out[p] = v
    return out


# --- company -----------------------------------------------------------------------------------
def company(payload: Any, ctx: SourceContext, c: Collector) -> list[Company | None]:
    out = []
    for lg in _objects(c, payload):
        legal_id = sid(lg.get("id"))
        c.focus(legal_id)
        if not legal_id:
            c.error("missing_id", "legal company id is missing", lg.path)
            continue
        client = lg.sub("client")
        addr = lg.sub("legalAddress")
        address = Address(
            line1=addr.text("streetAddress1", to="address.line1"),
            line2=addr.text("streetAddress2", to="address.line2"),
            city=addr.text("city", to="address.city"),
            state=addr.text("state", to="address.state"),
            postal_code=addr.text("zipCode", to="address.postal_code"),
        )
        c.trace_read(lg, "id", "copy", "source_id")
        if sid(client.get("id")):
            c.trace_read(client, "id", "copy", "parent_company_id", anchor=lg)
        else:
            c.trace_context("parent_company_id", "parent_company_id", ctx.parent_company_id, lg)
        out.append(
            c.build(
                Company,
                lg,
                "GET /api/clients/{clientId}/legals",
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=legal_id,
                source_id=legal_id,
                parent_company_id=sid(client.get("id")) or ctx.parent_company_id,
                parent_company_name=client.text("clientName", to="parent_company_name"),
                legal_name=lg.text("legalName", to="legal_name"),
                display_name=lg.text("dbaName", to="display_name"),
                status_raw=lg.text("status", to="status_raw"),  # status values are not documented
                federal_ein=lg.text("federalEIN", to="federal_ein"),
                naics_code=lg.text("naicsCode", to="naics_code"),
                address=address if address.model_dump(exclude_none=True) else None,
                phone=addr.text("phoneNumber", to="phone"),
            )
        )
    return out


# --- employee ----------------------------------------------------------------------------------
def employee(payload: Any, ctx: SourceContext, c: Collector) -> list[Employee | None]:
    require(ctx, "company_id")
    out = []
    for e in _objects(c, payload):
        emp_id = sid(e.get("id"))
        c.focus(emp_id)
        if not emp_id:
            c.error("missing_id", "employee.id is missing", e.path)
            continue
        e.consume("directDeposits")  # mapped by the direct_deposit entity
        name = e.sub("nameAddress")
        personal = e.sub("personal")
        # The contract documents the override address both at the top level and inside nameAddress.
        # Use the top-level one; fall back to the nested one; the other stays in extras.
        override = e.sub("overrideAddress")
        if not override.keys():
            override = name.sub("overrideAddress")
        gender_raw = e.text("gender", to="gender_raw")
        if (gender_raw or "").lower() in GENDER:
            c.trace_enum(e, "gender", "gender", "GENDER")
        ssn = e.secret("ssn", to="ssn_last4")

        org = [
            OrgAssignment(
                kind=o.text("title") or "organization",
                code=o.text("value"),
                description=o.text("valueDescription"),
            )
            for o in e.items("organizations")
        ] + [
            OrgAssignment(
                kind=lb.text("laborField") or "labor",
                code=lb.text("laborValue"),
                description=lb.text("laborDescription"),
            )
            for lb in e.items("labors")
        ]
        # Override address: used for paychecks unless the flag says no; used for tax forms only
        # if that flag says yes. A flag that was not acted on stays in extras with its address.
        use_paychecks, use_tax = override.peek("useForPaychecks"), override.peek("useForTaxForms")
        mailing = tax_form_address = None
        if override.keys() and (use_paychecks is not False or use_tax is True):
            override_address = _override_address(override)
            if use_paychecks is not False:
                override.consume("useForPaychecks")
                mailing = override_address
            if use_tax is True:
                override.consume("useForTaxForms")
                tax_form_address = override_address
        pronouns = _pronouns(name, personal, c, e.path)
        team = e.sub("team")
        if team.keys():
            org.append(
                OrgAssignment(
                    kind="team", code=sid(team.get("id")), description=team.text("teamName")
                )
            )
        manager = e.sub("assignedManager")
        supervisor = e.sub("assignedSupervisor")
        out.append(
            c.build(
                Employee,
                e,
                "GET /api/clients/{clientId}/legals/{legalId}/employees",
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=emp_id,
                employee_number=e.text("employeeNumber", to="employee_number"),
                first_name=name.text("firstName", to="first_name"),
                middle_name=name.text("middleName", to="middle_name"),
                last_name=name.text("lastName", to="last_name"),
                preferred_name=name.text("preferredName", to="preferred_name"),
                preferred_pronouns=pronouns,
                name_prefix=name.text("prefix", to="name_prefix"),
                name_suffix=name.text("suffix", to="name_suffix"),
                birth_date=e.day("birthDate", to="birth_date"),
                gender=GENDER.get((gender_raw or "").lower()),
                gender_raw=gender_raw,
                marital_status_raw=e.text("maritalStatus", to="marital_status_raw"),
                ssn_last4=ssn_last4(ssn),
                email=e.text("emailAddress", to="email"),
                personal_email=personal.text("personalEmailAddress", to="personal_email"),
                home_phone=personal.text("homePhone", to="home_phone"),
                mobile_phone=personal.text("mobilePhone", to="mobile_phone"),
                work_phone=personal.text("officePhone", to="work_phone"),
                fax=personal.text("faxNumber"),
                address=Address(
                    line1=name.text("address1"),
                    line2=name.text("address2"),
                    city=name.text("city"),
                    state=name.text("state"),
                    postal_code=name.text("zipCode"),
                ),
                mailing_address=mailing,
                tax_form_address=tax_form_address,
                tax_geo_code=name.text("residentGeoCode"),
                school_district_code=name.text("schoolDistrictCode"),
                employment_status_raw=e.text("employmentStatus", to="employment_status_raw"),
                status_date=e.day("statusDate", to="status_date"),
                hire_date=e.day("hireDate", to="hire_date"),
                original_hire_date=e.day("originalHireDate", to="original_hire_date"),
                rehire_date=e.day("rehireDate", to="rehire_date"),
                termination_date=e.day("terminationDate", to="termination_date"),
                termination_reason_raw=e.text("terminationReason", to="termination_reason_raw"),
                termination_type_raw=e.text("terminationType", to="termination_type_raw"),
                eligible_for_rehire=e.boolean("eligibleForRehire", to="eligible_for_rehire"),
                job_code=e.text("jobCode", to="job_code"),
                job_title=e.text("jobTitle", to="job_title"),
                job_group=e.text("jobGroup", to="job_group"),
                job_effective_date=e.day("jobEffectiveDate", to="job_effective_date"),
                soc_code=e.text("socCode"),
                eeo_job_category_raw=e.text("jobEeoCategory"),
                employee_type_raw=e.text("employmentCategoryCode", to="employee_type_raw"),
                employee_type_effective_date=e.day(
                    "employmentCategoryEffectiveDate", to="employee_type_effective_date"
                ),
                aca_employment_status_raw=e.text(
                    "acaEmploymentStatus", to="aca_employment_status_raw"
                ),
                full_time_equivalent=e.dec(
                    "employmentCategoryFullTimeEquivalent", to="full_time_equivalent"
                ),
                flsa_exempt=e.boolean("jobFLSAExempt", to="flsa_exempt"),
                is_manager=e.boolean("isManager", to="is_manager"),
                is_supervisor=e.boolean("isSupervisor", to="is_supervisor"),
                is_remote_worker=e.boolean("isRemoteWorker"),
                is_seasonal=e.boolean("jobIsSeasonal"),
                is_union_member=e.boolean("jobIsUnion"),
                workers_comp_code=e.text("workersCompCode"),
                timeclock_id=e.text("timeclockId", to="timeclock_id"),
                manager_id=sid(manager.get("id")),
                supervisor_id=sid(supervisor.get("id")),
                work_location_code=e.text("workLocation", to="work_location_code"),
                org_assignments=org,
                compensation=Compensation(
                    pay_type_raw=e.text("payType", to="compensation.pay_type_raw"),
                    hourly_rate=e.dec("hourlyRate", to="compensation.hourly_rate"),
                    pay_period_amount=e.dec("perPaySalary", to="compensation.pay_period_amount"),
                    annual_salary=e.dec("annualSalary", to="compensation.annual_salary"),
                    standard_hours_per_pay_period=e.dec(
                        "normalHours", to="compensation.standard_hours_per_pay_period"
                    ),
                    pay_group_raw=e.text("payGroup", to="compensation.pay_group_raw"),
                    pay_frequency_raw=e.text(
                        "payFrequency", to="compensation.pay_frequency_raw"
                    ),  # code meaning is undocumented
                    effective_date=e.day("salaryEffectiveDate", to="compensation.effective_date"),
                    alternate_rates=[
                        AlternateRate(
                            name=r.text("payRateName") or "unnamed", amount=r.dec("rateValue")
                        )
                        for r in e.items("payRates")
                    ],
                ),
                tax_withholdings=_tax_withholdings(e.sub("taxes")),
            )
        )
        if gender_raw and gender_raw.lower() not in GENDER:
            c.unmapped_code("gender", gender_raw, e.path)
    return out


def _override_address(o: Src) -> Address:
    """US fields first; the documented foreign / military variants fill the gaps."""
    return Address(
        line1=o.text("address1"),
        line2=o.text("address2"),
        city=o.text("city") or o.text("militaryCity"),
        state=o.text("state") or o.text("militaryState") or o.text("foreignStateProvince"),
        postal_code=o.text("zipCode") or o.text("foreignPostalCode"),
        country=o.text("country"),
    )


def _pronouns(name: Src, personal: Src, c: Collector, path: str) -> str | None:
    """Both nameAddress and personal carry preferredPronouns; keep a conflicting second value."""
    first = name.text("preferredPronouns", to="preferred_pronouns")
    second = personal.text("preferredPronouns", to="preferred_pronouns")
    if first and second and first != second:
        c.warn("conflicting_fields", "preferredPronouns differs between blocks", path)
        personal.keep("preferredPronouns")
    return first or second


TAX_JURISDICTIONS = {
    "federal": ("federal", None),
    "residentState": ("state", "resident"),
    "nonResidentState": ("state", "non_resident"),
    "residentCity": ("local", "resident"),
    "nonResidentCity": ("local", "non_resident"),
}


def _tax_withholdings(taxes: Src) -> list[TaxWithholding]:
    out = []
    for key, (jurisdiction, residency) in TAX_JURISDICTIONS.items():
        t = taxes.sub(key)
        if not t.keys():
            continue
        out.append(
            TaxWithholding(
                jurisdiction=jurisdiction,
                residency=residency,
                area=t.text("taxState"),
                description=t.text("taxDescription"),
                tax_category_id=t.text("taxCategoryId"),
                filing_status_raw=t.text("filingStatus"),
                primary_allowances=t.integer("primaryExemptions"),
                secondary_allowances=t.integer("secondaryExemptions"),
                dependents_amount=t.dec("dependantExemptionAmount"),
                other_income_amount=t.dec("additionalIncomeAmount"),
                deductions_amount=t.dec("deductionExemptionAmount"),
                exemption_amount=t.dec("exemptionAmount"),
                additional_amount=t.dec("additionalAmount"),
                additional_percent=t.dec("additionalPercent"),
                alternate_calculation_raw=t.text("alternateCalculation"),
                multiple_jobs=t.boolean("multipleJobsOrSpouseWorks"),
                withholding_blocked=t.boolean("isBlocked"),
            )
        )
    return out


# --- direct deposit ------------------------------------------------------------------------------
def _deposit_rows(
    payload: Any, ctx: SourceContext, c: Collector
) -> list[tuple[str, Src, Src | None]]:
    """Triples of (employee_id, deposit Src, owning employee Src or None). Accepts deposits for
    ctx.employee_id (owner None: the id came from your context) or employees embedding them."""
    rows = []
    for obj in _objects(c, payload):
        if obj.has("routingNumber") or obj.has("accountNumber"):
            require(ctx, "employee_id")
            rows.append((ctx.employee_id, obj, None))
        elif obj.has("directDeposits"):
            emp_id = sid(obj.get("id"))
            if not emp_id:
                c.error("missing_id", "employee.id is missing", obj.path)
                continue
            rows.extend((emp_id, d, obj) for d in obj.items("directDeposits"))
        elif obj.has("id"):
            # directDeposits is only present when the caller asked for deposit details.
            c.info("no_direct_deposit_details", "employee has no directDeposits", obj.path)
        else:
            c.warn("unrecognized_shape", "neither a direct deposit nor an employee", obj.path)
    return rows


def direct_deposit(
    payload: Any, ctx: SourceContext, c: Collector
) -> list[DirectDepositInstruction | None]:
    require(ctx, "company_id")
    out = []
    positions: dict[str, int] = {}
    for emp_id, d, owner in _deposit_rows(payload, ctx, c):
        position = positions[emp_id] = positions.get(emp_id, -1) + 1
        dep_id = sid(d.get("id"))
        c.focus(dep_id)
        routing = d.text("routingNumber", to="routing_number")
        account = d.secret("accountNumber", to="account_number_masked")
        acct_type_raw = d.text("accountType", to="account_type_raw")
        amount = d.dec("amount", to="amount")
        percent = d.dec("percent", to="percentage")
        sequence = d.integer("sequenceNumber", to="sequence")
        derived = []
        if amount is not None and percent is not None:
            c.warn(
                "conflicting_fields",
                "amount and percent are both set; documented as exclusive",
                d.path,
            )
        if amount is not None:
            allocation = AllocationType.FIXED_AMOUNT
        elif percent is not None:
            allocation = AllocationType.PERCENTAGE
        elif sequence == 0:  # documented: "Direct Deposit sequence. (0 = Net Pay)"
            allocation = AllocationType.REMAINING_BALANCE
            derived.append("allocation_type")
        else:
            allocation = None
        digest = hash_account(ctx.tenant_id, routing, account)
        if not dep_id:
            c.error("missing_id", "direct deposit id is missing", d.path)
            continue
        if acct_type_raw and acct_type_raw.lower() not in ACCOUNT_TYPE:
            c.unmapped_code("accountType", acct_type_raw, d.path)
        if ACCOUNT_TYPE.get((acct_type_raw or "").lower()):
            c.trace_enum(d, "accountType", "account_type", "ACCOUNT_TYPE")
        if amount is not None:
            c.trace_combine(
                "allocation_type",
                [(d, "amount")],
                "derive",
                "an amount is set, so the deposit is a fixed amount",
            )
        elif percent is not None:
            c.trace_combine(
                "allocation_type",
                [(d, "percent")],
                "derive",
                "a percent is set, so the deposit is a percentage",
            )
        elif sequence == 0:
            c.trace_combine(
                "allocation_type",
                [(d, "sequenceNumber")],
                "derive",
                "sequenceNumber 0 is documented as Net Pay: the remaining balance",
            )
        c.trace_read(d, "id", "copy", "source_id")
        if owner is not None:
            c.trace_read(owner, "id", "copy", "employee_id", anchor=d)
        else:
            c.trace_context("employee_id", "employee_id", ctx.employee_id, d)
        c.trace_sources(
            "source_position",
            "derive",
            [(d.path, None)],
            d,
            "position of this deposit in the employee's list (0-based)",
        )
        out.append(
            c.build(
                DirectDepositInstruction,
                d,
                DEPOSITS_ENDPOINT,
                derived=derived,
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=dep_id,
                employee_id=emp_id,
                name=d.text("description", to="name"),
                sequence=sequence,
                source_position=position,
                account_type=ACCOUNT_TYPE.get((acct_type_raw or "").lower()),
                account_type_raw=acct_type_raw,
                routing_number=routing,
                account_number_masked=mask_account(account),
                account_number_hash=digest,
                bank_name=d.text("bankName", to="bank_name"),
                allocation_type=allocation,
                amount=amount,
                percentage=percent,
                status_raw=d.text(
                    "status", to="status_raw"
                ),  # status descriptions are not documented
                frequency_raw=d.text("frequency", to="frequency_raw"),
            )
        )
    return out


# --- deductions ----------------------------------------------------------------------------------
def deduction(payload: Any, ctx: SourceContext, c: Collector) -> list[DeductionSetup | None]:
    require(ctx, "company_id")
    out = []
    for d in _objects(c, payload):
        ded_id = sid(d.get("id"))
        c.focus(ded_id)
        emp_id = sid(d.get("employeeId")) or ctx.employee_id
        if not ded_id or not emp_id:
            c.error("missing_id", "deduction needs id and an employee id", d.path)
            continue
        params = [
            CalcParam(
                type=p.text("paramType", to=f"calc_params[{i}].type"),
                value=p.dec("paramValue", to=f"calc_params[{i}].value"),
            )
            for i, p in enumerate(d.items("calcParams"))
        ]
        c.trace_read(d, "id", "copy", "source_id")
        if sid(d.get("employeeId")):
            c.trace_read(d, "employeeId", "copy", "employee_id")
        else:
            c.trace_context("employee_id", "employee_id", ctx.employee_id, d)
        c.trace_assigned(
            "setup_kind", d, "this response lists deductions configured for the employee"
        )
        out.append(
            c.build(
                DeductionSetup,
                d,
                DEDUCTIONS_ENDPOINT,
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=ded_id,
                employee_id=emp_id,
                setup_kind="configured",
                deduction_code=d.text("payItemCode", to="deduction_code"),
                # paramType meanings are undocumented, so amounts/percentages stay in calc_params.
                calc_params=params,
                reference_number=d.text("referenceNumber", to="reference_number"),
                start_date=d.day("startDate", to="start_date"),
                stop_date=d.day("stopDate", to="stop_date"),
            )
        )
    return out


# --- payroll -------------------------------------------------------------------------------------
def payroll(payload: Any, ctx: SourceContext, c: Collector) -> list[PayrollRun | Paycheck | None]:
    require(ctx, "company_id")
    run_src = None
    checks_payload = payload
    wrapped = isinstance(payload, dict) and ("payroll_run" in payload or "checks" in payload)
    if wrapped:
        if isinstance(payload.get("payroll_run"), dict):
            run_src = c.src(payload["payroll_run"], "$.payroll_run")
        checks_payload = payload.get("checks", [])
    out: list[PayrollRun | Paycheck | None] = []
    run_id = None
    if run_src is not None:
        run_id = sid(run_src.get("id"))
        c.focus(run_id)
        if not run_id:
            c.error("missing_id", "payroll_run.id is missing", run_src.path)
        else:
            c.trace_read(run_src, "id", "copy", "source_id")
            out.append(
                c.build(
                    PayrollRun,
                    run_src,
                    "GET .../payrollRunHistory/{payrollId}",
                    provider=PROVIDER,
                    tenant_id=ctx.tenant_id,
                    company_id=ctx.company_id,
                    source_id=run_id,
                    name=run_src.text("payOfTheMonth", to="name"),
                    pay_date=run_src.day("payDate", to="pay_date"),
                    period_start=run_src.day("periodBeginDate", to="period_start"),
                    period_end=run_src.day("periodEndDate", to="period_end"),
                    scheduled_pay_date=run_src.day("scheduledPayDate", to="scheduled_pay_date"),
                    run_type_raw=run_src.text("payrollRunType", to="run_type_raw"),
                    status_raw=run_src.text("status", to="status_raw"),
                    run_number=run_src.integer("runNumber", to="run_number"),
                )
            )
    for chk in _objects(
        c, checks_payload, _checks_wrapper(checks_payload), prefix="$.checks" if wrapped else "$"
    ):
        out.append(_paycheck(chk, run_id, ctx, c, run_src))
    return out


def _checks_wrapper(payload: Any) -> str:
    """Where the check objects are. A bare list or `{"checks": [...]}` is our convention; the live
    API returns the documented paged envelope `{"results": [...], ...}`, which is also accepted."""
    if isinstance(payload, dict) and "checks" not in payload and "results" in payload:
        return "results"
    return "checks"


def _paycheck(
    k: Src, run_id: str | None, ctx: SourceContext, c: Collector, run_src: Src | None = None
) -> Paycheck | None:
    check_id = sid(k.get("id"))
    c.focus(check_id)
    emp_id = sid(k.get("employeeId"))
    if not check_id or not emp_id:
        c.error("missing_id", "check needs id and employeeId", k.path)
        return None
    # The check repeats a snapshot of the employee (name, SSN, address). That belongs to the
    # Employee record, is sensitive, and is withheld from the paycheck (listed as redacted).
    for key in (
        "employeeSSN",
        "employeeName",
        "employeeAddress1",
        "employeeAddress2",
        "employeeCity",
        "employeeState",
        "employeeZipCode",
    ):
        k.secret(key)
    check_number = k.integer("checkNumber", to="check_number")
    c.trace_read(k, "id", "copy", "source_id")
    c.trace_read(k, "employeeId", "copy", "employee_id")
    if run_id and run_src is not None:
        c.trace_sources(
            "run_id",
            "copy",
            [(f"{run_src.path}.id", run_id)],
            k,
            "the payroll_run you sent together with these checks (a check names no run id itself)",
        )
    return c.build(
        Paycheck,
        k,
        "GET .../employees/{employeeId}/checks/{id}",
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=check_id,
        run_id=run_id,
        employee_id=emp_id,
        check_number=str(check_number) if check_number is not None else None,
        paycheck_type_raw=k.text("checkTypeDescription", to="paycheck_type_raw"),
        pay_date=k.day("checkDate", to="pay_date"),
        period_start=k.day("periodBeginDate", to="period_start"),
        period_end=k.day("periodEndDate", to="period_end"),
        gross_pay=k.dec("grossPay", to="gross_pay"),
        net_pay=k.dec("netPay", to="net_pay"),
        check_amount=k.dec("checkAmount", to="check_amount"),
        earnings=_earnings(k),
        taxes=_taxes(k),
        deductions=_deductions(k),
        benefits=_benefits(k),
        payments=_payments(k),
    )


def _earnings(k: Src) -> list[EarningLine]:
    out = []
    for e in k.items("earnings"):
        out.append(
            EarningLine(
                line_no=len(out) + 1,
                source_kind="earnings",
                code=e.text("itemCode"),
                description=e.text("itemDescription"),
                hours=e.dec("checkHours"),
                units=e.dec("checkUnits"),
                amount=e.dec("checkDollars"),
                is_memo=e.boolean("isMemoEarning"),
                accumulators=_accumulators(e, "Dollars"),
                extras=e.leftover(),
            )
        )
    return out


def _taxes(k: Src) -> list[TaxLine]:
    out = []
    for t in k.items("taxes"):
        is_employee = t.boolean("isEmployeeTax")
        liability = (
            None
            if is_employee is None
            else (Liability.EMPLOYEE if is_employee else Liability.EMPLOYER)
        )
        out.append(
            TaxLine(
                line_no=len(out) + 1,
                source_kind="taxes",
                liability=liability,
                code=t.text("itemCode"),
                description=t.text("itemDescription"),
                amount=t.dec("checkDollars"),
                taxable_wage=t.dec("checkTaxableWages"),
                accumulators=_accumulators(t, "Dollars"),
                extras=t.leftover(),
            )
        )
    return out


def _deductions(k: Src) -> list[DeductionLine]:
    out = []
    for kind in ("deductions", "garnishments"):
        for d in k.items(kind):
            out.append(
                DeductionLine(
                    line_no=len(out) + 1,
                    source_kind=kind,
                    code=d.text("itemCode"),
                    description=d.text("itemDescription"),
                    amount=d.dec("checkDollars"),
                    accumulators=_accumulators(d, "Dollars"),
                    extras=d.leftover(),
                )
            )
    return out


def _benefits(k: Src) -> list[BenefitContributionLine]:
    out = []
    for b in k.items("benefits"):
        out.append(
            BenefitContributionLine(
                line_no=len(out) + 1,
                source_kind="benefits",
                code=b.text("benefitTitle"),
                description=b.text("benefitDescription"),
                employee_contribution=b.dec("checkEmployeeDeduction"),
                employer_contribution=b.dec("checkEmployerContribution"),
                extras=b.leftover(),
            )
        )
    return out


def _payments(k: Src) -> list[PaycheckPayment]:
    out = []
    for d in k.items("directDeposits"):
        out.append(
            PaycheckPayment(
                line_no=len(out) + 1,
                amount=d.dec("depositAmount"),
                account_masked=mask_account(d.secret("account")),
                description=d.text("itemDescription"),
            )
        )
    return out


HANDLERS = {
    EntityType.COMPANY: company,
    EntityType.EMPLOYEE: employee,
    EntityType.DIRECT_DEPOSIT: direct_deposit,
    EntityType.DEDUCTION: deduction,
    EntityType.PAYROLL: payroll,
}
