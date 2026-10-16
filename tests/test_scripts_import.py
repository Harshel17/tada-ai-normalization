"""The documented helper scripts must keep working when the code they import changes."""

import sys

from scripts_loader import load_script


def test_the_coverage_report_script_runs_and_reports_all_three_providers(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["coverage_report.py", "--no-probe"])
    module = load_script("coverage_report")
    module.main()
    out = capsys.readouterr().out
    for provider in ("PrismHR", "isolved", "UKG Ready"):
        assert provider in out
    assert "EMPLOYEE COVERAGE" in out and "WHAT REMAINS IN extras" in out
