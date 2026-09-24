"""Smoke tests for ``autosaxs doctor``."""

from __future__ import annotations

from autosaxs.cli import main as cli_main
from autosaxs.cli.doctor import doctor, run_checks


def test_doctor_run_checks_returns_results():
    results = run_checks()
    assert results
    names = {r.name for r in results}
    assert "Python >= 3.10" in names
    assert "autosaxs" in names
    assert "pyFAI" in names
    assert any(n.startswith("ATSAS") for n in names)


def test_doctor_exit_code_zero_when_core_ok(capsys):
    rc = doctor()
    captured = capsys.readouterr()
    assert "autosaxs doctor" in captured.out
    assert "[OK]" in captured.out or "[XX]" in captured.out
    # In the project CI / dev env, core deps are installed.
    assert rc == 0


def test_cli_doctor_subcommand(capsys):
    rc = cli_main(["doctor"])
    captured = capsys.readouterr()
    assert "autosaxs doctor" in captured.out
    assert rc == 0
