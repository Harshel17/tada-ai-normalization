"""Write deterministic synthetic provider payloads, generated from the contracts in /contracts.

    uv run python scripts/generate_fixtures.py [--seed 20260101] [--out tests/fixtures/generated]

The same seed always produces byte-identical files. These payloads fill every documented field,
so they are stress inputs (odd codes, unparseable dates in free-text fields) rather than realistic
records; the hand-written fixtures next to them are the realistic ones.
"""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from support.schema_gen import cases  # noqa: E402


def dump(value) -> str:
    return json.dumps(
        value, indent=2, default=lambda o: float(o) if isinstance(o, Decimal) else str(o)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--out", type=Path, default=ROOT / "tests/fixtures/generated")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, (_provider, _entity, _ctx, build) in cases(args.seed).items():
        (args.out / f"{name}.json").write_text(dump(build()) + "\n", encoding="utf-8")
        print(f"wrote {name}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
