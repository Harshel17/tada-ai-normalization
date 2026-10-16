"""Regression guard: the canonical output of every fixture must not change by accident.

Instrumenting adapters (for field provenance) must not alter a single canonical value, issue or
extras entry. If a change is intended, regenerate with `scripts/snapshot_canonical.py --update`
and review the diff.
"""

import json
from pathlib import Path

import pytest

from conftest import FIXTURES
from scripts_loader import result_json  # noqa: I001
from support.scenarios import CASES

SNAP = Path(__file__).parent / "snapshots" / "canonical"


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_canonical_output_is_unchanged(case):
    expected = (SNAP / f"{case.id}.json").read_text()
    assert result_json(case) == expected


def test_every_fixture_file_is_covered_by_a_snapshot():
    used = {(c.folder, c.file) for c in CASES}
    on_disk = {(p.parent.name, p.name) for p in FIXTURES.glob("*/*.json")}
    assert on_disk == used
    assert len(used) == 14


def test_snapshots_hold_no_raw_secret():
    text = "".join(p.read_text() for p in SNAP.glob("*.json"))
    for secret in (
        "123-45-6789",
        "987-65-4321",
        "900-44-3333",
        "000123456789",
        "998877665544",
        "5550001111",
        "4400112233",
        "000999888777",
    ):
        assert secret not in text


def test_snapshots_are_valid_json_results():
    for p in SNAP.glob("*.json"):
        doc = json.loads(p.read_text())
        assert {"provider", "entity_type", "records", "issues"} <= doc.keys()
