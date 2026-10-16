"""Detection and preview conversion: all 17 fixture scenarios plus the unhappy paths."""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from normalization.api.app import create_app
from normalization.canonical.enums import Provider
from normalization.convert import FileIn, convert_files, detect_files
from normalization.detect import detect
from support.scenarios import CASES

FIX = Path(__file__).parent / "fixtures"
SNAP = Path(__file__).parent / "snapshots" / "canonical"


def load(case):
    return json.loads((FIX / case.folder / case.file).read_text(), parse_float=Decimal)


@pytest.fixture(scope="module")
def client():
    # detect/convert never touch the database, so an engine-less app is enough
    return TestClient(create_app(), raise_server_exceptions=True)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_every_fixture_is_detected_as_its_entity(case):
    d = detect(Provider(case.provider), load(case))
    assert d.status == "ok", d
    entities = {u.entity.value for u in d.units}
    assert case.entity in entities
    assert all(u.evidence for u in d.units)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_convert_matches_the_baseline_snapshot(case):
    ctx = {"tenant_id": "snapshot", **case.context}
    out = convert_files(Provider(case.provider), [FileIn(name=case.file, content=load(case))], ctx)
    unit = next(r for r in out.results if r.entity == case.entity)
    assert unit.status == "converted", unit
    expected = json.loads((SNAP / f"{case.id}.json").read_text())
    assert unit.records == expected["records"]


def test_nested_sections_are_counted_once_each():
    case = next(c for c in CASES if c.id == "prismhr-employee")
    d = detect(Provider.PRISMHR, load(case))
    assert {u.entity.value: sum(u.counts.values()) for u in d.units} == {
        "employee": 2,
        "direct_deposit": 3,
        "deduction": 2,
    }
    assert [u.nested_in for u in d.units] == [None, "employee", "employee"]


def test_missing_context_is_reported_not_guessed():
    case = next(c for c in CASES if c.id == "ukg-direct-deposit")
    out = convert_files(
        Provider.UKG_READY,
        [FileIn(name="d.json", content=load(case))],
        {"tenant_id": "t", "company_id": "CID1"},
    )
    r = out.results[0]
    assert (r.status, r.missing, r.records) == ("needs_context", ["employee_id"], [])


def test_sibling_ids_are_suggested_never_applied():
    emp = next(c for c in CASES if c.id == "prismhr-employee")
    ded = next(c for c in CASES if c.id == "prismhr-deduction-recurring")
    cm = next(c for c in CASES if c.id == "prismhr-company")
    files = [FileIn(name=c.file, content=load(c)) for c in (cm, emp, ded)]
    det = detect_files(Provider.PRISMHR, files)
    got = {(s.field, s.value) for s in det.suggestions}
    assert ("company_id", "ACME") in got and ("employee_id", "E1001") in got
    out = convert_files(Provider.PRISMHR, files, {"tenant_id": "t"})
    assert all(r.status == "needs_context" for r in out.results if r.entity != "company")


@pytest.mark.parametrize("doc", [None, {}, []])
def test_empty_documents(doc):
    assert detect(Provider.PRISMHR, doc).status == "empty"


@pytest.mark.parametrize("doc", [42, "text", {"hello": "world"}, [1, 2], [{"id": 1, "name": "x"}]])
@pytest.mark.parametrize("provider", list(Provider))
def test_generic_fields_are_never_enough(provider, doc):
    assert detect(provider, doc).status == "unsupported"


def test_wrong_provider_is_flagged_not_switched():
    case = next(c for c in CASES if c.id == "ukg-company")
    d = detect(Provider.PRISMHR, load(case))
    assert d.status == "unsupported"
    assert d.provider_hint and "UKG Ready" in d.provider_hint


def test_ambiguous_payload_needs_confirmation_and_override_is_validated():
    doc = [{"firstName": "Ann"}]
    d = detect(Provider.PRISMHR, doc)
    assert d.status == "ambiguous" and d.candidates
    ok = detect_files(Provider.PRISMHR, [FileIn(name="a", content=doc, entity="employee")])
    assert ok.files[0].detection.status == "ok"
    bad = detect_files(Provider.PRISMHR, [FileIn(name="a", content=doc, entity="payroll")])
    assert bad.files[0].detection.status == "unsupported"


def test_invalid_override_entity():
    d = detect_files(Provider.PRISMHR, [FileIn(name="a", content={}, entity="nope")])
    assert d.files[0].detection.status == "unsupported"


def test_duplicate_records_are_not_dropped_by_detection():
    case = next(c for c in CASES if c.id == "isolved-deduction")
    rows = load(case)
    d = detect(Provider.ISOLVED, rows + rows)
    assert d.units[0].counts == {"deduction": len(rows) * 2}


def test_mixed_provider_files_in_one_request_are_per_file():
    a = next(c for c in CASES if c.id == "prismhr-company")
    b = next(c for c in CASES if c.id == "ukg-company")
    det = detect_files(
        Provider.PRISMHR, [FileIn(name="a", content=load(a)), FileIn(name="b", content=load(b))]
    )
    assert [f.detection.status for f in det.files] == ["ok", "unsupported"]


# --- HTTP ---------------------------------------------------------------------------------------
def body(case, **extra):
    return {"files": [{"name": case.file, "content": load(case)}], **extra}


def post(client, path, payload):
    return client.post(path, content=json.dumps(payload, default=str))


def test_http_detect_and_convert(client):
    case = next(c for c in CASES if c.id == "isolved-payroll")
    r = post(client, "/detect/isolved", body(case))
    assert r.status_code == 200 and r.json()["files"][0]["detection"]["status"] == "ok"
    r = post(client, "/convert/isolved", body(case, context={"company_id": "4501"}))
    out = r.json()
    assert r.status_code == 200 and out["results"][0]["status"] == "converted"
    assert out["results"][0]["counts"] == {"payroll_run": 1, "paycheck": 1}


def test_http_errors(client):
    assert post(client, "/detect/nope", {"files": []}).status_code == 404
    assert client.post("/detect/prismhr", content=b"{not json").status_code == 400
    assert post(client, "/detect/prismhr", {"files": []}).status_code == 422
    dup = {"files": [{"name": "a", "content": {}}, {"name": "a", "content": {}}]}
    assert post(client, "/detect/prismhr", dup).status_code == 422


def test_convert_has_no_database_dependency_and_ignores_save_flags(client):
    route = next(r for r in client.app.routes if getattr(r, "path", "") == "/convert/{provider}")
    assert {p.name for p in route.dependant.query_params + route.dependant.dependencies} == set()
    case = next(c for c in CASES if c.id == "prismhr-company")
    r = post(client, "/convert/prismhr", body(case, save=True, persist=True))
    assert r.status_code == 200 and "stored" not in r.json()


def test_no_raw_secret_in_detection_output(client):
    for case in CASES:
        text = post(client, f"/detect/{case.provider}", body(case)).text
        for secret in ("123-45-6789", "987-65-4321", "900-44-3333", "000123456789", "998877665544"):
            assert secret not in text
