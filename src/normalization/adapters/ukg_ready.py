"""UKG Ready adapter (REST v2, cached reference docs + extracted JSON schemas).

UKG splits one person across several endpoints and has no company-master endpoint, so the input
shapes are the documented response bodies plus two small conventions of ours:

  company         GET /v2/companies/{cid}/lookup/eins           {"items": [ {id, ein_name, ...} ]}
  employee        GET /v2/companies/{cid}/employees/{aid}       one employee profile, or a bundle
                  {"employee": <profile>, "demographics": <..>, "pay_info": <..>,
                   "compensation_history": {"compensations": [..]}}      <- our bundle convention
                  or GET .../employees  {"employees": [ summaries ]}
  direct_deposit  GET .../employees/{aid}/direct-deposits      {"deposits": [ ... ]} (or one object)
  deduction       GET .../employees/{aid}/deductions            {"deductions": [ ... ]} (or one)
  payroll         GET .../payrolls                              {"payrolls": [ runs ]}
                  GET .../payrolls/{id}/pay-statements/{id}     one pay statement (needs
                  context.payroll_id for the run link), or the bundle
                  {"payroll": <run>, "pay_statements": [ statements ]}  <- our convention

context.company_id is the UKG company id (cid); employee-scoped responses also need
context.employee_id (the account id) because the body does not repeat it.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from ..canonical.enums import (
    AllocationType,
    DepositAccountType,
    EntityType,
    Gender,
    Liability,
    Provider,
    RatePeriod,
)
from ..canonical.models import (
    Address,
    BenefitContributionLine,  # noqa: F401  (UKG documents no benefit lines; see DECISIONS.md)
    BenefitProfile,
    Company,
    Compensation,
    DeductionLine,
    DeductionSetup,
    DirectDepositInstruction,
    EarningLine,
    Employee,
    LegalEntity,
    NormalizationResult,
    OrgAssignment,
    Paycheck,
    PaycheckPayment,
    PayrollRun,
    Provenance,
    TaxLine,
)
from ..errors import NormalizationError
from .base import (
    Collector,
    SourceContext,
    Src,
    as_list,
    hash_account,
    mask_account,
    none_if_empty,
    require,
    sid,
    ssn_last4,
)

PROVIDER = Provider.UKG_READY

GENDER = {"MALE": Gender.MALE, "FEMALE": Gender.FEMALE}  # UNDEFINED has no canonical meaning
ACCOUNT_TYPE = {"CHECKING": DepositAccountType.CHECKING, "SAVINGS": DepositAccountType.SAVINGS}
PREFERRED_PHONE = {"HOME": "home", "CELL": "mobile", "WORK": "work", "NONE": "none"}
RATE_PERIOD = {"MONTH": RatePeriod.MONTH, "WEEK": RatePeriod.WEEK}
# calc_method.type: the create docs say FLAT_AMOUNT requires `amount`, and GROSS_EARNING / NET_PAY /
# DIRECT_DEPOSIT_PER_OF_REMAINDER require `percentage`. FULL is not described: raw only.
PERCENT_METHODS = {"GROSS_EARNING", "NET_PAY", "DIRECT_DEPOSIT_PER_OF_REMAINDER"}
EMPLOYEE_PARTS = ("demographics", "pay_info", "compensation_history")


def normalize(
    entity: str, response: Any, reference: dict | None = None, context: Any = None
) -> NormalizationResult:
    if context is None:
        raise NormalizationError("missing_context", "context with tenant_id is required")
    ctx = SourceContext.model_validate(context)
    handler = HANDLERS.get(EntityType(entity))
    if handler is None:
        raise NormalizationError("unsupported_entity", f"UKG Ready has no handler for {entity}")
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


def _address(a: Src) -> Address | None:
    if not a.keys():
        return None
    house = " ".join(p for p in (a.text("number"), a.text("number_addition")) if p)
    return Address(
        line1=a.text("address_line_1"),
        house_number=house or None,
        line2=a.text("address_line_2"),
        line3=a.text("address_line_3"),
        city=a.text("city"),
        state=a.text("state_code") or a.text("state"),
        postal_code=a.text("zip"),
        country=a.text("country"),
    )


# --- company -------------------------------------------------------------------------------------
def company(payload: Any, ctx: SourceContext, c: Collector) -> list[Company | None]:
    require(ctx, "company_id")
    c.focus(ctx.company_id)
    entities, extras = [], {}
    for e in _objects(c, payload, "items"):
        ein_id = sid(e.get("id"))
        if not ein_id:
            c.error("missing_id", "EIN item has no id", e.path)
            continue
        n = len(entities)
        c.trace_read(e, "id", "copy", f"legal_entities[{n}].entity_id")
        entities.append(
            LegalEntity(
                entity_id=ein_id,
                name=e.text("ein_name", to=f"legal_entities[{n}].name"),
                tax_id=e.text("ein_tax_id", to=f"legal_entities[{n}].tax_id"),
                currency_code=e.text("ein_currency_code", to=f"legal_entities[{n}].currency_code"),
                country=e.text("ein_country_id", to=f"legal_entities[{n}].country"),
            )
        )
        if rest := e.leftover():
            extras.setdefault("legal_entities", {})[ein_id] = rest
    # UKG documents no company-master endpoint: name, address, status are simply not available.
    c.info(
        "company_master_unavailable", "UKG Ready exposes only the company's EINs, no name or status"
    )
    company_record = Company(
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=ctx.company_id,
        legal_entities=entities,
        extras=extras,
        provenance=Provenance(source_endpoint="GET /v2/companies/{cid}/lookup/eins"),
    )
    # Built directly (there is no single source object): the EIN items are the sources, and the ids
    # are the context you supplied, so say exactly that.
    c.trace_sources(
        "source_id", "context", [("context.company_id", ctx.company_id)], "$.items",
        "UKG has no company record: the id is the company id you supplied",
    )  # fmt: skip
    c.finish_trace(company_record, ("$.items",))
    return [company_record]


# --- employee ------------------------------------------------------------------------------------
def employee(payload: Any, ctx: SourceContext, c: Collector) -> list[Employee | None]:
    require(ctx, "company_id")
    if isinstance(payload, dict) and "employee" in payload:
        return [_employee_bundle(payload, ctx, c)]
    out = []
    for e in _objects(c, payload, "employees"):
        out.append(_employee_bundle({"employee": e._d}, ctx, c, base_path=e.path))  # noqa: SLF001
    return out


def _employee_bundle(
    bundle: dict, ctx: SourceContext, c: Collector, base_path: str = "$.employee"
) -> Employee | None:
    p = c.src(bundle["employee"], base_path)
    acct_id = sid(p.get("id"))
    c.focus(acct_id)
    if not acct_id:
        c.error("missing_id", "employee.id is missing", p.path)
        return None
    demo = c.src(bundle.get("demographics") or {}, "$.demographics")
    pay = c.src(bundle.get("pay_info") or {}, "$.pay_info")
    dates, phones = p.sub("dates"), p.sub("phones")

    # SSN may sit in social_security and/or national_id_numbers[type=SSN]; neither is kept.
    ssn = p.secret("social_security", to="ssn_last4")
    for n in p.items("national_id_numbers"):
        value = n.secret("value")
        if n.peek("type") == "SSN" and not ssn:
            ssn = value
    p.secret("social_insurance")
    p.secret("national_insurance")
    p.secret("bsn")

    managers = sorted(p.items("managers"), key=lambda m: m.integer("index") or 0)
    manager_ids = [sid(m.sub("empl_ref").get("account_id")) for m in managers]
    other_managers = []
    if len(managers) > 1:
        # canonical Employee has one manager_id: the first by index; the rest stay in extras
        c.info(
            "multiple_managers", "several managers listed; manager_id is the first by index", p.path
        )
        other_managers = [m._d for m in managers[1:]]  # noqa: SLF001

    org = []
    for d in p.sub("cost_centers_info").items("defaults"):
        org.append(
            OrgAssignment(
                kind=f"cost_center_{d.integer('index')}", code=sid(d.sub("value").get("id"))
            )
        )

    gender_raw = demo.text("gender", to="gender_raw")
    if gender_raw in GENDER:
        c.trace_enum(demo, "gender", "gender", "GENDER")
    mailing = (
        _address(p.sub("mailing_address")) if p.boolean("use_separate_mailing_address") else None
    )
    p.consume("use_separate_mailing_address")

    pay_type_id = sid(pay.sub("pay_type").get("id"))  # an id; its meaning is tenant data
    comp, history_rest = _compensation(bundle.get("compensation_history"), pay_type_id, acct_id, c)
    # standard_work_day is documented in milliseconds: a unit conversion, flagged as derived.
    work_day_ms = pay.dec("standard_work_day")
    hours_per_day = None if work_day_ms is None else work_day_ms / Decimal(3_600_000)
    if comp is None and (pay_type_id or hours_per_day is not None):
        comp = Compensation(pay_type_raw=pay_type_id)
    if comp is not None:
        comp.standard_hours_per_day = hours_per_day
        if hours_per_day is not None:
            c.trace_combine(
                "compensation.standard_hours_per_day",
                [(pay, "standard_work_day")],
                op="derive",
                note="milliseconds divided by 3,600,000",
            )
    pref = phones.peek("preferred_phone")
    if isinstance(pref, str) and pref in PREFERRED_PHONE:
        phones.consume("preferred_phone")  # an undocumented value stays in extras
    pay_grade = pay.sub("pay_grade")
    if pay_grade.keys():
        org.append(OrgAssignment(kind="pay_grade", code=sid(pay_grade.get("id"))))
    benefits = none_if_empty(
        BenefitProfile(
            medical_eligibility_raw=pay.text("medical_eligibility"),
            dental_eligibility_raw=pay.text("dental_eligibility"),
            dependent_benefits_eligibility_raw=pay.text("dependent_benefits_eligibility"),
            auto_enrollment_eligibility_raw=pay.text("benefit_auto_enrollment_eligibility"),
        )
    )
    pay.secret("bank_account_number")  # the employee's bank account: withheld
    default_job = pay.sub("default_job")
    employee_type = pay.sub("employee_type")
    if gender_raw and gender_raw not in GENDER:
        c.unmapped_code("gender", gender_raw, demo.path)

    def part_extras() -> dict[str, Any]:
        """Leftovers of the secondary endpoints, grouped by endpoint name. Evaluated last."""
        parts = {
            "demographics": demo.leftover(),
            "pay_info": pay.leftover(),
            "compensation_history": history_rest,
        }
        if other_managers:
            parts["other_managers"] = other_managers
        return {k: v for k, v in parts.items() if v}

    return c.build(
        Employee,
        p,
        "GET /v2/companies/{cid}/employees/{aid}",
        also_roots=("$.demographics", "$.pay_info", "$.compensation_history"),
        derived=["compensation.standard_hours_per_day"] if hours_per_day is not None else None,
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=acct_id,
        employee_number=p.text("employee_id", to="employee_number"),
        username=p.text("username", to="username"),
        first_name=p.text("first_name", to="first_name"),
        middle_name=p.text("middle_name", to="middle_name"),
        last_name=p.text("last_name", to="last_name"),
        preferred_name=p.text("nickname", to="preferred_name"),
        name_prefix=p.text("salutation", to="name_prefix"),
        name_suffix=p.text("suffix", to="name_suffix"),
        birth_date=dates.day("birthday", to="birth_date"),
        gender=GENDER.get(gender_raw or ""),
        gender_raw=gender_raw,
        ssn_last4=ssn_last4(ssn),
        email=p.text("primary_email", to="email"),
        secondary_email=p.text("secondary_email", to="secondary_email"),
        preferred_phone=PREFERRED_PHONE.get(pref) if isinstance(pref, str) else None,
        home_phone=phones.text("home_phone", to="home_phone"),
        mobile_phone=phones.text("cell_phone", to="mobile_phone"),
        work_phone=phones.text("work_phone", to="work_phone"),
        address=_address(p.sub("address")),
        mailing_address=mailing,
        employment_status_raw=p.text(
            "status", to="employment_status_raw"
        ),  # status values are not documented
        start_date=dates.day("started", to="start_date"),
        hire_date=dates.day("hired", to="hire_date"),
        rehire_date=dates.day("re_hired", to="rehire_date"),
        termination_date=dates.day("terminated", to="termination_date"),
        seniority_date=dates.day("seniority", to="seniority_date"),
        deceased_date=dates.day("deceased", to="deceased_date"),
        peo_start_date=dates.day("peo_hired", to="peo_start_date"),
        retirement_date=dates.day("retired", to="retirement_date"),
        job_code=sid(default_job.get("id")),
        job_effective_date=pay.day("job_last_changed", to="job_effective_date"),
        eeo_job_category_raw=pay.text("eeo_classification", to="eeo_job_category_raw"),
        is_seasonal=demo.boolean("seasonal", to="is_seasonal"),
        union_code_raw=sid(pay.sub("union").get("id")),
        workers_comp_code=pay.sub("workers_comp_code").text("external_id"),
        employee_type_raw=sid(employee_type.get("id")),
        manager_id=manager_ids[0] if manager_ids else None,
        org_assignments=org,
        compensation=comp,
        benefits=benefits,
        extras=part_extras(),
    )


def _compensation(
    history: Any, pay_type_id: str | None, employee_id: str | None, c: Collector
) -> tuple[Compensation | None, list]:
    """Latest compensation record by effective_from; the older ones are returned for extras."""
    listed = history.get("compensations") if isinstance(history, dict) else None
    rows = [
        c.src(r, f"$.compensation_history.compensations[{i}]")
        for i, r in enumerate(listed if isinstance(listed, list) else [])
        if isinstance(r, dict)
    ]
    if not rows:
        return None, []
    rows.sort(key=lambda r: r.day("effective_from") or date.min)
    latest, older = rows[-1], rows[:-1]
    period = latest.peek("amount_period")
    amount = latest.dec("amount")
    hourly = latest.dec("hourly_pay", to="compensation.hourly_rate")
    ref = latest.sub("employee")
    owner = sid(ref.peek("account_id"))
    if owner and employee_id and owner != employee_id:
        # keep the foreign reference visible instead of hiding the inconsistency
        c.warn("relationship_mismatch", "compensation belongs to a different employee", latest.path)
    else:
        ref.consume("account_id")
    hourly_rate, pay_period_amount, annual, other, other_period = hourly, None, None, None, None
    known = {"HOUR", "PAY_PERIOD", "YEAR", *RATE_PERIOD}
    if isinstance(period, str) and period in known:
        latest.consume("amount_period")  # an unknown period stays in extras along with `amount`
        route = {
            "HOUR": "compensation.hourly_rate",
            "PAY_PERIOD": "compensation.pay_period_amount",
            "YEAR": "compensation.annual_salary",
        }.get(period, "compensation.other_rate")
        if period == "HOUR":
            if hourly is None:
                hourly_rate = amount
                c.trace_combine(route, [(latest, "amount")], "route", "amount_period is HOUR")
            elif amount is not None and amount != hourly:
                c.warn("conflicting_fields", "amount and hourly_pay differ", latest.path)
                latest._used.discard("amount")  # noqa: SLF001  keep the unused amount in extras
        elif period == "PAY_PERIOD":
            pay_period_amount = amount
            c.trace_combine(route, [(latest, "amount")], "route", "amount_period is PAY_PERIOD")
        elif period == "YEAR":
            annual = amount
            c.trace_combine(route, [(latest, "amount")], "route", "amount_period is YEAR")
        else:
            other, other_period = amount, RATE_PERIOD[period]
            c.trace_combine(route, [(latest, "amount")], "route", f"amount_period is {period}")
    else:
        if period:
            c.unmapped_code("amount_period", str(period), latest.path)
        latest._used.discard("amount")  # noqa: SLF001
    # `time` is milliseconds; `time_period` is YEAR, MONTH, WEEK or PAY_PERIOD.
    # Only WEEK and PAY_PERIOD have a canonical field and are typed; the rest stays in extras.
    hours_per_week = hours_per_period = None
    time_period = latest.peek("time_period")
    if time_period in ("WEEK", "PAY_PERIOD"):
        time_ms = latest.dec("time")
        if time_ms is not None:
            hours = time_ms / Decimal(3_600_000)
            latest.consume("time_period")
            dest = (
                "standard_hours_per_week"
                if time_period == "WEEK"
                else "standard_hours_per_pay_period"
            )
            if time_period == "WEEK":
                hours_per_week = hours
            else:
                hours_per_period = hours
            c.trace_combine(
                f"compensation.{dest}",
                [(latest, "time"), (latest, "time_period")],
                op="derive",
                note="milliseconds divided by 3,600,000, for the stated period",
            )
    comp = Compensation(
        standard_hours_per_week=hours_per_week,
        standard_hours_per_pay_period=hours_per_period,
        hourly_rate=hourly_rate,
        pay_period_amount=pay_period_amount,
        annual_salary=annual,
        other_rate=other,
        other_rate_period=other_period,
        effective_date=latest.day("effective_from", to="compensation.effective_date"),
        currency=latest.text("currency", to="compensation.currency"),
        periods_per_year=latest.integer("num_pp_in_year", to="compensation.periods_per_year"),
        pay_type_raw=pay_type_id,
    )
    rest = latest.leftover()
    return comp, [o._d for o in older] + ([rest] if rest else [])  # noqa: SLF001


# --- direct deposit ------------------------------------------------------------------------------
def direct_deposit(
    payload: Any, ctx: SourceContext, c: Collector
) -> list[DirectDepositInstruction | None]:
    require(ctx, "company_id")
    out = []
    for position, d in enumerate(_objects(c, payload, "deposits")):
        dep_id = sid(d.get("id"))
        c.focus(dep_id)
        emp_ref = d.sub("employee")
        emp_id = sid(emp_ref.get("account_id")) or ctx.employee_id
        if not dep_id or not emp_id:
            c.error("missing_id", "deposit needs id and an employee (account) id", d.path)
            continue
        routing = d.text("routing_number", to="routing_number")
        account = d.secret("account_number", to="account_number_masked")
        calc_src = d.sub("calc_method")
        calc = calc_src.text("type", to="allocation_raw")
        amount = d.dec("amount", to="amount")
        pct = d.dec("percentage", to="percentage")
        if calc == "FLAT_AMOUNT":
            alloc = AllocationType.FIXED_AMOUNT
        elif calc in PERCENT_METHODS:
            alloc = AllocationType.PERCENTAGE
        else:
            alloc = None
            c.unmapped_code("calc_method.type", calc, d.path)
        acct_type = d.text("account_type", to="account_type_raw")
        if alloc is not None:
            c.trace_enum(
                calc_src,
                "type",
                "allocation_type",
                "calc_method.type (FLAT_AMOUNT / percent methods)",
            )
        if calc in PERCENT_METHODS:
            c.trace_read(calc_src, "type", "copy", "percentage_basis_raw")
        if ACCOUNT_TYPE.get(acct_type or ""):
            c.trace_enum(d, "account_type", "account_type", "ACCOUNT_TYPE")
        c.trace_read(d, "id", "copy", "source_id")
        if sid(emp_ref.get("account_id")):
            c.trace_read(emp_ref, "account_id", "copy", "employee_id", anchor=d)
        else:
            c.trace_context("employee_id", "employee_id", ctx.employee_id, d)
        c.trace_sources(
            "source_position",
            "derive",
            [(d.path, None)],
            d,
            "position of this deposit in the response list (0-based)",
        )
        out.append(
            c.build(
                DirectDepositInstruction,
                d,
                "GET /v2/companies/{cid}/employees/{aid}/direct-deposits",
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=dep_id,
                employee_id=emp_id,
                name=d.text("name", to="name"),
                instruction_type_raw=d.text("type", to="instruction_type_raw"),
                sequence=d.integer("sequence", to="sequence"),
                source_position=position,
                account_type=ACCOUNT_TYPE.get(acct_type or ""),
                account_type_raw=acct_type,
                routing_number=routing,
                account_number_masked=mask_account(account),
                account_number_hash=hash_account(ctx.tenant_id, routing, account),
                allocation_type=alloc,
                allocation_raw=calc,
                amount=amount,
                percentage=pct,
                percentage_basis_raw=calc if calc in PERCENT_METHODS else None,
                active=d.boolean("active", to="active"),
                effective_from=d.day("active_from", to="effective_from"),
                effective_to=d.day("active_to", to="effective_to"),
                prenote_status_raw=d.text("pre_note_status", to="prenote_status_raw"),
                frequency_raw=d.sub("payroll_frequency").text("type", to="frequency_raw"),
                currency=d.text("currency", to="currency"),
            )
        )
    return out


# --- deductions ----------------------------------------------------------------------------------
def deduction(payload: Any, ctx: SourceContext, c: Collector) -> list[DeductionSetup | None]:
    require(ctx, "company_id", "employee_id")
    out = []
    for d in _objects(c, payload, "deductions"):
        ded_id = sid(d.get("id"))
        c.focus(ded_id)
        if not ded_id:
            c.error("missing_id", "deduction id is missing", d.path)
            continue
        ded = d.sub("deduction")
        calc = d.sub("ee_calc_method")
        vendor = d.sub("vendor")
        c.trace_read(d, "id", "copy", "source_id")
        c.trace_read(vendor, "id", "copy", "payee_code", anchor=d)
        c.trace_context("employee_id", "employee_id", ctx.employee_id, d)
        c.trace_assigned(
            "setup_kind", d, "this response lists deductions configured for the employee"
        )
        out.append(
            c.build(
                DeductionSetup,
                d,
                "GET /v2/companies/{cid}/employees/{aid}/deductions/{id}",
                provider=PROVIDER,
                tenant_id=ctx.tenant_id,
                company_id=ctx.company_id,
                source_id=ded_id,
                employee_id=ctx.employee_id,
                setup_kind="configured",
                deduction_code=ded.text("code", to="deduction_code"),
                name=ded.text("name", to="name"),
                sequence=d.integer("sequence", to="sequence"),
                calc_method_raw=calc.sub("method").text("type", to="calc_method_raw"),
                amount=calc.dec("amount", to="amount"),
                percentage=calc.dec("percent", to="percentage"),
                annual_max_amount=d.dec("annual_max_amount", to="annual_max_amount"),
                employer_annual_max_amount=d.dec(
                    "er_annual_max_amount", to="employer_annual_max_amount"
                ),
                employee_frequency_raw=d.sub("ee_frequency").text(
                    "type", to="employee_frequency_raw"
                ),
                employer_frequency_raw=d.sub("er_frequency").text(
                    "type", to="employer_frequency_raw"
                ),
                start_date=d.day("effective_from", to="start_date"),
                stop_date=d.day("effective_to", to="stop_date"),
                payee_code=sid(vendor.get("id")),
                payee_name=vendor.text("name", to="payee_name"),
                memo=d.text("memo", to="memo"),
            )
        )
    return out


# --- payroll -------------------------------------------------------------------------------------
def payroll(payload: Any, ctx: SourceContext, c: Collector) -> list[PayrollRun | Paycheck | None]:
    require(ctx, "company_id")
    out: list[PayrollRun | Paycheck | None] = []
    run_id = ctx.payroll_id
    run_origin: str | None = None
    if isinstance(payload, dict) and "payrolls" in payload:
        return [_run(r, ctx, c) for r in _objects(c, payload, "payrolls")]
    if isinstance(payload, dict) and ("payroll" in payload or "pay_statements" in payload):
        if isinstance(payload.get("payroll"), dict):
            run = _run(c.src(payload["payroll"], "$.payroll"), ctx, c)
            out.append(run)
            run_id = run.source_id if run else run_id
            run_origin = "$.payroll.id" if run else None
        statements = _objects(c, payload.get("pay_statements") or [], prefix="$.pay_statements")
    else:
        statements = _objects(c, payload, "pay_statements")
    if not run_id:
        c.warn("no_run_link", "no payroll id supplied; paychecks are not linked to a run")
    out.extend(_paycheck(s, run_id, ctx, c, run_origin) for s in statements)
    if statements:
        c.info("net_pay_not_exposed", "UKG pay statements document no net pay total")
    return out


def _run(r: Src, ctx: SourceContext, c: Collector) -> PayrollRun | None:
    run_id = sid(r.get("id"))
    c.focus(run_id)
    if not run_id:
        c.error("missing_id", "payroll id is missing", r.path)
        return None
    ein_ref = r.sub("ein")
    c.trace_read(r, "id", "copy", "source_id")
    c.trace_read(ein_ref, "id", "copy", "ein_id", anchor=r)
    return c.build(
        PayrollRun,
        r,
        "GET /v2/companies/{cid}/payrolls/{id}",
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=run_id,
        name=r.text("name", to="name"),
        pay_date=r.day("pay_date", to="pay_date"),
        period_start=r.day("start_date", to="period_start"),
        period_end=r.day("end_date", to="period_end"),
        direct_deposit_date=r.day("direct_deposit_date", to="direct_deposit_date"),
        run_type_raw=r.sub("type").text("display_name", to="run_type_raw"),
        status_raw=r.text("status", to="status_raw"),
        ein_id=sid(ein_ref.get("id")),
    )


def _paycheck(
    s: Src, run_id: str | None, ctx: SourceContext, c: Collector, run_origin: str | None = None
) -> Paycheck | None:
    stmt_id = sid(s.get("id"))
    c.focus(stmt_id)
    emp_ref = s.sub("employee")
    emp_id = sid(emp_ref.get("account_id")) or ctx.employee_id
    if not stmt_id or not emp_id:
        c.error("missing_id", "pay statement needs id and employee.account_id", s.path)
        return None
    earnings = _earnings(s)
    amounts = [e.amount for e in earnings if e.amount is not None]
    derived = []
    gross = None
    if amounts:
        # UKG documents no gross/net totals on a pay statement. Gross = sum of earning lines is
        # computed here and flagged; net pay is NOT derived (what `payments` means is undocumented).
        gross = sum(amounts, Decimal(0))
        derived.append("gross_pay")
    c.trace_read(s, "id", "copy", "source_id")
    if sid(emp_ref.get("account_id")):
        c.trace_read(emp_ref, "account_id", "copy", "employee_id", anchor=s)
    else:
        c.trace_context("employee_id", "employee_id", ctx.employee_id, s)
    if run_id and run_origin:
        c.trace_sources(
            "run_id",
            "copy",
            [(run_origin, run_id)],
            s,
            "the payroll object sent together with these statements",
        )
    elif run_id:
        c.trace_context("run_id", "payroll_id", ctx.payroll_id, s)
    if gross is not None:
        c.trace_sources(
            "gross_pay",
            "derive",
            [(f"{s.path}.earnings", f"{len(amounts)} earning lines")],
            s,
            "UKG documents no gross total: gross is the sum of the earning amounts (no net pay)",
        )
    return c.build(
        Paycheck,
        s,
        "GET /v2/companies/{cid}/payrolls/{id}/pay-statements/{id}",
        derived=derived,
        provider=PROVIDER,
        tenant_id=ctx.tenant_id,
        company_id=ctx.company_id,
        source_id=stmt_id,
        run_id=run_id,
        employee_id=emp_id,
        paycheck_type_raw=s.sub("type").text("display_name", to="paycheck_type_raw"),
        status_raw=s.text("status", to="status_raw"),
        pay_date=s.day("pay_date", to="pay_date"),
        period_start=s.day("pay_period_start", to="period_start"),
        period_end=s.day("pay_period_end", to="period_end"),
        gross_pay=gross,
        earnings=earnings,
        taxes=_taxes(s),
        deductions=_deductions(s),
        payments=_payments(s),
    )


def _payments(s: Src) -> list[PaycheckPayment]:
    """The documented `payments` array of a pay statement: `ee_amount` and `type_name`, as given.

    What a payment IS (a net amount, a split, a tender) is not documented, so nothing is derived
    from it: no net pay, no reconciliation. The other keys (id, hours, quantity, batch) stay in
    the paycheck's extras.
    """
    return [
        PaycheckPayment(line_no=i, amount=p.dec("ee_amount"), description=p.text("type_name"))
        for i, p in enumerate(s.items("payments"), start=1)
    ]


def _earnings(s: Src) -> list[EarningLine]:
    out = []
    for e in s.items("earnings"):
        kind = e.sub("earning")
        out.append(
            EarningLine(
                line_no=len(out) + 1,
                source_kind="earnings",
                code=kind.text("code") or kind.text("external_id"),
                description=kind.text("name"),
                hours=e.dec("hours"),
                units=e.dec("quantity"),
                rate=e.dec("base_rate"),
                amount=e.dec("ee_amount"),
                work_date_from=e.day("date_range_from"),
                work_date_to=e.day("date_range_to"),
                extras=e.leftover(),
            )
        )
    return out


def _taxes(s: Src) -> list[TaxLine]:
    out = []
    for t in s.items("taxes"):
        tax = t.sub("company_tax")
        code, name = tax.text("code"), tax.text("name")
        ee, er = t.dec("ee_amount"), t.dec("er_amount")
        ee_wage, er_wage = t.dec("ee_subject_wage"), t.dec("er_subject_wage")
        # Remaining wage fields (gross wages etc.) ride along on the first line of this tax.
        rest = t.leftover()
        common = dict(source_kind="taxes", code=code, description=name)
        lines = []
        if ee is not None or ee_wage is not None:
            lines.append(
                TaxLine(
                    line_no=0,
                    liability=Liability.EMPLOYEE,
                    amount=ee,
                    taxable_wage=ee_wage,
                    **common,
                )
            )
        if er is not None or er_wage is not None:
            lines.append(
                TaxLine(
                    line_no=0,
                    liability=Liability.EMPLOYER,
                    amount=er,
                    taxable_wage=er_wage,
                    **common,
                )
            )
        if not lines:
            lines.append(TaxLine(line_no=0, **common))
        lines[0].extras = rest
        for ln in lines:
            ln.line_no = len(out) + 1
            out.append(ln)
    return out


def _deductions(s: Src) -> list[DeductionLine]:
    out = []
    for d in s.items("deductions"):
        ded = d.sub("deduction")
        out.append(
            DeductionLine(
                line_no=len(out) + 1,
                source_kind="deductions",
                code=ded.text("code") or ded.text("external_id"),
                description=ded.text("name"),
                amount=d.dec("ee_amount"),
                employer_amount=d.dec("er_amount"),
                extras=d.leftover(),
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
