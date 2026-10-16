"""Provider error responses are reported as such, not as records with missing ids (DECISIONS.md section 7)."""

import json

import pytest
from fastapi.testclient import TestClient

from normalization import cli, service
from normalization.api.app import create_app

CTX = {"tenant_id": "t", "company_id": "C1", "employee_id": "E1"}
ENTITIES = ("company", "employee", "direct_deposit", "deduction", "payroll")


def run(provider, entity, payload):
    return service.normalize(provider, entity, payload, CTX)


def only_error(res):
    errors = [i for i in res.issues if i.severity.value == "error"]
    assert [i.code for i in errors] == ["provider_error"] and res.records == [] and not res.ok
    assert not [
        i for i in res.issues if i.code in ("missing_id", "provider_error_message", "no_run_link")
    ]
    return errors[0].message


@pytest.mark.parametrize("entity", ENTITIES)
def test_prismhr_error_envelope_is_one_clear_error_for_every_entity(entity):
    msg = only_error(run("prismhr", entity, {"errorCode": "1", "errorMessage": "Session expired"}))
    assert (
        "prismhr" in msg
        and "code 1" in msg
        and "Session expired" in msg
        and "No records were created" in msg
    )


@pytest.mark.parametrize("entity", ENTITIES)
def test_isolved_validation_error_envelope(entity):
    payload = {
        "error": "Validation failed",
        "validationResults": [{"fields": ["ssn"], "message": "Invalid value"}],
    }
    msg = only_error(run("isolved", entity, payload))
    assert "Validation failed" in msg and "Invalid value" in msg


@pytest.mark.parametrize("entity", ENTITIES)
def test_ukg_standard_error_envelope(entity):
    payload = {
        "errors": [{"code": 401, "message": "The access token is invalid"}],
        "user_messages": [
            {"severity": "ERROR", "text": "The access token is invalid", "details": {"code": 401}}
        ],
    }
    msg = only_error(run("ukg-ready", entity, payload))
    assert "code 401" in msg and "The access token is invalid" in msg


def test_a_success_envelope_is_not_an_error():
    for code in ("", "0", None):
        res = run(
            "prismhr",
            "employee",
            {"errorCode": code, "errorMessage": "", "employee": [{"id": "E1", "firstName": "A"}]},
        )
        assert res.ok and len(res.records) == 1


def test_an_empty_success_envelope_keeps_its_old_behavior():
    res = run("prismhr", "employee", {"errorCode": "0", "errorMessage": ""})
    assert not [i for i in res.issues if i.code == "provider_error"]


def test_real_records_with_a_warning_style_message_are_still_normalized():
    res = run(
        "prismhr",
        "employee",
        {"errorCode": "7", "errorMessage": "partial", "employee": [{"id": "E1", "firstName": "A"}]},
    )
    assert (
        len(res.records) == 1 and [i.code for i in res.issues if i.code == "provider_error"] == []
    )
    assert "provider_error_message" in {i.code for i in res.issues}  # the existing note is kept


def test_a_legitimate_record_with_a_missing_id_still_gets_its_own_error():
    res = run("prismhr", "employee", {"errorCode": "0", "employee": [{"firstName": "NoId"}]})
    assert [i.code for i in res.issues if i.severity.value == "error"] == ["missing_id"]


def test_ukg_and_isolved_data_that_mentions_errors_is_not_mistaken_for_an_error_body():
    assert run(
        "ukg-ready", "employee", {"employee": {"id": 1, "first_name": "A"}, "errors": []}
    ).records
    assert run("isolved", "employee", {"results": [{"id": 1}], "error": None}).records


def test_the_message_is_shortened_and_never_carries_secrets():
    secret = "abcDEF1234567890abcDEF1234567890xyz"  # token-shaped, e.g. a session id
    res = run(
        "prismhr",
        "employee",
        {"errorCode": "9", "errorMessage": f"Bad session {secret} for 123-45-6789 " + "x" * 500},
    )
    msg = only_error(res)
    assert secret not in msg and "123-45-6789" not in msg and "[redacted]" in msg and len(msg) < 400


def test_the_payload_is_never_echoed():
    res = run(
        "prismhr",
        "employee",
        {
            "errorCode": "2",
            "errorMessage": "nope",
            "extension": {
                "password": "hunter2hunter2",
                "sessionId": "SESS-SECRET-VALUE-1234567890",
            },
        },
    )
    assert "hunter2hunter2" not in json.dumps(
        [i.model_dump(mode="json") for i in res.issues]
    ) and "SESS-SECRET" not in str(res.issues)


def test_the_api_answers_422_with_the_error_and_the_cli_fails_cleanly(tmp_path):
    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.post(
        "/normalize/prismhr/payroll",
        content=json.dumps(
            {"context": CTX, "payload": {"errorCode": "1", "errorMessage": "Session expired"}}
        ),
    )
    assert r.status_code == 422 and [i["code"] for i in r.json()["issues"]] == ["provider_error"]
    src, out = tmp_path / "e.json", tmp_path / "o.json"
    src.write_text(json.dumps({"errorCode": "1", "errorMessage": "Session expired"}))
    code = cli.main(
        [
            "normalize",
            "--provider",
            "prismhr",
            "--entity",
            "employee",
            "--input",
            str(src),
            "--tenant-id",
            "t",
            "--company-id",
            "C",
            "--output",
            str(out),
        ]
    )
    assert (
        code == cli.EXIT_DATA_ERRORS
        and json.loads(out.read_text())["issues"][0]["code"] == "provider_error"
    )


@pytest.mark.db
def test_an_error_response_is_never_stored(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)
    r = client.post(
        "/ingest/prismhr/employee",
        content=json.dumps(
            {"context": CTX, "payload": {"errorCode": "1", "errorMessage": "Session expired"}}
        ),
    )
    assert r.status_code == 422 and r.json()["stored"] == {}


def test_detection_names_an_error_file_for_what_it_is():
    from normalization.canonical.enums import Provider
    from normalization.convert import FileIn, detect_files

    for provider, payload in (
        (Provider.PRISMHR, {"errorCode": "1", "errorMessage": "Session expired"}),
        (
            Provider.ISOLVED,
            {
                "error": "Validation failed",
                "validationResults": [{"fields": ["a"], "message": "bad"}],
            },
        ),
        (Provider.UKG_READY, {"errors": [{"code": 401, "message": "The access token is invalid"}]}),
    ):
        d = detect_files(provider, [FileIn(name="x.json", content=payload)]).files[0].detection
        assert (
            d.status == "unsupported"
            and "error response" in d.message
            and "Nothing can be converted" in d.message
        )
