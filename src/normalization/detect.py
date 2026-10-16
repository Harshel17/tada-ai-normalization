# ruff: noqa: E501  (user-facing message strings are kept on one line for readability)
"""Provider-aware detection of what a JSON file contains.

Deterministic rules, no guessing and no LLM. Each rule looks for a documented wrapper key and/or a
set of DISTINCTIVE fields (never generic ones such as `id`, `name`, `status`, `amount`) and records
the evidence that triggered it. The shapes recognised are exactly the ones the adapters accept.

Vocabulary
  unit      one entity found in a file, e.g. "3 direct deposits nested inside the employee records"
  keys      the top-level keys of a wrapper document that this unit reads (None = the whole document)
  strong    enough distinctive evidence to classify without asking
  weak      some evidence, not enough: reported as AMBIGUOUS and the user must confirm
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from .canonical.enums import EntityType, Provider
from .provider_errors import provider_error

# keys of a paged list envelope (isolved: {results, nextPageUrl, previousPageUrl, totalItems, totalPages})
PAGING_KEYS = {"results", "nextPageUrl", "previousPageUrl", "totalItems", "totalPages"}
CONTEXT_KEYS = ("company_id", "employee_id", "payroll_id")

# Top-level keys that appear on provider response envelopes and carry no record data.
ENVELOPE_KEYS = {
    "errorCode", "errorMessage", "infoMessage", "updateMessage", "extension", "total", "count",
    "startpage", "totalItems", "totalPages", "nextPageUrl", "previousPageUrl", "links", "_links",
    "_total_count", "is_eligible_for_paypal",
}  # fmt: skip


class Unit(BaseModel):
    """One entity found in a file."""

    entity: EntityType
    shape: str  # which documented response shape this is, e.g. "getEmployee response"
    label: str  # human wording for the main count, e.g. "employee records"
    keys: list[str] | None = None  # top-level keys to slice out (None = whole document)
    counts: dict[str, int] = Field(
        default_factory=dict
    )  # expected canonical records by record_type
    derived_counts: dict[str, int] = Field(default_factory=dict)  # records we create, not copy
    evidence: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)  # context ids that MUST be supplied
    optional: list[str] = Field(
        default_factory=list
    )  # ids that improve linking but are not required
    nested_in: str | None = None  # "employee" when found inside employee records
    convention: bool = False  # a Tada bundle wrapper, not a real provider response
    strength: Literal["strong", "weak"] = "strong"
    notes: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    entity: EntityType
    reason: str


class Detection(BaseModel):
    status: Literal["ok", "ambiguous", "unsupported", "empty"]
    units: list[Unit] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)  # for status == ambiguous
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None
    provider_hint: str | None = None  # "this looks like PrismHR data" when another provider fits


# --- helpers ----------------------------------------------------------------------------------
def objs(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def has(o: dict, *keys: str) -> bool:
    return all(o.get(k) not in (None, "", [], {}) for k in keys)


def any_of(o: dict, *keys: str) -> list[str]:
    return [k for k in keys if o.get(k) not in (None, "", [], {})]


def items_of(doc: Any, wrapper: str | None) -> tuple[list[dict], str | None, str]:
    """Records in the shapes adapters accept: {wrapper: [...]}, {wrapper: {...}}, a bare list, a bare object.

    Returns (items, wrapper_key_used, kind) where kind is "wrapped", "list" or "object".
    """
    if isinstance(doc, list):
        return objs(doc), None, "list"
    if isinstance(doc, dict):
        if wrapper and wrapper in doc:
            inner = doc[wrapper]
            if isinstance(inner, list):
                return objs(inner), wrapper, "wrapped"
            if isinstance(inner, dict):
                return [inner], wrapper, "wrapped"
            return [], wrapper, "wrapped"
        return [doc], None, "object"
    return [], None, "object"


def keys_for(wrapper: str | None) -> list[str] | None:
    return [wrapper] if wrapper else None


def evidence_for(kind: str, wrapper: str | None, signals: list[str]) -> list[str]:
    where = {
        "wrapped": f"wrapper key “{wrapper}”",
        "list": "a list of records",
        "object": "a single record",
    }[kind]
    return [f"Found {where}", *signals]


# --- PrismHR ------------------------------------------------------------------------------------
PRISM_EMPLOYEE_SECTIONS = ("person", "client", "compensation", "contactInformation")
# Every top-level section name of the getEmployee Employee schema. The live API returns ALL of them, with
# null for sections that were not requested via `options`, so their presence is evidence even when empty.
PRISM_EMPLOYEE_SCHEMA_KEYS = frozenset(
    {
        "person", "contactInformation", "client", "compensation", "directDeposit", "hsa",
        "employeeEvents", "health", "skillsAndEducation", "absenceJournalId",
        "benefitEnrollmentCompleted", "scheduledDeduction", "newHireQuestions", "enrollmentInProgress",
    }
)  # fmt: skip


def prismhr(doc: Any) -> tuple[list[Unit], list[str]]:
    units: list[Unit] = []
    used: set[str] = set()
    if isinstance(doc, dict):
        cm = doc.get("clientMaster")
        if isinstance(cm, dict):
            sig = any_of(
                cm, "legalName", "clientName", "federalIdNumber", "serviceType", "statusClass", "id"
            )
            units.append(
                Unit(
                    entity=EntityType.COMPANY,
                    shape="getClientMaster response",
                    label="employer record",
                    keys=["clientMaster"],
                    counts={"company": 1},
                    evidence=[
                        "Found wrapper key “clientMaster”",
                        f"Employer fields present: {', '.join(sig)}",
                    ],
                    strength="strong" if len(sig) >= 2 else "weak",
                    requires=[],
                    optional=["company_id"],
                )
            )
            used.add("clientMaster")
        ed = doc.get("employeeDeductions")
        if isinstance(ed, dict) and isinstance(ed.get("deduction"), list):
            rows = [r for r in objs(ed["deduction"]) if has(r, "deductionCode")]
            empty = (
                ed["deduction"] == []
            )  # a valid answer: this employee has no recurring deductions
            if rows or empty:
                units.append(
                    Unit(
                        entity=EntityType.DEDUCTION,
                        shape="getDeductions response",
                        label="recurring deductions",
                        keys=["employeeDeductions"],
                        counts={"deduction": len(objs(ed["deduction"]))},
                        evidence=[
                            "Found wrapper key “employeeDeductions” with a “deduction” list",
                            "The list is empty: a valid response with zero records"
                            if empty
                            else "Items carry deductionCode",
                        ],
                        requires=["company_id", "employee_id"],
                        notes=[
                            "This response does not say which employee it belongs to.",
                            *(
                                ["No deduction records will be created from an empty list."]
                                if empty
                                else []
                            ),
                        ],
                    )
                )
                used.add("employeeDeductions")
        voucher_key = "payrollVouchers" if "payrollVouchers" in doc else "payrollVoucher"
        pv = doc.get(voucher_key)
        if isinstance(pv, list):
            vouchers = objs(pv)
            sig = [
                r
                for r in vouchers
                if has(r, "voucherId") and any_of(r, "employeeId", "payDate", "netPay")
            ]
            if sig:
                batches = {
                    str(v["batchId"]) for v in vouchers if v.get("batchId") not in (None, "")
                }
                units.append(
                    Unit(
                        entity=EntityType.PAYROLL,
                        shape="getPayrollVouchers response",
                        label="paychecks (vouchers)",
                        keys=[voucher_key],
                        counts={"paycheck": len(vouchers)},
                        derived_counts={"payroll_run": len(batches)},
                        evidence=[
                            f"Found wrapper key “{voucher_key}”",
                            "Items carry voucherId plus pay fields",
                        ],
                        requires=["company_id"],
                        notes=[
                            "PrismHR sends no pay-run record, so one run is created per distinct batchId."
                        ]
                        if batches
                        else [],
                    )
                )
                used.add(voucher_key)
        emp_items, emp_wrapper, kind = (
            items_of(doc, "employee") if "employee" in doc else ([], None, "object")
        )
        if "employee" in doc and isinstance(doc["employee"], (list, dict)):
            units += _prism_employees(emp_items, "wrapped", "employee")
            used.add("employee")
        elif not used:
            units += _prism_employees([doc], "object", None)
    elif isinstance(doc, list):
        vouchers = objs(doc)
        if vouchers and any(
            has(v, "voucherId") and any_of(v, "employeeId", "payDate", "netPay") for v in vouchers
        ):
            batches = {str(v["batchId"]) for v in vouchers if v.get("batchId") not in (None, "")}
            units.append(
                Unit(
                    entity=EntityType.PAYROLL,
                    shape="list of payroll vouchers",
                    label="paychecks (vouchers)",
                    counts={"paycheck": len(vouchers)},
                    derived_counts={"payroll_run": len(batches)},
                    evidence=["Found a list of records carrying voucherId plus pay fields"],
                    requires=["company_id"],
                )
            )
        else:
            units += _prism_employees(objs(doc), "list", None)
    return units, sorted(
        k
        for k in (doc.keys() if isinstance(doc, dict) else [])
        if k not in used and k not in ENVELOPE_KEYS
    )


def _prism_employees(items: list[dict], kind: str, wrapper: str | None) -> list[Unit]:
    if not items:
        return []
    sectioned = [
        i for i in items if any(isinstance(i.get(s), dict) for s in PRISM_EMPLOYEE_SECTIONS)
    ]
    sections_seen = sorted(
        {s for i in items for s in PRISM_EMPLOYEE_SECTIONS if isinstance(i.get(s), dict)}
    )
    strong = bool(sectioned) and (len(sections_seen) >= 2 or kind == "wrapped")
    # A person-only response (no `options`): sections are present as null. Three or more of PrismHR's own
    # section names plus an id and a name is reliable evidence; a bare name alone still is not.
    schema_hits = sorted(
        {
            k
            for i in items
            if has(i, "id") and (has(i, "firstName") or has(i, "lastName"))
            for k in PRISM_EMPLOYEE_SCHEMA_KEYS
            if k in i
        }
    )
    schema_strong = len(schema_hits) >= 3
    named = [i for i in items if has(i, "firstName") or has(i, "lastName")]
    if not strong and not schema_strong and not named:
        return []
    strong = strong or schema_strong
    if sections_seen:
        signals = [f"Employee sections present: {', '.join(sections_seen)}"]
    elif schema_strong:
        signals = [
            "Only the person-level fields are filled; the other sections are empty because they were not requested",
            f"PrismHR employee section names present: {', '.join(schema_hits)}",
        ]
    else:
        signals = ["Only name fields found (no person/client/compensation sections)"]
    ev = evidence_for(kind, wrapper, signals)
    units = [
        Unit(
            entity=EntityType.EMPLOYEE,
            shape="getEmployee response",
            label="employee records",
            keys=keys_for(wrapper),
            counts={"employee": len(items)},
            evidence=ev,
            requires=["company_id"],
            strength="strong" if strong else "weak",
        )
    ]
    if strong:
        ach = sum(
            len(objs((i.get("directDeposit") or {}).get("achVoucher")))
            for i in items
            if isinstance(i.get("directDeposit"), dict)
        )
        sched = sum(len(objs(i.get("scheduledDeduction"))) for i in items)
        if ach:
            units.append(
                Unit(
                    entity=EntityType.DIRECT_DEPOSIT,
                    shape="getEmployee response",
                    label="direct deposit instructions",
                    keys=keys_for(wrapper),
                    counts={"direct_deposit": ach},
                    nested_in="employee",
                    evidence=["Found directDeposit.achVoucher[] inside the employee records"],
                    requires=["company_id"],
                )
            )
        if sched:
            units.append(
                Unit(
                    entity=EntityType.DEDUCTION,
                    shape="getEmployee response",
                    label="scheduled deductions",
                    keys=keys_for(wrapper),
                    counts={"deduction": sched},
                    nested_in="employee",
                    evidence=["Found scheduledDeduction[] inside the employee records"],
                    requires=["company_id"],
                )
            )
    return units


# --- isolved ------------------------------------------------------------------------------------
def isolved(doc: Any) -> tuple[list[Unit], list[str]]:
    units: list[Unit] = []
    used: set[str] = set()
    if isinstance(doc, dict) and ("payroll_run" in doc or "checks" in doc):
        run = doc.get("payroll_run")
        checks = objs(doc.get("checks"))
        run_ok = isinstance(run, dict) and any_of(
            run, "payrollRunType", "runNumber", "scheduledPayDate", "payOfTheMonth"
        )
        check_ok = [c for c in checks if _isolved_check(c)]
        if run_ok or check_ok:
            counts, ev = (
                {},
                [
                    "Found a Tada bundle wrapper (payroll_run / checks): our convention, not a provider response"
                ],
            )
            if run_ok:
                counts["payroll_run"] = 1
                ev.append("payroll_run carries run-history fields")
            if checks:
                counts["paycheck"] = len(checks)
                ev.append("checks[] carry pay-check fields")
            units.append(
                Unit(
                    entity=EntityType.PAYROLL,
                    shape="payroll run + checks bundle",
                    label="payroll run and paychecks",
                    keys=[k for k in ("payroll_run", "checks") if k in doc],
                    counts=counts,
                    evidence=ev,
                    requires=["company_id"],
                    convention=True,
                )
            )
            used |= {"payroll_run", "checks"}
        return units, sorted(k for k in doc if k not in used and k not in ENVELOPE_KEYS)

    items, wrapper, kind = items_of(doc, "results")
    if not items:
        return [], []
    emp = [i for i in items if isinstance(i.get("nameAddress"), dict)]
    emp_strong = [
        i
        for i in emp
        if any_of(
            i,
            "employeeNumber",
            "employmentStatus",
            "hireDate",
            "payGroup",
            "payType",
            "emailAddress",
        )
    ]
    legals = [i for i in items if has(i, "legalName")]
    legal_strong = [
        i
        for i in legals
        if any_of(i, "federalEIN", "legalCode", "legalAddress", "payGroups", "client")
    ]
    deposits = [i for i in items if any_of(i, "accountNumber", "routingNumber")]
    deposit_strong = [
        i
        for i in deposits
        if any_of(i, "sequenceNumber", "bankName", "accountType", "percent", "amount")
    ]
    deds = [i for i in items if has(i, "payItemCode")]
    deds_strong = [i for i in deds if any_of(i, "calcParams", "employeeId", "startDate")]
    checks = [i for i in items if _isolved_check(i)]

    def add(
        entity,
        shape,
        label,
        group,
        strong_group,
        counts,
        signals,
        requires,
        optional=(),
        nested=None,
        notes=(),
    ):
        if not group:
            return
        units.append(
            Unit(
                entity=entity,
                shape=shape,
                label=label,
                keys=keys_for(wrapper),
                counts=counts,
                evidence=evidence_for(kind, wrapper, signals),
                requires=list(requires),
                optional=list(optional),
                nested_in=nested,
                strength="strong" if strong_group else "weak",
                notes=list(notes),
            )
        )

    add(
        EntityType.COMPANY,
        "legal companies list",
        "legal company records",
        legals,
        legal_strong,
        {"company": len(items)},
        [
            "Legal-company fields: "
            + ", ".join(
                sorted(
                    {
                        k
                        for i in legal_strong
                        for k in any_of(
                            i,
                            "legalName",
                            "federalEIN",
                            "legalCode",
                            "legalAddress",
                            "payGroups",
                            "client",
                        )
                    }
                )
            )
        ]
        if legal_strong
        else ["Only legalName found"],
        [],
        optional=["company_id"],
    )
    add(
        EntityType.EMPLOYEE,
        "employees list",
        "employee records",
        emp,
        emp_strong,
        {"employee": len(items)},
        [
            "Employee fields: nameAddress"
            + (
                ", "
                + ", ".join(
                    sorted(
                        {
                            k
                            for i in emp_strong
                            for k in any_of(
                                i,
                                "employeeNumber",
                                "employmentStatus",
                                "hireDate",
                                "payGroup",
                                "payType",
                                "emailAddress",
                            )
                        }
                    )
                )
                if emp_strong
                else ""
            )
        ],
        ["company_id"],
    )
    if emp_strong:
        nested = sum(len(objs(i.get("directDeposits"))) for i in emp_strong)
        if nested:
            units.append(
                Unit(
                    entity=EntityType.DIRECT_DEPOSIT,
                    shape="employees list",
                    label="direct deposit instructions",
                    keys=keys_for(wrapper),
                    counts={"direct_deposit": nested},
                    nested_in="employee",
                    requires=["company_id"],
                    evidence=["Found directDeposits[] inside the employee records"],
                )
            )
    add(
        EntityType.DIRECT_DEPOSIT,
        "direct deposits list",
        "direct deposit instructions",
        [] if emp else deposits,
        deposit_strong and not emp,
        {"direct_deposit": len(items)},
        [
            "Bank fields: "
            + ", ".join(
                sorted(
                    {
                        k
                        for i in deposit_strong
                        for k in any_of(
                            i,
                            "accountNumber",
                            "routingNumber",
                            "bankName",
                            "sequenceNumber",
                            "accountType",
                        )
                    }
                )
            )
        ],
        ["company_id", "employee_id"],
        notes=["A bare deposit list does not say which employee it belongs to."],
    )
    needs_emp = [] if all(has(i, "employeeId") for i in deds) else ["employee_id"]
    add(
        EntityType.DEDUCTION,
        "employee deductions list",
        "deduction setups",
        deds,
        deds_strong,
        {"deduction": len(items)},
        [
            "Deduction fields: payItemCode"
            + (", calcParams" if any(has(i, "calcParams") for i in deds) else "")
        ],
        ["company_id", *needs_emp],
    )
    add(
        EntityType.PAYROLL,
        "pay checks",
        "paychecks",
        checks,
        checks,
        {"paycheck": len(items)},
        [
            "Check fields: "
            + ", ".join(
                sorted(
                    {
                        k
                        for i in checks
                        for k in any_of(
                            i, "checkDate", "checkNumber", "grossPay", "netPay", "earnings", "taxes"
                        )
                    }
                )
            )
        ],
        ["company_id"],
        notes=["No payroll run was supplied, so these paychecks are not linked to a run."],
    )
    if not units and any(
        any_of(i, "clientName", "clientCode") and "legalCompanies" in i for i in items
    ):
        return [], []  # recognised but unsupported: handled by explain_unsupported
    return units, []


def _isolved_check(c: dict) -> bool:
    return (
        bool(any_of(c, "checkDate", "checkNumber", "checkAmount"))
        and bool(any_of(c, "earnings", "taxes", "grossPay", "netPay"))
        and has(c, "employeeId")
    )


# --- UKG Ready ------------------------------------------------------------------------------------
UKG_BUNDLE_PARTS = ("demographics", "pay_info", "compensation_history")


def ukg_ready(doc: Any) -> tuple[list[Unit], list[str]]:
    units: list[Unit] = []
    used: set[str] = set()
    if isinstance(doc, dict):
        eins = objs(doc.get("items")) if isinstance(doc.get("items"), list) else []
        if eins and any(has(e, "ein_tax_id") for e in eins):
            units.append(
                Unit(
                    entity=EntityType.COMPANY,
                    shape="EIN lookup response",
                    label="EINs (company tax entities)",
                    keys=["items"],
                    counts={"company": 1},
                    evidence=["Found wrapper key “items”", "Items carry ein_tax_id"],
                    requires=["company_id"],
                    notes=[
                        f"{len(eins)} EIN(s) become legal entities of ONE company record; UKG exposes no company name."
                    ],
                )
            )
            used.add("items")
        emp_obj = doc.get("employee")
        bundle_parts = [p for p in UKG_BUNDLE_PARTS if p in doc]
        # `employee` is a bundle only when it holds a profile (a name) or the bundle parts are present.
        # Otherwise it is a REFERENCE to the owner, e.g. {"account_id": 1} on a pay statement or a deposit.
        if isinstance(emp_obj, dict) and (
            has(emp_obj, "first_name") or has(emp_obj, "last_name") or bundle_parts
        ):
            parts = bundle_parts
            units.append(
                Unit(
                    entity=EntityType.EMPLOYEE,
                    shape="employee bundle",
                    label="employee record (profile + parts)",
                    keys=["employee", *parts],
                    counts={"employee": 1},
                    convention=True,
                    requires=["company_id"],
                    evidence=[
                        "Found a Tada bundle wrapper (employee + "
                        + (", ".join(parts) or "no other parts")
                        + "): our convention, not a provider response"
                    ],
                    strength="strong" if parts else "weak",
                )
            )
            used |= {"employee", *parts}
        emps = objs(doc.get("employees")) if isinstance(doc.get("employees"), list) else []
        if emps and all(
            any_of(e, "first_name", "last_name", "employee_id", "username") for e in emps
        ):
            units.append(
                Unit(
                    entity=EntityType.EMPLOYEE,
                    shape="employees list (summaries)",
                    label="employee summaries",
                    keys=["employees"],
                    counts={"employee": len(emps)},
                    requires=["company_id"],
                    evidence=[
                        "Found wrapper key “employees”",
                        "Items carry first_name / last_name / employee_id",
                    ],
                )
            )
            used.add("employees")
        for wrapper, entity, shape, label, rec, test in (
            (
                "deposits",
                EntityType.DIRECT_DEPOSIT,
                "direct deposits response",
                "direct deposit instructions",
                "direct_deposit",
                _ukg_deposit,
            ),
            (
                "deductions",
                EntityType.DEDUCTION,
                "deductions response",
                "deduction setups",
                "deduction",
                _ukg_deduction,
            ),
        ):
            rows = objs(doc.get(wrapper)) if isinstance(doc.get(wrapper), list) else []
            if wrapper == "deposits" and _empty_wrapper(doc, wrapper):
                units.append(
                    Unit(
                        entity=entity,
                        shape=shape,
                        label=label,
                        keys=[wrapper],
                        counts={rec: 0},
                        evidence=[
                            f"Found wrapper key “{wrapper}”",
                            "The list is empty: a valid response with zero records",
                        ],
                        requires=["company_id", "employee_id"],
                        notes=[
                            "This response does not say which employee it belongs to.",
                            "No records will be created from an empty list.",
                        ],
                    )
                )
                used.add(wrapper)
            elif rows and any(test(r) for r in rows):
                own = rec == "direct_deposit" and all(
                    isinstance(r.get("employee"), dict) and has(r["employee"], "account_id")
                    for r in rows
                )
                units.append(
                    Unit(
                        entity=entity,
                        shape=shape,
                        label=label,
                        keys=[wrapper],
                        counts={rec: len(rows)},
                        evidence=[
                            f"Found wrapper key “{wrapper}”",
                            "Items carry "
                            + (
                                "bank and calculation fields"
                                if rec == "direct_deposit"
                                else "a deduction object"
                            ),
                        ],
                        requires=["company_id"] if own else ["company_id", "employee_id"],
                        notes=[]
                        if own
                        else ["This response does not say which employee it belongs to."],
                    )
                )
                used.add(wrapper)
        runs = objs(doc.get("payrolls")) if isinstance(doc.get("payrolls"), list) else []
        if runs and any(_ukg_run(r) for r in runs):
            units.append(
                Unit(
                    entity=EntityType.PAYROLL,
                    shape="payrolls list",
                    label="payroll runs",
                    keys=["payrolls"],
                    counts={"payroll_run": len(runs)},
                    evidence=[
                        "Found wrapper key “payrolls”",
                        "Items carry pay_date / start_date / end_date",
                    ],
                    requires=["company_id"],
                )
            )
            used.add("payrolls")
        if "payroll" in doc or "pay_statements" in doc:
            run = doc.get("payroll")
            stmts = (
                objs(doc.get("pay_statements"))
                if isinstance(doc.get("pay_statements"), list)
                else []
            )
            run_ok = isinstance(run, dict) and _ukg_run(run)
            if not run_ok and _empty_wrapper(doc, "pay_statements"):
                units.append(
                    Unit(
                        entity=EntityType.PAYROLL,
                        shape="pay statements response",
                        label="pay statements",
                        keys=["pay_statements"],
                        counts={"paycheck": 0},
                        requires=["company_id"],
                        optional=["payroll_id"],
                        evidence=[
                            "Found wrapper key “pay_statements”",
                            "The list is empty: a valid response with zero records",
                        ],
                        notes=["No records will be created from an empty list."],
                    )
                )
                used.add("pay_statements")
            elif run_ok or stmts:
                counts = {}
                if run_ok:
                    counts["payroll_run"] = 1
                if stmts:
                    counts["paycheck"] = len(stmts)
                units.append(
                    Unit(
                        entity=EntityType.PAYROLL,
                        shape="payroll + pay statements bundle",
                        label="payroll run and pay statements",
                        keys=[k for k in ("payroll", "pay_statements") if k in doc],
                        counts=counts,
                        convention=True,
                        requires=["company_id"],
                        optional=[] if run_ok else ["payroll_id"],
                        evidence=[
                            "Found a Tada bundle wrapper (payroll / pay_statements): our convention, not a provider response"
                        ],
                    )
                )
                used |= {"payroll", "pay_statements"}
        if not used:
            profile = _ukg_profile(doc)
            statement = _ukg_statement(doc)
            dep = _ukg_deposit(doc)
            ded = _ukg_deduction(doc)
            if profile and not (dep or ded or statement):
                units.append(
                    Unit(
                        entity=EntityType.EMPLOYEE,
                        shape="employee profile",
                        label="employee record",
                        counts={"employee": 1},
                        requires=["company_id"],
                        evidence=[
                            "A single record with first_name / last_name and profile fields: "
                            + ", ".join(profile)
                        ],
                    )
                )
            if statement and not (dep or ded):
                units.append(
                    Unit(
                        entity=EntityType.PAYROLL,
                        shape="single pay statement",
                        label="pay statement",
                        counts={"paycheck": 1},
                        requires=["company_id"],
                        optional=["payroll_id"],
                        evidence=[
                            "A single record with earnings/taxes/deductions lines and a pay period"
                        ],
                    )
                )
            if dep and not (ded or statement or profile):
                own = isinstance(doc.get("employee"), dict)
                units.append(
                    Unit(
                        entity=EntityType.DIRECT_DEPOSIT,
                        shape="single direct deposit",
                        label="direct deposit instruction",
                        counts={"direct_deposit": 1},
                        requires=["company_id"] if own else ["company_id", "employee_id"],
                        evidence=["A single record with calc_method and bank fields"],
                    )
                )
            if ded and not (dep or statement or profile):
                units.append(
                    Unit(
                        entity=EntityType.DEDUCTION,
                        shape="single deduction",
                        label="deduction setup",
                        counts={"deduction": 1},
                        requires=["company_id", "employee_id"],
                        evidence=["A single record with a deduction object and effective dates"],
                    )
                )
        return units, sorted(k for k in doc if k not in used and k not in ENVELOPE_KEYS)
    if isinstance(doc, list):
        rows = objs(doc)
        if rows and any(_ukg_deposit(r) for r in rows):
            units.append(
                Unit(
                    entity=EntityType.DIRECT_DEPOSIT,
                    shape="list of direct deposits",
                    label="direct deposit instructions",
                    counts={"direct_deposit": len(rows)},
                    requires=["company_id", "employee_id"],
                    evidence=["A list of records with calc_method and bank fields"],
                )
            )
        elif rows and any(_ukg_deduction(r) for r in rows):
            units.append(
                Unit(
                    entity=EntityType.DEDUCTION,
                    shape="list of deductions",
                    label="deduction setups",
                    counts={"deduction": len(rows)},
                    requires=["company_id", "employee_id"],
                    evidence=["A list of records with a deduction object and effective dates"],
                )
            )
    return units, []


def _ukg_deposit(r: dict) -> bool:
    return bool(any_of(r, "calc_method", "account_type", "routing_number")) and bool(
        any_of(r, "sequence", "active", "name", "type", "active_from")
    )


def _ukg_deduction(r: dict) -> bool:
    d = r.get("deduction")
    return (
        isinstance(d, dict)
        and bool(any_of(d, "code", "name"))
        and bool(any_of(r, "effective_from", "ee_calc_method", "sequence"))
    )


def _ukg_run(r: dict) -> bool:
    return bool(any_of(r, "start_date", "end_date", "pay_date")) and bool(
        any_of(r, "status", "type", "ein", "billable")
    )


STATEMENT_LINE_KEYS = ("earnings", "taxes", "deductions", "payments")


def _ukg_statement(r: dict) -> bool:
    """A pay statement: a pay date/period AND paycheck line lists (filled, or at least two present)."""
    dated = bool(any_of(r, "pay_period_start", "pay_period_end", "pay_date"))
    filled = bool(any_of(r, "earnings", "taxes", "deductions"))
    lists_present = sum(isinstance(r.get(k), list) for k in STATEMENT_LINE_KEYS) >= 2
    return dated and (filled or lists_present)


def _empty_wrapper(doc: dict, key: str) -> bool:
    """`{key: []}` plus only underscore/links/envelope keys: a valid empty list response.

    Anything else (a non-list, a non-empty list of rubbish, unrelated keys) is NOT accepted here."""
    return doc.get(key) == [] and all(
        k == key or k.startswith("_") or k in ENVELOPE_KEYS or k == "links" for k in doc
    )


def _ukg_profile(r: dict) -> list[str]:
    if not (has(r, "first_name") or has(r, "last_name")):
        return []
    extra = any_of(
        r, "username", "employee_id", "primary_email", "dates", "phones", "address", "status"
    )
    return extra if extra else []


RULES: dict[Provider, Callable[[Any], tuple[list[Unit], list[str]]]] = {
    Provider.PRISMHR: prismhr,
    Provider.ISOLVED: isolved,
    Provider.UKG_READY: ukg_ready,
}

SUPPORTED_SHAPES: dict[Provider, list[str]] = {
    Provider.PRISMHR: [
        "{clientMaster}",
        "{employee: [...]}",
        "{employeeDeductions: {deduction: [...]}}",
        "{payrollVouchers: [...]}",
    ],
    Provider.ISOLVED: [
        "a list of legal companies",
        "{results: [employees]}",
        "a list of direct deposits",
        "a list of deductions",
        "pay checks, or {payroll_run, checks}",
    ],
    Provider.UKG_READY: [
        "{items: [EINs]}",
        "an employee profile, {employees: [...]} or {employee, demographics, pay_info, ...}",
        "{deposits: [...]}",
        "{deductions: [...]}",
        "{payrolls: [...]}, a pay statement, or {payroll, pay_statements}",
    ],
}

KNOWN_UNSUPPORTED: dict[Provider, list[tuple[Callable[[Any], bool], str]]] = {
    Provider.ISOLVED: [
        (
            lambda d: any(
                isinstance(i, dict) and "legalCompanies" in i for i in items_of(d, "results")[0]
            ),
            "This is isolved’s list of clients. Convert the legal companies (a client’s “legals” list) instead.",
        ),
        (
            lambda d: (
                isinstance(d, dict)
                and bool(any_of(d, "payrollRunType", "runNumber"))
                and not has(d, "checks")
            ),
            "This is a payroll run history record on its own. Send it together with its checks as {payroll_run, checks}.",
        ),
    ],
    Provider.UKG_READY: [
        (
            lambda d: (
                isinstance(d, dict)
                and _ukg_run(d)
                and not any(k in d for k in ("payrolls", "payroll", "pay_statements", "earnings"))
            ),
            "This is a single payroll run on its own. Send runs as {payrolls: [...]} or together with pay statements as {payroll, pay_statements}.",
        ),
        (
            lambda d: (
                isinstance(d, dict)
                and bool(
                    any_of(d, "citizenship", "ethnicity", "medical_eligibility", "default_job")
                )
                and "first_name" not in d
                and "employee" not in d
            ),
            "This looks like a single UKG employee part (demographics or pay info). Send it inside an employee bundle: {employee, demographics, pay_info}.",
        ),
    ],
}


def detect(provider: Provider, doc: Any) -> Detection:
    """Classify one parsed JSON document for the chosen provider."""
    if doc is None or doc == {} or doc == []:
        return Detection(status="empty", message="The file is empty: there is nothing to convert.")
    if not isinstance(doc, (dict, list)):
        return Detection(
            status="unsupported",
            message="The file holds a single value, not a provider response (an object or a list of records).",
        )
    if isinstance(doc, dict) and doc.get("results") == [] and set(doc) <= PAGING_KEYS:
        return Detection(
            status="empty",
            message="This is an empty page of results (no records), so there is nothing to convert.",
        )
    error = provider_error(provider, doc)
    if error:  # say what it is: an error response holds no records
        code, message = error
        return Detection(
            status="unsupported",
            message=f"This is an error response from {provider_name(provider)} (code {code}: {message}), not data. Nothing can be converted from it.",
        )
    units, ignored = RULES[provider](doc)
    warnings = (
        [f"Top-level key(s) not used by any detected record type: {', '.join(ignored)}."]
        if units and ignored
        else []
    )
    strong = [u for u in units if u.strength == "strong"]
    if strong:
        weak_only = [u for u in units if u.strength == "weak"]
        extra = [
            f"Weak evidence for {u.entity.value.replace('_', ' ')} was ignored: {u.evidence[-1]}."
            for u in weak_only
        ]
        return Detection(status="ok", units=strong, warnings=[*warnings, *extra])
    if units:
        return Detection(
            status="ambiguous",
            candidates=[
                Candidate(
                    entity=u.entity,
                    reason=f"{'; '.join(u.evidence)}. Not enough distinctive fields to be certain.",
                )
                for u in units
            ],
            message="This could be more than one kind of data, or one kind with too little evidence. Please confirm what it is.",
            warnings=warnings,
        )
    for test, message in KNOWN_UNSUPPORTED.get(provider, []):
        if test(doc):
            return Detection(
                status="unsupported", message=message, provider_hint=_provider_hint(provider, doc)
            )
    hint = _provider_hint(provider, doc)
    return Detection(
        status="unsupported",
        message=f"This does not match any {provider_name(provider)} response shape that this tool converts.",
        warnings=[f"Supported shapes: {'; '.join(SUPPORTED_SHAPES[provider])}."],
        provider_hint=hint,
    )


def provider_name(p: Provider) -> str:
    return {"prismhr": "PrismHR", "isolved": "isolved", "ukg-ready": "UKG Ready"}[p.value]


def _provider_hint(selected: Provider, doc: Any) -> str | None:
    """If another provider's rules match strongly, say so. Never switches provider automatically."""
    for other, rule in RULES.items():
        if other is selected:
            continue
        units, _ = rule(doc)
        strong = [u for u in units if u.strength == "strong"]
        if strong:
            return f"This looks like {provider_name(other)} data ({strong[0].label}), but {provider_name(selected)} is selected."
    return None


def unit_for_override(provider: Provider, doc: Any, entity: EntityType) -> tuple[Unit | None, str]:
    """Validate a user's choice of entity against the structure the adapter expects."""
    units, _ = RULES[provider](doc) if isinstance(doc, (dict, list)) else ([], [])
    for u in units:
        if u.entity is entity:
            return u.model_copy(
                update={"strength": "strong", "evidence": [*u.evidence, "Confirmed by you"]}
            ), ""
    shapes = "; ".join(SUPPORTED_SHAPES[provider])
    return (
        None,
        f"This file does not have the structure of {entity.value.replace('_', ' ')} data for {provider_name(provider)}. Expected one of: {shapes}.",
    )
