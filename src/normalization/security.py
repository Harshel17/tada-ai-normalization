"""Validation of the secret used to hash bank account numbers (ACCOUNT_HASH_SALT).

The salt protects short account numbers: without it (or with a guessable one) the stored
`account_number_hash` could be brute-forced. Messages here never contain the salt itself.
"""

MIN_SALT_LENGTH = 32  # characters; `secrets.token_hex(32)` gives 64 (256 bits)
KNOWN_INSECURE = frozenset(
    {"change-me", "changeme", "dev-only-salt", "salt", "secret", "password", "test", "example"}
)


class ConfigurationError(RuntimeError):
    """The deployment is misconfigured (a server-side problem, not bad input data)."""


def salt_problem(salt: str | None) -> str | None:
    """Why the salt is unacceptable outside tests, or None if it is fine. Never echoes the salt."""
    if not salt or not salt.strip():
        return "is not set"
    if salt.strip().lower() in KNOWN_INSECURE:
        return "is a well-known placeholder value"
    if len(salt) < MIN_SALT_LENGTH:
        return f"is shorter than {MIN_SALT_LENGTH} characters"
    if len(set(salt)) < 8:
        return "has too little variety to be random"
    return None


SETUP_HINT = "Run `uv run python scripts/setup_env.py` to generate one in your local .env."
