"""Create a clean, freshly migrated database for the browser tests (never your development data).

uv run python frontend/e2e/prepare_db.py        (run from the repository root)
"""

import argparse
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
URL = os.environ.get(
    "E2E_DATABASE_URL",
    "postgresql+psycopg://tada:tada_dev_password@localhost:54329/tada_normalize_e2e",
)


def main() -> int:
    target = make_url(URL)
    if not (target.database or "").endswith("_e2e"):
        sys.exit(f"refusing to reset {target.database!r}: the e2e database name must end in _e2e")
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        if not conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}
        ).scalar():
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()
    engine = create_engine(target)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = argparse.Namespace(x=[f"url={URL}"])
    command.upgrade(cfg, "head")
    print(f"e2e database ready: {target.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
