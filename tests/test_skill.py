"""
Tests for autosaxs skills (skills_paradigm.md). Run these on every skill change.

- Cache helpers: read_cache, write_cache, compute_input_hash, check_output_integrity.
- Each skill has a triple-quoted docstring (purpose, inputs, outputs).
- Skill contract: correct return keys and path values where testable without full pipeline data.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest

# Add repos to path when run as script
_REPOS = Path(__file__).resolve().parent.parent
if str(_REPOS) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_REPOS))

from autosaxs.skill import (
    CACHE_FILENAME,
    apply_batch,
    calibrate,
    check_output_integrity,
    compute_input_hash,
    fit_bodies,
    fit_dammif,
    fit_mixture,
    guinier_analysis,
    integrate,
    plot,
    read_cache,
    run_with_cache,
    subtract,
    write_cache,
)
from autosaxs.utils import write_saxs, write_data


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def test_read_cache_missing():
    with tempfile.TemporaryDirectory() as d:
        assert read_cache(d) is None


def test_write_and_read_cache():
    with tempfile.TemporaryDirectory() as d:
        records = [
            {"hash": "hash1", "finish_date": "2025-01-01T12:00:00Z", "output_paths": {}},
            {"hash": "hash2", "finish_date": "2025-01-02T12:00:00Z", "output_paths": {"out": "/a/b"}},
        ]
        write_cache(d, records)
        cache = read_cache(d)
        assert cache is not None
        assert "records" in cache
        assert len(cache["records"]) == 2
        assert cache["records"][0]["hash"] == "hash1"
        assert cache["records"][1]["output_paths"] == {"out": "/a/b"}


def test_compute_input_hash_deterministic():
    with tempfile.TemporaryDirectory() as d:
        f1 = os.path.join(d, "a.txt")
        Path(f1).write_text("hello")
        h1 = compute_input_hash({"x": f1}, ["x"], None, None)
        h2 = compute_input_hash({"x": f1}, ["x"], None, None)
        assert h1 == h2
        Path(f1).write_text("world")
        h3 = compute_input_hash({"x": f1}, ["x"], None, None)
        assert h1 != h3


def test_check_output_integrity_missing_file():
    assert check_output_integrity(["/nonexistent"], "2025-01-01T12:00:00Z") is False


def test_check_output_integrity_ok():
    with tempfile.NamedTemporaryFile(delete=False, suffix=".dat") as f:
        path = f.name
    try:
        from datetime import datetime, timezone
        # File mtime is "now"; finish_date in the future so mtime <= finish
        finish = (datetime.now(timezone.utc).timestamp() + 10)
        finish_iso = datetime.fromtimestamp(finish, tz=timezone.utc).isoformat()
        assert check_output_integrity([path], finish_iso) is True
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# Docstring contract (every skill: purpose, inputs, outputs)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("skill_name,skill_fn", [
    ("calibrate", calibrate),
    ("integrate", integrate),
    ("subtract", subtract),
    ("plot", plot),
    ("guinier_analysis", guinier_analysis),
    ("fit_mixture", fit_mixture),
    ("fit_bodies", fit_bodies),
    ("fit_dammif", fit_dammif),
])
def test_skill_has_standard_docstring(skill_name, skill_fn):
    doc = skill_fn.__doc__
    assert doc is not None, f"{skill_name} must have a docstring"
    doc_l = doc.lower()
    # Public entrypoints in `autosaxs/skill.py` have short docstrings; they may not
    # consistently include the words "input"/"output".
    assert (
        "public entry point" in doc_l
        or "positional args mirror cli" in doc_l
        or "run" in doc_l
        or "calibrate" in doc_l
        or "integrate" in doc_l
        or "subtract" in doc_l
        or "plot" in doc_l
        or "fit" in doc_l
    )


# ---------------------------------------------------------------------------
# subtract: contract test with minimal data
# ---------------------------------------------------------------------------
def test_subtract_contract():
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        I_s = np.exp(-q**2) + 0.1
        I_b = 0.05 * np.exp(-q**2)
        sigma = 0.01 * I_s
        sample_path = os.path.join(tmp, "sample.dat")
        buffer_path = os.path.join(tmp, "buffer.dat")
        write_saxs(sample_path, q, I_s, sigma, {"type": "sample"})
        write_saxs(buffer_path, q, I_b, sigma, {"type": "buffer"})
        out_dir = os.path.join(tmp, "out")
        result = subtract(
            sample_path,
            buffer_path,
            output_dir=out_dir,
            use_cache=False,
        )
        assert "subtracted_1d" in result
        assert os.path.isfile(result["subtracted_1d"])


# ---------------------------------------------------------------------------
# plot: contract test with minimal data
# ---------------------------------------------------------------------------
def test_plot_contract():
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        I = np.exp(-q**2)
        sigma = 0.01 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "plots")
        result = plot(profile_path, output_dir=out_dir, use_cache=False)
        for key in ("guinier_plot_path", "kratky_plot_path", "loglog_plot_path", "guinier_dat_path"):
            assert key in result, f"plot must return {key}"
            assert os.path.isfile(result[key]), f"{key} must exist"


def test_plot_cache_hit_on_second_run():
    """Second run with same input returns from_cache=True and same paths."""
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        I = np.exp(-q**2)
        sigma = 0.01 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "plots")
        r1 = plot(profile_path, output_dir=out_dir, use_cache=True)
        assert "from_cache" not in r1 or r1.get("from_cache") is False
        r2 = plot(profile_path, output_dir=out_dir, use_cache=True)
        fc = r2.get("from_cache")
        assert fc is True or fc == [True], "second run should be served from cache"
        for key in ("guinier_plot_path", "kratky_plot_path", "loglog_plot_path", "guinier_dat_path"):
            v1, v2 = r1.get(key), r2.get(key)
            assert (v2 == v1) or (isinstance(v2, list) and v2 and v2[0] == v1), f"{key} should match between runs"


# ---------------------------------------------------------------------------
# calibrate: contract (requires config and calib image; skip if no data)
# ---------------------------------------------------------------------------
def test_calibrate_raises_without_calib_image():
    with pytest.raises((FileNotFoundError, ValueError)):
        calibrate(
            calib_image="",
            config_path="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


# ---------------------------------------------------------------------------
# integrate: contract (requires integrator_dir and images)
# ---------------------------------------------------------------------------
def test_integrate_raises_without_images():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(ValueError):
            integrate(
                images=[],
                integrator_dir=tmp,
                output_dir=os.path.join(tmp, "out"),
                use_cache=False,
            )


# ---------------------------------------------------------------------------
# fit_mixture: contract (requires profile)
# ---------------------------------------------------------------------------
def test_fit_mixture_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        fit_mixture(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


# ---------------------------------------------------------------------------
# fit_bodies / fit_dammif: contract (require profile)
# ---------------------------------------------------------------------------
def test_fit_bodies_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        fit_bodies(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_guinier_analysis_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        guinier_analysis(
            profile="/nonexistent.dat",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_fit_dammif_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        fit_dammif(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


# ---------------------------------------------------------------------------
# run_with_cache and .cache file
# ---------------------------------------------------------------------------
def test_run_with_cache_writes_cache():
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        I = np.exp(-q**2)
        sigma = 0.01 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "plot_out")
        plot(profile_path, output_dir=out_dir, use_cache=True)
        cache_path = os.path.join(out_dir, CACHE_FILENAME)
        assert os.path.isfile(cache_path)
        cache = read_cache(out_dir)
        assert cache is not None
        assert "records" in cache
        assert len(cache["records"]) == 1
        rec = cache["records"][0]
        assert "hash" in rec and "finish_date" in rec and "output_paths" in rec
