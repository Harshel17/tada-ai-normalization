from typing import Any

from pydantic import BaseModel

from ..adapters.base import SourceContext
from ..canonical.models import Issue, Record


class NormalizeRequest(BaseModel):
    """Envelope for normalize / ingest calls.

    `payload` is the provider response exactly as received (any JSON). `context` carries the ids
    the provider does not repeat inside its own responses (tenant, company, sometimes employee).
    """

    context: SourceContext
    payload: Any


class NormalizeResponse(BaseModel):
    provider: str
    entity_type: str
    ok: bool
    counts: dict[str, int]
    issues: list[Issue]
    records: list[Record]


class IngestResponse(BaseModel):
    """Ingest returns counts, not the records: stored data is read back through the GET routes."""

    provider: str
    entity_type: str
    ok: bool
    counts: dict[str, int]
    stored: dict[str, dict[str, int]]
    issues: list[Issue]


class HealthResponse(BaseModel):
    status: str
    database: str


class ErrorResponse(BaseModel):
    detail: str


class ScopeCounts(BaseModel):
    provider: str
    tenant_id: str
    company_id: str
    companies: int
    employees: int
    direct_deposits: int
    deductions: int
    payroll_runs: int
    paychecks: int


class StatsResponse(BaseModel):
    """Supported providers / entities, and stored record counts per provider, tenant, company."""

    providers: list[str]
    entity_types: list[str]
    scopes: list[ScopeCounts]
