"""Shared plumbing for the three provider adapters.

The adapters are deliberately explicit field-by-field mappings. This module only provides:
  * Src        - wraps a source dict and remembers which keys were read, so every unread key
                 ends up in `extras` (nothing is silently dropped).
  * Collector  - gathers issues and builds validated canonical records.
  * parsers    - Decimal / date / text helpers with consistent null, empty and zero handling.
"""

import hashlib
import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from ..canonical.enums import Provider, Severity
from ..canonical.models import Issue, Provenance
from ..errors import NormalizationError
from ..security import SETUP_HINT, ConfigurationError, salt_problem
from ..sensitive import is_sensitive_name, scrub
from ..settings import get_settings

M = TypeVar("M", bound=BaseModel)
MAX_DECIMAL_EXPONENT = (
    30  # values from 1e-30 to below 1e31: far beyond any payroll amount, rate or hours
)


class SourceContext(BaseModel):
    """Facts the provider payload does not carry itself (they live in URLs or sign-in context)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    company_id: str | None = None  # PrismHR clientId / isolved legalId / UKG cid
    parent_company_id: str | None = None  # isolved clientId
    employee_id: str | None = None  # for per-employee responses that omit the employee id
    payroll_id: str | None = None  # for UKG pay statements fetched under one payroll
    trace: bool = False  # opt-in: record which source fields fed which canonical fields


def require(ctx: SourceContext, *names: str) -> None:
    missing = [n for n in names if not getattr(ctx, n)]
    if missing:
        raise NormalizationError(
            "missing_context", f"context.{', context.'.join(missing)} is required for this input"
        )


class Src:
    """A source dict that records which keys were read."""

    def __init__(self, data: dict[str, Any], path: str, redacted: list[str], issues: "Collector"):
        self._d = data
        self.path = path
        # `links` / `_links` are HATEOAS navigation URLs, not data: intentionally not kept.
        self._used: set[str] = {"links", "_links"} & data.keys()
        self._subs: dict[str, Src] = {}
        self._sublists: dict[str, list[Src]] = {}
        self._redacted = redacted
        self._c = issues
        self._claimed = False  # set once leftover() was taken (the owner stored it as `extras`)

    @property
    def collector(self) -> "Collector":
        return self._c

    # -- raw access ---------------------------------------------------------------------
    def get(self, key: str) -> Any:
        self._used.add(key)
        return self._d.get(key)

    def peek(self, key: str) -> Any:
        """Read without marking the key as consumed (it stays in `extras` unless consumed later)."""
        return self._d.get(key)

    def keep(self, key: str) -> None:
        """Un-consume a key we read but could not use, so its value stays in `extras`."""
        self._used.discard(key)

    def has(self, key: str) -> bool:
        return key in self._d

    def keys(self) -> list[str]:
        return list(self._d)

    # -- nested -------------------------------------------------------------------------
    def sub(self, key: str) -> "Src":
        """Nested object. Missing/null/non-object yields an empty Src so callers need no checks."""
        value = self._d.get(key)
        if isinstance(value, dict):
            child = Src(value, f"{self.path}.{key}", self._redacted, self._c)
            self._subs[key] = child
            return child
        if value is None or value == "" or value == []:
            self._used.add(key)
        elif key not in self._used:
            self._c.warn("unexpected_type", f"expected an object at {self.path}.{key}")
        return Src({}, f"{self.path}.{key}", self._redacted, self._c)

    def items(self, key: str) -> list["Src"]:
        """Nested array of objects. Missing/null yields []. Non-object entries are reported."""
        value = self._d.get(key)
        if value is None:
            self._used.add(key)
            return []
        if not isinstance(value, list):
            self._c.warn("unexpected_type", f"expected an array at {self.path}.{key}")
            return []
        out = []
        for i, item in enumerate(value):
            if isinstance(item, dict):
                out.append(Src(item, f"{self.path}.{key}[{i}]", self._redacted, self._c))
            else:
                self._c.warn("unexpected_type", f"expected an object at {self.path}.{key}[{i}]")
        self._sublists[key] = out
        return out

    # -- typed reads --------------------------------------------------------------------
    def text(self, key: str, to: str | None = None) -> str | None:
        """Trimmed string. null / "" / whitespace -> None. Numbers are stringified.

        `to` (here and on every typed read) names the canonical field this value feeds, so the
        optional trace can show where it came from. It never changes the value returned.
        """
        self._c.trace_read(self, key, "copy", to)
        v = self.get(key)
        if v is None:
            return None
        if isinstance(v, bool):
            return str(v).lower()
        if isinstance(v, (dict, list)):
            self._used.discard(key)  # keep it in extras
            self._c.warn("unexpected_type", f"expected a scalar at {self.path}.{key}")
            return None
        s = str(v).strip()
        return s or None

    def dec(self, key: str, to: str | None = None) -> Decimal | None:
        """Decimal. null / "" -> None. Zero stays zero. Garbage is reported and kept in extras."""
        self._c.trace_read(self, key, "decimal", to)
        v = self.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, bool):
            return self._bad(key, "decimal")
        try:
            d = Decimal(str(v).strip()) if not isinstance(v, Decimal) else v
        except InvalidOperation:
            return self._bad(key, "decimal")
        # finite and of a sane size (1e999999999 is "valid" to Decimal but needs a billion digits)
        if not d.is_finite() or abs(d.adjusted()) > MAX_DECIMAL_EXPONENT:
            return self._bad(key, "decimal")
        return d

    def integer(self, key: str, to: str | None = None) -> int | None:
        self._c.trace_read(self, key, "integer", to)
        d = self.dec(key)
        if d is None:
            return None
        if d != d.to_integral_value():
            self._used.discard(key)
            self._c.warn("invalid_integer", f"{self.path}.{key} is not a whole number")
            return None
        return int(d)

    def boolean(self, key: str, to: str | None = None) -> bool | None:
        """True/False from real booleans or the documented T/F, Y/N, true/false strings."""
        self._c.trace_read(self, key, "boolean", to)
        v = self.get(key)
        if v is None or v == "":
            return None
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "t", "y", "yes"):
            return True
        if s in ("false", "f", "n", "no"):
            return False
        return self._bad(key, "boolean")

    def day(self, key: str, to: str | None = None) -> date | None:
        """Date from 'YYYY-MM-DD' or an ISO datetime (only the calendar date is kept)."""
        self._c.trace_read(self, key, "date", to)
        v = self.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return date.fromisoformat(str(v).strip()[:10])
        except ValueError:
            return self._bad(key, "date")

    def instant(self, key: str, to: str | None = None) -> datetime | None:
        self._c.trace_read(self, key, "datetime", to)
        v = self.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            return datetime.fromisoformat(str(v).strip())
        except ValueError:
            return self._bad(key, "datetime")

    def secret(self, key: str, to: str | None = None) -> str | None:
        """Read a sensitive value: it is returned for masking but never lands in `extras`."""
        self._c.trace_read(self, key, "redact", to)
        v = self._d.get(key)
        self._used.add(key)
        if key in self._d:
            self._redacted.append(f"{self.path}.{key}")
        return None if v is None or str(v).strip() == "" else str(v).strip()

    def consume(self, *keys: str) -> None:
        """Mark keys as intentionally handled elsewhere (e.g. owned by another entity type)."""
        self._used.update(keys)

    def _bad(self, key: str, kind: str) -> None:
        self._used.discard(key)  # leave the offending value in extras
        self._c.warn(f"invalid_{kind}", f"{self.path}.{key} is not a valid {kind}")
        return None

    # -- leftovers ----------------------------------------------------------------------
    def leftover(self) -> dict[str, Any]:
        """Unread keys. A child whose leftover was already claimed (as a line item's `extras`)
        is not repeated in its parent's leftover."""
        self._claimed = True
        out: dict[str, Any] = {}
        for k, v in self._d.items():
            if k in self._used:
                continue
            if k in self._subs:
                if self._subs[k]._claimed:  # noqa: SLF001
                    continue
                rest = self._subs[k].leftover()
                if rest:
                    out[k] = rest
            elif k in self._sublists:
                kids = [s for s in self._sublists[k] if not s._claimed]  # noqa: SLF001
                if len(kids) < len(self._sublists[k]):
                    continue  # the owner took these as line extras
                rests = [s.leftover() for s in kids]
                if any(rests):
                    out[k] = rests
            else:
                out[k] = v
        return scrub(out, self.path, self._redacted)


class Collector:
    """Per-call state: issues found, and a helper to build validated records."""

    def __init__(self, provider: Provider, trace: bool = False):
        self.provider = provider
        self.issues: list[Issue] = []
        self.redacted: list[str] = []
        self._source_id: str | None = None
        self.tracing = trace
        self._pending: list[PendingStep] = []  # reads waiting for the record that owns them
        self._built: dict[int, tuple[Any, list[dict]]] = {}  # id(record) -> (record, trace)

    # -- provenance trace (opt-in; response only, never stored) -------------------------------
    def trace_read(
        self, src: "Src", key: str, op: str, dest: str | None, anchor: "Src | None" = None
    ) -> None:
        """`src[key]` feeds canonical field `dest`. `anchor` is the Src whose record owns the step
        (default `src`), so a record can claim a read of its parent object."""
        if not (self.tracing and dest):
            return
        value = src.peek(key)
        where = (anchor or src).path
        self._pending.append(PendingStep(dest, op, [(f"{src.path}.{key}", key, value)], where))

    def trace_enum(
        self, src: "Src", key: str, dest: str, table: str, anchor: "Src | None" = None
    ) -> None:
        """A documented code became a canonical enum member via the named lookup table."""
        if not self.tracing:
            return
        where = (anchor or src).path
        self._pending.append(
            PendingStep(dest, "enum", [(f"{src.path}.{key}", key, src.peek(key))], where, table)
        )

    def trace_combine(
        self,
        dest: str,
        parts: list[tuple["Src", str]],
        op: str = "combine",
        note: str = "",
        anchor: "Src | None" = None,
    ) -> None:
        """One canonical value built from several source fields (all of them contributed)."""
        if not (self.tracing and parts):
            return
        # parts the source did not carry (absent or empty) did not contribute: leave them out
        sources = [(f"{s.path}.{k}", k, s.peek(k)) for s, k in parts if s.peek(k) not in (None, "")]
        if not sources:
            return
        where = (anchor or parts[0][0]).path
        self._pending.append(PendingStep(dest, op, sources, where, None, note, True))

    def trace_sources(
        self,
        dest: str,
        op: str,
        sources: list[tuple[str, Any]],
        anchor: "Src | str",
        note: str = "",
    ) -> None:
        """A step whose sources are given as (path, value) pairs, e.g. a run id or a whole list."""
        if not (self.tracing and sources):
            return
        triples = [
            (path, re.sub(r"\[\d+\]", "", path).rsplit(".", 1)[-1], raw) for path, raw in sources
        ]
        where = anchor if isinstance(anchor, str) else anchor.path
        self._pending.append(PendingStep(dest, op, triples, where, None, note, True))

    def trace_assigned(self, dest: str, anchor: "Src", note: str) -> None:
        """A value the normalizer set itself: there is deliberately NO source field to show."""
        if self.tracing:
            self._pending.append(PendingStep(dest, "assigned", [], anchor.path, None, note, True))

    def trace_context(self, dest: str, name: str, value: Any, anchor: "Src") -> None:
        """A value that came from the caller's context (an id the file does not contain)."""
        if self.tracing and value not in (None, ""):
            step = PendingStep(dest, "context", [(f"context.{name}", name, value)], anchor.path)
            step.combined = True
            self._pending.append(step)

    def finish_trace(self, record: Any, roots: tuple[str, ...]) -> None:
        """For records built directly (not via `build`): claim the steps recorded under `roots`."""
        if self.tracing and record is not None:
            self._finish_trace(record, roots)

    def traces_for(self, records: list[Any]) -> list[list[dict]]:
        return [self._built[id(r)][1] if id(r) in self._built else [] for r in records]

    def _finish_trace(self, record: Any, roots: tuple[str, ...]) -> None:
        mine, rest = [], []
        for step in self._pending:
            owned = any(
                step.anchor == r or step.anchor.startswith((r + ".", r + "[")) for r in roots
            )
            (mine if owned else rest).append(step)
        self._pending = rest
        self._built[id(record)] = (record, resolve_steps(mine, record))

    def src(self, data: Any, path: str) -> Src:
        if not isinstance(
            data, dict
        ):  # a provider field of the wrong type: report it, do not crash
            self.error("unexpected_type", f"expected an object at {path}")
            data = {}
        return Src(data, path, self.redacted, self)

    def _add(self, severity: Severity, code: str, message: str, path: str | None = None) -> None:
        self.issues.append(
            Issue(
                severity=severity, code=code, message=message, path=path, source_id=self._source_id
            )
        )

    def error(self, code: str, message: str, path: str | None = None) -> None:
        self._add(Severity.ERROR, code, message, path)

    def warn(self, code: str, message: str, path: str | None = None) -> None:
        self._add(Severity.WARNING, code, message, path)

    def info(self, code: str, message: str, path: str | None = None) -> None:
        self._add(Severity.INFO, code, message, path)

    def focus(self, source_id: str | None) -> None:
        """Tag following issues with the record being processed."""
        self._source_id = source_id

    def unmapped_code(self, field: str, value: str | None, path: str | None = None) -> None:
        """A code that is not documented, so it was kept raw and no canonical value was set."""
        if value is not None:
            self.info("unmapped_code", f"{field} has a code without a documented meaning", path)

    def build(
        self,
        model: type[M],
        src: Src | None,
        endpoint: str,
        derived: list[str] | None = None,
        also_roots: tuple[str, ...] = (),
        **fields: Any,
    ) -> M | None:
        """Validate and build a canonical record.

        Called *after* every field was read from `src`, so whatever is still unread becomes
        `extras`, and the sensitive paths recorded so far become `provenance.redacted_paths`.
        """
        if src is None:  # a record we derived ourselves; there is no single source object
            fields["provenance"] = Provenance(
                source_endpoint=endpoint, derived_fields=derived or []
            )
            fields["extras"] = scrub(fields.get("extras", {}), "$", [])
        else:
            # leftover() is scrubbed; extras the adapter assembled itself get the same policy
            fields["extras"] = scrub(
                {**fields.get("extras", {}), **src.leftover()}, src.path, self.redacted
            )
            fields["provenance"] = provenance(endpoint, src, derived, also_roots)
        try:
            built = model(**fields)
            if self.tracing and src is not None:
                self._finish_trace(built, (src.path, *also_roots))
            return built
        except ValidationError as exc:
            for err in exc.errors():
                loc = ".".join(str(p) for p in err["loc"])
                self.error("canonical_validation", f"{model.__name__}.{loc}: {err['msg']}")
            return None


WITHHELD = "[withheld]"
WITHHELD_DEST_PARTS = ("hash", "token", "password", "ssn_full")


class PendingStep:
    """One recorded read: where it came from and the canonical field it was meant for."""

    def __init__(
        self,
        dest: str,
        op: str,
        sources: list[tuple[str, str, Any]],
        anchor: str,
        table: str | None = None,
        note: str = "",
        combined: bool = False,
    ):
        self.dest, self.op, self.sources = dest, op, sources
        self.anchor, self.table, self.note, self.combined = anchor, table, note, combined

    @property
    def present(self) -> bool:
        return any(v is not None and v != "" for _, _, v in self.sources)


def _dig(node: Any, dest: str) -> Any:
    """Value at a dotted canonical path like `compensation.annual_salary` or `jobs[0].title`."""
    for part in dest.replace("]", "").replace("[", ".").split("."):
        if isinstance(node, list):
            node = node[int(part)] if part.isdigit() and int(part) < len(node) else None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
        if node is None:
            return None
    return node


def _shown(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (dict, list)):
        return None
    return value


def resolve_steps(steps: list[PendingStep], record: BaseModel) -> list[dict]:
    """Keep only the reads that produced the record's final values.

    * a destination that ended up empty (source missing, null, or invalid) has no step;
    * when several reads fed one destination (fallbacks) only the FIRST read that held a value is
      kept, which is the one the adapter's `a or b` actually used;
    * combined steps keep every part, since all of them contributed.
    Raw and canonical values come from the source and the finished model, never from comparing them.
    """
    dumped = record.model_dump(mode="json")
    chosen: dict[str, PendingStep] = {}
    out: list[PendingStep] = []
    for st in steps:
        if st.combined:
            out.append(st)
        elif st.dest not in chosen and st.present:
            chosen[st.dest] = st
            out.append(st)
    result = []
    for st in out:
        canonical = _dig(dumped, st.dest)
        if canonical in (None, "", [], {}):
            continue  # the value did not make it into the record (invalid or unused)
        hide = st.op == "redact" or any(p in st.dest.lower() for p in WITHHELD_DEST_PARTS)
        srcs = []
        for path, key, raw in st.sources:
            sensitive = hide or is_sensitive_name(key)
            srcs.append({"path": path, "raw": WITHHELD if sensitive else _shown(raw)})
        result.append(
            {
                "dest": st.dest,
                "op": st.op,
                "value": WITHHELD
                if any(p in st.dest.lower() for p in WITHHELD_DEST_PARTS)
                else canonical,
                "sources": srcs,
                **({"table": st.table} if st.table else {}),
                **({"note": st.note} if st.note else {}),
            }
        )
    return result


def none_if_empty[T: BaseModel](model: T) -> T | None:
    """A nested model whose every field is null / empty is treated as absent."""
    return model if model.model_dump(exclude_none=True, exclude_defaults=True) else None


def provenance(
    endpoint: str, src: Src, derived: list[str] | None = None, also_roots: tuple[str, ...] = ()
) -> Provenance:
    """Provenance for one record. Call after all fields were read so redactions are captured.

    `also_roots` lets a record assembled from several responses (UKG employee bundle) claim the
    redactions made in its other parts.
    """
    roots = (src.path, *also_roots)
    own = [  # noqa: SLF001
        r for r in src._redacted if any(r == p or r.startswith((p + ".", p + "[")) for p in roots)
    ]
    return Provenance(
        source_endpoint=endpoint,
        source_path=src.path,
        redacted_paths=sorted(set(own)),
        derived_fields=derived or [],
    )


def sid(value: Any) -> str | None:
    """Source identifier -> stripped string (ids arrive as ints in some providers)."""
    if value is None or isinstance(value, bool):
        return None
    s = str(value).strip()
    return s or None


def last4(value: str | None) -> str | None:
    """Last four characters, only when there are more than four (never reveal a whole value)."""
    if not value:
        return None
    chars = "".join(ch for ch in value if ch.isalnum())
    return chars[-4:] if len(chars) > 4 else None


_SSN_SHAPE = re.compile(r"[0-9Xx*#\u2022]{5}[0-9]{4}")  # 9 digits or a masked form (XXX-XX-1234)


def ssn_last4(value: str | None) -> str | None:
    """Last four digits of an SSN, only for a valid digit-based (or digit-masked) SSN; else None."""
    if not value:
        return None
    compact = re.sub(r"[\s-]", "", value)
    return compact[-4:] if _SSN_SHAPE.fullmatch(compact) else None


def mask_account(value: str | None) -> str | None:
    if not value:
        return None
    tail = last4(value)
    return f"****{tail}" if tail else "****"


def account_hash_salt() -> str:
    """The configured salt, or ConfigurationError. Only APP_ENV=test accepts a short fixed salt."""
    settings = get_settings()
    salt = settings.account_hash_salt
    problem = (None if salt else "is not set") if settings.app_env == "test" else salt_problem(salt)
    if problem or salt is None:
        raise ConfigurationError(f"ACCOUNT_HASH_SALT {problem}. {SETUP_HINT}")
    return salt


def hash_account(tenant_id: str, routing: str | None, account: str | None) -> str | None:
    """Salted hash so identical accounts can be recognised without storing the number."""
    if not account:
        return None
    salt = account_hash_salt()
    raw = f"{salt}|{tenant_id}|{routing or ''}|{account}".encode()
    return hashlib.sha256(raw).hexdigest()


def as_list(payload: Any, *keys: str, prefix: str = "$") -> Iterator[tuple[str, Any]]:
    """Yield (path, object) for a list, a wrapper dict holding a list, or a single object."""
    if isinstance(payload, list):
        for i, item in enumerate(payload):
            yield f"{prefix}[{i}]", item
        return
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                value = payload[key]
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        yield f"{prefix}.{key}[{i}]", item
                    return
                if isinstance(value, dict):
                    yield f"{prefix}.{key}", value
                    return
        yield prefix, payload
        return
    raise NormalizationError("invalid_payload", "payload must be a JSON object or array")
