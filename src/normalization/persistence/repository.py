"""Saving and loading canonical records.

Idempotency strategy
--------------------
* Identity: every record is unique on (provider, tenant_id, company_id, source_id)
  (employee-scoped children add employee_id). The same source record can only ever map to one row.
* Each row stores `content_hash` = sha256 of the record's canonical JSON.
* Saving is one atomic `INSERT ... ON CONFLICT (identity) DO UPDATE ... WHERE content_hash differs`:
    - no row yet            -> inserted  (version 1)
    - row, same hash        -> unchanged (nothing is written; re-ingesting a payload is a no-op)
    - row, different hash   -> updated   (all columns replaced, version + 1)
  Two concurrent identical ingests cannot create duplicates because the database enforces the
  unique key.
* A whole ingest call is one transaction (the caller owns it): any failure rolls everything back.
* A result that contains ERROR issues is rejected up front, so a payload is never half-loaded.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..canonical.enums import Severity
from ..canonical.models import (
    BenefitContributionLine,
    Company,
    Compensation,
    DeductionLine,
    DeductionSetup,
    DirectDepositInstruction,
    EarningLine,
    Employee,
    NormalizationResult,
    Paycheck,
    PaycheckPayment,
    PayrollRun,
    TaxLine,
)
from ..duplicates import content_digest, identity
from . import tables as t


class IngestRejected(Exception):
    """The normalization result had errors, so nothing was written."""


@dataclass
class IngestSummary:
    counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, record_type: str, outcome: str) -> None:
        bucket = self.counts.setdefault(record_type, {"inserted": 0, "updated": 0, "unchanged": 0})
        bucket[outcome] += 1


# --- record -> row ---------------------------------------------------------------------------
def _plain(value: Any) -> Any:
    """Enum members -> their values, recursively (database drivers want plain strings)."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _json_safe(value: Any) -> Any:
    """JSON-compatible copy (Decimal/date become strings, exactly like the API output)."""
    return json.loads(json.dumps(value, default=str))


# Fields that are stored but never serialized (Field(exclude=True)); they still belong to a record's
# identity of content, so a changed value (e.g. after a salt change) is detected as an update.
INTERNAL_FIELDS = ("account_number_hash",)


def content_hash(record: Any) -> str:
    data = record.model_dump(mode="json")
    for name in INTERNAL_FIELDS:
        if getattr(record, name, None) is not None:
            data[name] = getattr(record, name)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _base_values(record: Any) -> dict[str, Any]:
    return {
        "provider": record.provider.value,
        "tenant_id": record.tenant_id,
        "company_id": record.company_id,
        "source_id": record.source_id,
        "extras": _json_safe(record.extras),
        "provenance": record.provenance.model_dump(mode="json"),
        "content_hash": content_hash(record),
    }


def _scalars(record: Any, skip: set[str]) -> dict[str, Any]:
    """Top-level canonical fields as python values, excluding the base columns and `skip`."""
    base = {
        "provider",
        "tenant_id",
        "company_id",
        "source_id",
        "extras",
        "provenance",
        "record_type",
    }
    out = {}
    for name in type(record).model_fields:
        if name in base or name in skip:
            continue
        out[name] = _plain(getattr(record, name))
    return out


def _json_field(record: Any, name: str) -> Any:
    value = getattr(record, name)
    if value is None:
        return None
    if isinstance(value, list):
        return [v.model_dump(mode="json") for v in value]
    return value.model_dump(mode="json")


def company_values(r: Company) -> dict[str, Any]:
    return {
        **_base_values(r),
        **_scalars(r, {"address", "legal_entities"}),
        "address": _json_field(r, "address"),
        "legal_entities": _json_field(r, "legal_entities"),
    }


EMPLOYEE_JSON_FIELDS = (
    "address",
    "mailing_address",
    "tax_form_address",
    "org_assignments",
    "labor_allocations",
    "emergency_contacts",
    "tax_withholdings",
    "benefits",
)


def employee_values(r: Employee) -> dict[str, Any]:
    comp = r.compensation
    comp_cols: dict[str, Any] = {}
    for name in Compensation.model_fields:
        value = getattr(comp, name, None)
        if name == "alternate_rates":  # list of models -> JSON ([] when there is no compensation)
            value = [m.model_dump(mode="json") for m in value or []]
        comp_cols[f"comp_{name}"] = _plain(value)
    return {
        **_base_values(r),
        **_scalars(r, {"compensation", *EMPLOYEE_JSON_FIELDS}),
        **{name: _json_field(r, name) for name in EMPLOYEE_JSON_FIELDS},
        "comp_present": comp is not None,
        **comp_cols,
    }


def deposit_values(r: DirectDepositInstruction) -> dict[str, Any]:
    return {**_base_values(r), **_scalars(r, set())}


def deduction_values(r: DeductionSetup) -> dict[str, Any]:
    return {
        **_base_values(r),
        **_scalars(r, {"calc_params"}),
        "calc_params": _json_field(r, "calc_params"),
    }


def run_values(r: PayrollRun) -> dict[str, Any]:
    return {**_base_values(r), **_scalars(r, set())}


LINE_FIELDS = {"earnings", "taxes", "deductions", "benefits", "payments"}


def paycheck_values(r: Paycheck) -> dict[str, Any]:
    return {**_base_values(r), **_scalars(r, LINE_FIELDS)}


# --- upsert --------------------------------------------------------------------------------------
NOT_UPDATED = {"id", "provider", "tenant_id", "company_id", "source_id", "first_ingested_at"}


def _upsert(
    session: Session, model: Any, values: dict[str, Any], key: tuple[str, ...]
) -> tuple[uuid.UUID, str]:
    """Atomic insert-or-update. Returns (row id, 'inserted' | 'updated' | 'unchanged')."""
    insert = pg_insert(model).values(**values)
    changed = {c: insert.excluded[c] for c in values if c not in NOT_UPDATED and c not in key}
    stmt = insert.on_conflict_do_update(
        index_elements=[getattr(model, k) for k in key],
        set_={**changed, "version": model.version + 1, "last_changed_at": func.now()},
        where=model.content_hash != insert.excluded.content_hash,
    ).returning(model.id, model.version)
    row = session.execute(stmt).first()
    if row is not None:
        return row.id, ("inserted" if row.version == 1 else "updated")
    # Conflict, but the hash matched: the WHERE clause skipped the update.
    existing = session.execute(
        select(model.id).where(*[getattr(model, k) == values[k] for k in key])
    ).scalar_one()
    return existing, "unchanged"


IDENTITY = ("provider", "tenant_id", "company_id")


def _replace_lines(session: Session, pk: uuid.UUID, check: Paycheck) -> None:
    """Lines are owned by their paycheck: on change, delete and re-insert them together."""
    for table in (
        t.EarningLineRow,
        t.TaxLineRow,
        t.DeductionLineRow,
        t.BenefitLineRow,
        t.PaycheckPaymentRow,
    ):
        session.execute(delete(table).where(table.paycheck_pk == pk))

    def rows(model: Any, lines: list[Any]) -> None:
        if lines:
            session.execute(
                pg_insert(model),
                [{"paycheck_pk": pk, **_plain(_line_dict(line))} for line in lines],
            )

    rows(t.EarningLineRow, check.earnings)
    rows(t.TaxLineRow, check.taxes)
    rows(t.DeductionLineRow, check.deductions)
    rows(t.BenefitLineRow, check.benefits)
    rows(t.PaycheckPaymentRow, check.payments)


def _line_dict(line: Any) -> dict[str, Any]:
    data = line.model_dump()
    if "extras" in data:
        data["extras"] = _json_safe(data["extras"])
    if "accumulators" in data:
        data["accumulators"] = {k: str(v) for k, v in data["accumulators"].items()}
    return data


# --- links -----------------------------------------------------------------------------------
LINK_SQL = [
    """UPDATE employees e SET company_pk = c.id FROM companies c
       WHERE e.company_pk IS NULL AND e.provider = :p AND e.tenant_id = :t
         AND c.provider = e.provider AND c.tenant_id = e.tenant_id
         AND c.company_id = e.company_id""",
    """UPDATE direct_deposit_instructions x SET employee_pk = e.id FROM employees e
       WHERE x.employee_pk IS NULL AND x.provider = :p AND x.tenant_id = :t
         AND e.provider = x.provider AND e.tenant_id = x.tenant_id
         AND e.company_id = x.company_id AND e.source_id = x.employee_id""",
    """UPDATE deduction_setups x SET employee_pk = e.id FROM employees e
       WHERE x.employee_pk IS NULL AND x.provider = :p AND x.tenant_id = :t
         AND e.provider = x.provider AND e.tenant_id = x.tenant_id
         AND e.company_id = x.company_id AND e.source_id = x.employee_id""",
    """UPDATE paychecks x SET employee_pk = e.id FROM employees e
       WHERE x.employee_pk IS NULL AND x.provider = :p AND x.tenant_id = :t
         AND e.provider = x.provider AND e.tenant_id = x.tenant_id
         AND e.company_id = x.company_id AND e.source_id = x.employee_id""",
    """UPDATE paychecks x SET run_pk = r.id FROM payroll_runs r
       WHERE x.run_pk IS NULL AND x.run_id IS NOT NULL AND x.provider = :p AND x.tenant_id = :t
         AND r.provider = x.provider AND r.tenant_id = x.tenant_id
         AND r.company_id = x.company_id AND r.source_id = x.run_id""",
]


def link_records(session: Session, provider: str, tenant_id: str) -> None:
    """Fill in foreign keys whose parent row arrived after the child (order-independent ingest).

    The natural-key columns (employee_id, run_id, company_id) are always stored, so a child can be
    saved before its parent; the surrogate FK is resolved as soon as the parent exists.
    """
    for sql in LINK_SQL:
        session.execute(text(sql), {"p": provider, "t": tenant_id})


# --- ingest --------------------------------------------------------------------------------------
def ingest(session: Session, result: NormalizationResult) -> IngestSummary:
    """Persist a normalization result. The caller owns the transaction (commit / rollback)."""
    errors = [i for i in result.issues if i.severity is Severity.ERROR]
    if errors:
        raise IngestRejected(
            f"{len(errors)} error(s) in the normalization result; first: [{errors[0].code}] "
            f"{errors[0].message}"
        )
    summary = IngestSummary()
    # Parents before children so freshly inserted parents can be linked in the same call.
    order = {"company": 0, "employee": 1, "payroll_run": 1}
    written: dict[tuple, str] = {}
    for record in sorted(result.records, key=lambda r: order.get(r.record_type, 2)):
        if record.company_id is None:
            raise IngestRejected(f"{record.record_type} {record.source_id} has no company_id")
        key, digest = identity(record), content_digest(record)
        if (
            written.get(key) == digest
        ):  # an identical repeat inside this payload (already warned about): written once
            summary.add(record.record_type, "unchanged")
            continue
        written[key] = digest
        pk, outcome = _save(session, record)
        summary.add(record.record_type, outcome)
        if record.record_type == "paycheck" and outcome != "unchanged":
            _replace_lines(session, pk, record)
    link_records(session, result.provider.value, _tenant(result))
    _audit(session, result, summary)
    return summary


def _tenant(result: NormalizationResult) -> str:
    return result.records[0].tenant_id if result.records else ""


def _save(session: Session, record: Any) -> tuple[uuid.UUID, str]:
    kind = record.record_type
    if kind == "company":
        return _upsert(session, t.CompanyRow, company_values(record), IDENTITY)
    if kind == "employee":
        return _upsert(session, t.EmployeeRow, employee_values(record), (*IDENTITY, "source_id"))
    if kind == "direct_deposit":
        key = (*IDENTITY, "employee_id", "source_id")
        return _upsert(session, t.DirectDepositRow, deposit_values(record), key)
    if kind == "deduction":
        key = (*IDENTITY, "employee_id", "source_id")
        return _upsert(session, t.DeductionSetupRow, deduction_values(record), key)
    if kind == "payroll_run":
        return _upsert(session, t.PayrollRunRow, run_values(record), (*IDENTITY, "source_id"))
    if kind == "paycheck":
        return _upsert(session, t.PaycheckRow, paycheck_values(record), (*IDENTITY, "source_id"))
    raise ValueError(f"cannot persist record type {kind}")


def _audit(session: Session, result: NormalizationResult, summary: IngestSummary) -> None:
    if not result.records:
        return
    first = result.records[0]
    payload_hash = hashlib.sha256(
        json.dumps([content_hash(r) for r in result.records]).encode()
    ).hexdigest()
    session.add(
        t.IngestRunRow(
            provider=first.provider.value,
            tenant_id=first.tenant_id,
            company_id=first.company_id,
            entity_type=result.entity_type,
            payload_hash=payload_hash,
            counts=summary.counts,
        )
    )


# --- reads ---------------------------------------------------------------------------------------
def _common(row: Any, include_extras: bool) -> dict[str, Any]:
    return {
        "provider": row.provider,
        "tenant_id": row.tenant_id,
        "company_id": row.company_id,
        "source_id": row.source_id,
        "extras": row.extras if include_extras else {},
        "provenance": row.provenance,
    }


def _columns(row: Any, model: type, skip: set[str] = frozenset()) -> dict[str, Any]:
    names = set(model.model_fields) - skip - {"extras", "provenance", "record_type"}
    names -= {"provider", "tenant_id", "company_id", "source_id"}
    return {n: getattr(row, n) for n in names if hasattr(row, n)}


def to_company(row: t.CompanyRow, include_extras: bool = False) -> Company:
    return Company(**_common(row, include_extras), **_columns(row, Company))


def to_employee(row: t.EmployeeRow, include_extras: bool = False) -> Employee:
    comp = None
    if row.comp_present:
        comp = Compensation(**{n: getattr(row, f"comp_{n}") for n in Compensation.model_fields})
    return Employee(
        **_common(row, include_extras),
        **_columns(row, Employee, {"compensation"}),
        compensation=comp,
    )


def to_deposit(row: t.DirectDepositRow, include_extras: bool = False) -> DirectDepositInstruction:
    return DirectDepositInstruction(
        **_common(row, include_extras), **_columns(row, DirectDepositInstruction)
    )


def to_deduction(row: t.DeductionSetupRow, include_extras: bool = False) -> DeductionSetup:
    return DeductionSetup(**_common(row, include_extras), **_columns(row, DeductionSetup))


def to_run(row: t.PayrollRunRow, include_extras: bool = False) -> PayrollRun:
    return PayrollRun(**_common(row, include_extras), **_columns(row, PayrollRun))


def to_paycheck(session: Session, row: t.PaycheckRow, include_extras: bool = False) -> Paycheck:
    def lines(model: Any, cls: type) -> list[Any]:
        stmt = select(model).where(model.paycheck_pk == row.id).order_by(model.line_no)
        out = []
        for line in session.scalars(stmt):
            data = {n: getattr(line, n) for n in cls.model_fields if hasattr(line, n)}
            if not include_extras and "extras" in data:
                data["extras"] = {}
            out.append(cls(**data))
        return out

    return Paycheck(
        **_common(row, include_extras),
        **_columns(row, Paycheck, LINE_FIELDS),
        earnings=lines(t.EarningLineRow, EarningLine),
        taxes=lines(t.TaxLineRow, TaxLine),
        deductions=lines(t.DeductionLineRow, DeductionLine),
        benefits=lines(t.BenefitLineRow, BenefitContributionLine),
        payments=lines(t.PaycheckPaymentRow, PaycheckPayment),
    )


def _scoped(model: Any, provider: str, tenant_id: str, company_id: str | None) -> Select:
    stmt = select(model).where(model.provider == provider, model.tenant_id == tenant_id)
    if company_id is not None:
        stmt = stmt.where(model.company_id == company_id)
    return stmt


def get_company(session: Session, provider: str, tenant_id: str, company_id: str) -> Company | None:
    row = session.scalars(_scoped(t.CompanyRow, provider, tenant_id, company_id)).first()
    return to_company(row) if row else None


def list_companies(
    session: Session, provider: str, tenant_id: str, limit: int = 50, offset: int = 0
) -> list[Company]:
    stmt = _scoped(t.CompanyRow, provider, tenant_id, None).order_by(t.CompanyRow.company_id)
    return [to_company(r) for r in session.scalars(stmt.limit(limit).offset(offset))]


def get_employee(
    session: Session, provider: str, tenant_id: str, company_id: str, employee_id: str
) -> Employee | None:
    stmt = _scoped(t.EmployeeRow, provider, tenant_id, company_id).where(
        t.EmployeeRow.source_id == employee_id
    )
    row = session.scalars(stmt).first()
    return to_employee(row) if row else None


def list_employees(
    session: Session,
    provider: str,
    tenant_id: str,
    company_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Employee]:
    stmt = _scoped(t.EmployeeRow, provider, tenant_id, company_id).order_by(t.EmployeeRow.source_id)
    return [to_employee(r) for r in session.scalars(stmt.limit(limit).offset(offset))]


def list_direct_deposits(
    session: Session, provider: str, tenant_id: str, company_id: str, employee_id: str
) -> list[DirectDepositInstruction]:
    m = t.DirectDepositRow
    stmt = (
        _scoped(m, provider, tenant_id, company_id)
        .where(m.employee_id == employee_id)
        .order_by(
            # a "remaining balance" deposit takes what is left, so it belongs last
            (func.coalesce(m.allocation_type, "") == "remaining_balance").asc(),
            m.sequence.nulls_last(),
            m.source_position.nulls_last(),
            m.source_id,
        )
    )
    return [to_deposit(r) for r in session.scalars(stmt)]


def list_deductions(
    session: Session, provider: str, tenant_id: str, company_id: str, employee_id: str
) -> list[DeductionSetup]:
    m = t.DeductionSetupRow
    stmt = (
        _scoped(m, provider, tenant_id, company_id)
        .where(m.employee_id == employee_id)
        .order_by(m.source_id)
    )
    return [to_deduction(r) for r in session.scalars(stmt)]


def list_payroll_runs(
    session: Session,
    provider: str,
    tenant_id: str,
    company_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[PayrollRun]:
    m = t.PayrollRunRow
    stmt = _scoped(m, provider, tenant_id, company_id).order_by(
        m.pay_date.desc().nulls_last(), m.source_id
    )
    return [to_run(r) for r in session.scalars(stmt.limit(limit).offset(offset))]


def list_paychecks(
    session: Session,
    provider: str,
    tenant_id: str,
    company_id: str,
    employee_id: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Paycheck]:
    m = t.PaycheckRow
    stmt = _scoped(m, provider, tenant_id, company_id)
    if employee_id is not None:
        stmt = stmt.where(m.employee_id == employee_id)
    if run_id is not None:
        stmt = stmt.where(m.run_id == run_id)
    stmt = stmt.order_by(m.pay_date.desc().nulls_last(), m.source_id).limit(limit).offset(offset)
    return [to_paycheck(session, r) for r in session.scalars(stmt)]


def get_paycheck(
    session: Session, provider: str, tenant_id: str, company_id: str, paycheck_id: str
) -> Paycheck | None:
    m = t.PaycheckRow
    row = session.scalars(
        _scoped(m, provider, tenant_id, company_id).where(m.source_id == paycheck_id)
    ).first()
    return to_paycheck(session, row) if row else None


# --- overview counts -------------------------------------------------------------------------
STAT_TABLES = {
    "companies": t.CompanyRow,
    "employees": t.EmployeeRow,
    "direct_deposits": t.DirectDepositRow,
    "deductions": t.DeductionSetupRow,
    "payroll_runs": t.PayrollRunRow,
    "paychecks": t.PaycheckRow,
}


def scope_counts(session: Session) -> list[dict[str, Any]]:
    """Record counts per (provider, tenant, company). Counts only: no record data leaves here."""
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for name, model in STAT_TABLES.items():
        stmt = select(model.provider, model.tenant_id, model.company_id, func.count()).group_by(
            model.provider, model.tenant_id, model.company_id
        )
        for provider, tenant_id, company_id, n in session.execute(stmt):
            row = rows.setdefault(
                (provider, tenant_id, company_id),
                {"provider": provider, "tenant_id": tenant_id, "company_id": company_id}
                | dict.fromkeys(STAT_TABLES, 0),
            )
            row[name] = n
    return [rows[k] for k in sorted(rows)]
