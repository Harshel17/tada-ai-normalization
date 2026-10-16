# ruff: noqa: E501
"""The one policy for sensitive data in source payloads that are kept as `extras`.

Mapped sensitive fields (SSN, account numbers) are read through `Src.secret()` and never reach `extras`.
This module protects everything else: unread source fields are preserved, so a secret under a key we never
mapped could otherwise be stored and returned. Three independent layers, all applied by `scrub`:

  1. KEY NAME  - a key whose words name a secret (`taxpayerNumber`, `socialSecurityNo`, `bankAccountNumber`,
                 `apiToken`, `password`, ...) is matched on its WORDS, not its spelling: `businessName` or
                 `className` contain the letters "ssn" but not the word.
  2. CONTEXT   - in an object that labels itself as sensitive (`{"type": "SSN", "value": "..."}`) the
                 value-carrying keys are redacted whatever they are called.
  3. VALUE     - a string holding an SSN-formatted number (###-##-####) is redacted under any key.

A redacted value is replaced by the marker and its path is recorded (`provenance.redacted_paths`), which is the
only structural metadata kept; empty values and booleans carry no secret and are left as they are.

LIMITS (stated, not hidden): a secret under a generic, innocuous key name with no sensitive label and no SSN
formatting (e.g. `{"ref": "123456789"}`, an unformatted account number under `"detail"`) cannot be recognised.
Unknown fields are therefore preserved on the basis of what can be recognised, not of what is known to be safe.
"""

import re
from typing import Any

REDACTED = "[redacted]"

_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SSN_VALUE = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

# words that make a key sensitive on their own
_WORDS = frozenset(
    ("ssn", "ssns", "bsn", "taxpayer", "password", "passwd", "pwd", "passphrase", "secret", "secrets",
     "token", "tokens", "credential", "credentials", "pin", "iban", "cvv", "cvc", "passport", "passports",
     "bearer")
)  # fmt: skip
# adjacent word pairs that make a key sensitive
_PAIRS = frozenset(
    {
        ("social", "security"), ("social", "insurance"), ("national", "insurance"),
        ("account", "num"), ("account", "number"), ("account", "no"),
        ("account", "nbr"), ("acct", "num"), ("acct", "number"), ("acct", "no"), ("acct", "nbr"),
        ("card", "number"), ("card", "num"), ("card", "no"), ("credit", "card"),
        ("api", "key"), ("access", "key"), ("private", "key"), ("secret", "key"),
        ("client", "secret"), ("driver", "license"), ("drivers", "license"), ("driver", "licence"),
        ("document", "number"), ("document", "content"), ("profile", "picture"),
        ("tax", "payer"), ("tax", "identification"), ("tin", "number"),
    }
)  # fmt: skip
# a bank "account" without "number" may be a short label/code (PrismHR voucher `bankAccount: "OPS-1"`): hidden only
# when its value looks like an account number (4+ digits) or is a nested structure
_WEAK_PAIRS = frozenset({("bank", "account")})
_LABEL_KEYS = frozenset({"type", "name", "label", "kind", "category", "idtype", "documenttype"})
_VALUE_KEYS = frozenset({"value", "number", "identifier", "idnumber", "id_number", "num"})


def words(text: str) -> list[str]:
    """`socialSecurityNo`, `social_security_no`, `Social Security No` -> ['social', 'security', 'no']."""
    return [w.lower() for w in _WORD.findall(str(text))]


def is_sensitive_name(name: Any) -> bool:
    """Does this key (or label) name a secret? Judged on its words."""
    ws = words(name)
    if any(w in _WORDS for w in ws):
        return True
    return any(pair in _PAIRS for pair in zip(ws, ws[1:], strict=False))


def _is_weak_name(name: Any) -> bool:
    ws = words(name)
    return any(pair in _WEAK_PAIRS for pair in zip(ws, ws[1:], strict=False))


def _looks_like_number(value: Any) -> bool:
    if isinstance(value, dict | list):
        return True
    return sum(ch.isdigit() for ch in str(value)) >= 4


def contains_ssn_format(text: str) -> bool:
    """Does the text hold an SSN-formatted number (###-##-####)?"""
    return bool(_SSN_VALUE.search(text))


def _carries_secret(value: Any) -> bool:
    """Empty values and booleans hold nothing to hide (`"hasSsn": true`, `"password": ""`)."""
    return value is not None and value != "" and not isinstance(value, bool)


def scrub(value: Any, path: str, redacted: list[str]) -> Any:
    """Copy of `value` with sensitive content replaced by the marker; replaced paths go to `redacted`."""
    if isinstance(value, dict):
        labelled = any(
            str(k).lower() in _LABEL_KEYS and isinstance(v, str) and is_sensitive_name(v)
            for k, v in value.items()
        )
        out = {}
        for k, v in value.items():
            here = f"{path}.{k}"
            hide = (
                is_sensitive_name(k)
                or (labelled and str(k).lower() in _VALUE_KEYS)
                or (_is_weak_name(k) and _carries_secret(v) and _looks_like_number(v))
            )
            if hide and _carries_secret(v):
                out[k] = REDACTED
                redacted.append(here)
            else:
                out[k] = scrub(v, here, redacted)
        return out
    if isinstance(value, list):
        return [scrub(v, f"{path}[{i}]", redacted) for i, v in enumerate(value)]
    if isinstance(value, str) and _SSN_VALUE.search(value):
        redacted.append(path)
        return REDACTED
    return value
