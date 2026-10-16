"""The salted account hash is INTERNAL: stored for persistence, never serialized or shown.

Covers every serialization path of a direct-deposit record: model dumps and repr, traces, issues, the CLI
output file, /normalize (200 and 422), /convert (with trace), /detect, the OpenAPI schema, the stored read
endpoints and the ingest response, plus the database behaviour (the hash IS stored and still drives updates).
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from normalization import cli, service
from normalization.api.app import create_app
from normalization.canonical.enums import Provider
from normalization.canonical.models import DirectDepositInstruction
from normalization.convert import FileIn, convert_files
from normalization.persistence import repository as repo
from normalization.persistence.db import transaction
from normalization.settings import get_settings

FIX = Path(__file__).parent / "fixtures"
CASES = [  # (provider, fixture folder, file, context beyond tenant)
    ("prismhr", "prismhr", "employee_get_employee.json", {"company_id": "ACME"}),
    ("isolved", "isolved", "direct_deposits.json", {"company_id": "4501", "employee_id": "90001"}),
    ("isolved", "isolved", "employees.json", {"company_id": "4501"}),
    ("ukg-ready", "ukg_ready", "direct_deposits.json", {"company_id": "CID1", "employee_id": "7770001"}),
]  # fmt: skip
IDS = ["prismhr", "isolved", "isolved-embedded", "ukg"]


def load(folder, name):
    return json.loads((FIX / folder / name).read_text(), parse_float=Decimal)


def deposits(provider, folder, name, ctx, trace=False):
    res = service.normalize(
        provider, "direct_deposit", load(folder, name), {"tenant_id": "t", **ctx}, trace=trace
    )
    assert res.ok and res.records, res.issues
    return res


def hashes_of(res):
    """The internal hashes (a deposit without an account number, e.g. a reserved live check, has none)."""
    return [r.account_number_hash for r in res.records if r.account_number_hash]


@pytest.mark.parametrize(("provider", "folder", "name", "ctx"), CASES, ids=IDS)
def test_the_hash_exists_internally_but_in_no_serialization(provider, folder, name, ctx):
    res = deposits(provider, folder, name, ctx, trace=True)
    hashes = hashes_of(res)
    assert hashes and all(len(h) == 64 for h in hashes)  # still computed, still on the model
    forms = [
        res.model_dump_json(),
        json.dumps(res.model_dump(mode="json")),
        repr(res),
        str(res),
        json.dumps(res.traces, default=str),
        str(res.issues),
    ]
    for rec in res.records:
        forms += [
            rec.model_dump_json(),
            json.dumps(rec.model_dump(mode="json")),
            repr(rec),
            str(rec),
        ]
    text_ = " ".join(forms)
    for h in hashes:
        assert h not in text_
        assert h[:12] not in text_  # not even a prefix (a record id once embedded one)
    assert "account_number_hash" not in text_
    masked = [r.account_number_masked for r in res.records if r.account_number_hash]
    assert masked and all(m.startswith("****") for m in masked)  # masking unchanged


@pytest.mark.parametrize(("provider", "folder", "name", "ctx"), CASES, ids=IDS)
def test_record_ids_do_not_depend_on_the_salt_so_rotating_it_never_duplicates_rows(
    provider, folder, name, ctx, monkeypatch
):
    before = deposits(provider, folder, name, ctx)
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "another-test-only-salt")
    get_settings.cache_clear()
    try:
        after = deposits(provider, folder, name, ctx)
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert hashes_of(before) != hashes_of(after)  # the secret really changed the hashes...
    assert [r.source_id for r in before.records] == [
        r.source_id for r in after.records
    ]  # ...but not the ids


def test_the_field_is_still_part_of_the_canonical_model_and_validates_input():
    assert "account_number_hash" in DirectDepositInstruction.model_fields
    rec = DirectDepositInstruction(
        provider="prismhr",
        tenant_id="t",
        company_id="c",
        source_id="s",
        employee_id="e",
        account_number_hash="abc123",
    )
    assert rec.account_number_hash == "abc123"
    assert "account_number_hash" not in rec.model_dump() and "abc123" not in repr(rec)


# --- HTTP ------------------------------------------------------------------------------------------------
def post(client, path, body):
    return client.post(path, content=json.dumps(body, default=str))


def test_no_http_response_contains_the_hash():
    client = TestClient(create_app())
    forbidden, texts = [], []
    for provider, folder, name, ctx in CASES:
        payload = load(folder, name)
        forbidden += hashes_of(deposits(provider, folder, name, ctx))
        body = {"context": {"tenant_id": "t", **ctx}, "payload": payload}
        texts.append(post(client, f"/normalize/{provider}/direct_deposit", body).text)
        texts.append(
            post(
                client,
                f"/convert/{provider}",
                {"files": [{"name": name, "content": payload}], "context": ctx, "trace": True},
            ).text
        )
        texts.append(
            post(
                client, f"/detect/{provider}", {"files": [{"name": name, "content": payload}]}
            ).text
        )
    joined = " ".join(texts)
    assert len(texts) == 12 and all(forbidden)
    for h in forbidden:
        assert h not in joined and h[:12] not in joined  # neither the hash nor a prefix of it
    assert "account_number_hash" not in joined and "****" in joined


def test_an_error_response_that_still_lists_records_has_no_hash():
    client = TestClient(create_app())
    payload = [
        *load("isolved", "direct_deposits.json"),
        5,
    ]  # one item that is not an object: an error
    expected = hashes_of(
        service.normalize(
            "isolved",
            "direct_deposit",
            payload,
            {"tenant_id": "t", "company_id": "4501", "employee_id": "1"},
        )
    )
    r = post(
        client,
        "/normalize/isolved/direct_deposit",
        {
            "context": {"tenant_id": "t", "company_id": "4501", "employee_id": "1"},
            "payload": payload,
        },
    )
    assert r.status_code == 422 and r.json()["records"] and expected
    for h in expected:
        assert h not in r.text
    assert "account_number_hash" not in r.text


def test_convert_results_have_no_hash_even_in_the_python_object():
    out = convert_files(
        Provider.ISOLVED,
        [FileIn(name="d.json", content=load("isolved", "direct_deposits.json"))],
        {"tenant_id": "t", "company_id": "4501", "employee_id": "1"},
        trace=True,
    )
    assert out.results[0].records and "account_number_hash" not in out.model_dump_json()


def test_the_openapi_schema_does_not_advertise_the_field():
    assert "account_number_hash" not in json.dumps(create_app().openapi())


def test_the_cli_output_file_has_no_hash(tmp_path):
    out = tmp_path / "out.json"
    code = cli.main(
        [
            "normalize",
            "--provider",
            "isolved",
            "--entity",
            "direct_deposit",
            "--input",
            str(FIX / "isolved" / "direct_deposits.json"),
            "--tenant-id",
            "t",
            "--company-id",
            "4501",
            "--employee-id",
            "1",
            "--output",
            str(out),
        ]
    )
    assert code in (cli.EXIT_OK, cli.EXIT_DATA_ERRORS)
    data = out.read_text()
    assert '"records"' in data and "account_number_hash" not in data and "****" in data


def test_snapshots_contain_no_hash_but_keep_the_masked_account():
    snap = FIX.parent / "snapshots" / "canonical"
    for f in snap.glob("*direct-deposit*.json"):
        text_ = f.read_text()
        assert "account_number_hash" not in text_ and "account_number_masked" in text_, f.name


# --- the database still stores it and still uses it ---------------------------------------------------------
@pytest.mark.db
def test_the_hash_is_stored_and_the_stored_read_endpoints_hide_it(db):
    res = deposits("prismhr", "prismhr", "employee_get_employee.json", {"company_id": "ACME"})
    with transaction(db) as s:
        repo.ingest(s, res)
    with db.connect() as conn:
        stored = sorted(
            r[0]
            for r in conn.execute(
                text("select account_number_hash from direct_deposit_instructions")
            )
        )
    assert stored == sorted(hashes_of(res)) and all(
        len(h) == 64 for h in stored
    )  # persisted, internal
    client = TestClient(create_app(db))
    r = client.get("/tenants/t/prismhr/companies/ACME/employees/E1001/direct-deposits")
    assert r.status_code == 200 and r.json()
    for h in stored:
        assert h not in r.text
    assert "account_number_hash" not in r.text and "****" in r.text
    with transaction(
        db
    ) as s:  # the repository still exposes it to code that needs it (e.g. matching)
        internal = repo.list_direct_deposits(s, "prismhr", "t", "ACME", "E1001")
        assert all(d.account_number_hash in stored for d in internal)


@pytest.mark.db
def test_the_ingest_response_has_no_hash_and_saving_twice_is_still_idempotent(db):
    client = TestClient(create_app(db))
    body = {
        "context": {"tenant_id": "t", "company_id": "ACME"},
        "payload": load("prismhr", "employee_get_employee.json"),
    }
    first = post(client, "/ingest/prismhr/direct_deposit", body)
    second = post(client, "/ingest/prismhr/direct_deposit", body)
    assert first.json()["stored"]["direct_deposit"]["inserted"] == 3
    assert second.json()["stored"]["direct_deposit"] == {
        "inserted": 0,
        "updated": 0,
        "unchanged": 3,
    }
    assert "account_number_hash" not in first.text + second.text


@pytest.mark.db
def test_content_hash_still_covers_the_hash_so_a_salt_change_refreshes_the_stored_value(
    db, monkeypatch
):
    res = deposits("prismhr", "prismhr", "employee_get_employee.json", {"company_id": "ACME"})
    rec = res.records[0]
    legacy = repo.hashlib.sha256(
        json.dumps(
            {**rec.model_dump(mode="json"), "account_number_hash": rec.account_number_hash},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert (
        repo.content_hash(rec) == legacy
    )  # identical to what was stored before the field became internal
    with transaction(db) as s:
        repo.ingest(s, res)
    monkeypatch.setenv("ACCOUNT_HASH_SALT", "a-different-test-only-salt")
    get_settings.cache_clear()
    try:
        rotated = deposits(
            "prismhr", "prismhr", "employee_get_employee.json", {"company_id": "ACME"}
        )
        assert hashes_of(rotated) != hashes_of(res)
        with transaction(db) as s:
            summary = repo.ingest(s, rotated)
        assert summary.counts["direct_deposit"]["updated"] == 3  # an update, never a duplicate
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    with db.connect() as conn:
        assert conn.execute(text("select count(*) from direct_deposit_instructions")).scalar() == 3
