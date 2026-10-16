"""List every provenance destination the adapters trace, per record type, for the frontend's grouping tests.

    uv run python scripts/dump_trace_dests.py          # rewrite frontend/src/test/fixtures/trace-dests.json
    uv run python scripts/dump_trace_dests.py --check  # exit 1 if the committed file is out of date

Runs the 17 fixture scenarios with `trace=True` (pinned test-only salt) and records, for each record type, the
destination paths seen and the operations used. Only field names and operation names: no values.
"""

import argparse
import json
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

os.environ["APP_ENV"] = "test"
os.environ["ACCOUNT_HASH_SALT"] = "change-me"  # test-only value, as in tests/conftest.py
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from support.scenarios import CASES  # noqa: E402

from normalization import service  # noqa: E402

OUT = ROOT / "frontend" / "src" / "test" / "fixtures" / "trace-dests.json"


def collect() -> dict[str, dict[str, list[str]]]:
    seen: dict[str, dict[str, set[str]]] = {}
    for c in CASES:
        payload = json.loads(
            (ROOT / "tests" / "fixtures" / c.folder / c.file).read_text(), parse_float=Decimal
        )
        res = service.normalize(
            c.provider, c.entity, payload, {"tenant_id": "t", **c.context}, trace=True
        )
        for rec, steps in zip(res.records, res.traces, strict=True):
            kind = seen.setdefault(rec.record_type, {})
            for step in steps:
                dest = re.sub(
                    r"\[\d+\]", "[]", step["dest"]
                )  # list positions do not matter for grouping
                kind.setdefault(dest, set()).add(step["op"])
    return {k: {d: sorted(ops) for d, ops in sorted(v.items())} for k, v in sorted(seen.items())}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true")
    text = json.dumps(collect(), indent=2) + "\n"
    if ap.parse_args().check:
        return 0 if OUT.exists() and OUT.read_text() == text else 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
