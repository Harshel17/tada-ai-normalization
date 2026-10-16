# ruff: noqa: E501  (the user-facing sentences stay on one line each)
"""Deterministic payroll integrity checks over the canonical records of one conversion.

Pure functions: no database, no network, no mutation of the records. Every check returns one of four statuses:

  passed         every input the check needs was present, and the rule held
  warning        the rule is expected to hold, but legitimate exceptions exist (so it is never "failed")
  failed         the rule cannot legitimately be false in valid data
  not_evaluated  a required input or assumption is missing; the reason says which (nothing is guessed)

Only rules that held on the live demo data (PrismHR, isolved, UKG Ready) are included. Deliberately NOT included,
because the live data showed they give false alarms: gross - taxes - deductions = net (deduction and benefit
semantics differ per provider and some responses omit tax lines) and check amount = net pay (a split paycheck's
check is smaller than net). The gross-to-net bridge is reported as not evaluated, with that reason.

The report holds only record ids, field names, dates and amounts: no names, SSNs or account data.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["passed", "warning", "failed", "not_evaluated"]
TOLERANCE = Decimal("0.01")  # cents: tolerates rounding, never a real difference
MAX_EXAMPLES = 10
# A money rule only runs for providers where it held on the live demo data. Elsewhere the check is reported as
# not evaluated WITH the reason, so a systematic difference in what a provider calls "gross" is never shown as an error.
EARNINGS_GROSS_TRUSTED = {"prismhr"}  # live: earnings added up to gross on every one of 50 vouchers
PAYMENTS_NET_TRUSTED = {
    "prismhr"
}  # live: payments added up to net on every one of 32 vouchers with payments
UNTRUSTED_REASON = {
    "isolved": "on live isolved data gross pay and the listed earning lines differed on every comparable check, so the comparison is not trusted",
}
DEPOSIT_TYPES = ("direct_deposit",)


class Finding(BaseModel):
    record: str  # "paycheck V-5001": the record type and its provider id
    detail: str


class Check(BaseModel):
    id: str
    group: str
    title: str
    status: Status
    reason: str
    evaluated: int = 0  # how many records (or groups) the rule really ran on
    flagged: int = 0  # how many broke it
    examples: list[Finding] = Field(default_factory=list)


class IntegrityReport(BaseModel):
    summary: dict[str, int]
    checks: list[Check]


# --- helpers ------------------------------------------------------------------------------------------------
def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _day(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _label(rec: dict) -> str:
    return f"{rec.get('record_type')} {str(rec.get('source_id'))[:60]}"


def _of(records: list[dict], *types: str) -> list[dict]:
    return [r for r in records if r.get("record_type") in types]


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


def _check(
    id_: str,
    group: str,
    title: str,
    status: Status,
    reason: str,
    evaluated: int = 0,
    flagged: int = 0,
    examples: list[Finding] | None = None,
) -> Check:  # noqa: PLR0913
    return Check(
        id=id_,
        group=group,
        title=title,
        status=status,
        reason=reason,
        evaluated=evaluated,
        flagged=flagged,
        examples=(examples or [])[:MAX_EXAMPLES],
    )


def _not_evaluated(id_: str, group: str, title: str, reason: str) -> Check:
    return _check(id_, group, title, "not_evaluated", reason)


# --- identity -----------------------------------------------------------------------------------------------
def _identity(rec: dict) -> tuple:
    employee = (
        rec.get("employee_id")
        if rec.get("record_type") in ("direct_deposit", "deduction")
        else None
    )
    return (
        rec.get("record_type"),
        rec.get("tenant_id"),
        rec.get("company_id"),
        employee,
        rec.get("source_id"),
    )


def check_duplicate_ids(records: list[dict]) -> Check:
    id_, group, title = "duplicate_ids", "Identity", "No record id appears twice"
    if not records:
        return _not_evaluated(id_, group, title, "There are no converted records.")
    seen: dict[tuple, list[dict]] = defaultdict(list)
    for rec in records:
        seen[_identity(rec)].append(rec)
    conflicts, same = [], []
    for rows in seen.values():
        if len(rows) < 2:
            continue
        bodies = [{k: v for k, v in r.items() if k != "provenance"} for r in rows]
        (same if all(b == bodies[0] for b in bodies) else conflicts).append(rows)
    if conflicts:
        ex = [
            Finding(
                record=_label(rows[0]),
                detail=f"appears {len(rows)} times with different content; saving would keep only one",
            )
            for rows in conflicts
        ]
        return _check(
            id_,
            group,
            title,
            "failed",
            f"{_plural(len(conflicts), 'record id')} used by records with different content.",
            len(seen),
            len(conflicts),
            ex,
        )
    if same:
        ex = [
            Finding(
                record=_label(rows[0]),
                detail=f"{len(rows)} source occurrences of 1 logical record, with identical content",
            )
            for rows in same
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"{_plural(len(same), 'logical record')} {_is(len(same), 'was', 'were')} found in more than one place "
            f"({_plural(sum(len(r) for r in same), 'source occurrence')} in all), each time with identical content. "
            "The same record can appear in several provider responses or as a copy embedded inside another one; "
            "this does not mean a file was uploaded twice. Saving keeps one row for each logical record.",
            len(seen),
            len(same),
            ex,
        )
    return _check(
        id_, group, title, "passed", f"All {_plural(len(seen), 'record id')} are unique.", len(seen)
    )


# --- relationships --------------------------------------------------------------------------------------------
def check_paycheck_run_link(records: list[dict]) -> Check:
    id_, group, title = (
        "paycheck_run_link",
        "Relationships",
        "Every paycheck belongs to a pay run in this conversion",
    )
    paychecks, runs = (
        _of(records, "paycheck"),
        {r["source_id"] for r in _of(records, "payroll_run")},
    )
    if not paychecks:
        return _not_evaluated(id_, group, title, "There are no paychecks.")
    linked = [p for p in paychecks if p.get("run_id")]
    if not linked:
        return _not_evaluated(
            id_,
            group,
            title,
            "The paychecks name no pay run (the provider's checks carry only a run number), so no link can be checked.",
        )
    if not runs:
        return _not_evaluated(
            id_,
            group,
            title,
            "No pay run was converted together with the paychecks, so the run ids cannot be checked.",
        )
    missing = [p for p in linked if str(p["run_id"]) not in runs]
    skipped = len(paychecks) - len(linked)
    tail = f" {_plural(skipped, 'paycheck')} name no run and were not checked." if skipped else ""
    if missing:
        ex = [
            Finding(
                record=_label(p),
                detail=f"names run {p['run_id']}, which is not among the converted runs",
            )
            for p in missing
        ]
        return _check(
            id_,
            group,
            title,
            "failed",
            f"{_plural(len(missing), 'paycheck')} name a run that was not converted.{tail}",
            len(linked),
            len(missing),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"Of {_plural(len(linked), 'paycheck')} that name a run, all point to a converted run.{tail}",
        len(linked),
    )


def check_employee_links(records: list[dict]) -> Check:
    id_, group, title = (
        "employee_links",
        "Relationships",
        "Deposits, deductions and paychecks point to a converted employee",
    )
    employees = {r["source_id"] for r in _of(records, "employee")}
    children = _of(records, "direct_deposit", "deduction", "paycheck")
    if not children:
        return _not_evaluated(id_, group, title, "There are no deposits, deductions or paychecks.")
    if not employees:
        return _not_evaluated(
            id_,
            group,
            title,
            "No employee records were converted, so the employee ids cannot be checked.",
        )
    missing: dict[str, list[dict]] = defaultdict(list)
    for c in children:
        if str(c.get("employee_id")) not in employees:
            missing[str(c.get("employee_id"))].append(c)
    if missing:
        ex = [
            Finding(
                record=f"employee {eid}",
                detail=f"referenced by {_plural(len(rows), 'record')}; not among the converted employees",
            )
            for eid, rows in missing.items()
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"{_plural(len(missing), 'employee id')} {_is(len(missing), 'is', 'are')} referenced but not converted here (they may be in another file).",
            len(children),
            sum(len(v) for v in missing.values()),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"All {_plural(len(children), 'record')} point to one of the {_plural(len(employees), 'converted employee')}. "
        "This shows each owner exists in this data, not that it is the right owner.",
        len(children),
    )


# --- dates ---------------------------------------------------------------------------------------------------
def _order_check(
    id_: str,
    group: str,
    title: str,
    rows: list[dict],
    first: str,
    last: str,
    severity: Status,
    why: str,
    names: tuple[str, str] | None = None,
) -> Check:
    first_name, last_name = names or (first.replace("_", " "), last.replace("_", " "))
    pairs = [(r, _day(r.get(first)), _day(r.get(last))) for r in rows]
    pairs = [(r, a, b) for r, a, b in pairs if a and b]
    if not pairs:
        return _not_evaluated(
            id_,
            group,
            title,
            f"No record has both {first_name} and {last_name} filled in.",
        )
    bad = [(r, a, b) for r, a, b in pairs if a > b]
    if bad:
        ex = [
            Finding(record=_label(r), detail=f"{first_name} {a} is after {last_name} {b}")
            for r, a, b in bad
        ]
        return _check(
            id_,
            group,
            title,
            severity,
            f"{_plural(len(bad), 'record')} {why}.",
            len(pairs),
            len(bad),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"All {_plural(len(pairs), 'record')} with both dates are in order.",
        len(pairs),
    )


def check_period_order(records: list[dict]) -> Check:
    return _order_check(
        "period_order",
        "Dates",
        "Pay periods start before they end",
        _of(records, "payroll_run", "paycheck"),
        "period_start",
        "period_end",
        "failed",
        "have a pay period that ends before it starts",
    )


def check_effective_dates(records: list[dict]) -> Check:
    rows = [
        {
            **r,
            "_from": r.get("effective_from") or r.get("start_date"),
            "_to": r.get("effective_to") or r.get("stop_date"),
        }
        for r in _of(records, "direct_deposit", "deduction")
    ]
    return _order_check(
        "effective_dates",
        "Dates",
        "Deposits and deductions start before they stop",
        rows,
        "_from",
        "_to",
        "failed",
        "stop before they start",
        names=("start date", "end date"),
    )


def check_employment_dates(records: list[dict]) -> Check:
    return _order_check(
        "employment_dates",
        "Dates",
        "Employees are hired before they leave",
        _of(records, "employee"),
        "hire_date",
        "termination_date",
        "warning",
        "list a termination date before the hire date (a rehire can look like this)",
    )


# --- money ---------------------------------------------------------------------------------------------------
def _is_derived(rec: dict, field: str) -> bool:
    return field in ((rec.get("provenance") or {}).get("derived_fields") or [])


def check_earnings_equal_gross(records: list[dict]) -> Check:
    id_, group, title = "earnings_equal_gross", "Money", "Earnings add up to gross pay"
    paychecks = _of(records, "paycheck")
    if not paychecks:
        return _not_evaluated(id_, group, title, "There are no paychecks.")
    ok, skipped, bad = [], defaultdict(int), []
    for p in paychecks:
        gross, lines = _dec(p.get("gross_pay")), p.get("earnings") or []
        if p.get("provider") not in EARNINGS_GROSS_TRUSTED and not _is_derived(p, "gross_pay"):
            skipped[
                UNTRUSTED_REASON.get(
                    p.get("provider"),
                    f"gross pay has not been validated against earning lines for {p.get('provider') or 'this provider'}",
                )
            ] += 1
        elif _is_derived(p, "gross_pay"):
            skipped[
                "gross pay was calculated from the earnings, so comparing them would prove nothing"
            ] += 1
        elif gross is None:
            skipped["no gross pay"] += 1
        elif not lines:
            skipped["no earning lines"] += 1
        else:
            counted = [e for e in lines if not e.get("is_memo")]
            amounts = [_dec(e.get("amount")) for e in counted]
            if not counted or any(a is None for a in amounts):
                skipped["some earning lines have no amount"] += 1
                continue
            total = sum(amounts, Decimal(0))
            ok.append(p)
            if abs(total - gross) > TOLERANCE:
                bad.append((p, total, gross))
    note = "; ".join(f"{_plural(n, 'paycheck')}: {why}" for why, n in skipped.items())
    if not ok:
        return _not_evaluated(
            id_, group, title, "Not enough data on any paycheck" + (f" ({note})." if note else ".")
        )
    tail = f" Not checked: {note}." if note else ""
    if bad:
        ex = [
            Finding(
                record=_label(p),
                detail=f"earnings add up to {t}, gross pay is {g} (difference {t - g})",
            )
            for p, t, g in bad
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"Of {_plural(len(ok), 'checked paycheck')}, {len(bad)} {_is(len(bad), 'has', 'have')} earning lines that do not add up to gross pay (memo lines excluded).{tail}",
            len(ok),
            len(bad),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"Checked {_plural(len(ok), 'paycheck')}: the earning lines add up to gross pay on all of them.{tail}",
        len(ok),
    )


def check_payments_equal_net(records: list[dict]) -> Check:
    id_, group, title = "payments_equal_net", "Money", "Payments add up to net pay"
    paychecks = _of(records, "paycheck")
    if not paychecks:
        return _not_evaluated(id_, group, title, "There are no paychecks.")
    ok, skipped, bad = [], defaultdict(int), []
    for p in paychecks:
        net, pays = _dec(p.get("net_pay")), p.get("payments") or []
        amounts = [_dec(x.get("amount")) for x in pays]
        if p.get("provider") not in PAYMENTS_NET_TRUSTED:
            skipped[
                f"payments have not been validated against net pay for {p.get('provider') or 'this provider'}"
            ] += 1
        elif net is None:
            skipped["no net pay"] += 1
        elif not pays:
            skipped["no payment lines"] += 1
        elif any(a is None for a in amounts):
            skipped["some payments have no amount"] += 1
        else:
            total = sum(amounts, Decimal(0))
            ok.append(p)
            if abs(total - net) > TOLERANCE:
                bad.append((p, total, net))
    note = "; ".join(f"{_plural(n, 'paycheck')}: {why}" for why, n in skipped.items())
    if not ok:
        return _not_evaluated(
            id_, group, title, "Not enough data on any paycheck" + (f" ({note})." if note else ".")
        )
    tail = f" Not checked: {note}." if note else ""
    if bad:
        ex = [
            Finding(
                record=_label(p),
                detail=f"payments add up to {t}, net pay is {n} (difference {t - n})",
            )
            for p, t, n in bad
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"Of {_plural(len(ok), 'checked paycheck')}, {len(bad)} {_is(len(bad), 'has', 'have')} payments that do not add up to net pay.{tail}",
            len(ok),
            len(bad),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"Checked {_plural(len(ok), 'paycheck')}: the payments add up to net pay on all of them.{tail}",
        len(ok),
    )


def check_gross_to_net() -> Check:
    return _not_evaluated(
        "gross_to_net", "Money", "Gross pay minus taxes and deductions equals net pay",
        "Not checked on purpose: what counts as a deduction (pre-tax, benefit, employer-paid) differs by provider and some responses omit tax lines, so this rule gave false alarms on live demo data.",
    )  # fmt: skip


# --- direct deposits (scoped to one employee, one rule family, active deposits only) ----------------------------
def _scope(rec: dict) -> tuple:
    """Deposits for different voucher types (regular, supplemental, ...) are independent allocations."""
    return (rec.get("employee_id"), rec.get("voucher_types_raw") or "")


def _active(rec: dict) -> bool:
    return rec.get("active") is not False and rec.get("status") in (None, "active")


def check_deposit_percentages(records: list[dict]) -> Check:
    id_, group, title = (
        "deposit_percentages",
        "Deposits",
        "Percentage deposits of one kind do not exceed 100 %",
    )
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for d in _of(records, *DEPOSIT_TYPES):
        if _active(d) and d.get("allocation_type") == "percentage":
            groups[(*_scope(d), d.get("allocation_raw") or "")].append(d)
    if not groups:
        return _not_evaluated(id_, group, title, "No active percentage deposits were converted.")
    evaluated, incomplete, bad = 0, 0, []
    for (employee, _vouchers, _kind), rows in groups.items():
        pcts = [_dec(r.get("percentage")) for r in rows]
        if any(p is None for p in pcts):
            incomplete += 1
            continue
        evaluated += 1
        total = sum(pcts, Decimal(0))
        if total > Decimal(100) + TOLERANCE:
            bad.append((employee, total, len(rows)))
    if not evaluated:
        return _not_evaluated(
            id_,
            group,
            title,
            "Every group of percentage deposits has a deposit without a percentage.",
        )
    tail = (
        f" {_plural(incomplete, 'group')} had a missing percentage and was not checked."
        if incomplete
        else ""
    )
    if bad:
        ex = [
            Finding(record=f"employee {e}", detail=f"{n} deposits of the same kind add up to {t} %")
            for e, t, n in bad
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"{_plural(len(bad), 'employee')} have percentage deposits of one kind that add up to more than 100 %.{tail}",
            evaluated,
            len(bad),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"All {_plural(evaluated, 'group')} of percentage deposits add up to 100 % or less.{tail}",
        evaluated,
    )


def check_single_remaining_balance(records: list[dict]) -> Check:
    id_, group, title = (
        "deposit_remaining_balance",
        "Deposits",
        "At most one deposit takes the remaining balance",
    )
    by_scope: dict[tuple, list[dict]] = defaultdict(list)
    for d in _of(records, *DEPOSIT_TYPES):
        if _active(d):
            by_scope[_scope(d)].append(d)
    if not by_scope:
        return _not_evaluated(id_, group, title, "No active deposits were converted.")
    bad = {
        k: [d for d in rows if d.get("allocation_type") == "remaining_balance"]
        for k, rows in by_scope.items()
    }
    bad = {k: rows for k, rows in bad.items() if len(rows) > 1}
    if bad:
        ex = [
            Finding(
                record=f"employee {e}" + (f", voucher type {v}" if v else ""),
                detail=f"{len(rows)} deposits each take the remaining balance",
            )
            for (e, v), rows in bad.items()
        ]
        return _check(
            id_,
            group,
            title,
            "warning",
            f"{_plural(len(bad), 'employee')} have more than one remaining-balance deposit for the same voucher type.",
            len(by_scope),
            len(bad),
            ex,
        )
    return _check(
        id_,
        group,
        title,
        "passed",
        f"None of the {_plural(len(by_scope), 'employee and voucher type')} with deposits has more than one remaining-balance deposit.",
        len(by_scope),
    )


# --- entry point ---------------------------------------------------------------------------------------------
def evaluate(records: list[dict]) -> IntegrityReport:
    """Run every check over the canonical records (as returned by the API) of one conversion."""
    checks = [
        check_duplicate_ids(records),
        check_paycheck_run_link(records),
        check_employee_links(records),
        check_period_order(records),
        check_effective_dates(records),
        check_employment_dates(records),
        check_earnings_equal_gross(records),
        check_payments_equal_net(records),
        check_gross_to_net(),
        check_deposit_percentages(records),
        check_single_remaining_balance(records),
    ]
    summary = {
        s: sum(1 for c in checks if c.status == s)
        for s in ("passed", "warning", "failed", "not_evaluated")
    }
    return IntegrityReport(summary=summary, checks=checks)
