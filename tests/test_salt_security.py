"""ACCOUNT_HASH_SALT configuration: no insecure fallback, safe errors, and the setup script."""

import importlib.util
import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from normalization import cli
from normalization.adapters.base import hash_account
from normalization.api.app import create_app
from normalization.security import ConfigurationError, salt_problem
from normalization.settings import Settings, get_settings

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures"
GOOD = "q7H3vK9xZ2mB5nC8wL1dR4tY6uJ0aE3gS5fP7hN9bV2cX4zM6kD8jF1lQ3rT5yW7"  # test-only, never a real secret


def load_script():
    spec = importlib.util.spec_from_file_location("setup_env", ROOT / "scripts" / "setup_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def configure(monkeypatch):
    """Set APP_ENV / ACCOUNT_HASH_SALT for one test (an empty string beats any value in a local .env)."""

    def apply(env: str = "production", salt: str = ""):
        monkeypatch.setenv("APP_ENV", env)
        monkeypatch.setenv("ACCOUNT_HASH_SALT", salt)
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


# --- validation ---------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("salt", "reason"),
    [
        (None, "not set"), ("", "not set"), ("   ", "not set"),
        ("change-me", "placeholder"), ("Change-Me", "placeholder"), ("dev-only-salt", "placeholder"),
        ("short-but-random-8Xk2", "shorter"), ("q7H3vK9xZ2mB5nC8wL1dR4tY6uJ0aE3", "shorter"),
        ("a" * 64, "variety"), ("ab" * 40, "variety"),
    ],
)  # fmt: skip
def test_unsafe_salts_are_rejected_with_a_reason_that_never_contains_the_salt(salt, reason):
    problem = salt_problem(salt)
    assert problem and reason in problem
    if salt and salt.strip():
        assert salt not in problem


def test_a_long_random_salt_is_accepted():
    assert salt_problem(GOOD) is None


# --- behaviour where account numbers are hashed ------------------------------------------------------------
def test_there_is_no_default_salt_and_production_is_the_default_mode(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ACCOUNT_HASH_SALT", raising=False)
    s = Settings(_env_file=None)
    assert s.account_hash_salt is None and s.app_env == "production"


@pytest.mark.parametrize("salt", ["", "change-me", "dev-only-salt", "tooshort", "a" * 64])
def test_production_refuses_to_hash_with_a_missing_or_weak_salt(configure, salt):
    configure("production", salt)
    with pytest.raises(ConfigurationError) as err:
        hash_account("t", "021000021", "7766554433")
    message = str(err.value)
    assert "ACCOUNT_HASH_SALT" in message and "setup_env.py" in message
    assert "7766554433" not in message and (not salt or salt not in message)


def test_production_hashes_with_a_strong_salt_and_the_salt_changes_the_hash(configure):
    configure("production", GOOD)
    a = hash_account("t", "021000021", "7766554433")
    assert a and len(a) == 64 and a == hash_account("t", "021000021", "7766554433")
    assert a != hash_account("t2", "021000021", "7766554433")
    configure("production", GOOD[::-1])
    assert hash_account("t", "021000021", "7766554433") != a  # a different secret, a different hash
    assert GOOD not in a


def test_only_test_mode_accepts_a_short_fixed_salt(configure):
    configure("test", "change-me")
    assert hash_account("t", "1", "2") is not None
    configure("test", "")
    with pytest.raises(ConfigurationError):
        hash_account("t", "1", "2")


def test_no_source_file_hardcodes_a_fallback_salt():
    offenders = [
        p.name
        for p in (ROOT / "src").rglob("*.py")
        if p.name != "security.py"
        and ("dev-only-salt" in p.read_text() or '"change-me"' in p.read_text())
    ]
    assert offenders == []


# --- API and CLI report a misconfiguration safely ----------------------------------------------------------
def deposit_request():
    payload = json.loads((FIX / "isolved" / "direct_deposits.json").read_text())
    return json.dumps(
        {
            "context": {"tenant_id": "t", "company_id": "4501", "employee_id": "9"},
            "payload": payload,
        }
    )


def test_the_api_answers_500_with_a_safe_message_and_other_entities_still_work(configure):
    configure("production", "change-me")
    client = TestClient(create_app())
    r = client.post("/normalize/isolved/direct_deposit", content=deposit_request())
    assert r.status_code == 500 and "ACCOUNT_HASH_SALT" in r.json()["detail"]
    assert "change-me" not in r.text and "000123456789" not in r.text
    company = (FIX / "isolved" / "legals.json").read_text()
    ok = client.post(
        "/normalize/isolved/company",
        content=json.dumps(
            {"context": {"tenant_id": "t", "company_id": "4501"}, "payload": json.loads(company)}
        ),
    )
    assert ok.status_code == 200  # no account number is hashed here, so no salt is needed


def test_the_cli_reports_a_misconfiguration_without_the_salt(configure, tmp_path, capsys):
    configure("production", "change-me")
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
            "9",
            "--output",
            str(out),
        ]
    )
    err = capsys.readouterr().err
    assert (
        code == cli.EXIT_USAGE
        and "ACCOUNT_HASH_SALT" in err
        and "change-me" not in err.replace("ACCOUNT_HASH_SALT", "")
    )
    assert not out.exists()


# --- scripts/setup_env.py -------------------------------------------------------------------------------------
def test_setup_env_generates_a_random_owner_only_salt_without_printing_it(tmp_path, capsys):
    mod = load_script()
    env, example = tmp_path / ".env", tmp_path / ".env.example"
    example.write_text("DATABASE_URL=postgresql://x\nACCOUNT_HASH_SALT=\n")
    assert mod.main(["--env-file", str(env), "--example", str(example)]) == 0
    shown = capsys.readouterr()
    content = env.read_text()
    salt = mod.read_salt(content.splitlines())
    assert len(salt) == 64 and int(salt, 16) >= 0  # 32 random bytes as hex
    assert salt not in shown.out + shown.err
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert "DATABASE_URL=postgresql://x" in content and salt_problem(salt) is None


def test_setup_env_keeps_a_secure_salt_replaces_placeholders_and_fixes_permissions(
    tmp_path, capsys
):
    mod = load_script()
    env = tmp_path / ".env"
    env.write_text(f"A=1\nACCOUNT_HASH_SALT={GOOD}\n")
    env.chmod(0o644)
    mod.main(["--env-file", str(env), "--example", str(tmp_path / "none")])
    assert mod.read_salt(env.read_text().splitlines()) == GOOD  # never rotated silently
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    env.write_text("A=1\nACCOUNT_HASH_SALT=change-me\nB=2\n")
    mod.main(["--env-file", str(env), "--example", str(tmp_path / "none")])
    text = env.read_text()
    assert "change-me" not in text and "A=1" in text and "B=2" in text
    assert salt_problem(mod.read_salt(text.splitlines())) is None
    assert GOOD not in capsys.readouterr().out


def test_setup_env_check_mode_changes_nothing_and_reports(tmp_path, capsys):
    mod = load_script()
    env = tmp_path / ".env"
    assert mod.main(["--check", "--env-file", str(env)]) == 1  # missing
    env.write_text("ACCOUNT_HASH_SALT=dev-only-salt\n")
    env.chmod(0o600)
    assert mod.main(["--check", "--env-file", str(env)]) == 1
    assert env.read_text() == "ACCOUNT_HASH_SALT=dev-only-salt\n"
    env.write_text(f"ACCOUNT_HASH_SALT={GOOD}\n")
    env.chmod(0o644)
    assert mod.main(["--check", "--env-file", str(env)]) == 1  # secure salt but world-readable
    env.chmod(0o600)
    assert mod.main(["--check", "--env-file", str(env)]) == 0
    assert GOOD not in capsys.readouterr().out + capsys.readouterr().err


def test_repository_hygiene_for_the_salt():
    example = (ROOT / ".env.example").read_text()
    assert (
        "ACCOUNT_HASH_SALT=\n" in example + "\n"
        and "change-me" not in example.split("ACCOUNT_HASH_SALT=")[1].splitlines()[0]
    )
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in ignored
