"""Provenance for company, direct deposit, deduction, payroll run and paycheck headers (Phase A1).

Three kinds of proof, so a trace can never merely look plausible:
  * exact paths for representative fields of every provider and record type;
  * independent attribution: every traced source path is resolved IN THE PAYLOAD, and copy / number / date /
    boolean steps are re-derived from that raw value and compared with the record, so a wrong path or a
    wrong list index fails; no step may name a path that does not exist;
  * privacy: no secret, no account hash and no unmasked source value in any trace.
Paycheck line items (earnings, taxes, ...) are Phase A2 and deliberately NOT traced yet.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from normalization import service
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files
from support.scenarios import CASES

FIX = Path(__file__).parent / "fixtures"
LIVE = FIX / "live_shapes"
STRUCTURAL = {
    "record_type",
    "provider",
    "tenant_id",
    "company_id",
    "source_id",
    "extras",
    "provenance",
}
LINE_ITEMS = ("earnings", "taxes", "deductions", "benefits", "payments")  # Phase A2
SECRETS = (
    "123-45-6789", "987-65-4321", "900-44-3333", "912-34-6789", "000123456789", "998877665544",
    "5550001111", "4400112233", "000999888777", "7766554433", "99112233", "55667788", "3344556677",
)  # fmt: skip


def load(path: Path):
    return json.loads(path.read_text(), parse_float=Decimal)


def run(case_or_args, trace=True):
    if hasattr(case_or_args, "provider"):
        c = case_or_args
        payload = load(FIX / c.folder / c.file)
        return payload, service.normalize(
            c.provider, c.entity, payload, {"tenant_id": "t", **c.context}, trace=trace
        )
    provider, entity, payload, ctx = case_or_args
    return payload, service.normalize(
        provider, entity, payload, {"tenant_id": "t", **ctx}, trace=trace
    )


def by_dest(result, i):
    return {t["dest"]: t for t in result.traces[i]}


def paths(step):
    return [s["path"] for s in step["sources"]]


# --- resolving a source path inside the payload -------------------------------------------------------------
TOKEN = re.compile(r"\.([^.\[\]]+)|\[(\d+)\]")


def resolve(payload, path):
    """Value at a source path like `$.employee[0].client.jobCode` (raises KeyError if it does not exist)."""
    assert path.startswith("$"), path
    node = payload
    for key, index in TOKEN.findall(path[1:]):
        node = node[int(index)] if index != "" else node[key]
    return node


def exists(payload, path):
    try:
        resolve(payload, path)
        return True
    except (KeyError, IndexError, TypeError):
        return False


def dig(dump, dest):
    node = dump
    for part in dest.replace("]", "").replace("[", ".").split("."):
        node = (
            node[int(part)]
            if isinstance(node, list)
            else node.get(part)
            if isinstance(node, dict)
            else None
        )
        if node is None:
            return None
    return node


BOOLS = {
    "true": True,
    "t": True,
    "y": True,
    "yes": True,
    "false": False,
    "f": False,
    "n": False,
    "no": False,
}


def expected_from_raw(op, raw):
    """What the canonical value must be for a simple operation, computed independently of the adapters."""
    if op == "copy":
        return str(raw).strip()
    if op in ("decimal", "route"):
        return Decimal(str(raw).strip())
    if op == "integer":
        return str(int(Decimal(str(raw))))
    if op == "boolean":
        return raw if isinstance(raw, bool) else BOOLS[str(raw).strip().lower()]
    if op == "date":
        return str(raw).strip()[:10]
    raise AssertionError(op)


def canonical_matches(op, expected, actual):
    if op in ("decimal", "route"):
        return Decimal(str(actual)) == expected
    if op == "integer":
        return str(actual) == expected
    return actual == expected


# =================================================================================================
# 1. Exact paths
# =================================================================================================
def case(case_id):
    return next(c for c in CASES if c.id == case_id)


def test_prismhr_company_exact_paths_zip_plus4_and_status_enum():
    payload, res = run(case("prismhr-company"))
    t = by_dest(res, 0)
    assert paths(t["legal_name"]) == ["$.clientMaster.legalName"] and paths(t["display_name"]) == [
        "$.clientMaster.clientName"
    ]
    assert paths(t["federal_ein"]) == ["$.clientMaster.federalIdNumber"]
    assert paths(t["address.line1"]) == ["$.clientMaster.addressLine1"] and paths(
        t["address.state"]
    ) == ["$.clientMaster.stateCode"]
    zip_step = t["address.postal_code"]
    assert zip_step["op"] == "combine" and paths(zip_step) == [
        "$.clientMaster.zipCode",
        "$.clientMaster.zipSuffix",
    ]
    assert (t["status"]["op"], t["status"]["table"], paths(t["status"])) == (
        "enum",
        "COMPANY_STATUS",
        ["$.clientMaster.statusClass"],
    )
    assert paths(t["status_raw"]) == ["$.clientMaster.status"] and paths(t["source_id"]) == [
        "$.clientMaster.id"
    ]


def test_prismhr_direct_deposit_exact_paths_routing_masking_and_decisions():
    payload, res = run(case("prismhr-direct-deposit"))
    base = "$.employee[0].directDeposit.achVoucher[0]"
    t = by_dest(res, 0)
    assert paths(t["routing_number"]) == [f"{base}.transitNum"]
    masked = t["account_number_masked"]
    assert masked["op"] == "redact" and paths(masked) == [f"{base}.accountNum"]
    assert [s["raw"] for s in masked["sources"]] == ["[withheld]"] and masked["value"].startswith(
        "****"
    )
    assert (t["allocation_type"]["op"], paths(t["allocation_type"])) == ("enum", [f"{base}.method"])
    assert (
        t["amount"]["op"] == "route"
        and paths(t["amount"]) == [f"{base}.amount"]
        and "percentage" not in t
    )
    assert paths(t["employee_id"]) == ["$.employee[0].id"]  # the parent object, not the voucher
    assert paths(t["voucher_types_raw"]) == [
        "$.employee[0].directDeposit.voucherType"
    ]  # the employee-level default
    assert t["source_position"]["op"] == "derive" and paths(t["source_position"]) == [base]
    sid = t["source_id"]
    assert sid["op"] == "derive" and f"{base}.accountNum" in paths(sid)
    assert [s["raw"] for s in sid["sources"] if s["path"].endswith("accountNum")] == ["[withheld]"]
    second = by_dest(res, 1)
    assert (
        "percentage" in second and "amount" not in second and second["percentage"]["op"] == "route"
    )


def test_prismhr_recurring_deduction_routes_amount_by_usepercent_and_marks_what_is_not_from_the_file():
    payload, res = run(case("prismhr-deduction-recurring"))
    first, second = by_dest(res, 0), by_dest(res, 1)
    pct = first["percentage"]
    assert pct["op"] == "route" and paths(pct) == [
        "$.employeeDeductions.deduction[0].amount",
        "$.employeeDeductions.deduction[0].usePercent",
    ]
    assert "usePercent is true" in pct["note"] and "amount" not in first
    assert (
        "amount" in second
        and "usePercent is not true" in second["amount"]["note"]
        and "percentage" not in second
    )
    assert (
        first["setup_kind"]["op"] == "assigned" and first["setup_kind"]["sources"] == []
    )  # no invented source
    ctx = first["employee_id"]
    assert (
        ctx["op"] == "context"
        and paths(ctx) == ["context.employee_id"]
        and ctx["sources"][0]["raw"] == "E1001"
    )
    assert paths(first["deduction_code"]) == ["$.employeeDeductions.deduction[0].deductionCode"]


def test_prismhr_scheduled_deduction_status_enum_and_employee_parent():
    payload, res = run(case("prismhr-deduction"))
    t = by_dest(res, 0)
    assert (t["status"]["op"], t["status"]["table"]) == ("enum", "SCHEDULED_DEDUCTION_STATUS")
    assert (
        paths(t["status"])
        == paths(t["status_raw"])
        == ["$.employee[0].scheduledDeduction[0].status"]
    )
    assert paths(t["employee_id"]) == ["$.employee[0].id"] and t["setup_kind"]["op"] == "assigned"


def test_prismhr_paycheck_header_and_derived_run_use_real_voucher_paths_only():
    payload, res = run(case("prismhr-payroll"))
    assert [r.record_type for r in res.records] == ["payroll_run", "paycheck", "paycheck"]
    run_t, check_t = by_dest(res, 0), by_dest(res, 1)
    for dest, key in (
        ("pay_date", "payDate"),
        ("period_start", "payPeriodStart"),
        ("period_end", "payPeriodEnd"),
    ):
        step = run_t[dest]
        assert step["op"] == "derive" and paths(step) == [
            f"$.payrollVouchers[0].{key}",
            f"$.payrollVouchers[1].{key}",
        ]
        assert "derived, not copied" in step["note"]
    assert paths(run_t["source_id"]) == [
        "$.payrollVouchers[0].batchId",
        "$.payrollVouchers[1].batchId",
    ]
    assert paths(check_t["gross_pay"]) == ["$.payrollVouchers[0].totalEarnings"] and paths(
        check_t["run_id"]
    ) == ["$.payrollVouchers[0].batchId"]
    assert paths(check_t["employee_id"]) == ["$.payrollVouchers[0].employeeId"]
    assert (check_t["paycheck_type"]["op"], check_t["paycheck_type"]["table"]) == (
        "enum",
        "VOUCHER_TYPE",
    )


def test_prismhr_run_dates_are_traced_only_when_every_voucher_agrees():
    payload = load(FIX / "prismhr" / "payroll_vouchers.json")
    payload["payrollVouchers"][1]["payDate"] = "2025-02-01"  # the vouchers of the batch disagree
    _, res = run(("prismhr", "payroll", payload, {"company_id": "ACME"}))
    run_record = res.records[0]
    assert run_record.pay_date is None and "run_dates_inconsistent" in [i.code for i in res.issues]
    t = by_dest(res, 0)
    assert "pay_date" not in t  # nothing is invented for a date that was not kept
    assert "period_start" in t and "source_id" in t


def test_prismhr_check_number_fallback_names_only_the_field_that_was_used():
    payload = load(FIX / "prismhr" / "payroll_vouchers.json")
    v = payload["payrollVouchers"][0]
    v["checkNumber"] = ""
    v["checkNum"] = "ALT-9"
    _, res = run(("prismhr", "payroll", payload, {"company_id": "ACME"}))
    step = by_dest(res, 1)["check_number"]
    assert (
        paths(step) == ["$.payrollVouchers[0].checkNum"]
        and "documented second name" in step["note"]
    )


def test_isolved_exact_paths_company_deposit_deduction_run_and_paycheck():
    _, co = run(case("isolved-company"))
    t = by_dest(co, 0)
    assert paths(t["parent_company_id"]) == ["$[0].client.id"] and paths(
        t["address.postal_code"]
    ) == ["$[0].legalAddress.zipCode"]
    assert paths(t["phone"]) == ["$[0].legalAddress.phoneNumber"]
    _, dep = run(case("isolved-direct-deposit"))
    d = by_dest(dep, 0)
    assert (
        paths(d["routing_number"]) == ["$[0].routingNumber"]
        and d["account_number_masked"]["sources"][0]["raw"] == "[withheld]"
    )
    assert d["allocation_type"]["op"] == "derive" and paths(d["allocation_type"]) == ["$[0].amount"]
    assert d["employee_id"]["op"] == "context" and paths(d["source_id"]) == ["$[0].id"]
    _, emb = run(case("isolved-direct-deposit-embedded"))
    assert paths(by_dest(emb, 0)["employee_id"]) == [
        "$.results[0].id"
    ]  # from the owning employee, not the context
    _, ded = run(case("isolved-deduction"))
    e = by_dest(ded, 0)
    assert paths(e["deduction_code"]) == ["$[0].payItemCode"] and paths(
        e["calc_params[1].value"]
    ) == ["$[0].calcParams[1].paramValue"]
    _, pay = run(case("isolved-payroll"))
    assert paths(by_dest(pay, 0)["period_start"]) == ["$.payroll_run.periodBeginDate"]
    check = by_dest(pay, 1)
    assert paths(check["run_id"]) == ["$.payroll_run.id"] and paths(check["gross_pay"]) == [
        "$.checks[0].grossPay"
    ]
    assert (check["check_number"]["op"], paths(check["check_number"])) == (
        "integer",
        ["$.checks[0].checkNumber"],
    )


def test_isolved_checks_without_a_run_have_no_run_id_step():
    payload = load(FIX / "isolved" / "payroll.json")["checks"]
    _, res = run(("isolved", "payroll", payload, {"company_id": "4501"}))
    assert res.records[0].run_id is None and "run_id" not in by_dest(res, 0)


def test_ukg_company_built_directly_names_ein_items_and_the_context_id():
    _, res = run(case("ukg-company"))
    t = by_dest(res, 0)
    assert paths(t["legal_entities[0].name"]) == ["$.items[0].ein_name"] and paths(
        t["legal_entities[1].tax_id"]
    ) == ["$.items[1].ein_tax_id"]
    assert t["source_id"]["op"] == "context" and paths(t["source_id"]) == ["context.company_id"]
    assert not any(
        p.startswith("$.items") for p in paths(t["source_id"])
    )  # the id is NOT claimed to come from an EIN


def test_ukg_exact_paths_deposit_deduction_run_and_paycheck_including_derived_gross():
    _, dep = run(case("ukg-direct-deposit"))
    d = by_dest(dep, 0)
    assert (d["allocation_type"]["op"], paths(d["allocation_type"])) == (
        "enum",
        ["$.deposits[0].calc_method.type"],
    )
    assert paths(d["effective_from"]) == ["$.deposits[0].active_from"] and paths(
        d["frequency_raw"]
    ) == ["$.deposits[0].payroll_frequency.type"]
    _, ded = run(case("ukg-deduction"))
    e = by_dest(ded, 0)
    assert paths(e["calc_method_raw"]) == ["$.deductions[0].ee_calc_method.method.type"] and paths(
        e["payee_code"]
    ) == ["$.deductions[0].vendor.id"]
    _, pay = run(case("ukg-payroll"))
    r = by_dest(pay, 0)
    assert paths(r["period_start"]) == ["$.payroll.start_date"] and paths(r["ein_id"]) == [
        "$.payroll.ein.id"
    ]
    c1, c2 = by_dest(pay, 1), by_dest(pay, 2)
    gross = c1["gross_pay"]
    assert (
        gross["op"] == "derive"
        and paths(gross) == ["$.pay_statements[0].earnings"]
        and "sum of the earning amounts" in gross["note"]
    )
    assert (
        "gross_pay" not in c2
    )  # no earning amounts, so nothing was derived and nothing is claimed
    assert paths(c1["run_id"]) == ["$.payroll.id"] and paths(c1["employee_id"]) == [
        "$.pay_statements[0].employee.account_id"
    ]


def test_a_value_taken_from_the_context_never_claims_a_file_path():
    for c in CASES:
        _, res = run(c)
        for i in range(len(res.records)):
            for step in res.traces[i]:
                if step["op"] == "context":
                    assert all(p.startswith("context.") for p in paths(step)), (c.id, step["dest"])
                if step["op"] == "assigned":
                    assert step["sources"] == [] and step["note"], (c.id, step["dest"])


# =================================================================================================
# 2. Independent attribution over every fixture
# =================================================================================================
LIVE_SHAPE_CASES = [
    ("prismhr", "payroll", LIVE / "prismhr" / "payroll_voucher_singular.json", {"company_id": "ACME"}),
    ("isolved", "payroll", LIVE / "isolved" / "checks_paged.json", {"company_id": "4501"}),
    ("isolved", "direct_deposit", LIVE / "isolved" / "direct_deposits_unmasked.json", {"company_id": "4501", "employee_id": "9"}),
    ("ukg-ready", "payroll", LIVE / "ukg_ready" / "pay_statement_single.json", {"company_id": "CID1", "payroll_id": "9"}),
    ("ukg-ready", "direct_deposit", LIVE / "ukg_ready" / "direct_deposits_unmasked.json", {"company_id": "CID1", "employee_id": "7"}),
    ("ukg-ready", "employee", LIVE / "ukg_ready" / "employee_bundle_unmasked.json", {"company_id": "CID1"}),
]  # fmt: skip
ALL = [pytest.param(c, id=c.id) for c in CASES] + [
    pytest.param(x, id=f"live-{x[0]}-{x[1]}-{x[2].stem}") for x in LIVE_SHAPE_CASES
]


def realize(item):
    if hasattr(item, "provider"):
        return run(item)
    provider, entity, path, ctx = item
    return run((provider, entity, load(path), ctx))


@pytest.mark.parametrize("item", ALL)
def test_every_traced_source_path_exists_in_the_payload_and_simple_steps_match_it(item):
    payload, res = realize(item)
    checked = 0
    for rec, steps in zip(res.records, res.traces, strict=True):
        dump = rec.model_dump(mode="json")
        for step in steps:
            for src in step["sources"]:
                if src["path"].startswith("context."):
                    assert step["op"] == "context"
                    continue
                assert exists(payload, src["path"]), (
                    f"{rec.record_type} {step['dest']}: invented path {src['path']}"
                )
            canonical = dig(dump, step["dest"])
            assert canonical not in (None, "", [], {}), (rec.record_type, step["dest"])
            if (
                step["op"] in ("copy", "decimal", "integer", "boolean", "date")
                and len(step["sources"]) == 1
            ):
                raw = resolve(payload, step["sources"][0]["path"])
                if str(step["sources"][0]["raw"]) != "[withheld]":
                    assert str(step["sources"][0]["raw"]).strip() == str(raw).strip() or isinstance(
                        raw, (bool, Decimal, int)
                    ), (step["dest"],)
                expected = expected_from_raw(step["op"], raw)
                assert canonical_matches(step["op"], expected, canonical), (
                    rec.record_type,
                    step["dest"],
                    raw,
                    canonical,
                )
                checked += 1
            if step["op"] == "route":
                raw = resolve(payload, step["sources"][0]["path"])
                assert canonical_matches("route", expected_from_raw("route", raw), canonical), (
                    step["dest"],
                    raw,
                    canonical,
                )
            if step["op"] == "redact" and step["dest"].endswith("masked"):
                raw = str(resolve(payload, step["sources"][0]["path"]))
                tail = "".join(ch for ch in raw if ch.isalnum())[-4:]
                assert canonical == (
                    f"****{tail}" if len("".join(ch for ch in raw if ch.isalnum())) > 4 else "****"
                )
    assert checked > 0  # the oracle really compared values for this input


@pytest.mark.parametrize("item", ALL)
def test_tracing_on_never_changes_the_canonical_output(item):
    if hasattr(item, "provider"):
        payload = load(FIX / item.folder / item.file)
        args = (item.provider, item.entity, payload, {"tenant_id": "t", **item.context})
    else:
        args = (item[0], item[1], load(item[2]), {"tenant_id": "t", **item[3]})
    off = service.normalize(*args, trace=False)
    on = service.normalize(*args, trace=True)
    assert off.traces == [] and on.traces
    assert off.model_dump_json() == on.model_dump_json()
    assert [r.account_number_hash for r in off.records if r.record_type == "direct_deposit"] == [
        r.account_number_hash for r in on.records if r.record_type == "direct_deposit"
    ]


# =================================================================================================
# 3. Coverage floors and privacy
# =================================================================================================
def header_leaves(dump, record_type):
    skip = STRUCTURAL | (set(LINE_ITEMS) if record_type == "paycheck" else set())
    out = []

    def walk(node, path):
        if node in (None, "", [], {}):
            return
        if isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        else:
            out.append(path)

    for key, value in dump.items():
        if key not in skip:
            walk(value, key)
    return out


@pytest.mark.parametrize("c", [c for c in CASES if c.entity != "employee"], ids=lambda c: c.id)
def test_every_filled_header_field_of_the_a1_record_types_has_a_source_or_an_explicit_reason(c):
    _, res = run(c)
    for rec, steps in zip(res.records, res.traces, strict=True):
        traced = {t["dest"] for t in steps}
        missing = [
            p
            for p in header_leaves(rec.model_dump(mode="json"), rec.record_type)
            if p not in traced
        ]
        assert missing == [], (c.id, rec.record_type, rec.source_id, missing)


def test_line_items_are_not_traced_yet_and_are_not_pretended_to_be():
    for c in (case("prismhr-payroll"), case("isolved-payroll"), case("ukg-payroll")):
        _, res = run(c)
        for rec, steps in zip(res.records, res.traces, strict=True):
            if rec.record_type == "paycheck":
                assert not [
                    t
                    for t in steps
                    if re.match(r"(earnings|taxes|deductions|benefits|payments)\[", t["dest"])
                ]


@pytest.mark.parametrize("item", ALL)
def test_no_secret_hash_or_unmasked_value_appears_in_any_trace(item):
    _, res = realize(item)
    text = json.dumps(res.traces, default=str)
    for secret in SECRETS:
        assert secret not in text, secret
    hashes = [r.account_number_hash for r in res.records if getattr(r, "account_number_hash", None)]
    for h in hashes:
        assert h not in text and h[:12] not in text
    assert not re.search(r"[0-9a-f]{40,}", text)
    assert "account_number_hash" not in text
    for steps in res.traces:
        for step in steps:
            if step["op"] == "redact":
                assert all(s["raw"] == "[withheld]" for s in step["sources"])


def test_the_convert_endpoint_returns_the_new_traces_for_every_record_type():
    files = [
        FileIn(name=c.file, content=load(FIX / c.folder / c.file))
        for c in CASES
        if c.provider == "prismhr" and c.id != "prismhr-deduction-recurring"
    ]
    out = convert_files(
        Provider.PRISMHR,
        files,
        {"tenant_id": "t", "company_id": "ACME", "employee_id": "E1001"},
        trace=True,
    )
    kinds = {r.entity for r in out.results if r.status == "converted" and any(r.traces)}
    assert {"company", "employee", "direct_deposit", "deduction", "payroll"} <= kinds
    assert "account_number_hash" not in out.model_dump_json()


def _dump_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "dump_trace_dests.py"
    spec = importlib.util.spec_from_file_location("dump_trace_dests", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_trace_destinations_can_be_collected_without_the_frontend():
    """The backend half of the grouping reference needs no frontend files (backend-only checkouts)."""
    collected = _dump_module().collect()
    assert collected and all(collected.values())


def test_the_frontend_grouping_fixture_matches_what_the_adapters_trace():
    """frontend/src/test/fixtures/trace-dests.json feeds the Pipeline Explorer's grouping tests; keep it current.

    Skipped only when the frontend directory itself is absent (a backend-only checkout); in the full repository
    it is compared exactly as before.
    """
    mod = _dump_module()
    if not (mod.ROOT / "frontend").is_dir():
        pytest.skip("frontend/ is not part of this checkout (backend-only)")
    assert mod.OUT.read_text() == json.dumps(mod.collect(), indent=2) + "\n", (
        "run: uv run python scripts/dump_trace_dests.py"
    )
