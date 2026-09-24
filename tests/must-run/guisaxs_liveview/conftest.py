"""Liveview commit-gate fixtures."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_lib_path = Path(__file__).resolve().parent / "_lib.py"
_spec = importlib.util.spec_from_file_location("liveview_test_lib_conftest", _lib_path)
assert _spec and _spec.loader
_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lib)


@pytest.fixture(scope="module", autouse=True)
def _require_validation_dir_fixture():
    if not Path(_lib.VALIDATION_DIR).is_dir():
        raise FileNotFoundError(_lib._VALIDATION_MISSING_MSG)
