"""Create or repair the local .env: generate a random ACCOUNT_HASH_SALT and make the file owner-only.

    uv run python scripts/setup_env.py            # create .env from .env.example if missing, fix the salt
    uv run python scripts/setup_env.py --check    # only verify (exit 1 if the salt is missing or insecure)

The salt is never printed. An existing secure salt is kept (changing it changes the hash of every
account number processed afterwards; see DECISIONS.md, "Account hash salt"). A missing, empty or
placeholder salt is replaced with `secrets.token_hex(32)` (64 hex characters = 256 bits).
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from normalization.security import salt_problem  # noqa: E402

KEY = "ACCOUNT_HASH_SALT"


def read_salt(lines: list[str]) -> str | None:
    for line in lines:
        name, sep, value = line.strip().partition("=")
        if sep and name.strip() == KEY:
            return value.strip().strip("'\"")
    return None


def write_private(path: Path, text: str) -> None:
    """Write with owner-only permissions from the start (no window where it is world-readable)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    path.chmod(0o600)


def ensure(env_file: Path, example: Path) -> str:
    """Returns what happened, in words that never include the salt."""
    if not env_file.exists():
        env_file.write_text(example.read_text() if example.exists() else "", encoding="utf-8")
    lines = env_file.read_text(encoding="utf-8").splitlines()
    current = read_salt(lines)
    if salt_problem(current) is None:
        write_private(env_file, "\n".join(lines) + "\n")
        return "kept the existing secure salt"
    fresh = f"{KEY}={secrets.token_hex(32)}"
    if current is None:
        lines.append(fresh)
    else:
        lines = [fresh if ln.strip().partition("=")[0].strip() == KEY else ln for ln in lines]
    write_private(env_file, "\n".join(lines) + "\n")
    return "generated a new random salt"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--env-file", type=Path, default=ROOT / ".env")
    ap.add_argument("--example", type=Path, default=ROOT / ".env.example")
    ap.add_argument("--check", action="store_true", help="verify only; change nothing")
    args = ap.parse_args(argv)
    if args.check:
        if not args.env_file.exists():
            print(f"{args.env_file.name} does not exist", file=sys.stderr)
            return 1
        problem = salt_problem(read_salt(args.env_file.read_text(encoding="utf-8").splitlines()))
        mode = args.env_file.stat().st_mode & 0o077
        if problem or mode:
            print(
                f"{KEY} {problem or 'is fine'}; file permissions are {'too open' if mode else 'owner-only'}",
                file=sys.stderr,
            )
            return 1
        print(f"{KEY} is configured securely; {args.env_file.name} is owner-only")
        return 0
    print(
        f"{KEY}: {ensure(args.env_file, args.example)} ({args.env_file.name} is now owner-only; the value is not shown)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
