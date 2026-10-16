"""Read-only probe of the Tada demo providers (development tool, not part of the submission tests).

    uv run python scripts/live_probe.py prism

Safety rules (enforced in code and covered by tests/test_live_probe.py):
  * the API key comes from DEMO_SIGNIN_API_KEY or the gitignored .env and is never printed;
  * the only POST is the documented /signin; every provider call is a documented GET;
  * the returned base_url must be https on an allowed host, redirects are refused, and every
    request URL is re-checked against the same host;
  * responses are processed in memory and normalized without touching PostgreSQL;
  * the report contains structure only: key names, types, counts, issue codes. Never values.
"""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

SIGNIN_URL = "https://tada-demo-provider-signin.onrender.com/signin"
SIGNIN_HOST = "tada-demo-provider-signin.onrender.com"
TENANT = {"prismhr": "prism", "isolved": "isol", "ukg-ready": "ukg"}

# Hosts that appear in the assignment materials. An empty tuple means "nothing documented":
# the base_url host must then be allowed explicitly with --allow-host after you have looked at it.
DOCUMENTED_HOSTS: dict[str, tuple[str, ...]] = {
    "prismhr": ("api.prismhr.com", SIGNIN_HOST),  # OpenAPI servers[0]; the sign-in host itself
    "isolved": ("myisolved.com", SIGNIN_HOST),  # Swagger host (subdomains also accepted)
    # UKG Ready: the cached reference pages use https://secure2.saashr.com/ta/rest/v2/... in their examples
    "ukg-ready": ("secure2.saashr.com", SIGNIN_HOST),
}
# documented per-endpoint headers that may be added to a GET (never auth headers)
EXTRA_HEADERS = {"X-End-of-Support-Timeline"}
TIMEOUT = 90  # the hosted service can be slow on its first request after inactivity
MAX_BYTES = 20 * 1024 * 1024


class ProbeError(Exception):
    """A failure whose message is safe to print (it never contains credentials or payloads)."""


# --- secrets ------------------------------------------------------------------------------------
def load_key(env: dict[str, str] | None = None, dotenv: Path = Path(".env")) -> str:
    env = os.environ if env is None else env
    key = env.get("DEMO_SIGNIN_API_KEY", "").strip()
    if not key and dotenv.is_file():
        for line in dotenv.read_text().splitlines():
            name, sep, value = line.strip().partition("=")
            if sep and name.strip() == "DEMO_SIGNIN_API_KEY":
                key = value.strip().strip("'\"")
    if not key:
        raise ProbeError(
            "DEMO_SIGNIN_API_KEY is not set. Export it or add it to the gitignored .env file."
        )
    return key


# --- network guards -----------------------------------------------------------------------------
def check_base_url(url: Any, provider: str, extra_hosts: tuple[str, ...] = ()) -> str:
    """Return the validated base URL (no trailing slash) or raise ProbeError."""
    if not isinstance(url, str) or not url:
        raise ProbeError("sign-in response has no usable base_url")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ProbeError("base_url is not https; refusing")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ProbeError("base_url carries credentials, a query or a fragment; refusing")
    if parts.port not in (None, 443):
        raise ProbeError("base_url uses a non-standard port; refusing")
    host = (parts.hostname or "").lower()
    allowed = (*DOCUMENTED_HOSTS[provider], *(h.lower() for h in extra_hosts))
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise ProbeError(
            f"base_url host {host!r} is not a documented demo host for {provider}. "
            "Check it, then re-run with --allow-host <host> if it is the expected one."
        )
    return url.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        return None


_OPENER = urllib.request.build_opener(
    _NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context())
)


def _send(req: urllib.request.Request) -> tuple[int, bytes]:
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ProbeError(f"HTTP {exc.code} redirect refused") from None
        return exc.code, exc.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProbeError(f"network error: {type(exc).__name__}") from None


def signin(provider: str, key: str, send=_send) -> dict:
    body = json.dumps({"tenant": TENANT[provider]}).encode()
    req = urllib.request.Request(
        SIGNIN_URL, data=body, method="POST",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
    )  # fmt: skip
    status, raw = send(req)
    if status != 200:
        raise ProbeError(f"sign-in failed with HTTP {status}")
    try:
        return json.loads(raw, parse_float=Decimal)
    except ValueError:
        raise ProbeError("sign-in response is not JSON") from None


class Session:
    """GET-only client bound to one validated host."""

    def __init__(self, base_url: str, header_name: str, header_value: str, send=_send):
        self.base_url, self._h, self._v, self._send = base_url, header_name, header_value, send
        self.host = urllib.parse.urlsplit(base_url).hostname

    def get(
        self, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, Any]:
        if not path.startswith("/") or any(
            bad in path for bad in ("@", "\\", "..", "//", "?", "#")
        ):
            raise ProbeError("unsafe request path; refusing")
        url = (
            self.base_url
            + path
            + (("?" + urllib.parse.urlencode(params, doseq=True)) if params else "")
        )
        if urllib.parse.urlsplit(url).hostname != self.host:
            raise ProbeError("request host changed; refusing")
        extra = {k: v for k, v in (headers or {}).items() if k in EXTRA_HEADERS}
        if len(extra) != len(headers or {}):
            raise ProbeError("header not on the documented allowlist; refusing")
        req = urllib.request.Request(
            url, method="GET", headers={**extra, self._h: self._v, "Accept": "application/json"}
        )
        status, raw = self._send(req)
        if len(raw) > MAX_BYTES:
            raise ProbeError("response too large")
        try:
            return status, json.loads(raw, parse_float=Decimal) if raw else None
        except ValueError:
            return status, None


# --- structure-only reporting ------------------------------------------------------------------
def shape(value: Any, depth: int = 4) -> Any:
    """Key names and value TYPES only (never values), lists summarised by length and first item."""
    if isinstance(value, dict):
        return {k: shape(v, depth - 1) if depth > 0 else "…" for k, v in value.items()}
    if isinstance(value, list):
        return (
            [f"list[{len(value)}]", shape(value[0], depth - 1)]
            if value and depth > 0
            else f"list[{len(value)}]"
        )
    return type(value).__name__ if value is not None else "null"


def leaf_count(record: dict) -> int:
    """Number of filled scalar fields of a canonical record (structural keys excluded)."""
    skip = {
        "record_type",
        "provider",
        "tenant_id",
        "company_id",
        "source_id",
        "extras",
        "provenance",
    }

    def walk(n: Any) -> int:
        if n in (None, "", [], {}):
            return 0
        if isinstance(n, list):
            return sum(walk(x) for x in n)
        if isinstance(n, dict):
            return sum(walk(v) for v in n.values())
        return 1

    return sum(walk(v) for k, v in record.items() if k not in skip)


SENSITIVE_WORDS = (
    "ssn",
    "social",
    "national",
    "account",
    "routing",
    "bank",
    "password",
    "token",
    "secret",
    "pin",
)


def _value_class(v: Any) -> str:
    """The SHAPE of a sensitive value, e.g. `999-99-9999` or `***-**-9999` (digits become 9, letters a/A,
    punctuation kept), so masked and unmasked values can be told apart without revealing either."""
    if v in (None, ""):
        return "empty"
    out = "".join(
        "9" if c.isdigit() else ("a" if c.islower() else "A") if c.isalpha() else c
        for c in str(v)[:24]
    )
    return out + ("…" if len(str(v)) > 24 else "")


def sensitive_audit(payload: Any, output_text: str) -> dict:
    """Which sensitive-looking keys the live payload has, what KIND of value each holds, and whether any
    raw value reached our normalized output. Key names and classes only; never values."""
    seen: dict[str, dict[str, int]] = {}
    leaked: set[str] = set()

    def walk(n: Any, key: str = "", path: str = "") -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                walk(v, str(k), f"{path}.{k}" if path else str(k))
        elif isinstance(n, list):
            for x in n:
                walk(x, key, path + "[]")
        elif key and any(w in path.lower() for w in SENSITIVE_WORDS) and not isinstance(n, bool):
            cls = _value_class(n)
            seen.setdefault(path, {}).setdefault(cls, 0)
            seen[path][cls] += 1
            if n not in (None, "") and len(str(n)) >= 5 and str(n) in output_text:
                leaked.add(path)

    walk(payload)
    return {"sensitive_keys": seen, "raw_value_in_output": sorted(leaked)}


def summarize(provider: str, entity: str, payload: Any, ctx: dict, label: str) -> dict:
    """Detect and normalize one live payload in memory; report structure and issue codes only."""
    from normalization import service
    from normalization.detect import detect
    from normalization.canonical.enums import Provider

    det = detect(Provider(provider), payload)
    res = service.normalize(
        provider, entity, payload, {"tenant_id": "live-probe", **ctx}, trace=True
    )
    issues: dict[str, int] = {}
    for i in res.issues:
        k = f"{i.severity.value}:{i.code}"
        issues[k] = issues.get(k, 0) + 1
    extras, covered = set(), []
    for rec, steps in zip(res.records, res.traces, strict=False):
        extras |= set(rec.extras)
        dump = rec.model_dump(mode="json")
        covered.append({"filled_fields": leaf_count(dump), "traced_fields": len(steps)})
    return {
        "label": label,
        "detected": [
            {"entity": u.entity.value, "counts": u.counts, "nested_in": u.nested_in}
            for u in det.units
        ]
        or det.status,
        "normalize_ok": res.ok,
        "records": res.counts(),
        "issue_codes": issues,
        "extras_top_level_keys": sorted(extras),
        "trace_coverage": covered[:3],
        "sensitive_audit": sensitive_audit(
            payload,
            json.dumps([r.model_dump(mode="json") for r in res.records], default=str)
            + json.dumps(res.traces, default=str)
            + " ".join(f"{i.code} {i.message} {i.path}" for i in res.issues)
            + repr(res),
        ),
    }


# --- PrismHR ------------------------------------------------------------------------------------
PRISM_OPTIONS = "Person,Client,Compensation,DirectDeposit,ScheduledDeductions,ContactInformation"


def ids_from(payload: Any, limit: int) -> list[str]:
    """Employee ids from a list response, only if they sit under a documented-looking key."""
    found: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            for k, v in n.items():
                if (
                    k in ("employeeId", "id")
                    and isinstance(v, (str, int))
                    and not isinstance(v, bool)
                ):
                    found.append(str(v))
                elif k == "employeeId" and isinstance(
                    v, list
                ):  # {"employeeList": {"employeeId": [...]}}
                    found.extend(
                        str(x) for x in v if isinstance(x, (str, int)) and not isinstance(x, bool)
                    )
                else:
                    walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(payload)
    return found[:limit]


def run_prism(sess: Session, client_id: str) -> list[dict]:
    out: list[dict] = []
    ctx = {"company_id": client_id}

    def step(
        name: str, path: str, params: dict, entities: list[str], extra_ctx: dict | None = None
    ):
        status, body = sess.get(path, params)
        entry: dict = {"call": name, "http": status, "shape": shape(body, 3)}
        if isinstance(body, dict) and body.get(
            "errorCode"
        ):  # provider error text (no payload data)
            entry["provider_error"] = {
                "code": str(body.get("errorCode"))[:40],
                "message": str(body.get("errorMessage"))[:160],
            }
        if status == 200 and isinstance(body, (dict, list)):
            entry["results"] = [
                summarize("prismhr", e, body, {**ctx, **(extra_ctx or {})}, f"{name}→{e}")
                for e in entities
            ]
        out.append(entry)
        return status, body

    step(
        "getClientMaster", "/clientMaster/v1/getClientMaster", {"clientId": client_id}, ["company"]
    )
    _, lst = step("getEmployeeList", "/employee/v1/getEmployeeList", {"clientId": client_id}, [])
    ids = ids_from(lst, 3)
    out[-1]["employee_ids_found"] = len(ids)
    if ids:
        one = {"clientId": client_id, "employeeId": ids[0]}
        step("getEmployee (1 id, no options)", "/employee/v1/getEmployee", one, ["employee"])
        step(
            "getEmployee (1 id, documented option keywords)",
            "/employee/v1/getEmployee",
            {**one, "options": PRISM_OPTIONS},
            ["employee", "direct_deposit", "deduction"],
        )
        if len(ids) > 1:  # the contract types employeeId as an array: repeated query parameter
            step(
                "getEmployee (2 ids, repeated parameter)",
                "/employee/v1/getEmployee",
                {"clientId": client_id, "employeeId": ids[:2]},
                ["employee"],
            )
        step(
            "getDeductions",
            "/deductions/v1/getDeductions",
            {**one},
            ["deduction"],
            {"employee_id": ids[0]},
        )
    end = date.today()
    step("getPayrollVouchers", "/payroll/v1/getPayrollVouchers",
         {"clientId": client_id, "payDateStart": str(end - timedelta(days=730)), "payDateEnd": str(end), "count": "5", "startpage": "0"},
         ["payroll"])  # fmt: skip
    return out


# --- isolved ------------------------------------------------------------------------------------
def result_ids(payload: Any, limit: int) -> list[str]:
    """Ids of the objects in a paged `{"results": [...]}` response (or a bare list)."""
    rows = payload.get("results") if isinstance(payload, dict) else payload
    return [
        str(r["id"])
        for r in (rows or [])
        if isinstance(r, dict) and isinstance(r.get("id"), (str, int))
    ][:limit]


def run_isolved(sess: Session, client_id: str, legal_id: str) -> list[dict]:
    out: list[dict] = []
    base_ctx = {"company_id": legal_id, "parent_company_id": client_id}
    root = f"/api/clients/{client_id}/legals/{legal_id}"

    def step(
        name: str,
        path: str,
        params: dict | None,
        units: list[tuple[str, Any, dict]],
        keep: bool = False,
    ):
        """units: (entity, payload-or-None-for-response, extra context)."""
        status, body = sess.get(path, params)
        entry: dict = {"call": name, "http": status, "shape": shape(body, 3)}
        if status == 200 and isinstance(body, (dict, list)):
            entry["results"] = [
                summarize(
                    "isolved", e, body if pl is None else pl(body), {**base_ctx, **x}, f"{name}→{e}"
                )
                for e, pl, x in units
            ]
        out.append(entry)
        return status, body

    _, legals = step(
        "legals",
        f"/api/clients/{client_id}/legals",
        {"includeDetails": "true"},
        [("company", None, {})],
    )
    flags = {
        k: "true"
        for k in (
            "includeMiscFields",
            "includeLabors",
            "includeOrganizations",
            "includeAlternateRates",
            "includeTaxes",
        )
    }
    _, emps = step(
        "employees (include flags)",
        f"{root}/employees",
        {"page": "0", "pageSize": "5", **flags},
        [("employee", None, {}), ("direct_deposit", None, {})],
    )
    ids = result_ids(emps, 5)
    out[-1]["employee_ids_found"] = len(ids)
    for eid in ids[:3]:
        n = ids.index(eid) + 1
        step(
            f"directdeposits employee#{n}",
            f"{root}/employees/{eid}/directdeposits",
            None,
            [("direct_deposit", None, {"employee_id": eid})],
        )
        step(
            f"deductions employee#{n}",
            f"/api/corestream/clients/{client_id}/legals/{legal_id}/employees/{eid}/deductions",
            None,
            [("deduction", None, {"employee_id": eid})],
        )
        step(
            f"checks employee#{n}",
            f"{root}/employees/{eid}/checks",
            {"page": "0", "includeDetails": "true"},
            [("payroll", None, {})],
        )
    # most demo employees have no checks: scan (read-only) for the first one that does
    for n, eid in enumerate(ids, 1):
        status, body = sess.get(
            f"{root}/employees/{eid}/checks", {"page": "0", "includeDetails": "true"}
        )
        if status == 200 and isinstance(body, dict) and body.get("results"):
            out.append(
                {
                    "call": f"checks (first employee with checks, #{n} of {len(ids)})",
                    "http": status,
                    "shape": shape(body, 2),
                    "results": [summarize("isolved", "payroll", body, base_ctx, "checks→payroll")],
                }
            )
            break
    end = date.today()
    step("legal-level checks", f"{root}/checks",
         {"includeDetails": "true", "filter.fromDate": str(end - timedelta(days=730)), "filter.toDate": str(end), "filter.page": "0", "filter.pageSize": "5"},
         [("payroll", None, {})])  # fmt: skip
    groups = []
    if isinstance(legals, list):
        for lg in legals:
            if isinstance(lg, dict) and str(lg.get("id")) == legal_id:
                groups = [
                    str(g["id"])
                    for g in (lg.get("payGroups") or [])
                    if isinstance(g, dict) and g.get("id") is not None
                ]
    out[0]["pay_groups_found"] = len(groups)
    if groups:
        _, runs = step("payrollRunHistory (pay group #1)", f"{root}/paygroupId/{groups[0]}/payrollRunHistory", None,
                       [("payroll", lambda b: {"payroll_run": b[0]} if isinstance(b, list) and b else {}, {})])  # fmt: skip
    return out


def link_analysis(sess: Session, client_id: str, legal_id: str, employee_ids: list[str]) -> dict:
    """Can a live check be matched to a run-history record from documented identifiers? Counts only.

    A check carries payrollRunNumber / payrollRunType / period and check dates but NOT a run id; run
    history carries id / runNumber / payrollRunType / period dates / payDate. For several match keys this
    reports how many checks have exactly one candidate run, several, or none, and whether runs share a key.
    """
    root = f"/api/clients/{client_id}/legals/{legal_id}"
    _, legals = sess.get(f"/api/clients/{client_id}/legals", {"includeDetails": "true"})
    groups = [
        str(g["id"])
        for lg in (legals if isinstance(legals, list) else [])
        if isinstance(lg, dict) and str(lg.get("id")) == legal_id
        for g in (lg.get("payGroups") or [])
        if isinstance(g, dict) and g.get("id") is not None
    ]
    runs: list[dict] = []
    for g in groups:
        st, body = sess.get(f"{root}/paygroupId/{g}/payrollRunHistory")
        if st == 200 and isinstance(body, list):
            runs += [dict(r, _group=g) for r in body if isinstance(r, dict)]
    checks: list[dict] = []
    for e in employee_ids:
        st, body = sess.get(f"{root}/employees/{e}/checks", {"page": "0", "includeDetails": "true"})
        if st == 200 and isinstance(body, dict):
            checks += [c for c in body.get("results") or [] if isinstance(c, dict)]
    keys = {
        "runNumber": lambda c: (c.get("payrollRunNumber"),),
        "runNumber+type": lambda c: (c.get("payrollRunNumber"), c.get("payrollRunType")),
        "runNumber+type+period": lambda c: (c.get("payrollRunNumber"), c.get("payrollRunType"), c.get("periodBeginDate"), c.get("periodEndDate")),
        "runNumber+type+period+payDate": lambda c: (c.get("payrollRunNumber"), c.get("payrollRunType"), c.get("periodBeginDate"), c.get("periodEndDate"), c.get("checkDate")),
    }  # fmt: skip
    run_keys = {
        "runNumber": lambda r: (r.get("runNumber"),),
        "runNumber+type": lambda r: (r.get("runNumber"), r.get("payrollRunType")),
        "runNumber+type+period": lambda r: (r.get("runNumber"), r.get("payrollRunType"), r.get("periodBeginDate"), r.get("periodEndDate")),
        "runNumber+type+period+payDate": lambda r: (r.get("runNumber"), r.get("payrollRunType"), r.get("periodBeginDate"), r.get("periodEndDate"), r.get("payDate")),
    }  # fmt: skip
    report: dict = {
        "pay_groups": len(groups),
        "runs": len(runs),
        "checks": len(checks),
        "check_payrollRunType_values": sorted({str(c.get("payrollRunType")) for c in checks}),
        "run_payrollRunType_values": sorted({str(r.get("payrollRunType")) for r in runs}),
        "check_fields_that_look_like_a_run_id": sorted(k for k in (checks[0] if checks else {}) if "run" in k.lower()),
        "by_key": {},
    }  # fmt: skip
    for name in keys:
        index: dict[Any, list[dict]] = {}
        for r in runs:
            index.setdefault(run_keys[name](r), []).append(r)
        unique = several = none = 0
        for c in checks:
            n = len(index.get(keys[name](c), []))
            unique += n == 1
            several += n > 1
            none += n == 0
        report["by_key"][name] = {
            "checks_with_exactly_one_run": unique,
            "checks_with_several_runs": several,
            "checks_with_no_run": none,
            "runs_sharing_a_key_with_another_run": sum(
                len(v) for v in index.values() if len(v) > 1
            ),
        }
    return report


def run_ukg(sess: Session, cid: str) -> list[dict]:
    """UKG Ready: documented GET endpoints; the separate employee sections are assembled in memory into the
    bundle shape our adapter expects ({employee, demographics, pay_info, compensation_history})."""
    out: list[dict] = []
    ctx = {"company_id": cid}
    root = f"/rest/v2/companies/{cid}"
    dd_headers = {
        "X-End-of-Support-Timeline": "2026-01-29"
    }  # required by the documented direct-deposits call

    def call(name: str, path: str, params: dict | None = None, headers: dict | None = None):
        status, body = sess.get(path, params, headers)
        entry: dict = {"call": name, "http": status, "shape": shape(body, 3)}
        if isinstance(body, dict) and isinstance(body.get("errors"), list) and body["errors"]:
            first = body["errors"][0] if isinstance(body["errors"][0], dict) else {}
            entry["provider_error"] = {
                "code": first.get("code"),
                "message": str(first.get("message"))[:160],
            }
        out.append(entry)
        return entry, status, body

    def normalize_into(entry: dict, units: list[tuple[str, Any, dict]]) -> None:
        entry["results"] = [
            summarize("ukg-ready", e, payload, {**ctx, **x}, f"{entry['call']}→{e}")
            for e, payload, x in units
        ]

    e, st, body = call("lookup/eins", f"{root}/lookup/eins")
    if st == 200 and isinstance(body, dict):
        normalize_into(e, [("company", body, {})])
    e, st, emps = call("employees (list)", f"{root}/employees")
    rows = emps.get("employees") if isinstance(emps, dict) else None
    ids = [str(r["id"]) for r in rows or [] if isinstance(r, dict) and r.get("id") is not None]
    e["employees_found"] = len(ids)
    if st == 200 and isinstance(emps, dict) and rows:
        normalize_into(e, [("employee", emps, {})])
    for n, aid in enumerate(ids[:3], 1):
        ep = f"{root}/employees/{aid}"
        pe, ps, profile = call(f"employee#{n} profile", ep)
        _, ds, demo = call(f"employee#{n} demographics", f"{ep}/demographics")
        _, pis, payinfo = call(f"employee#{n} pay-info", f"{ep}/pay-info")
        _, cs, comp = call(f"employee#{n} compensation/history", f"{ep}/compensation/history")
        if ps == 200 and isinstance(profile, dict):
            normalize_into(pe, [("employee", profile, {})])  # the profile alone
            bundle = {"employee": profile}
            for key, st2, part in (
                ("demographics", ds, demo),
                ("pay_info", pis, payinfo),
                ("compensation_history", cs, comp),
            ):
                if st2 == 200 and isinstance(part, dict):
                    bundle[key] = part
            be = {
                "call": f"employee#{n} ASSEMBLED bundle (parts: {sorted(k for k in bundle if k != 'employee')})",
                "http": 200,
                "shape": shape(bundle, 2),
            }
            normalize_into(be, [("employee", bundle, {})])
            out.append(be)
        de, dst, deps = call(
            f"employee#{n} direct-deposits", f"{ep}/direct-deposits", None, dd_headers
        )
        if dst == 200 and isinstance(deps, (dict, list)):
            normalize_into(de, [("direct_deposit", deps, {"employee_id": aid})])
        ee, edt, deds = call(f"employee#{n} deductions", f"{ep}/deductions")
        if edt == 200 and isinstance(deds, (dict, list)):
            normalize_into(ee, [("deduction", deds, {"employee_id": aid})])
    # scan (read-only) for the first employees that actually have direct deposits
    found = []
    scanned = 0
    for aid in ids[:40]:
        scanned += 1
        st, body = sess.get(f"{root}/employees/{aid}/direct-deposits", None, dd_headers)
        if st == 200 and isinstance(body, dict) and body.get("deposits"):
            found.append((aid, body))
            if len(found) == 2:
                break
    scan = {
        "call": f"direct-deposits scan ({scanned} employees looked at)",
        "http": 200,
        "employees_with_deposits_found": len(found),
        "results": [],
    }
    for aid, body in found:
        scan["results"].append(
            summarize(
                "ukg-ready",
                "direct_deposit",
                body,
                {**ctx, "employee_id": aid},
                "direct-deposits (non-empty)→direct_deposit",
            )
        )
    out.append(scan)
    pe, pst, pays = call("payrolls (default range)", f"{root}/payrolls")
    plist = pays.get("payrolls") if isinstance(pays, dict) else None
    if not plist:  # documented parameters pay_date_from / pay_date_to: widen the window
        today = date.today()
        pe, pst, pays = call(
            "payrolls (pay_date_from/to, 3 years)",
            f"{root}/payrolls",
            {"pay_date_from": str(today - timedelta(days=1095)), "pay_date_to": str(today)},
        )
        plist = pays.get("payrolls") if isinstance(pays, dict) else None
    pe["payrolls_found"] = len(plist or [])
    if pst == 200 and isinstance(pays, dict) and plist:
        normalize_into(pe, [("payroll", pays, {})])
        # scan the payrolls (read-only) for the first one that has pay statements
        chosen = None
        looked = 0
        for p in plist[:60]:
            if not isinstance(p, dict) or p.get("id") is None:
                continue
            looked += 1
            st, body = sess.get(f"{root}/payrolls/{p['id']}/pay-statements")
            stmts = body.get("pay_statements") if isinstance(body, dict) else None
            if st == 200 and stmts:
                chosen = (str(p["id"]), stmts)
                break
        sc = {
            "call": f"pay-statements scan ({looked} payrolls looked at)",
            "http": 200,
            "payroll_with_statements_found": chosen is not None,
        }
        out.append(sc)
        if chosen:
            pid, stmts = chosen
            sc["statements_in_payroll"] = len(stmts)
            _, rs, run = call("payroll (single, one with statements)", f"{root}/payrolls/{pid}")
            full = []
            for stmt in stmts[:3]:
                if isinstance(stmt, dict) and stmt.get("id") is not None:
                    fe, fs, one = call(
                        f"pay-statement {len(full) + 1} (single)",
                        f"{root}/payrolls/{pid}/pay-statements/{stmt['id']}",
                    )
                    if fs == 200 and isinstance(one, dict):
                        full.append(one)
                        normalize_into(fe, [("payroll", one, {"payroll_id": pid})])
            if rs == 200 and isinstance(run, dict) and full:
                ae = {
                    "call": "payroll + full pay statements ASSEMBLED ({payroll, pay_statements})",
                    "http": 200,
                    "shape": shape({"payroll": run, "pay_statements": full}, 2),
                }
                normalize_into(ae, [("payroll", {"payroll": run, "pay_statements": full}, {})])
                out.append(ae)
    return out


CLI_PROVIDER = {"prism": "prismhr", "isolved": "isolved", "ukg": "ukg-ready"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "provider",
        choices=list(CLI_PROVIDER),
        help="provider to probe",
    )
    ap.add_argument(
        "--allow-host", action="append", default=[], help="extra base_url host you have verified"
    )
    ap.add_argument(
        "--link-analysis",
        action="store_true",
        help="isolved: test whether checks can be matched to runs",
    )
    args = ap.parse_args(argv)
    provider = CLI_PROVIDER[args.provider]
    try:
        key = load_key()
        resp = signin(provider, key)
        auth, usage = resp.get("auth") or {}, resp.get("usage") or {}
        base = check_base_url(resp.get("base_url"), provider, tuple(args.allow_host))
        secret = auth.get("sessionId" if provider == "prismhr" else "access_token")
        if not isinstance(secret, str) or not secret:
            raise ProbeError("sign-in response has no usable credential")
        header = usage.get("header_name") or (
            "sessionId" if provider == "prismhr" else "Authorization"
        )
        value = f"{usage.get('header_prefix') or ''}{secret}"
        companies = [
            c for c in (resp.get("context") or {}).get("companies") or [] if isinstance(c, dict)
        ]
        report = {
            "signin_shape": shape({k: v for k, v in resp.items() if k != "auth"}, 3)
            | {"auth": sorted(auth)},
            "usage": {k: usage.get(k) for k in ("header_name", "header_prefix") if k in usage},
            "base_url_host": urllib.parse.urlsplit(base).hostname,
            "base_url_path": urllib.parse.urlsplit(base).path or "/",
            "companies": len(companies),
        }
        sess = Session(base, header, value)
        if provider == "prismhr":
            client_id = str(companies[0].get("client_id")) if companies else ""
            if not client_id:
                raise ProbeError("sign-in returned no context.companies[].client_id")
            report["calls"] = run_prism(sess, client_id)
        elif provider == "ukg-ready":
            cid = str(companies[0].get("company_id") or "") if companies else ""
            if not cid:
                raise ProbeError("sign-in returned no context.companies[].company_id")
            report["calls"] = run_ukg(sess, cid)
        else:
            probed = []
            for co in companies[:2]:
                client_id, legal_id = str(co.get("client_id") or ""), str(co.get("legal_id") or "")
                if client_id and legal_id:
                    entry: dict = {"company_index": len(probed) + 1}
                    if args.link_analysis:
                        _, emps = sess.get(
                            f"/api/clients/{client_id}/legals/{legal_id}/employees",
                            {"page": "0", "pageSize": "50"},
                        )
                        entry["link_analysis"] = link_analysis(
                            sess, client_id, legal_id, result_ids(emps, 50)
                        )
                    else:
                        entry["calls"] = run_isolved(sess, client_id, legal_id)
                    probed.append(entry)
            if not probed:
                raise ProbeError("sign-in returned no client_id / legal_id pair")
            report["company_runs"] = probed
    except ProbeError as exc:
        print(f"probe stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
