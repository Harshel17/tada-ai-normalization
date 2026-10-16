"""Employee mapping coverage, measured on payloads generated from the provider contracts.

    uv run python scripts/coverage_report.py [--seed 20260101] [--no-probe]

Every documented employee field is filled with a unique value (tests/support/schema_gen.py) and
normalized. Each leaf lands in one of three places:

  canonical  a dedicated canonical field
  withheld   deliberately redacted (SSN, account numbers, document contents ...)
  extras     preserved verbatim in `extras`

"audit formula" is the formula used in docs/AUDIT.md (1 - extras - withheld over ALL leaves of the
payload). "strict" excludes subtrees that belong to other entity types (direct-deposit rows,
scheduled deductions) so they are not counted as employee coverage.

The generator emits placeholder strings (`birthDate-12`) for fields the contract types as plain
strings, and the adapters rightly refuse to parse those, so they land in extras. The PROBE step
re-runs each extras leaf with a typed value (date / decimal / Y-N flag / integer); if that makes the
field leave extras it is counted as canonical ("mapped, needs a valid value").
"""

import argparse
import copy
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from support.schema_gen import cases  # noqa: E402

from normalization import service  # noqa: E402
from normalization.sensitive import is_sensitive_name  # noqa: E402

CTX = {"prismhr": "ACME", "isolved": "4501", "ukg-ready": "CID1"}
CASES = {"PrismHR": "prismhr_employee", "isolved": "isolved_employee", "UKG Ready": "ukg_employee"}
# subtrees of the employee payload that belong to other entity types
OWNED_ELSEWHERE = {
    "prismhr": ("employee.directDeposit.achVoucher", "employee.scheduledDeduction"),
    "isolved": ("results.directDeposits",),
    "ukg-ready": (),
}
ENVELOPE = {"errorCode", "errorMessage", "infoMessage", "updateMessage", "nextPageUrl",
            "previousPageUrl", "totalItems", "totalPages", "extension", "any", "total"}  # fmt: skip
PROBES = ["2024-02-03", "12.5", "Y", "7"]

# Why a field is still in extras: first matching rule wins (matched on the field's path).
CATEGORIES = [
    ("protected demographics (ethnicity, disability, veteran, citizenship, tobacco)",
     r"ethnic|hispanic|veteran|vet\b|handicap|blind|disab|citizenship|smoker|tobacco|visa|"
     r"eeoGender|genderDesignation|selfId|wotc|eeo4?_|nationality|referral|recruiter|eye_color|height"),
    ("documents, images and identity papers",
     r"i9|attachments|document|profilePicture|photo|eeImage|driverLicense|passport|alien|"
     r"citizenshipStatus|messages"),
    ("health, skills, vehicles, company property",
     r"^health|skillsAndEducation|autoPolicies|vehicle|property|osha|allergy"),
    ("provider workflow, UI and sync metadata",
     r"checksum|Checksum|onboard|handbook|newHire|enrollment|Id$|_id$|^id$|\.id$|index|locale|"
     r"first_screen|locked|password|hardware|managed_cost|account_extra|time_entry_extra|custom_dates|"
     r"timezone|isPending|isPhoto|links|labors|organizations|displayOrder|uniqueId|legalCode|"
     r"legacy|userId|absenceJournal|employeeEvents|backgroundTest|electronic|primary_account|"
     r"force_change|add_to_new_hire|ein\.|ttoc|gl_codes|limits|time_period|^time$|^team|punch|Punch"),
    ("tax details not modelled (geo codes, W-5, IRS lock-ins, local certificates)",
     r"tax|Tax|w5|W5|irs|eic|geo|Geo|lock|deliverCheck|exempt|Exempt|reciprocity"),
    ("HR review, probation and tracking dates", r"review|Review|probation|seniority|compa|quartile|"
     r"lastPayDate|lastStatus|newHireReport|activeDuty|benefits$|frozen|retired|deceased|status"),
    ("benefit detail not modelled", r"benefit|Benefit|cafeteria|hsa|retirement|pension|profit|healthIns"),
]  # fmt: skip


def leaves(node, path=()):
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in ("links", "_links"):
                yield from leaves(v, (*path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from leaves(v, (*path, i))
    elif node not in (None, ""):
        yield path, node


def dotted(path):
    return ".".join(str(k) for k in path if not isinstance(k, int))


def extras_paths(records, provider):
    out = set()
    for rec in records:
        for p, _ in leaves(rec.extras):
            out.add(dotted(p))
    return out


def normalise_payload_path(provider, path):
    d = dotted(path)
    if provider == "ukg-ready":
        d = re.sub(r"^employee\.", "", d)
        d = d.replace("compensation_history.compensations.", "compensation_history.")
    else:  # strip the response wrapper: employee[] / results[]
        d = d.split(".", 1)[1] if "." in d else d
    return d


def set_at(doc, path, value):
    node = doc
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value


def run(provider, entity_payload, name):
    _, entity, extra, _ = cases(0)[name]
    ctx = {"tenant_id": "t", "company_id": CTX[provider], **extra}
    return service.normalize(provider, entity, entity_payload, ctx)


def classify(path):
    for label, pattern in CATEGORIES:
        if re.search(pattern, path):
            return label
    return "other provider-specific fields"


def measure(label, name, seed, probe):
    provider, _, _, build = cases(seed)[name]
    payload = build()
    result = run(provider, payload, name)
    in_extras = extras_paths(result.records, provider)
    withheld = {p for rec in result.records for p in rec.provenance.redacted_paths}
    elsewhere = OWNED_ELSEWHERE[provider]

    total = strict_total = 0
    buckets: Counter = Counter()
    remaining: Counter = Counter()
    listing: dict = {}
    for path, _value in leaves(payload):
        d = dotted(path)
        if any(k in ENVELOPE for k in path if isinstance(k, str)):
            continue
        total += 1
        if any(d == e or d.startswith(e + ".") for e in elsewhere):
            continue
        strict_total += 1
        norm = normalise_payload_path(provider, path)
        key = path[-1] if isinstance(path[-1], str) else ""
        is_secret = is_sensitive_name(key) or any(w.endswith("." + str(key)) for w in withheld)
        if is_secret:  # redacted in place ("[redacted]" in extras) or dropped: either way withheld
            buckets["withheld"] += 1
        elif norm not in in_extras:
            buckets["canonical"] += 1
        elif probe and probes_to_canonical(provider, payload, path, norm, name):
            buckets["canonical"] += 1
        else:
            buckets["extras"] += 1
            remaining[classify(norm)] += 1
            listing.setdefault(classify(norm), []).append(norm)

    extras_all = sum(1 for p, _ in leaves(payload) if normalise_payload_path(provider, p) in in_extras
                     and not any(dotted(p) == e or dotted(p).startswith(e + ".") for e in elsewhere))  # fmt: skip
    audit = 100 * (total - len(in_extras_leaves(result.records)) - len(withheld)) // total
    return {
        "label": label, "total": total, "strict_total": strict_total, "buckets": buckets,
        "remaining": remaining, "listing": listing, "audit_pct": audit, "extras_leaves": extras_all,
    }  # fmt: skip


def in_extras_leaves(records):
    return [p for rec in records for p in leaves(rec.extras)]


def probes_to_canonical(provider, payload, path, norm, name):
    """Does a typed value move this field out of extras? (leaves with a documented parse only)"""
    for candidate in PROBES:
        doc = copy.deepcopy(payload)
        set_at(doc, path, candidate)
        try:
            res = run(provider, doc, name)
        except Exception:  # noqa: BLE001
            continue
        if norm not in extras_paths(res.records, provider):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--no-probe", action="store_true")
    parser.add_argument("--list", help="also list the paths in this reason (substring match)")
    args = parser.parse_args()
    rows = [measure(label, name, args.seed, not args.no_probe) for label, name in CASES.items()]
    print(f"EMPLOYEE COVERAGE (contract-generated payloads, seed {args.seed})\n")
    print(f"{'provider':10} {'leaves':>7} {'audit-formula %':>16} | strict: {'canonical':>9} {'withheld':>9} {'extras':>7} {'canonical %':>12}")  # fmt: skip
    for r in rows:
        b, t = r["buckets"], r["strict_total"]
        print(f"{r['label']:10} {r['total']:>7} {r['audit_pct']:>15}% | {'':7} {b['canonical']:>9} {b['withheld']:>9} {b['extras']:>7} {100 * b['canonical'] // t:>11}%")  # fmt: skip
    print("\nWHAT REMAINS IN extras, by reason")
    for r in rows:
        print(f"\n  {r['label']}")
        for reason, n in r["remaining"].most_common():
            print(f"    {n:>4}  {reason}")
            if args.list and args.list in reason:
                print("          " + ", ".join(sorted(set(r["listing"][reason]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
