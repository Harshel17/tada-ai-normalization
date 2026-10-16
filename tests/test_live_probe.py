"""The live probe's safety rules, tested offline with fake transports (no network, no credentials)."""

import json
import urllib.request
from pathlib import Path

import pytest

from scripts_loader import load_script

probe = load_script("live_probe")
FIX = Path(__file__).parent / "fixtures" / "prismhr"
SECRETS = ("123-45-6789", "987-65-4321", "000123456789", "998877665544", "5550001111", "4400112233")
KEY = "sk-test-DO-NOT-LEAK-123"


def test_key_comes_from_env_then_dotenv_and_is_required(tmp_path):
    assert probe.load_key({"DEMO_SIGNIN_API_KEY": f" {KEY} "}, tmp_path / ".env") == KEY
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"OTHER=1\nDEMO_SIGNIN_API_KEY='{KEY}'\n")
    assert probe.load_key({}, dotenv) == KEY
    with pytest.raises(probe.ProbeError) as err:
        probe.load_key({}, tmp_path / "missing")
    assert KEY not in str(err.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.prismhr.com/x",  # not https
        "https://evil.example.com",  # not documented
        "https://api.prismhr.com.evil.com",  # look-alike suffix
        "https://user:pw@api.prismhr.com",  # credentials in the URL
        "https://api.prismhr.com:8443",  # odd port
        "https://api.prismhr.com?x=1",  # query
        "", None, 42,
    ],
)  # fmt: skip
def test_untrusted_base_urls_are_refused(url):
    with pytest.raises(probe.ProbeError):
        probe.check_base_url(url, "prismhr")


def test_documented_hosts_and_explicit_allowances_are_accepted():
    assert probe.check_base_url(
        "https://api.prismhr.com/prismhr-api/services/rest/", "prismhr"
    ).endswith("/rest")
    assert probe.check_base_url("https://x.myisolved.com", "isolved")
    assert probe.check_base_url("https://tada-demo-provider-signin.onrender.com/prism", "prismhr")
    assert (
        probe.check_base_url("https://secure2.saashr.com/ta", "ukg-ready")
        == "https://secure2.saashr.com/ta"
    )
    with pytest.raises(probe.ProbeError):
        probe.check_base_url(
            "https://secure2.saashr.com", "prismhr"
        )  # the UKG host is not accepted for other providers
    with pytest.raises(probe.ProbeError):
        probe.check_base_url("https://ukg.example.net", "ukg-ready")
    assert probe.check_base_url("https://ukg.example.net", "ukg-ready", ("ukg.example.net",))


def test_redirects_are_never_followed():
    handler = probe._NoRedirect()
    assert (
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example.com") is None
    )


def test_session_is_get_only_and_pinned_to_its_host():
    seen = []

    def send(req):
        seen.append(req)
        return 200, b'{"ok": 1.50}'

    s = probe.Session("https://api.prismhr.com/rest", "sessionId", "tok", send)
    status, body = s.get("/a/b", {"clientId": "X"})
    assert status == 200 and str(body["ok"]) == "1.50"
    assert seen[0].get_method() == "GET" and seen[0].data is None
    bare = probe.Session("https://api.prismhr.com", "sessionId", "tok", send)
    for bad in (
        "@evil.example.com/x",
        ".evil.example.com",
        "/../x",
        "//evil.example.com",
        "/a?b=1",
        "x",
    ):
        with pytest.raises(probe.ProbeError):
            bare.get(bad)  # would change the host or smuggle a query


def test_signin_is_the_only_post_and_never_echoes_the_key():
    seen = []

    def send(req: urllib.request.Request):
        seen.append(req)
        return 401, b'{"error":"bad key"}'

    with pytest.raises(probe.ProbeError) as err:
        probe.signin("prismhr", KEY, send)
    assert "401" in str(err.value) and KEY not in str(err.value)
    assert seen[0].get_method() == "POST" and json.loads(seen[0].data) == {"tenant": "prism"}


def test_shape_reports_types_never_values():
    out = json.dumps(
        probe.shape({"ssn": "123-45-6789", "n": [{"name": "Ann", "amt": 5}], "z": None})
    )
    assert "123-45-6789" not in out and "Ann" not in out
    assert "str" in out and "list[1]" in out and "null" in out


def test_prism_run_against_fixtures_reports_structure_only_without_a_database():
    fixtures = {
        "/clientMaster/v1/getClientMaster": "client_master.json",
        "/employee/v1/getEmployee": "employee_get_employee.json",
        "/deductions/v1/getDeductions": "deductions_get_deductions.json",
        "/payroll/v1/getPayrollVouchers": "payroll_vouchers.json",
    }
    calls = []

    def send(req):
        calls.append(req)
        path = req.full_url.split("/rest", 1)[1].split("?")[0]
        if path == "/employee/v1/getEmployeeList":
            return 200, json.dumps(
                {"employeeList": [{"employeeId": "E1001"}, {"employeeId": "E1002"}]}
            ).encode()
        return 200, (FIX / fixtures[path]).read_bytes()

    s = probe.Session("https://api.prismhr.com/rest", "sessionId", "tok", send)
    report = probe.run_prism(s, "ACME")
    assert {c["call"] for c in report} >= {
        "getClientMaster",
        "getEmployeeList",
        "getPayrollVouchers",
        "getDeductions",
    }
    assert all(r.get_method() == "GET" for r in calls)
    employee = next(c for c in report if "documented option keywords" in c["call"])
    assert {r["label"].split("→")[1] for r in employee["results"]} == {
        "employee",
        "direct_deposit",
        "deduction",
    }
    assert employee["results"][0]["records"] == {"employee": 2}
    text = json.dumps(report)
    for secret in SECRETS:
        assert secret not in text
    assert "Jordan" not in text and "Rivera" not in text  # no personal values either


def test_employee_ids_are_read_from_a_list_under_employee_id():
    assert probe.ids_from({"employeeList": {"employeeId": ["A1", "A2", "A3", "A4"]}}, 3) == [
        "A1",
        "A2",
        "A3",
    ]
    assert probe.ids_from({"employeeList": [{"employeeId": "B1"}]}, 3) == ["B1"]
    assert probe.ids_from({"x": [True, None]}, 3) == []


def test_sensitive_audit_reports_key_names_and_classes_never_values():
    payload = {"a": {"accountNum": "99887766", "bankAccount": "", "ssn": "***-**-6789"}}
    out = probe.sensitive_audit(payload, "...99887766...")
    text = json.dumps(out)
    assert "99887766" not in text and "6789" not in text
    assert out["sensitive_keys"]["a.ssn"] == {"***-**-9999": 1}  # the shape, never the value
    assert out["raw_value_in_output"] == ["a.accountNum"]


def test_list_parameters_are_sent_as_repeated_query_parameters():
    seen = []
    s = probe.Session(
        "https://api.prismhr.com", "sessionId", "tok", lambda r: seen.append(r) or (200, b"{}")
    )
    s.get("/x", {"clientId": "C", "employeeId": ["A", "B"]})
    assert seen[0].full_url.endswith("?clientId=C&employeeId=A&employeeId=B")


def test_isolved_run_over_fixtures_is_get_only_and_reports_structure_only():
    fx = Path(__file__).parent / "fixtures" / "isolved"
    calls = []

    def send(req):
        calls.append(req)
        path = req.full_url.split("/rest", 1)[1].split("?")[0]
        if path.endswith("/legals"):
            return 200, (fx / "legals.json").read_bytes()
        if path.endswith("/employees"):
            return 200, (fx / "employees.json").read_bytes()
        if path.endswith("/directdeposits"):
            return 200, (fx / "direct_deposits.json").read_bytes()
        if path.endswith("/deductions"):
            return 401, b'{"message": "Authorization has been denied for this request."}'
        return 404, b""

    s = probe.Session("https://myisolved.com/rest", "Authorization", "bearer tok", send)
    report = probe.run_isolved(s, "10", "4501")
    assert all(r.get_method() == "GET" for r in calls)
    by = {c["call"]: c for c in report}
    assert by["legals"]["results"][0]["records"] == {"company": 1}
    assert by["employees (include flags)"]["employee_ids_found"] == 2
    assert (
        by["deductions employee#1"]["http"] == 401 and "results" not in by["deductions employee#1"]
    )
    text = json.dumps(report)
    assert "987-65-4321" not in text and "Alex" not in text and "bearer" not in text.lower()


def test_result_ids_reads_paged_results_and_bare_lists():
    assert probe.result_ids({"results": [{"id": 1}, {"id": "b"}, {"x": 1}]}, 5) == ["1", "b"]
    assert probe.result_ids([{"id": 7}], 5) == ["7"]
    assert probe.result_ids({"results": None}, 5) == []


def test_link_analysis_counts_unique_ambiguous_and_missing_matches_without_values():
    legals = [{"id": 4501, "payGroups": [{"id": 1}]}]
    runs = [
        {"id": 1, "runNumber": 5, "payrollRunType": "Regular", "periodBeginDate": "d1", "periodEndDate": "d2", "payDate": "d3"},
        {"id": 2, "runNumber": 5, "payrollRunType": "Regular", "periodBeginDate": "x1", "periodEndDate": "x2", "payDate": "x3"},
    ]  # fmt: skip
    checks = {"results": [
        {"payrollRunNumber": 5, "payrollRunType": "Regular", "periodBeginDate": "d1", "periodEndDate": "d2", "checkDate": "d3"},
        {"payrollRunNumber": 9, "payrollRunType": "Regular", "periodBeginDate": "z", "periodEndDate": "z", "checkDate": "z"},
    ]}  # fmt: skip

    def send(req):
        path = req.full_url.split("/rest", 1)[1].split("?")[0]
        if path.endswith("/legals"):
            return 200, json.dumps(legals).encode()
        if path.endswith("/payrollRunHistory"):
            return 200, json.dumps(runs).encode()
        return 200, json.dumps(checks).encode()

    s = probe.Session("https://myisolved.com/rest", "Authorization", "bearer tok", send)
    out = probe.link_analysis(s, "10", "4501", ["1"])
    assert (out["runs"], out["checks"]) == (2, 2)
    assert out["by_key"]["runNumber"]["checks_with_several_runs"] == 1  # two runs share number 5
    assert out["by_key"]["runNumber+type+period+payDate"]["checks_with_exactly_one_run"] == 1
    assert out["by_key"]["runNumber+type+period+payDate"]["checks_with_no_run"] == 1


def test_ukg_run_over_fixtures_assembles_the_bundle_and_stays_get_only():
    fx = Path(__file__).parent / "fixtures" / "ukg_ready"
    bundle = json.loads((fx / "employee_bundle.json").read_text())
    calls = []

    def send(req):
        calls.append(req)
        path = req.full_url.split("/ta", 1)[1].split("?")[0]
        if path.endswith("/lookup/eins"):
            return 200, (fx / "eins.json").read_bytes()
        if path.endswith("/employees"):
            return 200, json.dumps(
                {"employees": [{"id": 7770001, "first_name": "M", "last_name": "D"}]}
            ).encode()
        if path.endswith("/employees/7770001"):
            return 200, json.dumps(bundle["employee"]).encode()
        if path.endswith("/demographics"):
            return 200, json.dumps(bundle["demographics"]).encode()
        if path.endswith("/pay-info"):
            return 200, json.dumps(bundle["pay_info"]).encode()
        if path.endswith("/compensation/history"):
            return 200, json.dumps(bundle["compensation_history"]).encode()
        if path.endswith("/direct-deposits"):
            return 200, (fx / "direct_deposits.json").read_bytes()
        if path.endswith("/deductions"):
            return 200, (fx / "deductions.json").read_bytes()
        return 404, b""

    s = probe.Session("https://secure2.saashr.com/ta", "Authorization", "Bearer tok", send)
    report = probe.run_ukg(s, "CID1")
    assert all(r.get_method() == "GET" for r in calls)
    assembled = next(c for c in report if "ASSEMBLED bundle" in c["call"])
    assert "demographics" in assembled["call"] and assembled["results"][0]["records"] == {
        "employee": 1
    }
    text = json.dumps(report)
    for secret in ("900-44-3333", "Maria", "Bearer"):
        assert secret not in text


def test_sensitive_audit_follows_the_path_so_national_id_values_are_caught():
    payload = {"national_id_numbers": [{"type": "SSN", "value": "900443333"}]}
    out = probe.sensitive_audit(payload, "...900443333...")
    assert out["raw_value_in_output"] == ["national_id_numbers[].value"]


def test_only_documented_extra_headers_are_allowed_and_never_auth_headers():
    seen = []
    s = probe.Session(
        "https://secure2.saashr.com/ta",
        "Authorization",
        "Bearer tok",
        lambda r: seen.append(r) or (200, b"{}"),
    )
    s.get("/rest/x", None, {"X-End-of-Support-Timeline": "2026-01-29"})
    assert (
        seen[0].get_header("X-end-of-support-timeline") == "2026-01-29"
        and seen[0].get_header("Authorization") == "Bearer tok"
    )
    for bad in (
        {"Authorization": "Bearer evil"},
        {"Cookie": "x"},
        {"X-End-of-Support-Timeline": "x", "Host": "evil.example.com"},
    ):
        with pytest.raises(probe.ProbeError):
            s.get("/rest/x", None, bad)
