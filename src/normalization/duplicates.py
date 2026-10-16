# ruff: noqa: E501
"""Duplicate record identities inside ONE incoming payload.

A record's identity is exactly what persistence keys on (`persistence/repository.py::_save`): provider, tenant, company and the
provider-local id, plus the owning employee for direct deposits and deductions (the same deposit id may legitimately exist under
two employees). A company has no local id: its identity is the scope itself.

Policy (DECISIONS.md section 7):
  * two records with the same identity and DIFFERENT content are a conflict: an ERROR (`duplicate_record_id`), so the whole
    payload is rejected before anything is written and neither CLI, API nor database can silently keep "the last one";
  * two records with the same identity and the same content (provenance aside) are a harmless repeat: a WARNING
    (`duplicate_record_identical`); both stay in the result, and ingestion writes the record once.
Separate requests are never compared here: re-ingesting something already stored is the normal idempotent update.
"""

import hashlib
import json
from collections import defaultdict
from typing import Any

from .canonical.enums import Severity
from .canonical.models import Issue

PER_EMPLOYEE = {"direct_deposit", "deduction"}


def identity(record: Any) -> tuple:
    kind = record.record_type
    base = (kind, record.provider.value, record.tenant_id, record.company_id)
    if kind == "company":
        return base
    if kind in PER_EMPLOYEE:
        return (*base, record.employee_id, record.source_id)
    return (*base, record.source_id)


def content_digest(record: Any) -> str:
    """Hash of everything the record says, ignoring only WHERE it was read (provenance). The internal account hash counts."""
    data = record.model_dump(mode="json", exclude={"provenance"})
    internal = getattr(record, "account_number_hash", None)
    if internal is not None:
        data["_account_hash"] = internal
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def duplicate_issues(records: list[Any]) -> list[Issue]:
    seen: dict[tuple, list[Any]] = defaultdict(list)
    for record in records:
        seen[identity(record)].append(record)
    issues: list[Issue] = []
    for key, rows in seen.items():
        if len(rows) < 2:
            continue
        kind, source_id = key[0], rows[0].source_id
        label = kind.replace("_", " ")
        if len({content_digest(r) for r in rows}) > 1:
            issues.append(
                Issue(
                    severity=Severity.ERROR,
                    code="duplicate_record_id",
                    message=(
                        f"{len(rows)} {label} records in this payload share the id {source_id!r} but differ in content, so one would "
                        "silently replace the other. Nothing was saved. Remove or correct the duplicate and try again."
                    ),
                    source_id=source_id,
                )
            )
        else:
            issues.append(
                Issue(
                    severity=Severity.WARNING,
                    code="duplicate_record_identical",
                    message=f"The same {label} (id {source_id!r}) appears {len(rows)} times in this payload with identical content; it is stored once.",
                    source_id=source_id,
                )
            )
    return issues
