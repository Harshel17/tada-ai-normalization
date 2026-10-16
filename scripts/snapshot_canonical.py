"""Write (or check) the canonical-output snapshots in tests/snapshots/canonical/.

    uv run python scripts/snapshot_canonical.py            # check: exit 1 if any output changed
    uv run python scripts/snapshot_canonical.py --update   # rewrite the snapshots (an intentional change)

The snapshots are the full NormalizationResult of every fixture run, so a change to an adapter that
alters even one canonical value, issue or `extras` entry shows up as a diff.
"""

import argparse
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

# The snapshots hold account hashes, so they are produced with a pinned TEST-ONLY salt (never your real one).
os.environ["APP_ENV"] = "test"
os.environ["ACCOUNT_HASH_SALT"] = "change-me"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from support.scenarios import CASES  # noqa: E402

from normalization import service  # noqa: E402

SNAP = ROOT / "tests" / "snapshots" / "canonical"


def result_json(case) -> str:
    text = (ROOT / "tests" / "fixtures" / case.folder / case.file).read_text()
    payload = json.loads(text, parse_float=Decimal)
    result = service.normalize(
        case.provider, case.entity, payload, {"tenant_id": "snapshot", **case.context}
    )
    return json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()
    SNAP.mkdir(parents=True, exist_ok=True)
    changed = []
    for case in CASES:
        path = SNAP / f"{case.id}.json"
        new = result_json(case)
        if args.update:
            path.write_text(new)
        elif not path.exists() or path.read_text() != new:
            changed.append(case.id)
    if changed:
        print("canonical output changed for:", ", ".join(changed))
        return 1
    print(f"{'updated' if args.update else 'verified'} {len(CASES)} snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
