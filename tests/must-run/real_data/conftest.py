"""Shared fixtures for commit-gate real-data tests."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_hp = Path(__file__).resolve().parent / "_helpers.py"
_spec = importlib.util.spec_from_file_location("autosaxs_real_data_helpers", _hp)
assert _spec and _spec.loader
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)


@pytest.fixture(scope="module", autouse=True)
def _require_validation_dir_fixture():
    H.require_validation_dir()
