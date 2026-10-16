# ruff: noqa: E501
"""Provider ERROR responses are recognised before any entity mapping is attempted.

An error body is not an employee or a paycheck; mapping it as one only produces misleading follow-up errors ("missing id").
What each provider documents (contracts/):
  * PrismHR  - every response carries `errorCode` ("unique error code; "0" on success") and `errorMessage`;
  * isolved  - 400 responses carry `ValidationErrorModel` {error, validationResults[{fields, message}]};
  * UKG Ready - standard errors are {"errors": [{code, message}], "user_messages": [...]} (reference-docs/Error handling.html).
A body counts as an error envelope ONLY when it holds nothing but the error keys, so a real record set that happens to carry a
warning-style message is still normalized. The message that is shown is provider text, shortened, with SSN-formatted numbers and
long token-like strings removed; the payload itself is never echoed.
"""

import re
from typing import Any

from .canonical.enums import Provider

_TOKENISH = re.compile(r"[A-Za-z0-9_\-+/=]{24,}")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
PRISM_ENVELOPE = {"errorCode", "errorMessage", "infoMessage", "updateMessage", "extension"}
ISOLVED_ENVELOPE = {"error", "validationResults", "message", "Message"}
UKG_ENVELOPE = {"errors", "user_messages"}


def safe_text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value).split())
    text = _SSN.sub("[redacted]", _TOKENISH.sub("[redacted]", text))
    text = "".join(ch for ch in text if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def provider_error(provider: Provider, payload: Any) -> tuple[str, str] | None:
    """(code, safe message) when the payload is a documented error envelope, else None."""
    if not isinstance(payload, dict) or not payload:
        return None
    keys = set(payload)
    if provider is Provider.PRISMHR and keys <= PRISM_ENVELOPE and "errorCode" in keys:
        code = str(payload.get("errorCode") if payload.get("errorCode") is not None else "").strip()
        if code not in ("", "0"):
            return code, safe_text(payload.get("errorMessage") or "no message given")
    if (
        provider is Provider.ISOLVED
        and keys <= ISOLVED_ENVELOPE
        and keys & {"error", "validationResults"}
    ):
        results = payload.get("validationResults")
        parts = [payload.get("error")] if payload.get("error") else []
        if isinstance(results, list):
            parts += [r.get("message") for r in results if isinstance(r, dict) and r.get("message")]
        if parts or results:
            return "validation", safe_text(
                "; ".join(safe_text(p, 120) for p in parts[:3]) or "no message given"
            )
    if (
        provider is Provider.UKG_READY
        and keys <= UKG_ENVELOPE
        and isinstance(payload.get("errors"), list)
        and payload["errors"]
    ):
        errors = [e for e in payload["errors"] if isinstance(e, dict)]
        if errors:
            code = str(errors[0].get("code", "")).strip() or "error"
            return code, safe_text(
                "; ".join(
                    safe_text(e.get("message", ""), 120) for e in errors[:3] if e.get("message")
                )
                or "no message given"
            )
    return None
