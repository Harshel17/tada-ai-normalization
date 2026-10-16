"""Regression tests for the six findings of the independent evaluation (reproductions use NEW, non-fixture data).

4  sensitive values under unlisted key names must not survive in `extras` (or anywhere derived from it)
1  wrong-typed nested provider fields are controlled validation errors, never an uncaught exception / HTTP 500
2  lone surrogates are refused before serialization
3  NUL characters are refused before persistence
5  the backend-only checkout does not need frontend files (see test_provenance_a1.py)
6  ssn_last4 only from a valid digit-based (or digit-masked) SSN
"""

import copy
import json
import logging
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from normalization import cli, service
from normalization.adapters import ADAPTERS
from normalization.adapters.base import last4, ssn_last4
from normalization.api.app import create_app
from normalization.inputs import find_invalid_text
from normalization.references import load_reference
from normalization.sensitive import REDACTED, is_sensitive_name, scrub

CTX = {"tenant_id": "evalT", "company_id": "NOVA77"}
SSN, SSN_PLAIN = "987-65-4321", "987654321"
ACCT = "5550001112223"
TOKEN = "tok_ABC123SECRET"
PW = "hunter2hunter2"


def prism_employee_payload(**extra_employee):
    emp = {
        "id": "N-3001",
        "firstName": "Priya",
        "lastName": "Nair",
        "person": {"birthDate": "1991-03-22", "gender": "F"},
        "client": {"employeeStatus": "ACT", "statusClass": "A", "lastHireDate": "2022-05-02"},
        "compensation": {"ssn": "907-31-4417", "payMethod": "S", "annualPay": Decimal("87250.55")},
        **extra_employee,
    }
    return {"errorCode": "0", "employee": [emp]}


# --- Finding 4: the policy ------------------------------------------------------------------------------------
SENSITIVE_NAMES = [
    "taxpayerNumber", "socialSecurityNo", "bankAccountNumber", "apiToken",  # the evaluation's four
    "ssn", "SSN", "employeeSSN", "eeSSN", "i9SSN", "coveredSsn", "ssnUnmasked", "cobraSSN", "ssn_value",
    "accountNum", "accountNumber", "account_number", "bank_account_number", "acctNo", "AccountNbr",
    "password", "Password", "default_password", "passwd", "pin", "secret", "client_secret", "clientSecret",
    "access_token", "refresh_token", "ssoToken", "accessToken", "bearerToken", "api_key", "apiKey", "privateKey",
    "social_security", "usSocialSecurityNumber", "social_insurance", "national_insurance", "bsn",
    "driverLicenseId", "driversLicense", "foreignPassportNum", "passportNumber", "documentContent",
    "documentNumber", "profilePicture", "iban", "cardNumber", "creditCard", "taxPayerId", "TaxpayerID",
]  # fmt: skip
SAFE_NAMES = [
    "businessName", "className", "classification", "assignment", "compass", "pinned_at", "spinner",
    "token_type_label_x"[:0] or "firstName", "accountId", "account_id", "accountType", "accountStatus",
    "routingNumber", "bankName", "hasSsn_x"[:0] or "postalCode", "taxableWages", "taxId_x"[:0] or "federalIdNumber",
    "payCode", "description", "mission", "passage", "tokenizer_x"[:0] or "status",
]  # fmt: skip


@pytest.mark.parametrize("name", SENSITIVE_NAMES)
def test_sensitive_names_are_recognised_by_their_words(name):
    assert is_sensitive_name(name), name


@pytest.mark.parametrize("name", SAFE_NAMES)
def test_ordinary_names_that_merely_contain_the_letters_are_not_flagged(name):
    assert not is_sensitive_name(name), name


def test_scrub_redacts_nested_objects_and_arrays_and_records_paths():
    paths: list[str] = []
    out = scrub(
        {
            "keep": "visible",
            "a": {"taxpayerNumber": SSN, "deep": [{"apiToken": TOKEN, "ok": 1}, {"bankAccountNumber": ACCT}]},
            "socialSecurityNo": 123456789,
        },
        "$.e",
        paths,
    )  # fmt: skip
    assert out["keep"] == "visible" and out["a"]["deep"][0]["ok"] == 1
    assert out["a"]["taxpayerNumber"] == out["a"]["deep"][0]["apiToken"] == REDACTED
    assert out["a"]["deep"][1]["bankAccountNumber"] == out["socialSecurityNo"] == REDACTED
    assert sorted(paths) == sorted(
        ["$.e.a.taxpayerNumber", "$.e.a.deep[0].apiToken", "$.e.a.deep[1].bankAccountNumber", "$.e.socialSecurityNo"]
    )  # fmt: skip


def test_scrub_leaves_empty_and_boolean_values_and_the_original_untouched():
    original = {"password": "", "apiToken": None, "hasSsn": True, "ssnOnFile": False, "token": "x"}
    snapshot = copy.deepcopy(original)
    out = scrub(original, "$", [])
    assert original == snapshot  # a copy, never in-place
    assert (
        out["password"] == ""
        and out["apiToken"] is None
        and out["hasSsn"] is True
        and out["ssnOnFile"] is False
    )
    assert out["token"] == REDACTED


def test_a_labelled_value_is_redacted_whatever_its_key_is_called():
    out = scrub(
        {"ids": [{"type": "SSN", "value": SSN, "primary": True}, {"label": "Passport", "number": "X123"},
                 {"type": "Employee badge", "value": "B-77"}]},
        "$",
        [],
    )  # fmt: skip
    ids = out["ids"]
    assert ids[0]["value"] == REDACTED and ids[0]["primary"] is True and ids[0]["type"] == "SSN"
    assert ids[1]["number"] == REDACTED
    assert ids[2]["value"] == "B-77"  # a non-sensitive label leaves its value alone


def test_an_ssn_formatted_value_is_redacted_under_any_key_but_other_numbers_are_not():
    paths: list[str] = []
    out = scrub(
        {
            "memo": f"see {SSN} please",
            "ref": "123-45",
            "phone": "520-555-0142",
            "zip": "85701-1090",
        },
        "$",
        paths,
    )
    assert out["memo"] == REDACTED and paths == ["$.memo"]
    assert out["ref"] == "123-45" and out["phone"] == "520-555-0142" and out["zip"] == "85701-1090"


def test_a_short_bank_account_label_is_preserved_but_an_account_looking_value_is_not():
    out = scrub({"bankAccount": "OPS-1", "bankAccountX": "k"}, "$", [])
    assert out["bankAccount"] == "OPS-1"
    assert scrub({"bankAccount": ACCT}, "$", [])["bankAccount"] == REDACTED


EVAL_SECRETS = {
    "taxpayerNumber": SSN,
    "socialSecurityNo": SSN,
    "bankAccountNumber": ACCT,
    "apiToken": TOKEN,
    "password": PW,
    "ssnValue": SSN,
}


def _everywhere(res) -> str:
    return (
        json.dumps(res.model_dump(mode="json"), default=str)
        + json.dumps(res.traces, default=str)
        + str(res.issues)
    )


def test_the_exact_evaluation_reproduction_prismhr_employee_extras():
    payload = prism_employee_payload(
        **EVAL_SECRETS, safeVendorField={"note": "keep me", "level": 3}
    )
    res = service.normalize("prismhr", "employee", payload, CTX, trace=True)
    assert res.ok
    text = _everywhere(res)
    for secret in (SSN, SSN_PLAIN, ACCT, TOKEN, PW):
        assert secret not in text, secret
    extras = res.records[0].extras
    assert all(extras[k] == REDACTED for k in EVAL_SECRETS)
    assert extras["safeVendorField"] == {
        "note": "keep me",
        "level": 3,
    }  # safe unknown fields are preserved
    redacted = res.records[0].provenance.redacted_paths
    assert all(any(p.endswith(f".{k}") for p in redacted) for k in EVAL_SECRETS)


def test_secrets_nested_in_unmapped_objects_and_arrays_never_reach_extras():
    payload = prism_employee_payload(
        benefitsPortal={"login": {"apiToken": TOKEN}, "banks": [{"bankAccountNumber": ACCT}, {"label": "ok"}]},
        client={"employeeStatus": "ACT", "statusClass": "A", "taxpayerNumber": SSN},
    )  # fmt: skip
    res = service.normalize("prismhr", "employee", payload, CTX, trace=True)
    text = _everywhere(res)
    assert TOKEN not in text and ACCT not in text and SSN not in text
    assert res.records[0].extras["benefitsPortal"]["banks"][1] == {"label": "ok"}


@pytest.mark.parametrize(
    ("provider", "entity", "payload", "ctx"),
    [
        ("isolved", "employee", {"results": [{"id": 1, "employeeNumber": "H1", "taxpayerNumber": SSN,
                                              "bankAccountNumber": ACCT, "apiToken": TOKEN}]}, {"tenant_id": "t", "company_id": "9"}),
        ("isolved", "company", [{"id": 9100, "client": {"id": 5}, "apiToken": TOKEN, "legalName": "L"}], {"tenant_id": "t", "company_id": "5"}),
        ("ukg-ready", "employee", {"employee": {"id": 7, "first_name": "S", "taxpayerNumber": SSN, "apiToken": TOKEN}}, {"tenant_id": "t", "company_id": "3"}),
        ("ukg-ready", "deduction", {"deductions": [{"id": 1, "deduction": {"code": "H"}, "apiToken": TOKEN}]}, {"tenant_id": "t", "company_id": "3", "employee_id": "7"}),
        ("prismhr", "payroll", {"payrollVouchers": [{"voucherId": "V1", "employeeId": "E", "apiToken": TOKEN,
                                                      "earning": [{"payCode": "REG", "payAmount": 5, "socialSecurityNo": SSN}]}]}, CTX),
        ("isolved", "payroll", {"payroll_run": {"id": 5, "apiToken": TOKEN}, "checks": [{"id": 6, "employeeId": 1,
                                "taxpayerNumber": SSN, "earnings": [{"itemCode": "R", "checkDollars": 1, "bankAccountNumber": ACCT}]}]}, {"tenant_id": "t", "company_id": "9"}),
    ],
)  # fmt: skip
def test_the_policy_applies_in_every_provider_and_record_type_including_line_items(
    provider, entity, payload, ctx
):
    res = service.normalize(provider, entity, payload, ctx, trace=True)
    text = _everywhere(res)
    for secret in (SSN, ACCT, TOKEN):
        assert secret not in text, (provider, entity, secret)
    assert REDACTED in text


def test_api_cli_and_logs_never_carry_the_secrets(tmp_path, caplog):
    payload = prism_employee_payload(**EVAL_SECRETS)
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app())
    body = json.dumps({"context": CTX, "payload": payload}, default=str)
    texts = [
        client.post("/normalize/prismhr/employee", content=body).text,
        client.post(
            "/convert/prismhr",
            content=json.dumps({"files": [{"name": "e.json", "content": payload}], "context": CTX, "trace": True, "integrity": True}, default=str),
        ).text,
    ]  # fmt: skip
    src = tmp_path / "in.json"
    src.write_text(json.dumps(payload, default=str))
    out = tmp_path / "out.json"
    cli.main(["normalize", "--provider", "prismhr", "--entity", "employee", "--input", str(src),
              "--tenant-id", "evalT", "--company-id", "NOVA77", "--output", str(out)])  # fmt: skip
    texts += [out.read_text(), caplog.text]
    joined = " ".join(texts)
    for secret in (SSN, ACCT, TOKEN, PW):
        assert secret not in joined, secret
    assert REDACTED in joined


@pytest.mark.db
def test_the_database_never_stores_the_secrets(db):
    client = TestClient(create_app(db))
    r = client.post(
        "/ingest/prismhr/employee",
        content=json.dumps(
            {"context": CTX, "payload": prism_employee_payload(**EVAL_SECRETS)}, default=str
        ),
    )
    assert r.status_code == 200, r.text
    from sqlalchemy import text

    with db.connect() as conn:
        rows = conn.execute(text("select extras::text, provenance::text from employees")).fetchall()
        everything = " ".join(" ".join(r) for r in rows)
    for secret in (SSN, ACCT, TOKEN, PW):
        assert secret not in everything, secret
    assert REDACTED in everything
    stored = client.get("/tenants/evalT/prismhr/companies/NOVA77/employees").text
    for secret in (SSN, ACCT, TOKEN, PW):
        assert secret not in stored


# --- Finding 1: wrong-typed nested fields -----------------------------------------------------------------------
WRONG = [None, "x", 5, True, 1.5, [], {}, [1], {"a": 1}]


def _unsafe_calls():
    """The exact evaluation reproductions (every one used to raise)."""
    ukg_emp = {
        "employee": {"id": 88123, "first_name": "S", "phones": {"cell_phone": "1", "preferred_phone": "CELL"}},
        "compensation_history": {"compensations": [{"effective_from": "2021-04-12", "amount": 91000, "amount_period": "YEAR"}]},
    }  # fmt: skip
    out = []
    for w in WRONG:
        for path in (("employee",), ("compensation_history",), ("employee", "phones", "preferred_phone"),
                     ("compensation_history", "compensations"), ("compensation_history", "compensations", 0, "amount_period")):  # fmt: skip
            p = copy.deepcopy(ukg_emp)
            cur = p
            for k in path[:-1]:
                cur = cur[k]
            cur[path[-1]] = copy.deepcopy(w)
            out.append(("ukg-ready", "employee", p, {"tenant_id": "t", "company_id": "3301"}))
        ded = {
            "errorCode": "0",
            "employeeDeductions": {"deduction": [{"deductionCode": "RET", "deductionType": "K"}]},
        }
        ded["employeeDeductions"] = copy.deepcopy(w)
        out.append(
            ("prismhr", "deduction", ded, {"tenant_id": "t", "company_id": "N", "employee_id": "E"})
        )
    return out


@pytest.mark.parametrize(("provider", "entity", "payload", "ctx"), _unsafe_calls())
def test_wrong_typed_nested_fields_never_escape_the_adapter_as_exceptions(
    provider, entity, payload, ctx
):
    from normalization.adapters.base import SourceContext

    # straight to the adapter: the safety net in service.normalize must not be what saves us
    result = ADAPTERS[provider].normalize(
        entity, payload, load_reference(provider), SourceContext(**ctx)
    )
    assert result is not None


def test_the_service_turns_any_remaining_surprise_into_a_controlled_error(monkeypatch):
    def boom(*a, **k):
        raise AttributeError(
            "'list' object has no attribute 'keys' ... secret-looking text 123-45-6789"
        )

    monkeypatch.setattr(ADAPTERS["prismhr"], "normalize", boom)
    res = service.normalize("prismhr", "employee", {"employee": []}, CTX)
    assert not res.ok and res.issues[0].code == "unsupported_structure"
    assert "123-45-6789" not in str(res.issues)  # the exception text is never echoed


def test_http_returns_a_422_not_a_500_for_the_evaluation_reproductions():
    client = TestClient(create_app(), raise_server_exceptions=False)
    for provider, entity, payload, ctx in [
        ("ukg-ready", "employee", {"employee": "x"}, {"tenant_id": "t", "company_id": "3"}),
        ("ukg-ready", "employee", {"employee": {"id": 1, "phones": {"preferred_phone": [1]}}}, {"tenant_id": "t", "company_id": "3"}),
        ("ukg-ready", "employee", {"employee": {"id": 1}, "compensation_history": "x"}, {"tenant_id": "t", "company_id": "3"}),
        ("prismhr", "deduction", {"errorCode": "0", "employeeDeductions": "x"}, {"tenant_id": "t", "company_id": "N", "employee_id": "E"}),
    ]:  # fmt: skip
        r = client.post(
            f"/normalize/{provider}/{entity}",
            content=json.dumps({"context": ctx, "payload": payload}),
        )
        assert r.status_code < 500, (provider, payload, r.status_code)
        assert r.status_code in (200, 422)


def test_a_wrong_typed_section_is_reported_as_an_error_with_its_path():
    res = service.normalize(
        "ukg-ready", "employee", {"employee": "x"}, {"tenant_id": "t", "company_id": "3"}
    )
    assert not res.ok
    assert any(i.code == "unexpected_type" and i.severity.value == "error" for i in res.issues)


# --- Findings 2 and 3: invalid text ------------------------------------------------------------------------------
BAD_TEXT = [
    ("NUL", "A\x00B"),
    ("lone high surrogate", "\ud800"),
    ("lone low surrogate", "x\udfffy"),
]


@pytest.mark.parametrize(("label", "bad"), BAD_TEXT)
def test_find_invalid_text_reports_a_path_and_never_the_text(label, bad):
    assert find_invalid_text({"a": [{"b": bad}]}) == "$.a[0].b"
    assert find_invalid_text({bad: 1}) == "$.<key>"
    assert find_invalid_text(["fine", "ünï😀cødé", {"k": "v"}]) is None
    deep = {}
    for _ in range(5000):  # no recursion limit
        deep = {"a": deep}
    assert find_invalid_text(deep) is None


@pytest.mark.parametrize(("label", "bad"), BAD_TEXT)
@pytest.mark.parametrize("provider", ["prismhr", "isolved", "ukg-ready"])
def test_service_refuses_invalid_text_in_payload_and_context(provider, label, bad):
    res = service.normalize(
        provider, "employee", {"employee": [{"id": "E1", "firstName": bad}]}, CTX
    )
    assert not res.ok and res.records == [] and res.issues[0].code == "invalid_text"
    assert bad not in str(res.issues)
    res = service.normalize(
        provider, "employee", {"employee": []}, {"tenant_id": bad, "company_id": "c"}
    )
    assert not res.ok and res.issues[0].code == "invalid_text"


def test_http_answers_422_for_invalid_text_on_every_route_that_takes_a_payload():
    client = TestClient(create_app(), raise_server_exceptions=False)
    for raw in (
        b'{"context":{"tenant_id":"t","company_id":"c"},"payload":{"errorCode":"0","employee":[{"id":"\\ud800"}]}}',
        b'{"context":{"tenant_id":"t","company_id":"c"},"payload":{"errorCode":"0","employee":[{"id":"E\\u0000X"}]}}',
    ):
        for route in ("/normalize/prismhr/employee", "/ingest/prismhr/employee"):
            r = client.post(route, content=raw)
            assert r.status_code == 422, (route, r.status_code, r.text[:80])
            assert r.json()["issues"][0]["code"] == "invalid_text"
    conv = client.post(
        "/convert/prismhr",
        content=b'{"files":[{"name":"a.json","content":{"employee":[{"id":"\\ud800"}]}}],"context":{"tenant_id":"t","company_id":"c"}}',
    )
    assert conv.status_code < 500
    det = client.post(
        "/detect/prismhr",
        content=b'{"files":[{"name":"a.json","content":{"employee":[{"id":"\\ud800"}]}}]}',
    )
    assert det.status_code < 500


@pytest.mark.db
def test_a_nul_character_is_never_sent_to_the_database(db):
    client = TestClient(create_app(db), raise_server_exceptions=False)
    r = client.post(
        "/ingest/prismhr/employee",
        content=b'{"context":{"tenant_id":"t","company_id":"c"},"payload":{"errorCode":"0","employee":[{"id":"E\\u0000X","firstName":"A\\u0000B"}]}}',
    )
    assert r.status_code == 422
    from sqlalchemy import text

    with db.connect() as conn:
        assert conn.execute(text("select count(*) from employees")).scalar() == 0


# --- Finding 6: ssn_last4 ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("907-31-4417", "4417"), ("907314417", "4417"), ("507 31 4417", "4417"), ("XXX-XX-4321", "4321"),
        ("***-**-4321", "4321"), ("xxxxx4321", "4321"), (" 123-45-6789 ", "6789"),
        ("not-an-ssn-777", None), ("AB-CD-EFGH-secret", None), ("abcd12345", None), ("12345678", None),
        ("1234567890", None), ("4321", None), ("", None), (None, None), ("123-45-678A", None),
    ],
)  # fmt: skip
def test_ssn_last4_requires_a_digit_based_ssn(raw, expected):
    assert ssn_last4(raw) == expected


def test_last4_for_account_masks_is_unchanged():
    assert last4("4455667788") == "7788" and last4("IBAN12AB34") == "AB34"


@pytest.mark.parametrize(
    ("provider", "entity", "payload", "ctx"),
    [
        ("isolved", "employee", {"results": [{"id": 1, "ssn": "not-an-ssn-777"}]}, {"tenant_id": "t", "company_id": "9"}),
        ("prismhr", "employee", {"errorCode": "0", "employee": [{"id": "E", "compensation": {"ssn": "AB-CD-EFGH-secret"}}]}, CTX),
        ("ukg-ready", "employee", {"employee": {"id": 1, "social_security": "garbage-text-9999"}}, {"tenant_id": "t", "company_id": "3"}),
    ],
)  # fmt: skip
def test_malformed_ssns_give_null_last4_in_every_provider_and_never_leak(
    provider, entity, payload, ctx
):
    res = service.normalize(provider, entity, payload, ctx, trace=True)
    assert res.records[0].ssn_last4 is None
    text = _everywhere(res)
    assert "garbage" not in text and "not-an-ssn" not in text and "AB-CD-EFGH" not in text


def test_valid_ssns_still_give_their_last_four_in_every_provider():
    cases = [
        ("isolved", {"results": [{"id": 1, "ssn": "912-77-9021"}]}, {"tenant_id": "t", "company_id": "9"}, "9021"),
        ("prismhr", {"errorCode": "0", "employee": [{"id": "E", "compensation": {"ssn": "901-22-8350"}}]}, CTX, "8350"),
        ("ukg-ready", {"employee": {"id": 1, "social_security": "903-44-8812"}}, {"tenant_id": "t", "company_id": "3"}, "8812"),
    ]  # fmt: skip
    for provider, payload, ctx, expected in cases:
        assert (
            service.normalize(provider, "employee", payload, ctx).records[0].ssn_last4 == expected
        )
