from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from the environment / a local .env file. Nothing here has a production default."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tada:tada_dev_password@localhost:54329/tada_normalize"
    # APP_ENV: "production" (the safe default) or "test"; only "test" accepts a short, fixed salt.
    app_env: str = "production"
    # No default: a missing or placeholder salt is an error where account numbers are hashed.
    # Generate a local one with `uv run python scripts/setup_env.py`.
    account_hash_salt: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
