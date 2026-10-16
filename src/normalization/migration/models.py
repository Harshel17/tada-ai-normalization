# ruff: noqa: E501
"""Request and response shapes of the migration preview."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..canonical.models import Employee

Readiness = Literal["READY", "NEEDS_REVIEW", "BLOCKED"]
FieldStatus = Literal["mapped", "operator_supplied", "needs_review", "omitted", "missing_required"]
Severity = Literal["blocking", "review", "info"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Destination(Strict):
    """The destination tenant context. The destination company id lives in the URL, never in the body."""

    company_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    # documented: ein.id, "can be used for POST in multi-ein companies"; strict: a string is not an integer
    ein_id: int | None = Field(default=None, ge=0, strict=True)


class OperatorInput(Strict):
    """Values only a person can supply or confirm. Nothing here is ever inferred."""

    username: str | None = None
    use_source_employee_number: bool = (
        False  # send the source employee number as UKG employee_id (max 20)
    )
    use_source_id_as_external_id: bool = False  # send the source id as UKG external_id
    address_country: str | None = (
        None  # ISO 3166-1 alpha-3, when the source address has no usable country
    )


class MigrationRequest(Strict):
    employee: Employee
    destination: Destination
    operator: OperatorInput = Field(default_factory=OperatorInput)


class FieldMapping(Strict):
    destination: str  # dotted path in the request body, e.g. "phones.cell_phone"
    source: str | None  # canonical field it came from; None for operator-supplied values
    status: FieldStatus
    value: Any = None  # shown only for non-sensitive values
    note: str = ""


class Requirement(Strict):
    field: str
    satisfied: bool
    detail: str


class MigrationIssue(Strict):
    severity: Severity
    code: str
    field: str | None = None
    message: str


class NotMigrated(Strict):
    """A canonical field that is NOT in the request, and why (nothing is dropped silently)."""

    source: str
    reason: Literal["sensitive", "separate_endpoint", "unsupported", "not_confirmed", "uncertain"]
    note: str


class ContractRef(Strict):
    provider: str
    method: str
    endpoint: str
    schema_file: str
    schema_anchor: str
    caveat: str


class MigrationPreview(Strict):
    dry_run: Literal[True] = True
    notice: str
    destination_provider: str
    destination_company_id: str
    source: dict[
        str, str | None
    ]  # provider / tenant / company / source record ids, kept apart from the destination
    contract: ContractRef
    readiness: Readiness
    readiness_meaning: str
    request_body: dict[str, Any]
    fields: list[FieldMapping]
    requirements: list[Requirement]
    issues: list[MigrationIssue]
    not_migrated: list[NotMigrated]
