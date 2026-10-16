"""Normalization service: pick the adapter, run it, return a result with structured issues.

This module (and everything below it) knows nothing about FastAPI or PostgreSQL.
"""

from typing import Any

from pydantic import ValidationError

from .adapters import ADAPTERS, ENTITIES
from .adapters.base import SourceContext
from .canonical.enums import EntityType, Provider, Severity
from .canonical.models import Issue, NormalizationResult
from .duplicates import duplicate_issues
from .errors import NormalizationError
from .inputs import find_invalid_text
from .provider_errors import provider_error
from .references import load_reference

PROVIDER_ALIASES = {"ukg": "ukg-ready", "ukg_ready": "ukg-ready", "ukgready": "ukg-ready"}


class UnknownProviderOrEntity(ValueError):
    """Caller asked for a provider or entity type we do not support."""


def resolve_provider(name: str) -> Provider:
    key = PROVIDER_ALIASES.get(name.lower(), name.lower())
    if key not in ADAPTERS:
        raise UnknownProviderOrEntity(
            f"unknown provider {name!r}; expected one of {sorted(ADAPTERS)}"
        )
    return Provider(key)


def resolve_entity(name: str) -> EntityType:
    key = name.lower().replace("-", "_")
    if key not in ENTITIES:
        raise UnknownProviderOrEntity(
            f"unknown entity type {name!r}; expected one of {list(ENTITIES)}"
        )
    return EntityType(key)


def _context_dict(context: dict | SourceContext) -> dict:
    return context.model_dump() if isinstance(context, SourceContext) else dict(context)


def normalize(
    provider: str,
    entity_type: str,
    payload: Any,
    context: dict | SourceContext,
    trace: bool = False,
) -> NormalizationResult:
    """Normalize one provider response. Never raises for bad *data*; returns error issues.

    Raises UnknownProviderOrEntity for an unsupported provider/entity (a caller bug).
    """
    prov = resolve_provider(provider)
    entity = resolve_entity(entity_type)
    adapter = ADAPTERS[prov.value]
    if trace:  # opt-in provenance; the adapters stay unchanged for callers that do not ask
        ctx = SourceContext.model_validate(context)
        context = ctx.model_copy(update={"trace": True})

    def failed(code: str, message: str) -> NormalizationResult:
        return NormalizationResult(
            provider=prov,
            entity_type=entity.value,
            issues=[Issue(severity=Severity.ERROR, code=code, message=message)],
        )

    # NUL (not storable) and lone surrogates (not encodable) are refused up front
    for what, doc in (("payload", payload), ("context", _context_dict(context))):
        bad = find_invalid_text(doc)
        if bad:
            return failed(
                "invalid_text",
                f"{what} holds a NUL or invalid Unicode character at {bad.replace('$', what, 1)}",
            )
    found = provider_error(prov, payload)
    if (
        found
    ):  # an error response is not a record: say so, and do not run it through the entity mapping
        code, message = found
        return failed(
            "provider_error",
            f"{prov.value} returned an error response (code {code}): {message}. "
            "No records were created.",
        )
    try:
        result = adapter.normalize(entity.value, payload, load_reference(prov.value), context)
    except NormalizationError as exc:
        return failed(exc.code, exc.message)
    except ValidationError:
        raise  # a bad *context* (e.g. no tenant_id) is the caller's mistake, reported as such
    except (AttributeError, TypeError, KeyError, IndexError, ValueError, ArithmeticError):
        # Last line of defence: an unforeseen field type is bad DATA, not a server error.
        # The exception text is not reported: it could quote the payload.
        return failed(
            "unsupported_structure",
            f"the payload structure is not supported by the {prov.value} {entity.value} mapping",
        )
    result.issues.extend(
        duplicate_issues(result.records)
    )  # a conflicting repeat is an error: nothing is stored
    if not result.records and result.ok:
        result.issues.append(
            Issue(severity=Severity.WARNING, code="no_records", message="payload held no records")
        )
    return result
