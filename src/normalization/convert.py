"""Multi-file detect + preview conversion. Pure functions: no FastAPI, no database.

Nothing here stores anything. Saving is a separate, explicit step that reuses `/ingest`.
"""

from typing import Any

from pydantic import BaseModel, Field

from . import service
from .canonical.enums import EntityType, Provider
from .detect import Detection, Unit, detect, provider_name, unit_for_override
from .integrity import IntegrityReport, evaluate

CONTEXT_FIELDS = ("company_id", "employee_id", "payroll_id")


class FileIn(BaseModel):
    name: str
    content: Any
    entity: str | None = None  # the user's confirmation when detection was ambiguous


class IdSuggestion(BaseModel):
    field: str
    value: str
    source_file: str
    why: str


class FileDetection(BaseModel):
    name: str
    detection: Detection


class DetectOut(BaseModel):
    provider: str
    files: list[FileDetection]
    suggestions: list[IdSuggestion] = Field(default_factory=list)


class UnitResult(BaseModel):
    file: str
    entity: str
    label: str
    status: str  # converted | needs_context | failed
    missing: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    issues: list[dict] = Field(default_factory=list)
    records: list[dict] = Field(default_factory=list)
    traces: list[list[dict]] = Field(default_factory=list)  # parallel to records; opt-in


class ConvertOut(BaseModel):
    provider: str
    files: list[FileDetection]
    results: list[UnitResult]
    suggestions: list[IdSuggestion] = Field(default_factory=list)
    integrity: IntegrityReport | None = (
        None  # opt-in: deterministic checks over all converted records
    )


def _s(v: Any) -> str | None:
    return str(v) if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v) else None


def find_ids(provider: Provider, doc: Any) -> list[tuple[str, str, str]]:
    """Identifiers a file states explicitly: (field, value, why). Never derived or invented."""
    out: list[tuple[str, str, str]] = []
    if provider is Provider.PRISMHR and isinstance(doc, dict):
        cm = doc.get("clientMaster")
        if isinstance(cm, dict) and _s(cm.get("id")):
            out.append(
                ("company_id", _s(cm["id"]) or "", "clientMaster.id (the PrismHR client id)")
            )
        emps = doc.get("employee")
        for e in emps if isinstance(emps, list) else []:
            if isinstance(e, dict) and _s(e.get("id")):
                out.append(("employee_id", _s(e["id"]) or "", "employee[].id"))
    elif provider is Provider.ISOLVED:
        rows = (
            doc
            if isinstance(doc, list)
            else (doc.get("results") if isinstance(doc, dict) else None)
        )
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict) or not _s(r.get("id")):
                continue
            if r.get("legalName"):
                out.append(("company_id", _s(r["id"]) or "", "legal company id"))
            elif isinstance(r.get("nameAddress"), dict):
                out.append(("employee_id", _s(r["id"]) or "", "employee id"))
    elif provider is Provider.UKG_READY and isinstance(doc, dict):
        emp = doc.get("employee")
        if isinstance(emp, dict) and _s(emp.get("id")):
            out.append(("employee_id", _s(emp["id"]) or "", "employee.id (the UKG account id)"))
    return out


def suggest(provider: Provider, files: list[FileIn]) -> list[IdSuggestion]:
    seen: set[tuple[str, str]] = set()
    out: list[IdSuggestion] = []
    for f in files:
        for field, value, why in find_ids(provider, f.content):
            if (field, value) not in seen:
                seen.add((field, value))
                out.append(IdSuggestion(field=field, value=value, source_file=f.name, why=why))
    return out


def detect_files(provider: Provider, files: list[FileIn]) -> DetectOut:
    return DetectOut(
        provider=provider.value,
        files=[FileDetection(name=f.name, detection=_detect_one(provider, f)) for f in files],
        suggestions=suggest(provider, files),
    )


def _detect_one(provider: Provider, f: FileIn) -> Detection:
    if f.entity is None:
        return detect(provider, f.content)
    try:
        entity = service.resolve_entity(f.entity)
    except service.UnknownProviderOrEntity as exc:
        return Detection(status="unsupported", message=str(exc))
    unit, why = unit_for_override(provider, f.content, entity)
    if unit is None:
        return Detection(status="unsupported", message=why)
    return Detection(status="ok", units=[unit])


def _slice(doc: Any, unit: Unit) -> Any:
    if unit.keys is None or not isinstance(doc, dict):
        return doc
    return {k: doc[k] for k in unit.keys if k in doc}


def convert_files(
    provider: Provider,
    files: list[FileIn],
    context: dict[str, str | None],
    trace: bool = False,
    integrity: bool = False,
) -> ConvertOut:
    det = detect_files(provider, files)
    by_name = {f.name: f for f in files}
    results: list[UnitResult] = []
    ctx = {k: v for k, v in context.items() if v}
    for fd in det.files:
        if fd.detection.status != "ok":
            continue
        doc = by_name[fd.name].content
        for unit in fd.detection.units:
            missing = [r for r in unit.requires if not ctx.get(r)]
            base = {"file": fd.name, "entity": unit.entity.value, "label": unit.label}
            if missing:
                results.append(UnitResult(**base, status="needs_context", missing=missing))
                continue
            res = service.normalize(
                provider.value, unit.entity.value, _slice(doc, unit), ctx, trace=trace
            )
            results.append(
                UnitResult(
                    **base,
                    status="converted" if res.ok else "failed",
                    counts=res.counts(),
                    issues=[i.model_dump(mode="json") for i in res.issues],
                    records=[r.model_dump(mode="json") for r in res.records],
                    traces=res.traces if trace else [],
                )
            )
    report = None
    if (
        integrity
    ):  # one report over every converted record, so relationships across files can be checked
        report = evaluate([rec for r in results if r.status == "converted" for rec in r.records])
    return ConvertOut(
        provider=provider.value,
        files=det.files,
        results=results,
        suggestions=det.suggestions,
        integrity=report,
    )


__all__ = ["CONTEXT_FIELDS", "ConvertOut", "DetectOut", "EntityType", "FileIn", "convert_files",
           "detect_files", "provider_name"]  # fmt: skip
