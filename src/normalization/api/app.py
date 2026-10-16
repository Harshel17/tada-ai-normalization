"""FastAPI layer. Thin on purpose: parse the request, call the service / repository, shape the
response. All normalization logic lives in `normalization.service`.

LOCAL USE ONLY. There is no authentication or authorization here; the API reads and writes
payroll data, so do not expose it to a network you do not fully control.
"""

import json
import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .. import convert as conv
from .. import service
from ..adapters import ADAPTERS, ENTITIES
from ..canonical.models import (
    Company,
    DeductionSetup,
    DirectDepositInstruction,
    Employee,
    Paycheck,
    PayrollRun,
)
from ..inputs import find_invalid_text
from ..migration import MigrationPreview, MigrationRequest, preview_ukg_ready
from ..persistence import repository as repo
from ..persistence.db import make_engine, transaction
from ..security import ConfigurationError
from .schemas import (
    HealthResponse,
    IngestResponse,
    NormalizeRequest,
    NormalizeResponse,
    StatsResponse,
)

log = logging.getLogger("tada.api")
MAX_BODY_BYTES = 20 * 1024 * 1024

REQUEST_BODY_DOC = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": NormalizeRequest.model_json_schema()},
        },
    }
}


def create_app(engine: Engine | None = None) -> FastAPI:
    """App factory. Tests pass their own engine; otherwise one is built lazily from settings."""
    app = FastAPI(
        title="Tada Normalize",
        version="0.1.0",
        description="Normalizes PrismHR / isolved / UKG Ready payloads into one canonical model. "
        "Local use only: no authentication.",
    )
    state: dict[str, Engine | None] = {"engine": engine}

    def get_engine() -> Engine:
        if state["engine"] is None:
            state["engine"] = make_engine()
        return state["engine"]

    def get_session() -> Iterator[Session]:
        with transaction(get_engine()) as session:
            yield session

    DB = Annotated[Session, Depends(get_session)]

    # --- errors ------------------------------------------------------------------------------
    @app.exception_handler(service.UnknownProviderOrEntity)
    async def unknown(_: Request, exc: service.UnknownProviderOrEntity) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ConfigurationError)
    async def misconfigured(_: Request, exc: ConfigurationError) -> JSONResponse:
        log.error(
            "server misconfiguration: %s", exc
        )  # the message never contains the secret itself
        return JSONResponse({"detail": f"server misconfiguration: {exc}"}, status_code=500)

    @app.exception_handler(OperationalError)
    async def db_down(_: Request, exc: OperationalError) -> JSONResponse:
        log.error("database unavailable: %s", type(exc.orig).__name__)  # never log SQL/params
        return JSONResponse({"detail": "database unavailable"}, status_code=503)

    @app.exception_handler(repo.IngestRejected)
    async def rejected(_: Request, exc: repo.IngestRejected) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    # --- helpers -----------------------------------------------------------------------------
    async def read_envelope(request: Request) -> NormalizeRequest:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "request body too large")
        try:
            # parse_float=Decimal keeps "100000.00" exactly as sent (floats would lose the scale)
            raw = json.loads(body, parse_float=Decimal)
        except ValueError as exc:
            raise HTTPException(400, "request body is not valid JSON") from exc
        try:
            return NormalizeRequest.model_validate(raw)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            raise HTTPException(422, "; ".join(errors)) from exc

    # --- health ------------------------------------------------------------------------------
    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Liveness. Reports database reachability but never fails because of it."""
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
            db = "up"
        except Exception:  # noqa: BLE001
            db = "down"
        return HealthResponse(status="ok", database=db)

    @app.get("/stats", response_model=StatsResponse)
    def stats(session: DB) -> Any:
        """Supported providers / entity types and stored record counts (counts only, no data)."""
        return StatsResponse(
            providers=list(ADAPTERS),
            entity_types=list(ENTITIES),
            scopes=repo.scope_counts(session),
        )

    # --- normalize / ingest ------------------------------------------------------------------
    @app.post(
        "/normalize/{provider}/{entity_type}",
        response_model=NormalizeResponse,
        openapi_extra=REQUEST_BODY_DOC,
        responses={422: {"model": NormalizeResponse, "description": "Input had errors"}},
    )
    async def normalize(provider: str, entity_type: str, request: Request) -> Any:
        """Normalize without storing anything. Returns 422 (with the full result) on errors."""
        service.resolve_provider(provider)  # unknown provider / entity -> 404 before the body
        service.resolve_entity(entity_type)
        envelope = await read_envelope(request)
        result = service.normalize(provider, entity_type, envelope.payload, envelope.context)
        body = NormalizeResponse(
            provider=result.provider.value,
            entity_type=result.entity_type,
            ok=result.ok,
            counts=result.counts(),
            issues=result.issues,
            records=result.records,
        )
        return JSONResponse(body.model_dump(mode="json"), status_code=200 if result.ok else 422)

    @app.post(
        "/ingest/{provider}/{entity_type}",
        response_model=IngestResponse,
        openapi_extra=REQUEST_BODY_DOC,
        responses={422: {"model": IngestResponse}, 503: {"description": "Database unavailable"}},
    )
    async def ingest(provider: str, entity_type: str, request: Request, session: DB) -> Any:
        """Normalize and store in one transaction. Nothing is written if there are any errors."""
        service.resolve_provider(provider)  # unknown provider / entity -> 404 before the body
        service.resolve_entity(entity_type)
        envelope = await read_envelope(request)
        result = service.normalize(provider, entity_type, envelope.payload, envelope.context)
        if not result.ok:
            body = IngestResponse(
                provider=result.provider.value,
                entity_type=result.entity_type,
                ok=False,
                counts=result.counts(),
                stored={},
                issues=result.issues,
            )
            return JSONResponse(body.model_dump(mode="json"), status_code=422)
        summary = repo.ingest(session, result)
        log.info("ingested %s %s: %s", provider, entity_type, summary.counts)
        return IngestResponse(
            provider=result.provider.value,
            entity_type=result.entity_type,
            ok=True,
            counts=result.counts(),
            stored=summary.counts,
            issues=result.issues,
        )

    # --- detect / convert (preview only: nothing is ever stored here) -----------------------
    async def read_files(request: Request) -> dict:
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "request body too large")
        try:
            raw = json.loads(body, parse_float=Decimal)
        except ValueError as exc:
            raise HTTPException(400, "request body is not valid JSON") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("files"), list) or not raw["files"]:
            raise HTTPException(422, "files: expected a non-empty list of {name, content}")
        bad = find_invalid_text(raw)
        if bad:  # only the location is reported, never the text
            raise HTTPException(422, f"request holds a NUL or invalid Unicode character at {bad}")
        return raw

    def parse_files(raw: dict) -> list[conv.FileIn]:
        try:
            files = [conv.FileIn.model_validate(f) for f in raw["files"]]
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            raise HTTPException(422, "; ".join(errors)) from exc
        names = [f.name for f in files]
        if len(set(names)) != len(names):
            raise HTTPException(422, "files: names must be unique")
        return files

    @app.post("/detect/{provider}", openapi_extra={"requestBody": {"required": True}})
    async def detect_route(provider: str, request: Request) -> Any:
        """Say what each file contains. Deterministic rules; never stores anything."""
        prov = service.resolve_provider(provider)
        files = parse_files(await read_files(request))
        return conv.detect_files(prov, files).model_dump(mode="json")

    @app.post("/convert/{provider}", openapi_extra={"requestBody": {"required": True}})
    async def convert_route(provider: str, request: Request) -> Any:
        """Detect then normalize every file. PREVIEW ONLY: there is deliberately no save option.

        Optional boolean flags: `trace` adds provenance, `integrity` adds deterministic checks.

        Saving is a separate explicit step through `/ingest/{provider}/{entity_type}`.
        """
        prov = service.resolve_provider(provider)
        raw = await read_files(request)
        files = parse_files(raw)
        ctx_in = raw.get("context") or {}
        if not isinstance(ctx_in, dict):
            raise HTTPException(422, "context: expected an object")
        context = {
            k: (str(ctx_in[k]) if ctx_in.get(k) not in (None, "") else None)
            for k in ("tenant_id", *conv.CONTEXT_FIELDS)
        }
        context["tenant_id"] = context["tenant_id"] or "preview"
        out = conv.convert_files(
            prov,
            files,
            context,
            trace=raw.get("trace") is True,
            integrity=raw.get("integrity") is True,
        ).model_dump(mode="json")
        if out.get("integrity") is None:
            out.pop(
                "integrity", None
            )  # without the flag the response is exactly what it always was
        return out

    @app.post(
        "/migrate/preview/{destination}",
        response_model=MigrationPreview,
        openapi_extra={"requestBody": {"required": True}},
    )
    async def migrate_preview(destination: str, request: Request) -> Any:
        """DRY RUN: map one canonical employee to the destination provider's create request.

        Read-only: no provider call, no credentials, no database access, nothing is stored or sent.
        Only `ukg-ready` is supported. `READY` means the draft passed the documented local checks,
        not that UKG accepts it.
        """
        if destination != "ukg-ready":
            raise HTTPException(
                422, f"unsupported destination {destination!r}; supported: ['ukg-ready']"
            )
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "request body too large")
        try:
            raw = json.loads(body, parse_float=Decimal)
        except ValueError as exc:
            raise HTTPException(400, "request body is not valid JSON") from exc
        bad = find_invalid_text(raw)
        if bad:  # only the location is reported, never the text
            raise HTTPException(422, f"request holds a NUL or invalid Unicode character at {bad}")
        try:
            req = MigrationRequest.model_validate(raw)
        except (
            ValidationError
        ) as exc:  # field paths and rules only: the offending values are not echoed
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
            raise HTTPException(422, "; ".join(errors)) from exc
        return preview_ukg_ready(req)

    # --- reads -------------------------------------------------------------------------------
    # Stored records come back in canonical form WITHOUT source extras (the raw leftovers can hold
    # sensitive provider data). Account numbers are masked and SSNs are last-4 at the source.
    base = "/tenants/{tenant_id}/{provider}"
    Limit = Annotated[int, Query(ge=1, le=500)]
    Offset = Annotated[int, Query(ge=0)]

    def provider_name(provider: str) -> str:
        return service.resolve_provider(provider).value

    def found(item: Any, what: str) -> Any:
        if item is None:
            raise HTTPException(404, f"{what} not found")
        return item

    @app.get(f"{base}/companies", response_model=list[Company])
    def list_companies(
        tenant_id: str, provider: str, session: DB, limit: Limit = 50, offset: Offset = 0
    ) -> Any:
        return repo.list_companies(session, provider_name(provider), tenant_id, limit, offset)

    @app.get(f"{base}/companies/{{company_id}}", response_model=Company)
    def get_company(tenant_id: str, provider: str, company_id: str, session: DB) -> Any:
        item = repo.get_company(session, provider_name(provider), tenant_id, company_id)
        return found(item, "company")

    @app.get(f"{base}/companies/{{company_id}}/employees", response_model=list[Employee])
    def list_employees(
        tenant_id: str,
        provider: str,
        company_id: str,
        session: DB,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id)
        return repo.list_employees(*args, limit, offset)

    @app.get(f"{base}/companies/{{company_id}}/employees/{{employee_id}}", response_model=Employee)
    def get_employee(
        tenant_id: str, provider: str, company_id: str, employee_id: str, session: DB
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id, employee_id)
        return found(repo.get_employee(*args), "employee")

    @app.get(
        f"{base}/companies/{{company_id}}/employees/{{employee_id}}/direct-deposits",
        response_model=list[DirectDepositInstruction],
    )
    def employee_deposits(
        tenant_id: str, provider: str, company_id: str, employee_id: str, session: DB
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id, employee_id)
        return repo.list_direct_deposits(*args)

    @app.get(
        f"{base}/companies/{{company_id}}/employees/{{employee_id}}/deductions",
        response_model=list[DeductionSetup],
    )
    def employee_deductions(
        tenant_id: str, provider: str, company_id: str, employee_id: str, session: DB
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id, employee_id)
        return repo.list_deductions(*args)

    @app.get(
        f"{base}/companies/{{company_id}}/employees/{{employee_id}}/paychecks",
        response_model=list[Paycheck],
    )
    def employee_paychecks(
        tenant_id: str,
        provider: str,
        company_id: str,
        employee_id: str,
        session: DB,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> Any:
        return repo.list_paychecks(
            session,
            provider_name(provider),
            tenant_id,
            company_id,
            employee_id=employee_id,
            limit=limit,
            offset=offset,
        )

    @app.get(f"{base}/companies/{{company_id}}/payroll-runs", response_model=list[PayrollRun])
    def list_runs(
        tenant_id: str,
        provider: str,
        company_id: str,
        session: DB,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id)
        return repo.list_payroll_runs(*args, limit, offset)

    @app.get(
        f"{base}/companies/{{company_id}}/payroll-runs/{{run_id}}/paychecks",
        response_model=list[Paycheck],
    )
    def run_paychecks(
        tenant_id: str,
        provider: str,
        company_id: str,
        run_id: str,
        session: DB,
        limit: Limit = 50,
        offset: Offset = 0,
    ) -> Any:
        return repo.list_paychecks(
            session,
            provider_name(provider),
            tenant_id,
            company_id,
            run_id=run_id,
            limit=limit,
            offset=offset,
        )

    @app.get(f"{base}/companies/{{company_id}}/paychecks/{{paycheck_id}}", response_model=Paycheck)
    def get_paycheck(
        tenant_id: str, provider: str, company_id: str, paycheck_id: str, session: DB
    ) -> Any:
        args = (session, provider_name(provider), tenant_id, company_id, paycheck_id)
        return found(repo.get_paycheck(*args), "paycheck")

    return app


app = create_app()
