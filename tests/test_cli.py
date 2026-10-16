"""M6: command line interface. Needs neither FastAPI nor PostgreSQL (except `ingest`)."""

import json
import subprocess
import sys

import pytest

from conftest import FIXTURES
from normalization.cli import main
from normalization.settings import get_settings

PRISM_ARGS = [
    "--provider", "prismhr", "--entity", "employee", "--tenant-id", "demo", "--company-id", "ACME",
]  # fmt: skip
EMPLOYEES = str(FIXTURES / "prismhr" / "employee_get_employee.json")


def run(tmp_path, *extra, name="out.json"):
    out = tmp_path / name
    code = main(["normalize", *PRISM_ARGS, "--input", EMPLOYEES, "--output", str(out), *extra])
    return code, out


def test_normalize_writes_canonical_json(tmp_path):
    code, out = run(tmp_path)
    doc = json.loads(out.read_text())
    assert code == 0 and doc["ok"] is True and doc["counts"] == {"employee": 2}
    first = doc["records"][0]
    assert first["record_type"] == "employee" and first["tenant_id"] == "demo"
    assert first["compensation"]["annual_salary"] == "100000.00"  # decimals stay exact strings


def test_does_not_overwrite_without_force(tmp_path, capsys):
    run(tmp_path)
    code, _ = run(tmp_path)
    assert code == 2 and "--force" in capsys.readouterr().err
    code, _ = run(tmp_path, "--force")
    assert code == 0


def test_context_can_come_from_a_file(tmp_path):
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"tenant_id": "from-file", "company_id": "ACME"}))
    out = tmp_path / "o.json"
    code = main(["normalize", "--provider", "prismhr", "--entity", "employee", "--input", EMPLOYEES,
                 "--context-file", str(ctx), "--output", str(out)])  # fmt: skip
    assert code == 0 and json.loads(out.read_text())["records"][0]["tenant_id"] == "from-file"


def test_missing_tenant_is_a_usage_error(tmp_path, capsys):
    out = tmp_path / "o.json"
    code = main(["normalize", "--provider", "prismhr", "--entity", "employee", "--input", EMPLOYEES,
                 "--output", str(out)])  # fmt: skip
    assert code == 2 and "tenant_id" in capsys.readouterr().err and not out.exists()


def test_bad_input_files_are_usage_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    out = tmp_path / "o.json"
    base = ["normalize", *PRISM_ARGS, "--output", str(out)]
    assert main([*base, "--input", str(bad)]) == 2
    assert main([*base, "--input", str(tmp_path / "missing.json")]) == 2
    assert not out.exists()


def test_data_errors_exit_1_but_still_write_the_issues(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"employee": [{"firstName": "no id"}]}))
    out = tmp_path / "o.json"
    code = main(["normalize", *PRISM_ARGS, "--input", str(src), "--output", str(out)])
    doc = json.loads(out.read_text())
    assert code == 1 and doc["ok"] is False and doc["issues"][0]["code"] == "missing_id"


def test_works_with_no_database_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    get_settings.cache_clear()
    try:
        code, out = run(tmp_path)
    finally:
        get_settings.cache_clear()
    assert code == 0 and out.exists()


def test_list_providers(capsys):
    assert main(["list-providers"]) == 0
    assert capsys.readouterr().out.split() == ["prismhr", "isolved", "ukg-ready"]


def test_module_entry_point(tmp_path):
    out = tmp_path / "o.json"
    proc = subprocess.run(
        [sys.executable, "-m", "normalization", "normalize", *PRISM_ARGS, "--input", EMPLOYEES,
         "--output", str(out)],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["counts"] == {"employee": 2}


@pytest.mark.db
def test_ingest_command_stores_and_is_idempotent(db, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        args = ["ingest", *PRISM_ARGS, "--input", EMPLOYEES]
        assert main(args) == 0
        assert json.loads(capsys.readouterr().out)["employee"]["inserted"] == 2
        assert main(args) == 0
        assert json.loads(capsys.readouterr().out)["employee"]["unchanged"] == 2
    finally:
        get_settings.cache_clear()
