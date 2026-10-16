"""Final check: `account_number_hash` stays INTERNAL.

It is computed for every direct deposit and stored in PostgreSQL (persistence and update detection need it), and it must
never leave the process: not in /normalize, /convert, /detect, /ingest, stored reads, /migrate/preview, extras, provenance,
traces, issues, error responses, the OpenAPI document or the CLI output. The test also looks for ANY 64-character hex string
(the hash's shape), so a renamed leak would be caught too. Account numbers themselves are never printed here.
"""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from normalization import cli, service
from normalization.api.app import create_app

FIX = Path(__file__).parent / "fixtures"
HEX64 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
DEPOSIT_SOURCES = [  # (provider, folder, file, context)
    ("prismhr", "prismhr", "employee_get_employee.json", {"company_id": "ACME"}),
    ("isolved", "isolved", "direct_deposits.json", {"company_id": "4501", "employee_id": "90001"}),
    ("isolved", "isolved", "employees.json", {"company_id": "4501"}),
    ("ukg-ready", "ukg_ready", "direct_deposits.json", {"company_id": "CID1", "employee_id": "7770001"}),
    ("ukg-ready", "live_shapes/ukg_ready", "direct_deposits_unmasked.json", {"company_id": "CID1", "employee_id": "7770001"}),
    ("isolved", "live_shapes/isolved", "direct_deposits_unmasked.json", {"company_id": "4501", "employee_id": "90001"}),
]  # fmt: skip


def load(folder, name):
    return json.loads((FIX / folder / name).read_text(), parse_float=Decimal)


def internal_hashes(provider, folder, name, ctx):
    res = service.normalize(
        provider, "direct_deposit", load(folder, name), {"tenant_id": "t", **ctx}
    )
    return [r.account_number_hash for r in res.records if r.account_number_hash]


ALL = {tuple(c[:3]): internal_hashes(*c[0:2], c[2], c[3]) for c in DEPOSIT_SOURCES}
HASHES = sorted({h for hs in ALL.values() for h in hs})


def assert_clean(text_, where):
    assert "account_number_hash" not in text_, where
    assert not HEX64.search(text_), f"{where}: a 64-hex string (hash-shaped) is present"
    for h in HASHES:
        assert h not in text_ and h[:12] not in text_, where


def test_the_hash_is_really_computed_for_every_deposit_source():
    assert HASHES and all(len(h) == 64 for h in HASHES)
    for key, hs in ALL.items():
        assert hs, key  # the check below is not vacuous for any source


@pytest.fixture
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


def post(client, path, body):
    return client.post(path, content=json.dumps(body, default=str))


@pytest.mark.parametrize(("provider", "folder", "name", "ctx"), DEPOSIT_SOURCES)
def test_no_stateless_api_response_carries_the_hash(client, provider, folder, name, ctx):
    payload = load(folder, name)
    context = {"tenant_id": "t", **ctx}
    pages = [
        post(client, f"/normalize/{provider}/direct_deposit", {"context": context, "payload": payload}).text,
        post(client, f"/convert/{provider}", {"files": [{"name": name, "content": payload}], "context": ctx, "trace": True, "integrity": True}).text,
        post(client, f"/detect/{provider}", {"files": [{"name": name, "content": payload}]}).text,
    ]  # fmt: skip
    for page in pages:
        assert_clean(page, f"{provider}/{name}")


def test_extras_provenance_traces_and_issues_hold_no_hash_even_if_the_source_tries_to_inject_one(
    client,
):
    """A SOURCE field that happens to be named like ours is the provider's own data: it is redacted, and ours is never copied from it."""
    payload = load("isolved", "direct_deposits.json")
    payload[0]["account_number_hash"] = "f" * 64
    payload[0]["accountNumberHash"] = "e" * 64
    ctx = {"tenant_id": "t", "company_id": "4501", "employee_id": "90001"}
    res = service.normalize("isolved", "direct_deposit", payload, ctx, trace=True)
    first = res.records[0]
    assert first.account_number_hash and first.account_number_hash not in (
        "f" * 64,
        "e" * 64,
    )  # ours is computed, never copied
    assert first.extras["account_number_hash"] == first.extras["accountNumberHash"] == "[redacted]"
    everything = json.dumps(
        [
            first.extras,
            first.provenance.model_dump(),
            [i.model_dump() for i in res.issues],
            res.traces,
        ],
        default=str,
    )
    assert "f" * 64 not in everything and "e" * 64 not in everything
    for h in HASHES:
        assert h not in everything and h[:12] not in everything
    assert not HEX64.search(everything)
    r = post(client, "/normalize/isolved/direct_deposit", {"context": ctx, "payload": payload})
    assert "f" * 64 not in r.text and "e" * 64 not in r.text and not HEX64.search(r.text)
    assert first.account_number_hash not in r.text
    assert (
        "account_number_hash" not in r.json()["records"][0]
    )  # as a record field it does not exist


def test_error_responses_carry_no_hash(client):
    payload = [
        *load("isolved", "direct_deposits.json"),
        5,
    ]  # one bad item: a 422 that still lists the good records
    ctx = {"tenant_id": "t", "company_id": "4501", "employee_id": "90001"}
    cases = [
        post(client, "/normalize/isolved/direct_deposit", {"context": ctx, "payload": payload}),
        post(client, "/normalize/isolved/direct_deposit", {"context": ctx, "payload": [{"accountNumber": "x\x00y", "routingNumber": "1"}]}),
        post(client, "/normalize/isolved/direct_deposit", {"context": ctx, "payload": {"results": "x"}}),
        post(client, "/normalize/nope/direct_deposit", {"context": ctx, "payload": []}),
        post(client, "/migrate/preview/ukg-ready", {"employee": {}, "destination": {"company_id": "1"}}),
        client.post("/normalize/isolved/direct_deposit", content=b"{not json"),
    ]  # fmt: skip
    for r in cases:
        assert_clean(r.text, f"{r.status_code}")
    assert (
        cases[0].status_code == 422 and cases[0].json()["records"]
    )  # the error case really lists deposit records


def test_the_migration_preview_never_returns_the_hash(client):
    emp = service.normalize(
        "prismhr",
        "employee",
        load("prismhr", "employee_get_employee.json"),
        {"tenant_id": "t", "company_id": "ACME"},
    ).records[0]
    body = {
        "employee": emp.model_dump(mode="json"),
        "destination": {"company_id": "4410"},
        "operator": {"username": "u"},
    }
    r = post(client, "/migrate/preview/ukg-ready", body)
    assert r.status_code == 200
    assert_clean(r.text, "migrate")


def test_the_openapi_document_and_cli_output_have_no_hash(tmp_path):
    assert_clean(json.dumps(create_app().openapi()), "openapi")
    out = tmp_path / "o.json"
    cli.main(["normalize", "--provider", "isolved", "--entity", "direct_deposit", "--input", str(FIX / "isolved/direct_deposits.json"),
              "--tenant-id", "t", "--company-id", "4501", "--employee-id", "90001", "--output", str(out)])  # fmt: skip
    assert_clean(out.read_text(), "cli")


def test_the_frontend_source_never_reads_or_declares_the_field():
    root = Path(__file__).parents[1] / "frontend" / "src"
    if not root.exists():
        pytest.skip("frontend/ is not part of this checkout (backend-only)")
    offenders = [
        p.name
        for p in root.rglob("*.ts*")
        if ".test." not in p.name and p.name != "mask.ts" and "account_number_hash" in p.read_text()
    ]
    assert (
        offenders == []
    )  # no type, component or client code mentions it (mask.ts only hides it, below)
    mask_ts = (root / "lib" / "mask.ts").read_text()
    assert (
        "account_number_hash" in mask_ts
    )  # defence in depth: the display mask would hide it if one ever arrived


# --- persistence: the hash stays available INTERNALLY ---------------------------------------------------------------------
@pytest.mark.db
def test_the_hash_is_stored_and_used_internally_but_no_read_or_write_response_shows_it(db):
    client = TestClient(create_app(db))
    ctx = {"tenant_id": "hashcheck", "company_id": "ACME"}
    responses = []
    for provider, folder, name, extra in DEPOSIT_SOURCES[:4]:
        c = {
            **ctx,
            "company_id": extra["company_id"],
            **({"employee_id": extra["employee_id"]} if "employee_id" in extra else {}),
        }
        r = post(
            client,
            f"/ingest/{provider}/direct_deposit",
            {"context": c, "payload": load(folder, name)},
        )
        assert r.status_code == 200, r.text
        responses.append(r.text)
    with db.connect() as conn:
        stored = [
            row[0]
            for row in conn.execute(
                text("select account_number_hash from direct_deposit_instructions")
            )
        ]
    present = [
        h for h in stored if h
    ]  # a deposit with no account number (a remaining-balance row) has none
    assert present and all(
        len(h) == 64 for h in present
    )  # stored in the database: persistence still works
    expected = set()
    for provider, folder, name, extra in DEPOSIT_SOURCES[:4]:
        res = service.normalize(
            provider, "direct_deposit", load(folder, name), {"tenant_id": "hashcheck", **extra}
        )
        expected |= {r.account_number_hash for r in res.records if r.account_number_hash}
    assert (
        set(present) == expected
    )  # exactly the internally computed hashes (they are tenant-scoped)
    scoped = sorted(expected)
    reads = []
    for path in (
        "/tenants/hashcheck/prismhr/companies/ACME/employees",
        "/tenants/hashcheck/prismhr/companies/ACME/employees/E1001/direct-deposits",
        "/tenants/hashcheck/isolved/companies/4501/employees/90001/direct-deposits",
        "/tenants/hashcheck/ukg-ready/companies/CID1/employees/7770001/direct-deposits",
        "/stats",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        reads.append(r.text)
    for page in (*responses, *reads):
        assert_clean(page, "stored")
        assert not any(h in page or h[:12] in page for h in scoped)
    assert any("****" in page for page in reads)  # the masked account is what readers get


@pytest.mark.db
def test_a_rejected_ingest_and_a_re_save_expose_nothing_and_write_nothing_new(db):
    client = TestClient(create_app(db))
    ctx = {"tenant_id": "hashcheck2", "company_id": "4501", "employee_id": "90001"}
    bad = post(
        client,
        "/ingest/isolved/direct_deposit",
        {"context": ctx, "payload": [*load("isolved", "direct_deposits.json"), 5]},
    )
    assert bad.status_code == 422
    assert_clean(bad.text, "rejected ingest")
    with db.connect() as conn:
        assert (
            conn.execute(
                text(
                    "select count(*) from direct_deposit_instructions where tenant_id='hashcheck2'"
                )
            ).scalar()
            == 0
        )
    good = {"context": ctx, "payload": load("isolved", "direct_deposits.json")}
    first, second = (
        post(client, "/ingest/isolved/direct_deposit", good),
        post(client, "/ingest/isolved/direct_deposit", good),
    )
    assert second.json()["stored"]["direct_deposit"] == {
        "inserted": 0,
        "updated": 0,
        "unchanged": 3,
    }  # the hash still drives idempotency
    assert_clean(first.text + second.text, "ingest")
