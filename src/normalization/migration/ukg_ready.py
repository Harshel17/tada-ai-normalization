# ruff: noqa: E501
"""Canonical employee -> UKG Ready "create employee" request PREVIEW (dry run, nothing is sent).

Contract: `contracts/ukg-ready/reference-schemas.json`, page `__v2__companies__(cid)__employees.html`, the POST
schema (`POST /v2/companies/{cid}/employees`, "Creates a new Employee record"). Only documented fields are used and the
finished body is validated against that very schema (`schema_check`). Nothing is invented: `username` (the only
required value the canonical model cannot supply) must come from the operator; source ids are sent only when the
operator confirms; sensitive data (SSN, bank, compensation, tax) is never part of the preview.

READY means "passes the documented local checks". It never means UKG accepts the request.
"""

import re
from datetime import date
from typing import Any

from ..canonical.models import Address, Employee
from ..sensitive import contains_ssn_format
from . import schema_check
from .models import (
    ContractRef,
    FieldMapping,
    MigrationIssue,
    MigrationPreview,
    MigrationRequest,
    NotMigrated,
    Requirement,
)

NOTICE = "DRY RUN — No data has been sent to the destination provider."
READINESS_MEANING = (
    "READY means the draft passed the documented local checks only. It does not mean UKG Ready will accept "
    "the request: the reference is a snapshot, not a verified OpenAPI contract, and nothing was tested against a tenant."
)
ENDPOINT = "/v2/companies/{cid}/employees"
CAVEAT = (
    "The local UKG reference states: 'this JSON extraction is not a complete endpoint contract' and 'No official "
    "OpenAPI artifact or current live version is verified.'"
)
DATE_MIN, DATE_MAX = (
    date(1900, 12, 31),
    date(2079, 6, 5),
)  # documented range for every dates.* field
PREFERRED_PHONE = {
    "home": "HOME",
    "mobile": "CELL",
    "work": "WORK",
    "none": "NONE",
}  # reverse of the read mapping
# canonical date field -> documented dates.* key
DATES = {
    "hire_date": "hired",
    "start_date": "started",
    "birth_date": "birthday",
    "rehire_date": "re_hired",
    "peo_start_date": "peo_hired",
    "seniority_date": "seniority",
}
# fields deliberately never part of a preview
SENSITIVE = {
    "ssn_last4": "UKG social_security needs the full SSN, which is never stored; the last four are not sent",
    "compensation": "pay data is out of scope for the core employee request and is not shown",
    "tax_withholdings": "tax withholding data is sensitive and out of scope",
    "tax_form_address": "tax-form addresses belong to tax setup, not the core employee request",
}
SEPARATE = {
    "gender": "UKG takes gender on a separate documented call (PUT /employees/{aid}/demographics), not in the create body",
}
# canonical fields that are consumed by the mapping (or reported individually above) and so need no generic entry
HANDLED = {
    "first_name", "last_name", "middle_name", "preferred_name", "name_suffix", "name_prefix", "email",
    "secondary_email", "work_email", "personal_email", "home_phone", "mobile_phone", "work_phone", "preferred_phone",
    "address", "mailing_address", "employee_number", "username", "manager_id", "supervisor_id", "gender",
    "gender_raw", "employment_status", "employment_status_raw", "extras", "provenance", "record_type", "provider",
    "tenant_id", "company_id", "source_id", "original_hire_date", "termination_date", "retirement_date",
    "deceased_date", "last_worked_date", "leave_return_date", "status_date", *DATES, *SENSITIVE,
}  # fmt: skip
UNSUPPORTED_NOTE = "not a field of the documented create-employee body"


def _has(value: Any) -> bool:
    return value not in (None, "", [], {}, False)


class _Draft:
    def __init__(self) -> None:
        self.body: dict[str, Any] = {}
        self.fields: list[FieldMapping] = []
        self.issues: list[MigrationIssue] = []
        self.not_migrated: list[NotMigrated] = []

    def issue(self, severity: str, code: str, message: str, field: str | None = None) -> None:
        self.issues.append(
            MigrationIssue(severity=severity, code=code, field=field, message=message)
        )

    def put(
        self, path: str, value: Any, source: str | None, status: str = "mapped", note: str = ""
    ) -> None:
        cur = self.body
        *parents, leaf = path.split(".")
        for key in parents:
            cur = cur.setdefault(key, {})
        cur[leaf] = value
        self.fields.append(
            FieldMapping(destination=path, source=source, status=status, value=value, note=note)
        )

    def omit(self, destination: str, source: str | None, note: str) -> None:
        self.fields.append(
            FieldMapping(destination=destination, source=source, status="omitted", note=note)
        )

    def skip(self, source: str, reason: str, note: str) -> None:
        self.not_migrated.append(NotMigrated(source=source, reason=reason, note=note))


def _safe_text(draft: _Draft, value: str | None, source: str, destination: str) -> str | None:
    """A string fit to send: trimmed; refused (and reported) if it holds control characters or an SSN-formatted number."""
    if not _has(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        draft.omit(destination, source, "contains control characters")
        draft.issue(
            "review",
            "unsafe_value",
            f"{source} contains control characters and was not used",
            destination,
        )
        return None
    if contains_ssn_format(text):
        draft.omit(destination, source, "looks like an SSN")
        draft.issue(
            "review",
            "sensitive_value",
            f"{source} holds an SSN-formatted number and was withheld",
            destination,
        )
        return None
    return text


def _address(
    draft: _Draft, addr: Address | None, dest: str, source: str, operator_country: str | None
) -> None:
    if addr is None or not any(_has(v) for v in addr.model_dump().values()):
        return
    country = (addr.country or "").strip().upper()
    from_operator = False
    if not re.fullmatch(r"[A-Z]{3}", country):
        if addr.country and not operator_country:
            draft.issue(
                "review",
                "country_not_alpha3",
                f"{source}.country is not an ISO 3166-1 alpha-3 code and was not converted",
                f"{dest}.country",
            )
        country = (operator_country or "").strip().upper()
        from_operator = True
    if not country:
        draft.omit(dest, source, "the documented address object requires an ISO alpha-3 country")
        draft.issue(
            "review",
            "address_country_required",
            f"{source} was not sent: the documented address requires a country (ISO alpha-3). Supply it as operator input.",
            dest,
        )
        return
    mapping = [
        ("address_line_1", addr.line1, "line1"),
        ("address_line_2", addr.line2, "line2"),
        ("address_line_3", addr.line3, "line3"),
        ("number", addr.house_number, "house_number"),
        ("city", addr.city, "city"),
        ("state_code", addr.state, "state"),
        ("zip", addr.postal_code, "postal_code"),
    ]  # fmt: skip
    draft.put(f"{dest}.country", country, None if from_operator else f"{source}.country",
              "operator_supplied" if from_operator else "mapped")  # fmt: skip
    for key, value, name in mapping:
        text = _safe_text(draft, value, f"{source}.{name}", f"{dest}.{key}")
        if text is not None:
            draft.put(f"{dest}.{key}", text, f"{source}.{name}")
    if _has(addr.county):
        draft.skip(f"{source}.county", "unsupported", UNSUPPORTED_NOTE)


def preview_ukg_ready(request: MigrationRequest) -> MigrationPreview:
    """Pure and deterministic: the same input always gives the same preview; the input is never modified."""
    emp: Employee = request.employee
    op = request.operator
    d = _Draft()

    # --- required: username (operator only), first_name, last_name -------------------------------------------------
    username = _safe_text(d, op.username, "operator.username", "username")
    if username:
        d.put(
            "username",
            username,
            None,
            "operator_supplied",
            "supplied by the operator; not taken from the source",
        )
    else:
        d.fields.append(FieldMapping(destination="username", source=None, status="missing_required",
                                     note="required by the documented schema"))  # fmt: skip
        hint = (
            " The source record has a username, but it is not assumed to be valid in the destination."
            if emp.username
            else ""
        )
        d.issue(
            "blocking",
            "missing_username",
            f"username is required by the documented schema and was not supplied.{hint}",
            "username",
        )
    for key, value in (("first_name", emp.first_name), ("last_name", emp.last_name)):
        text = _safe_text(d, value, key, key)
        if text:
            d.put(key, text, key)
        else:
            d.fields.append(FieldMapping(destination=key, source=key, status="missing_required",
                                         note="required by the documented schema"))  # fmt: skip
            d.issue(
                "blocking",
                f"missing_{key}",
                f"{key} is required by the documented schema and the canonical record has none",
                key,
            )

    # --- optional name and contact fields --------------------------------------------------------------------------
    for dest, value, source in (
        ("middle_name", emp.middle_name, "middle_name"),
        ("nickname", emp.preferred_name, "preferred_name"),
        ("suffix", emp.name_suffix, "name_suffix"),
        ("salutation", emp.name_prefix, "name_prefix"),
        ("primary_email", emp.email, "email"),
        ("secondary_email", emp.secondary_email, "secondary_email"),
        ("phones.cell_phone", emp.mobile_phone, "mobile_phone"),
        ("phones.home_phone", emp.home_phone, "home_phone"),
        ("phones.work_phone", emp.work_phone, "work_phone"),
    ):  # fmt: skip
        text = _safe_text(d, value, source, dest)
        if text:
            d.put(dest, text, source)
    if not emp.email:  # which of several addresses is the primary one is a person's decision
        for source in ("personal_email", "work_email"):
            if _has(getattr(emp, source)):
                d.skip(
                    source,
                    "uncertain",
                    "the source has no primary email; choosing between personal and work is left to a person",
                )
    else:
        for source in ("personal_email", "work_email"):
            if _has(getattr(emp, source)) and getattr(emp, source) != emp.email:
                d.skip(
                    source,
                    "unsupported",
                    "the documented body has only primary_email and secondary_email",
                )
    if emp.preferred_phone is not None:
        code = PREFERRED_PHONE.get(emp.preferred_phone)
        if code and any(k.startswith("phones.") for k in _paths(d.body)):
            d.put("phones.preferred_phone", code, "preferred_phone")
        elif code:
            d.omit("phones.preferred_phone", "preferred_phone", "no phone number is being sent")
        else:
            d.omit("phones.preferred_phone", "preferred_phone", "unknown code")
            d.issue(
                "review",
                "unknown_code",
                "preferred_phone has a value without a documented destination code",
                "phones.preferred_phone",
            )

    # --- addresses -------------------------------------------------------------------------------------------------
    _address(d, emp.address, "address", "address", op.address_country)
    if emp.mailing_address is not None:
        _address(d, emp.mailing_address, "mailing_address", "mailing_address", op.address_country)
        if "mailing_address" in d.body:
            d.put("use_separate_mailing_address", True, "mailing_address")

    # --- dates (documented range 1900-12-31 .. 2079-06-05) ---------------------------------------------------------
    for source, key in DATES.items():
        value: date | None = getattr(emp, source)
        if value is None:
            continue
        if not DATE_MIN <= value <= DATE_MAX:
            d.omit(f"dates.{key}", source, "outside the documented date range")
            d.issue(
                "review",
                "date_out_of_range",
                f"{source} is outside the documented range 1900-12-31 to 2079-06-05",
                f"dates.{key}",
            )
        else:
            d.put(f"dates.{key}", value.isoformat(), source)
    for source in ("original_hire_date", "termination_date", "retirement_date", "deceased_date", "last_worked_date",
                   "leave_return_date", "status_date"):  # fmt: skip
        if _has(getattr(emp, source)):
            d.skip(source, "uncertain" if source in ("termination_date", "retirement_date", "deceased_date") else "unsupported",
                   "not set through create: the employee's state is a decision for the destination, not a date copied across"
                   if source in ("termination_date", "retirement_date", "deceased_date") else UNSUPPORTED_NOTE)  # fmt: skip

    # --- identifiers: source ids stay separate from the destination-assigned id ------------------------------------
    if op.use_source_employee_number:
        number = _safe_text(d, emp.employee_number, "employee_number", "employee_id")
        if number:
            d.put(
                "employee_id",
                number,
                "employee_number",
                "operator_supplied",
                "operator confirmed using the source employee number",
            )
        else:
            d.issue(
                "info",
                "no_source_employee_number",
                "confirmation given but the canonical record has no employee number",
                "employee_id",
            )
    elif _has(emp.employee_number):
        d.skip(
            "employee_number",
            "not_confirmed",
            "not sent as employee_id unless the operator confirms it",
        )
    if op.use_source_id_as_external_id:
        ext = _safe_text(d, emp.source_id, "source_id", "external_id")
        if ext:
            d.put(
                "external_id",
                ext,
                "source_id",
                "operator_supplied",
                "operator confirmed using the source record id",
            )
    if request.destination.ein_id is not None:
        d.put(
            "ein.id",
            request.destination.ein_id,
            None,
            "operator_supplied",
            "destination EIN chosen by the operator (multi-EIN companies)",
        )
    for source in ("manager_id", "supervisor_id"):
        if _has(getattr(emp, source)):
            d.skip(
                source,
                "uncertain",
                "a source manager id is not a destination account id; managers are linked after both exist",
            )

    # --- codes without a destination meaning, state, and everything not in the request -----------------------------
    if emp.gender is not None:
        d.skip("gender", "separate_endpoint", SEPARATE["gender"])
    elif emp.gender_raw:
        d.issue(
            "review",
            "unknown_code",
            "gender has a source code without a documented canonical meaning",
            "gender",
        )
        d.skip("gender_raw", "uncertain", "unknown source code")
    if emp.employment_status is not None or emp.employment_status_raw:
        if emp.employment_status_raw and emp.employment_status is None:
            d.issue(
                "review",
                "unknown_code",
                "employment status has a source code without a documented canonical meaning",
                "status",
            )
        d.skip(
            "employment_status",
            "unsupported",
            "status is read-only in the documented schema ('only used for GET')",
        )
        if emp.employment_status is not None and emp.employment_status.value != "active":
            d.issue(
                "review",
                "not_active_in_source",
                "the source employee is not active; create will not carry that state over",
                "status",
            )
    for source, why in SENSITIVE.items():
        if _has(getattr(emp, source)):
            d.skip(source, "sensitive", why)
    dumped = emp.model_dump()
    for source, value in dumped.items():
        if source not in HANDLED and _has(value):
            d.skip(source, "unsupported", UNSUPPORTED_NOTE)
    if emp.extras:
        d.skip(
            "extras",
            "unsupported",
            f"{len(emp.extras)} preserved source key(s) have no destination field",
        )

    # --- validate against the documented schema; optional violations are dropped and reported ----------------------
    schema = schema_check.employee_create_schema()
    for _ in range(6):
        problems = schema_check.validate(d.body, schema, schema)
        if not problems:
            break
        for path, message in problems:
            if path in schema["required"] and message.startswith("is required"):
                continue  # already reported as a missing required value
            if path in schema["required"] or path == "$":
                d.issue("blocking", "schema_violation", f"{path} {message}", path)
                continue
            target = (
                path.split(".")[0]
                if message.startswith("is required") and path.endswith(".country")
                else path
            )
            _remove(d.body, target)
            d.omit(target, None, message)
            d.issue(
                "review",
                "schema_violation",
                f"{target} {message}; it was left out of the draft",
                target,
            )
    d.issues = _dedupe(d.issues)
    d.not_migrated = list({n.source: n for n in d.not_migrated}.values())

    required = [
        Requirement(
            field=name, satisfied=name in d.body, detail="supplied" if name in d.body else "missing"
        )  # fmt: skip
        for name in schema["required"]
    ]
    blocked = any(i.severity == "blocking" for i in d.issues) or not all(
        r.satisfied for r in required
    )
    review = any(i.severity == "review" for i in d.issues)
    return MigrationPreview(
        notice=NOTICE,
        destination_provider="ukg-ready",
        destination_company_id=request.destination.company_id,
        source={
            "provider": emp.provider.value,
            "tenant_id": emp.tenant_id,
            "company_id": emp.company_id,
            "source_id": emp.source_id,
        },  # fmt: skip
        contract=ContractRef(
            provider="ukg-ready",
            method="POST",
            endpoint=ENDPOINT.replace("{cid}", request.destination.company_id),
            schema_file=schema_check.CONTRACT_FILE,
            schema_anchor=f"{schema_check.EMPLOYEE_PAGE}#{schema['_anchor']}",
            caveat=CAVEAT,
        ),  # fmt: skip
        readiness="BLOCKED" if blocked else "NEEDS_REVIEW" if review else "READY",
        readiness_meaning=READINESS_MEANING,
        request_body=d.body,
        fields=d.fields,
        requirements=required,
        issues=d.issues,
        not_migrated=d.not_migrated,
    )


def _paths(node: dict[str, Any], prefix: str = "") -> list[str]:
    out = []
    for k, v in node.items():
        out += _paths(v, f"{prefix}{k}.") if isinstance(v, dict) else [f"{prefix}{k}"]
    return out


def _remove(body: dict[str, Any], path: str) -> None:
    *parents, leaf = path.split(".")
    chain = [body]
    for key in parents:
        nxt = chain[-1].get(key)
        if not isinstance(nxt, dict):
            return
        chain.append(nxt)
    chain[-1].pop(leaf, None)
    for key, node in zip(reversed(parents), reversed(chain[1:]), strict=True):
        if node:
            break
        chain[chain.index(node) - 1].pop(key, None)


def _dedupe(issues: list[MigrationIssue]) -> list[MigrationIssue]:
    seen, out = set(), []
    for i in issues:
        key = (i.severity, i.code, i.field, i.message)
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out
