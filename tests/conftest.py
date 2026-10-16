import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

# Tests never depend on a developer's real secret: APP_ENV=test is the only mode that accepts a short, fixed
# hash salt, and it is pinned here BEFORE the settings are read. The canonical snapshots were produced with it.
os.environ["APP_ENV"] = "test"
os.environ["ACCOUNT_HASH_SALT"] = "change-me"  # test-only value; not a secret

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(provider_dir: str, name: str):
    """Fixtures are read with Decimal floats, exactly like the CLI and API do."""
    with (FIXTURES / provider_dir / name).open(encoding="utf-8") as fh:
        return json.load(fh, parse_float=Decimal)


@pytest.fixture
def fixture():
    return load_fixture


@pytest.fixture
def prism_ctx():
    return {"tenant_id": "tenant-prism", "company_id": "ACME"}


@pytest.fixture
def isolved_ctx():
    return {"tenant_id": "tenant-isolved", "company_id": "4501", "parent_company_id": "77"}


@pytest.fixture
def ukg_ctx():
    return {"tenant_id": "tenant-ukg", "company_id": "CID1", "employee_id": "7770001"}


# --- PostgreSQL fixtures (tests marked `db`) ------------------------------------------------------
import argparse  # noqa: E402
import os  # noqa: E402

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

DEFAULT_TEST_URL = "postgresql+psycopg://tada:tada_dev_password@localhost:54329/tada_normalize_test"


def _ensure_database(url: str) -> None:
    """Create the test database if missing (connects to the server's maintenance DB)."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL)
    try:
        _ensure_database(url)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable ({type(exc).__name__}); run `docker compose up -d`")
    # Build the schema with the real Alembic migrations, so they are tested too.
    cfg = Config(str(FIXTURES.parent.parent / "alembic.ini"))
    cfg.cmd_opts = argparse.Namespace(x=[f"url={url}"])
    cfg.set_main_option("script_location", str(FIXTURES.parent.parent / "alembic"))
    eng = create_engine(url)
    with eng.begin() as conn:  # always start from nothing, whatever an earlier run left behind
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


TABLES = (
    "earning_lines, tax_lines, deduction_lines, benefit_contribution_lines, paycheck_payments, "
    "paychecks, payroll_runs, direct_deposit_instructions, deduction_setups, employees, "
    "companies, ingest_runs"
)


@pytest.fixture
def db(engine):
    """A clean database for each test."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    return engine
