"""M6: HTTP API."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from normalization import service
from normalization.api import app as app_module
from normalization.api.app import create_app
from normalization.persistence import repository as repo
from normalization.persistence.db import transaction

PRISM_CTX = {"tenant_id": "tenant-prism", "company_id": "ACME"}


def post(client, path, payload, context=None, raw=None):
    body = (
        raw
        if raw is not None
        else json.dumps({"context": context or PRISM_CTX, "payload": payload})
    )
    return client.post(path, content=body, headers={"content-type": "application/json"})


def post_file(client, path, provider_dir, name, context=None):
    """Send a fixture file byte-for-byte (so numbers like 500.00 keep their written form)."""
    from conftest import FIXTURES

    text = (FIXTURES / provider_dir / name).read_text()
    body = f'{{"context": {json.dumps(context or PRISM_CTX)}, "payload": {text}}}'
    return client.post(path, content=body, headers={"content-type": "application/json"})


def raw_fixture(provider_dir, name):
    from conftest import FIXTURES

    return json.loads((FIXTURES / provider_dir / name).read_text())


@pytest.fixture
def client():
    # no database engine needed for normalize-only tests
    return TestClient(create_app(engine=create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none")))


class TestNormalizeEndpoint:
    def test_matches_the_service(self, client, fixture):
        r = post_file(
            client, "/normalize/prismhr/employee", "prismhr", "employee_get_employee.json"
        )
        assert r.status_code == 200
        body = r.json()
        expected = service.normalize(
            "prismhr", "employee", fixture("prismhr", "employee_get_employee.json"), PRISM_CTX
        )
        assert body["ok"] is True and body["counts"] == {"employee": 2}
        assert body["records"] == json.loads(expected.model_dump_json())["records"]

    def test_decimal_scale_survives_http_json(self, client):
        # "100000.00" must not become 100000.0 on the way in (floats would lose the scale)
        raw = (
            '{"context": {"tenant_id": "t", "company_id": "ACME"},'
            ' "payload": {"employee": [{"id": "E1", "compensation": {"annualPay": 100000.00,'
            ' "salary": 3846.150}}]}}'
        )
        body = post(client, "/normalize/prismhr/employee", None, raw=raw).json()
        comp = body["records"][0]["compensation"]
        assert comp["annual_salary"] == "100000.00" and comp["pay_period_amount"] == "3846.150"

    @pytest.mark.parametrize(
        ("provider", "entity", "d", "name", "ctx"),
        [
            (
                "isolved",
                "payroll",
                "isolved",
                "payroll.json",
                {"tenant_id": "t", "company_id": "4501"},
            ),
            ("ukg", "payroll", "ukg_ready", "payroll.json", {"tenant_id": "t", "company_id": "C"}),
        ],
    )
    def test_other_providers_and_aliases(self, client, provider, entity, d, name, ctx):
        r = post_file(client, f"/normalize/{provider}/{entity}", d, name, ctx)
        assert r.status_code == 200 and "paycheck" in r.json()["counts"]

    def test_unknown_provider_or_entity_is_404(self, client):
        assert post(client, "/normalize/workday/employee", {}).status_code == 404
        assert post(client, "/normalize/prismhr/benefits", {}).status_code == 404

    def test_unknown_provider_is_404_even_when_the_body_is_garbage(self, client):
        assert post(client, "/normalize/workday/employee", None, raw="{bad").status_code == 404
        assert post(client, "/ingest/prismhr/nonsense", None, raw="{}").status_code == 404

    def test_invalid_json_is_400(self, client):
        r = post(client, "/normalize/prismhr/employee", None, raw="{not json")
        assert r.status_code == 400 and "valid JSON" in r.json()["detail"]

    def test_bad_envelope_is_422(self, client):
        assert (
            post(client, "/normalize/prismhr/employee", None, raw='{"payload": {}}').status_code
            == 422
        )
        extra = json.dumps({"context": {"tenant_id": "t", "bogus": 1}, "payload": {}})
        assert post(client, "/normalize/prismhr/employee", None, raw=extra).status_code == 422

    def test_data_errors_return_422_with_issues_and_good_records(self, client):
        payload = raw_fixture("prismhr", "employee_get_employee.json")
        payload["employee"].append({"firstName": "No id"})
        r = post(client, "/normalize/prismhr/employee", payload)
        body = r.json()
        assert r.status_code == 422 and body["ok"] is False
        assert body["counts"] == {"employee": 2}
        assert "missing_id" in [i["code"] for i in body["issues"]]

    def test_missing_context_is_reported_as_an_issue(self, client):
        r = post(client, "/normalize/prismhr/employee", {"employee": []}, {"tenant_id": "t"})
        assert r.status_code == 422 and r.json()["issues"][0]["code"] == "missing_context"

    def test_oversized_body_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "MAX_BODY_BYTES", 10)
        assert post(client, "/normalize/prismhr/employee", {"employee": []}).status_code == 413

    def test_openapi_documents_the_request_body(self, client):
        spec = client.get("/openapi.json").json()
        body = spec["paths"]["/normalize/{provider}/{entity_type}"]["post"]["requestBody"]
        assert "context" in json.dumps(body)


class TestHealth:
    def test_health_reports_database_state(self, client):
        r = client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok", "database": "down"}

    @pytest.mark.db
    def test_health_with_database(self, engine):
        assert TestClient(create_app(engine)).get("/health").json() == {
            "status": "ok",
            "database": "up",
        }


@pytest.mark.db
class TestIngestAndRead:
    @pytest.fixture
    def api(self, db):
        return TestClient(create_app(db))

    def ingest(self, api, entity, d, name, ctx=None, provider="prismhr"):
        return post_file(api, f"/ingest/{provider}/{entity}", d, name, ctx)

    def load_prism(self, api):
        for entity, name in (
            ("company", "client_master.json"),
            ("employee", "employee_get_employee.json"),
            ("direct_deposit", "employee_get_employee.json"),
            ("deduction", "employee_get_employee.json"),
            ("payroll", "payroll_vouchers.json"),
        ):
            assert self.ingest(api, entity, "prismhr", name).status_code == 200

    def test_ingest_then_read_back(self, api):
        r = self.ingest(api, "employee", "prismhr", "employee_get_employee.json")
        assert r.status_code == 200
        assert r.json()["stored"]["employee"] == {"inserted": 2, "updated": 0, "unchanged": 0}
        base = "/tenants/tenant-prism/prismhr/companies/ACME"
        emp = api.get(f"{base}/employees/E1001").json()
        assert (
            emp["first_name"] == "Jordan" and emp["compensation"]["pay_period_amount"] == "3846.15"
        )
        assert emp["extras"] == {}  # raw source leftovers are never served
        assert "123-45-6789" not in json.dumps(emp)
        assert len(api.get(f"{base}/employees").json()) == 2

    def test_ingest_is_idempotent_over_http(self, api):
        self.ingest(api, "employee", "prismhr", "employee_get_employee.json")
        again = self.ingest(api, "employee", "prismhr", "employee_get_employee.json").json()
        assert again["stored"]["employee"] == {"inserted": 0, "updated": 0, "unchanged": 2}

    def test_all_read_routes(self, api):
        self.load_prism(api)
        base = "/tenants/tenant-prism/prismhr/companies/ACME"
        assert api.get("/tenants/tenant-prism/prismhr/companies").json()[0]["source_id"] == "ACME"
        assert api.get(f"{base}").json()["legal_name"] == "Acme Widgets, Inc."
        deposits = api.get(f"{base}/employees/E1001/direct-deposits").json()
        assert [d["allocation_type"] for d in deposits] == [
            "fixed_amount",
            "percentage",
            "remaining_balance",
        ]
        assert "000123456789" not in json.dumps(deposits)
        deds = api.get(f"{base}/employees/E1001/deductions").json()
        assert {d["deduction_code"] for d in deds} == {"PARK", "ADV"}
        runs = api.get(f"{base}/payroll-runs").json()
        assert [r["source_id"] for r in runs] == ["B-2025-02"]
        checks = api.get(f"{base}/payroll-runs/B-2025-02/paychecks").json()
        assert {c["source_id"] for c in checks} == {"V-5001", "V-5002"}
        one = api.get(f"{base}/paychecks/V-5001").json()
        assert one["net_pay"] == "2410.37" and len(one["taxes"]) == 4
        mine = api.get(f"{base}/employees/E1001/paychecks").json()
        assert [c["source_id"] for c in mine] == ["V-5001"]

    def test_other_tenants_see_nothing(self, api):
        self.load_prism(api)
        other = "/tenants/someone-else/prismhr/companies/ACME"
        assert api.get(f"{other}/employees").json() == []
        assert api.get(f"{other}/employees/E1001").status_code == 404
        assert api.get(f"{other}/paychecks/V-5001").status_code == 404
        assert api.get("/tenants/someone-else/prismhr/companies").json() == []

    def test_errors_are_never_stored(self, api, db):
        payload = raw_fixture("prismhr", "employee_get_employee.json")
        payload["employee"].append({"firstName": "No id"})
        r = post(api, "/ingest/prismhr/employee", payload)
        assert r.status_code == 422 and r.json()["stored"] == {}
        with transaction(db) as s:
            assert repo.list_employees(s, "prismhr", "tenant-prism", "ACME") == []

    def test_unknown_resources_are_404(self, api):
        assert api.get("/tenants/t/prismhr/companies/NOPE").status_code == 404
        assert api.get("/tenants/t/workday/companies").status_code == 404
        assert api.get("/tenants/t/prismhr/companies/ACME/employees?limit=0").status_code == 422

    def test_stats_report_real_counts_per_scope(self, api):
        empty = api.get("/stats").json()
        assert empty["scopes"] == []
        assert empty["providers"] == ["prismhr", "isolved", "ukg-ready"]
        assert "direct_deposit" in empty["entity_types"]
        self.load_prism(api)
        post_file(
            api,
            "/ingest/prismhr/employee",
            "prismhr",
            "employee_get_employee.json",
            {"tenant_id": "other", "company_id": "ACME"},
        )
        scopes = {(s["tenant_id"], s["provider"]): s for s in api.get("/stats").json()["scopes"]}
        mine = scopes[("tenant-prism", "prismhr")]
        assert mine["company_id"] == "ACME"
        assert (mine["companies"], mine["employees"], mine["direct_deposits"]) == (1, 2, 3)
        assert (mine["deductions"], mine["payroll_runs"], mine["paychecks"]) == (2, 1, 2)
        assert scopes[("other", "prismhr")]["employees"] == 2  # tenants are counted separately
        assert scopes[("other", "prismhr")]["paychecks"] == 0

    def test_stats_contain_counts_only(self, api):
        self.load_prism(api)
        text = api.get("/stats").text
        for leaked in ("Jordan", "Rivera", "6789", "ssn", "account"):
            assert leaked not in text

    def test_database_down_is_503(self):
        down = TestClient(create_app(create_engine("postgresql+psycopg://x:y@127.0.0.1:1/none")))
        r = post(
            down, "/ingest/prismhr/employee", raw_fixture("prismhr", "employee_get_employee.json")
        )
        assert r.status_code == 503 and r.json() == {"detail": "database unavailable"}
