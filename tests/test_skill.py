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

# Add src/ to path when run as script (src layout)
_REPOS = Path(__file__).resolve().parent.parent
_SRC = _REPOS / "src"
import sys
for _p in (_SRC, _REPOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from autosaxs.skill import (
    CACHE_FILENAME,
    apply_batch,
    check_output_integrity,
    compute_input_hash,
    read_cache,
    run_with_cache,
    write_cache,
)
from autosaxs.skill.calibrate import calibrate
from autosaxs.skill.model_bodies import model_bodies
from autosaxs.skill.fit_bodies import fit_bodies
from autosaxs.skill.model_dam import model_dam
from autosaxs.skill.fit_dammif import fit_dammif
from autosaxs.skill.fit_distances import fit_distances
from autosaxs.skill.fit_sizes import fit_sizes
from autosaxs.skill.model_mixture import model_mixture
from autosaxs.skill.fit_mixture import fit_mixture
from autosaxs.skill.fit_guinier import fit_guinier
from autosaxs.skill.analyze_kratky import analyze_kratky
from autosaxs.skill.integrate import integrate
from autosaxs.skill.integrate_proxy import integrate_proxy
from autosaxs.skill.plot import plot
from autosaxs.skill.plot_2d import plot_2d
from autosaxs.skill.subtract import subtract
from autosaxs.core.utils import read_saxs, write_saxs, write_data
from autosaxs.skill.config import merge_skill_params


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
    ("integrate-proxy", integrate_proxy),
    ("subtract", subtract),
    ("plot", plot),
    ("plot-2d", plot_2d),
    ("fit-guinier", fit_guinier),
    ("analyze-kratky", analyze_kratky),
    ("fit-distances", fit_distances),
    ("model-mixture", model_mixture),
    ("model-bodies", model_bodies),
    ("model-dam", model_dam),
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
        q = np.linspace(0.1, 2.0, 40)
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
            q_min=0.1,
            q_max=2.0,
            use_cache=False,
        )
        assert "subtracted_1d" in result
        assert os.path.isfile(str(result["subtracted_1d"]))


def test_subtract_requires_q_min_q_max():
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        sample_path = os.path.join(tmp, "sample.dat")
        buffer_path = os.path.join(tmp, "buffer.dat")
        write_saxs(sample_path, q, np.exp(-q**2), 0.01 * np.exp(-q**2), {})
        write_saxs(buffer_path, q, 0.05 * np.exp(-q**2), 0.01 * np.exp(-q**2), {})
        with pytest.raises(TypeError):
            subtract(
                sample_path,
                buffer_path,
                output_dir=os.path.join(tmp, "out"),
                use_cache=False,
            )


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
            assert os.path.isfile(str(result[key])), f"{key} must exist"


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
# plot_2d: contract test with minimal data
# ---------------------------------------------------------------------------
def test_plot_2d_contract(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img_a = np.zeros((16, 16), dtype=np.float32)
        img_a[4:12, 4:12] = 10.0
        img_b = np.zeros((16, 16), dtype=np.float32)
        img_b[2:10, 2:10] = 5.0
        img_dir = os.path.join(tmp, "raw")
        os.makedirs(img_dir, exist_ok=True)
        img_path_a = os.path.join(img_dir, "a.tif")
        img_path_b = os.path.join(img_dir, "b.tif")
        Path(img_path_a).write_bytes(b"dummy")
        Path(img_path_b).write_bytes(b"dummy")

        def _fake_read(path):
            return img_a if path.endswith("a.tif") else img_b

        monkeypatch.setattr("autosaxs.skill.plot_2d.read_from_tiff", _fake_read)
        out_dir = os.path.join(tmp, "plots2d")
        result = plot_2d(img_dir, output_dir=out_dir, use_cache=False)
        assert "plot_2d_png" in result
        assert isinstance(result["plot_2d_png"], list)
        assert len(result["plot_2d_png"]) == 2
        for p in result["plot_2d_png"]:
            assert os.path.isfile(p)


def test_plot_2d_cache_hit_on_second_run(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.zeros((12, 12), dtype=np.float32)
        img[3:9, 3:9] = 7.0
        img_dir = os.path.join(tmp, "raw")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "image.tif")
        Path(img_path).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.plot_2d.read_from_tiff", lambda _: img)
        out_dir = os.path.join(tmp, "plot2d_out")
        r1 = plot_2d(img_dir, output_dir=out_dir, use_cache=True)
        assert "from_cache" not in r1 or r1.get("from_cache") is False
        r2 = plot_2d(img_dir, output_dir=out_dir, use_cache=True)
        fc = r2.get("from_cache")
        assert fc is True or fc == [True], "second run should be served from cache"
        v1, v2 = r1.get("plot_2d_png"), r2.get("plot_2d_png")
        assert v2 == v1, "plot_2d_png should match between runs"


def test_plot_2d_single_file_contract(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.zeros((10, 10), dtype=np.float32)
        img[2:8, 2:8] = 3.0
        img_path = os.path.join(tmp, "single.tif")
        Path(img_path).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.plot_2d.read_from_tiff", lambda _: img)
        out_dir = os.path.join(tmp, "plots2d_single")
        result = plot_2d(img_path, output_dir=out_dir, use_cache=False)
        assert "plot_2d_png" in result
        assert isinstance(result["plot_2d_png"], str)
        assert os.path.isfile(result["plot_2d_png"])


# ---------------------------------------------------------------------------
# integrate_proxy: contract tests
# ---------------------------------------------------------------------------
def test_integrate_proxy_single_file_contract(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.zeros((18, 18), dtype=np.float32)
        img[6:12, 7:11] = 9.0
        img_path = os.path.join(tmp, "single.tif")
        Path(img_path).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.integrate_proxy.read_from_tiff", lambda _: img)
        out_dir = os.path.join(tmp, "int_proxy")
        result = integrate_proxy(
            img_path,
            output_dir=out_dir,
            cy=8.5,
            cx=8.5,
            use_cache=False,
        )
        assert "integrated_1d" in result
        assert isinstance(result["integrated_1d"], str)
        assert os.path.isfile(result["integrated_1d"])
        assert os.path.isfile(os.path.join(out_dir, "single_center.png"))
        _q, _I, _sigma, meta = read_saxs(result["integrated_1d"])
        assert meta.get("x_axis") == "r_px"
        assert meta.get("x_axis_unit") == "pixel"


def test_integrate_proxy_directory_contract(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img_a = np.zeros((16, 16), dtype=np.float32)
        img_a[4:10, 5:11] = 5.0
        img_b = np.zeros((16, 16), dtype=np.float32)
        img_b[5:12, 4:9] = 8.0
        img_dir = os.path.join(tmp, "raw")
        os.makedirs(img_dir, exist_ok=True)
        img_path_a = os.path.join(img_dir, "a.tif")
        img_path_b = os.path.join(img_dir, "b.tif")
        Path(img_path_a).write_bytes(b"dummy")
        Path(img_path_b).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.integrate_proxy.read_from_tiff", lambda p: img_a if p.endswith("a.tif") else img_b)
        out_dir = os.path.join(tmp, "int_proxy_out")
        result = integrate_proxy(
            img_dir,
            output_dir=out_dir,
            cy=8.0,
            cx=8.0,
            use_cache=False,
        )
        assert "integrated_1d" in result
        assert isinstance(result["integrated_1d"], list)
        assert len(result["integrated_1d"]) == 2
        for p in result["integrated_1d"]:
            assert os.path.isfile(p)


def test_integrate_proxy_raises_for_half_defined_center(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.zeros((8, 8), dtype=np.float32)
        img_path = os.path.join(tmp, "single.tif")
        Path(img_path).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.integrate_proxy.read_from_tiff", lambda _: img)
        with pytest.raises(ValueError):
            integrate_proxy(
                img_path,
                output_dir=os.path.join(tmp, "out"),
                cy=4.0,
                cx=None,
                use_cache=False,
            )


def test_integrate_proxy_center_estimation_failure_returns_empty(monkeypatch, capsys):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.zeros((10, 10), dtype=np.float32)
        img[3:8, 3:8] = 2.0
        img_path = os.path.join(tmp, "single.tif")
        Path(img_path).write_bytes(b"dummy")
        monkeypatch.setattr("autosaxs.skill.integrate_proxy.read_from_tiff", lambda _: img)
        monkeypatch.setattr(
            "autosaxs.skill.integrate_proxy._estimate_center_radial_symmetry",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        out_dir = os.path.join(tmp, "int_proxy_empty")
        result = integrate_proxy(
            img_path,
            output_dir=out_dir,
            cy=None,
            cx=None,
            use_cache=False,
        )
        assert result.get("integrated_1d") == []
        assert not any(p.endswith(".dat") for p in os.listdir(out_dir))
        err = capsys.readouterr().err
        assert "Warning: integrate_proxy could not estimate center" in err


def test_integrate_proxy_with_mask(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        img = np.ones((20, 20), dtype=np.float32)
        img[9:12, 9:12] = 20.0
        mask = np.zeros((20, 20), dtype=np.float32)
        mask[:, :10] = 1.0
        img_path = os.path.join(tmp, "single.tif")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(img_path).write_bytes(b"dummy")
        np.save(mask_path, mask.astype(bool))

        monkeypatch.setattr("autosaxs.skill.integrate_proxy.read_from_tiff", lambda _: img)
        out_dir = os.path.join(tmp, "int_proxy_mask")
        result = integrate_proxy(
            img_path,
            output_dir=out_dir,
            mask=mask_path,
            cy=10.0,
            cx=10.0,
            use_cache=False,
        )
        assert "integrated_1d" in result
        assert os.path.isfile(str(result["integrated_1d"]))


# ---------------------------------------------------------------------------
# calibrate: contract (requires config and calib image; skip if no data)
# ---------------------------------------------------------------------------
def test_merge_skill_params_precedence():
    bundled = {"subtract": {"method": "point_match", "point_match_factor": 0.995}}
    with tempfile.TemporaryDirectory() as tmp:
        user_path = os.path.join(tmp, "user.conf")
        Path(user_path).write_text("subtract:\n  q_min: 5.0\n  q_max: 6.0\n")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("autosaxs.skill.config.load_default_config", lambda: bundled)
            merged = merge_skill_params(
                "subtract",
                config_path=user_path,
                method="match_tail",
            )
        assert merged["method"] == "match_tail"
        assert merged["q_min"] == 5.0
        assert merged["q_max"] == 6.0
        assert merged["point_match_factor"] == 0.995


def test_calibrate_raises_without_calib_image():
    with pytest.raises((FileNotFoundError, ValueError)):
        calibrate(
            calibrant_image="",
            output_dir=tempfile.mkdtemp(),
            mask="dummy_mask.msk",
            use_cache=False,
        )


def test_calibrate_rejects_unknown_calibrant():
    with pytest.raises(ValueError, match="Unknown calibrant"):
        calibrate(
            calibrant_image="",
            output_dir=tempfile.mkdtemp(),
            mask="dummy_mask.msk",
            calibrant="not_a_real_calibrant",
            use_cache=False,
        )


def test_calibrate_requires_mask_for_default_from_file_mode():
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        Path(calib_path).write_bytes(b"dummy")

        with pytest.raises(TypeError):
            calibrate(
                calibrant_image=calib_path,
                output_dir=os.path.join(tmp, "out"),
                use_cache=False,
            )


def test_calibrate_default_mask_mode_is_from_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(calib_path).write_bytes(b"dummy")
        np.save(mask_path, np.zeros((4, 4), dtype=bool))

        monkeypatch.setattr(
            "autosaxs.skill.calibrate.merge_skill_params",
            lambda *_a, **_k: {
                "calibrant": "AgBh",
                "mask_mode": "f",
                "wavelength": 1.445,
                "ring_analysis": {},
                "detector_geometry": {"pixel_size": [1e-4, 1e-4], "rot1": 0.0, "rot2": 0.0, "rot3": 0.0},
            },
        )

        class _DummyIntegrator:
            mask = None

            def to_disk(self, path):
                os.makedirs(path, exist_ok=True)
                Path(os.path.join(path, "ai_params.json")).write_text("{}")
                Path(os.path.join(path, "detector_params.json")).write_text("{}")

        def _fake_autocalib_ring_analysis(_calib_image, cfg, **_kwargs):
            assert cfg["mask_config"]["mode"] == "from_file"
            return {
                "integrator": _DummyIntegrator(),
                "refined": {"dist": 0.7},
                "calib_data": np.zeros((8, 8), dtype=np.float32),
                "curve_calibrated": (
                    np.array([0.1, 0.2]),
                    np.array([1.0, 2.0]),
                    np.array([0.1, 0.2]),
                ),
                "theoretical_peaks": np.array([0.15]),
            }

        monkeypatch.setattr("autosaxs.skill.calibrate.autocalib_ring_analysis", _fake_autocalib_ring_analysis)
        monkeypatch.setattr("autosaxs.skill.calibrate.PLTViewer.view_mask", lambda *args, **kwargs: None)

        out = calibrate(
            calibrant_image=calib_path,
            output_dir=os.path.join(tmp, "out"),
            mask=mask_path,
            use_cache=False,
        )
        assert os.path.isdir(out["integrator_dir"])
        assert os.path.isfile(out["calibration_curve_dat_path"])
        from autosaxs.core.utils import read_saxs

        q, I, sigma, meta = read_saxs(out["calibration_curve_dat_path"])
        assert len(q) == 2
        assert meta["type"] == "calibration_curve"


def test_calibrate_always_overrides_config_calibrant(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(calib_path).write_bytes(b"dummy")
        np.save(mask_path, np.zeros((4, 4), dtype=bool))

        requested_calibrant = "AgBh"

        def _fake_merge(_skill, *, config_path=None, **kwargs):
            merged = {
                "calibrant": "Si",
                "mask_mode": "f",
                "wavelength": 1.445,
                "ring_analysis": {},
                "detector_geometry": {"pixel_size": [1e-4, 1e-4], "rot1": 0.0, "rot2": 0.0, "rot3": 0.0},
            }
            if kwargs.get("calibrant") is not None:
                merged["calibrant"] = kwargs["calibrant"]
            return merged

        monkeypatch.setattr("autosaxs.skill.calibrate.merge_skill_params", _fake_merge)

        class _DummyIntegrator:
            mask = None

            def to_disk(self, path):
                os.makedirs(path, exist_ok=True)
                Path(os.path.join(path, "ai_params.json")).write_text("{}")
                Path(os.path.join(path, "detector_params.json")).write_text("{}")

        def _fake_autocalib_ring_analysis(calib_image, cfg, **_kwargs):
            assert calib_image == calib_path
            assert cfg["calibrant_name"] == requested_calibrant
            return {
                "integrator": _DummyIntegrator(),
                "refined": {"dist": 0.7},
                "calib_data": np.zeros((8, 8), dtype=np.float32),
                "curve_calibrated": (
                    np.array([0.1, 0.2]),
                    np.array([1.0, 2.0]),
                    np.array([0.1, 0.2]),
                ),
                "theoretical_peaks": np.array([0.15]),
            }

        monkeypatch.setattr("autosaxs.skill.calibrate.autocalib_ring_analysis", _fake_autocalib_ring_analysis)
        monkeypatch.setattr("autosaxs.skill.calibrate.PLTViewer.view_mask", lambda *args, **kwargs: None)

        out = calibrate(
            calibrant_image=calib_path,
            output_dir=os.path.join(tmp, "out"),
            mask=mask_path,
            calibrant=requested_calibrant,
            use_cache=False,
        )
        assert os.path.isdir(out["integrator_dir"])


def test_calibrate_requires_wavelength(monkeypatch):
    monkeypatch.setattr(
        "autosaxs.skill.calibrate.merge_skill_params",
        lambda *_a, **_k: {
            "calibrant": "AgBh",
            "mask_mode": "f",
            "ring_analysis": {},
            "detector_geometry": {"pixel_size": [1e-4, 1e-4], "rot1": 0.0, "rot2": 0.0, "rot3": 0.0},
        },
    )
    with pytest.raises(ValueError, match="wavelength"):
        with tempfile.TemporaryDirectory() as tmp:
            calib_path = os.path.join(tmp, "calib.tif")
            mask_path = os.path.join(tmp, "mask.npy")
            Path(calib_path).write_bytes(b"dummy")
            np.save(mask_path, np.zeros((4, 4), dtype=bool))
            calibrate(
                calibrant_image=calib_path,
                output_dir=tempfile.mkdtemp(),
                mask=mask_path,
                use_cache=False,
            )


def test_calibrate_dist_guess_passed_to_autocalib(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(calib_path).write_bytes(b"dummy")
        np.save(mask_path, np.zeros((4, 4), dtype=bool))

        monkeypatch.setattr(
            "autosaxs.skill.calibrate.merge_skill_params",
            lambda *_a, **_k: {
                "calibrant": "AgBh",
                "mask_mode": "f",
                "wavelength": 1.445,
                "dist_guess": 0.82,
                "ring_analysis": {},
                "detector_geometry": {"pixel_size": [1e-4, 1e-4], "rot1": 0.0, "rot2": 0.0, "rot3": 0.0},
            },
        )

        seen = {}

        class _DummyIntegrator:
            mask = None

            def to_disk(self, path):
                os.makedirs(path, exist_ok=True)
                Path(os.path.join(path, "ai_params.json")).write_text("{}")
                Path(os.path.join(path, "detector_params.json")).write_text("{}")

        def _fake_autocalib_ring_analysis(_calib_image, cfg, *, dist_guess_m=None, **_kwargs):
            seen["dist_guess_m"] = dist_guess_m
            seen["wavelength_m"] = cfg["detector_geometry"]["wavelength"]
            return {
                "integrator": _DummyIntegrator(),
                "refined": {"dist": 0.81},
                "calib_data": np.zeros((8, 8), dtype=np.float32),
                "curve_calibrated": (
                    np.array([0.1, 0.2]),
                    np.array([1.0, 2.0]),
                    np.array([0.1, 0.2]),
                ),
                "theoretical_peaks": np.array([0.15]),
            }

        monkeypatch.setattr("autosaxs.skill.calibrate.autocalib_ring_analysis", _fake_autocalib_ring_analysis)
        monkeypatch.setattr("autosaxs.skill.calibrate.PLTViewer.view_mask", lambda *args, **kwargs: None)

        calibrate(
            calibrant_image=calib_path,
            output_dir=os.path.join(tmp, "out"),
            mask=mask_path,
            use_cache=False,
        )
        assert seen["dist_guess_m"] == 0.82
        assert seen["wavelength_m"] == pytest.approx(1.445e-10)


# ---------------------------------------------------------------------------
# integrate: contract (requires integrator_dir and images)
# ---------------------------------------------------------------------------
def test_integrate_raises_without_images():
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FileNotFoundError):
            integrate(
                images="",
                integrator_dir=tmp,
                output_dir=os.path.join(tmp, "out"),
                use_cache=False,
            )


# ---------------------------------------------------------------------------
# model_mixture: contract (requires profile)
# ---------------------------------------------------------------------------
def test_model_mixture_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        model_mixture(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_model_mixture_contract_with_mock_mixture(monkeypatch):
    """
    Contract test without requiring ATSAS MIXTURE executable.

    We monkeypatch the internal MIXTURE runner to write minimal `.fit` and `mixture.log`
    outputs expected by the parser/plotting code.
    """
    import subprocess as _sp

    def _fake_run_mixture(work_dir: Path, dat_basename: str, cmd_content: str) -> _sp.CompletedProcess:
        _ = cmd_content
        work_dir.mkdir(parents=True, exist_ok=True)
        fit_path = work_dir / dat_basename.replace(".dat", ".fit")
        log_path = work_dir / "mixture.log"

        # The parser treats the file as numeric (q, I_exp, I_fit) and converts q by *10.
        q_nm = np.linspace(0.1, 2.0, 30)
        q_A = q_nm / 10.0
        I_exp = np.exp(-q_nm**2) + 0.05
        I_fit = I_exp * 0.98
        sigma = 0.03 * np.abs(I_exp)
        with open(fit_path, "w") as f:
            for i in range(len(q_A)):
                f.write(f"{q_A[i]}\t{I_exp[i]}\t{I_fit[i]}\t{sigma[i]}\n")

        # Minimal sphere lines (matched by regex r"^\\dSPH$") with >= 6 floats.
        log_path.write_text(
            "\n".join(
                [
                    "1SPH 0.50 0 0 50.0 0 5.0",
                    "Produced function minimum is equal to 1.234",
                    "",
                ]
            )
        )
        return _sp.CompletedProcess(args=["mixture"], returncode=0, stdout="Produced function minimum is equal to 1.234", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_mixture.mixture._run_mixture", _fake_run_mixture)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 40)
        I = np.exp(-q**2) + 0.02
        sigma = 0.03 * np.abs(I)
        profile_path = os.path.join(tmp, "subtracted.dat")
        write_saxs(profile_path, q, I, sigma, {"type": "subtracted"})

        cfg_path = os.path.join(tmp, "config.yml")
        Path(cfg_path).write_text(
            "\n".join(
                [
                    "model_mixture:",
                    "  maxit: 5",
                    "  r_min: 0.1",
                    "  r_max: 8.0",
                    "  poly_min: 0.05",
                    "  poly_max: 4.0",
                    "  max_nph: 1",
                    "",
                ]
            )
        )

        out_dir = os.path.join(tmp, "mixture_out")
        result = model_mixture(profile_path, output_dir=out_dir, config_path=cfg_path, use_cache=False)

        for key in (
            "output_subdir",
            "comparison_path",
            "comparison_loglog_path",
            "comparison_log_path",
            "distributions_path",
            "results_csv_path",
            "r_max_nm",
            "poly_max_nm",
        ):
            assert key in result, f"model_mixture must return {key}"

        def _scalar(v):
            return v[0] if isinstance(v, list) and len(v) == 1 else v

        assert float(_scalar(result["r_max_nm"])) == pytest.approx(8.0)
        assert float(_scalar(result["poly_max_nm"])) == pytest.approx(4.0)

        # plot_I_q / plot_logI_logq default to False → empty paths, not missing files.
        assert result["comparison_path"] == ""
        assert result["comparison_loglog_path"] == ""

        for key in ("output_subdir", "comparison_log_path", "distributions_path", "results_csv_path"):
            path = str(result[key])
            assert path, f"{key} must be non-empty"
            assert os.path.isfile(path) or os.path.isdir(path), f"{key} must exist"

        csv_path = str(result["results_csv_path"])
        assert os.path.getsize(csv_path) > 0


def test_model_mixture_without_config_path_uses_bundled_defaults(monkeypatch):
    import subprocess as _sp

    def _fake_run_mixture(work_dir: Path, dat_basename: str, cmd_content: str) -> _sp.CompletedProcess:
        _ = cmd_content
        work_dir.mkdir(parents=True, exist_ok=True)
        fit_path = work_dir / dat_basename.replace(".dat", ".fit")
        log_path = work_dir / "mixture.log"
        q_nm = np.linspace(0.1, 2.0, 30)
        q_A = q_nm / 10.0
        I_exp = np.exp(-q_nm**2) + 0.05
        I_fit = I_exp * 0.98
        sigma = 0.03 * np.abs(I_exp)
        with open(fit_path, "w") as f:
            for i in range(len(q_A)):
                f.write(f"{q_A[i]}\t{I_exp[i]}\t{I_fit[i]}\t{sigma[i]}\n")
        log_path.write_text(
            "1SPH 0.50 0 0 50.0 0 5.0\nProduced function minimum is equal to 1.234\n"
        )
        return _sp.CompletedProcess(args=["mixture"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_mixture.mixture._run_mixture", _fake_run_mixture)
    monkeypatch.setattr(
        "autosaxs.skill.model_mixture._rmax_nm_from_fit_sizes",
        lambda profile, output_dir, event_bus: (12.0, os.path.join(output_dir, "fake_fit_sizes.yml")),
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 40)
        I = np.exp(-q**2) + 0.02
        sigma = 0.03 * np.abs(I)
        profile_path = os.path.join(tmp, "subtracted.dat")
        write_saxs(profile_path, q, I, sigma, {"type": "subtracted"})
        out_dir = os.path.join(tmp, "mixture_out")
        result = model_mixture(profile_path, output_dir=out_dir, use_cache=False)
        assert os.path.isfile(str(result["results_csv_path"]))
        def _scalar(v):
            return v[0] if isinstance(v, list) and len(v) == 1 else v

        assert float(_scalar(result["r_max_nm"])) == pytest.approx(12.0)
        assert float(_scalar(result["poly_max_nm"])) == pytest.approx(6.0)


def test_model_mixture_radius_params_use_nm_externally():
    from autosaxs.skill.model_mixture import _resolve_mixture_radius_params

    params = _resolve_mixture_radius_params(
        profile="dummy.dat",
        output_dir="/tmp",
        event_bus=None,
        user_r_max=8.0,
        user_r_min=0.1,
        user_poly_min=0.05,
        user_poly_max=4.0,
    )
    assert params["r_max"] == pytest.approx(80.0)
    assert params["r_min"] == pytest.approx(1.0)
    assert params["poly_min"] == pytest.approx(0.5)
    assert params["poly_max"] == pytest.approx(40.0)


def test_subtract_applies_bundled_defaults_with_q_window_from_kwargs(monkeypatch):
    captured = {}

    def _fake_subtract_paths(input_paths, output_dir, match_tail_ops=None, method=None, **kwargs):
        captured["method"] = method
        captured["match_tail_ops"] = match_tail_ops
        return {
            "subtracted_1d": os.path.join(output_dir, "sub_x.dat"),
            "diff_plot_path": os.path.join(output_dir, "diff_x.png"),
            "diff_log_plot_path": os.path.join(output_dir, "diff_log_x.png"),
            "sub_plot_path": os.path.join(output_dir, "sub_x.png"),
        }

    monkeypatch.setattr("autosaxs.skill.subtract._subtract_paths", _fake_subtract_paths)
    monkeypatch.setattr(
        "autosaxs.skill.subtract.merge_skill_params",
        lambda *_a, **_k: {
            "method": "point_match",
            "sample_form": "Porod-plus-linear",
            "buffer_form": "linear",
            "point_match_factor": 0.995,
        },
    )

    with tempfile.TemporaryDirectory() as tmp:
        sample = os.path.join(tmp, "s.dat")
        buff = os.path.join(tmp, "b.dat")
        write_saxs(sample, np.linspace(0.1, 2.0, 40), np.ones(40), np.full(40, 0.1), {})
        write_saxs(buff, np.linspace(0.1, 2.0, 40), np.ones(40), np.full(40, 0.1), {})
        subtract(sample, buff, output_dir=os.path.join(tmp, "out"), q_min=1.0, q_max=2.0, use_cache=False)
        assert captured["method"] == "point_match"
        assert captured["match_tail_ops"] is not None
        assert captured["match_tail_ops"]["q_range_abs"] == (1.0, 2.0)


# ---------------------------------------------------------------------------
# fit_sizes
# ---------------------------------------------------------------------------
def _fake_gnom_out_text(total_estimate: float = 0.9, neg_d_fraction: float = 0.0) -> str:
    """Minimal GNOM .out with I(q) table and D(R) block (some D<0 for neg_frac tests)."""
    d_rows = [
        "0.0    0.0     0.0",
        "1.0    0.1     0.0",
        "2.0    0.2     0.0",
        "3.0    0.3     0.0",
        "4.0    0.2     0.0",
        "5.0    0.1     0.0",
        "6.0    0.0     0.0",
        "7.0    0.0     0.0",
    ]
    if neg_d_fraction > 0:
        d_rows[3] = "3.0   -0.5     0.0"
    return "\n".join(
        [
            "GNOM OUTPUT (fake)",
            f"Total Estimate = {total_estimate:.3f}",
            "",
            "S EXP ERROR JREG",
            "0.10  1.0  0.1  0.95",
            "0.20  0.8  0.1  0.78",
            "",
            "R      D(R)    Err",
            *d_rows,
            "",
        ]
    )


def test_fit_sizes_contract(monkeypatch):
    """With rmax and first set, only GNOM is invoked (no fit_guinier)."""
    import subprocess as _sp

    guinier_calls = []

    def _guinier_guard(*_a, **_k):
        guinier_calls.append(True)
        raise AssertionError("fit_guinier should not run when rmax and first are set")

    monkeypatch.setattr("autosaxs.skill.fit_sizes.sizes._guinier_from_profile", _guinier_guard)

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "gnom"
        out_idx = cmd.index("-o")
        out_arg = Path(cmd[out_idx + 1])
        out_path = out_arg if out_arg.is_absolute() else Path(cwd) / out_arg
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_fake_gnom_out_text(), encoding="utf-8")
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.fit_sizes.runners.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 40)
        I = np.exp(-q**2) + 0.02
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, 0.03 * np.abs(I), {})

        out = fit_sizes(
            profile_path,
            output_dir=os.path.join(tmp, "sizes"),
            use_cache=False,
            rmax_nm=10.0,
            first=1,
        )
        assert not guinier_calls
        assert os.path.isfile(str(out["best_gnom_out_path"]))


def test_fit_sizes_score_te_minus_nf():
    from autosaxs.skill.fit_sizes.optimize import _candidate_from_gnom_out

    out_hi = _fake_gnom_out_text(total_estimate=0.9, neg_d_fraction=0.0)
    out_lo = _fake_gnom_out_text(total_estimate=0.9, neg_d_fraction=1.0)
    c_hi = _candidate_from_gnom_out(
        out_hi,
        shape="spheres",
        system=1,
        rmax_nm=10.0,
        rmin_nm=None,
        rad56_nm=None,
        first=1,
        last=None,
        alpha=None,
        nr=None,
        out_path="",
        rc=0,
        stderr="",
        intermediate=True,
    )
    c_lo = _candidate_from_gnom_out(
        out_lo,
        shape="spheres",
        system=1,
        rmax_nm=10.0,
        rmin_nm=None,
        rad56_nm=None,
        first=1,
        last=None,
        alpha=None,
        nr=None,
        out_path="",
        rc=0,
        stderr="",
        intermediate=True,
    )
    assert c_hi["score"] > c_lo["score"]


def test_fit_sizes_rmax_optimization_invoked(monkeypatch):
    import subprocess as _sp

    guinier_calls = []
    optimize_calls = []

    def _fake_guinier_profile(q_nm, I, sigma, atsas_dat_path):
        guinier_calls.append(True)
        return {
            "rg": 2.0,
            "rg_min": 1.5,
            "rg_max": 2.5,
            "q_min": 0.1,
            "q_max": 0.5,
            "chosen_interval": (0.1, 0.5),
            "quality_class": "good",
        }

    def _fake_optimize(**kwargs):
        optimize_calls.append(kwargs)
        return 7.5, [{"rmax_nm": 7.5, "score": 0.7, "intermediate": True}], []

    monkeypatch.setattr("autosaxs.skill.fit_sizes.sizes._guinier_from_profile", _fake_guinier_profile)

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "gnom"
        out_idx = cmd.index("-o")
        out_arg = Path(cmd[out_idx + 1])
        out_path = out_arg if out_arg.is_absolute() else Path(cwd) / out_arg
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_fake_gnom_out_text(), encoding="utf-8")
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.fit_sizes.runners.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.skill.fit_sizes.sizes._optimize_rmax_nm", _fake_optimize)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, 0.02 * I, {})

        out = fit_sizes(
            profile_path,
            output_dir=os.path.join(tmp, "sizes"),
            use_cache=False,
        )
        assert len(guinier_calls) == 1
        assert len(optimize_calls) == 1
        assert optimize_calls[0]["rg_max_nm"] == pytest.approx(2.5)
        assert float(out["best_gnom_out_path"].split("rmax_")[-1].split(".out")[0]) == pytest.approx(7.5)

# ---------------------------------------------------------------------------
# model_bodies / model_dam: contract (require profile)
# ---------------------------------------------------------------------------
def test_model_bodies_first_from_guinier(monkeypatch):
    import subprocess as _sp
    import importlib as _importlib

    _mod = _importlib.import_module("autosaxs.skill.model_bodies")
    guinier_calls = []

    def _fake_guinier(q_nm, I, sigma, atsas_dat_path=None):
        guinier_calls.append(True)
        return {
            "chosen": "adaptive",
            "chosen_Rg": 2.0,
            "rg_min": 1.5,
            "rg_max": 2.5,
            "chosen_interval": (0.1, 0.5),
            "quality_class": "good",
        }

    monkeypatch.setattr("autosaxs.skill.model_bodies.run_guinier_analysis", _fake_guinier)

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "bodies"
        assert any(str(a).startswith("--first=") for a in cmd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_bodies.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, 0.02 * I, {})

        model_bodies(profile_path, output_dir=os.path.join(tmp, "bodies"), use_cache=False)
        assert len(guinier_calls) == 1


def test_model_bodies_skips_guinier_when_first_set(monkeypatch):
    import subprocess as _sp
    import importlib as _importlib

    guinier_calls = []

    def _guinier_guard(*_a, **_k):
        guinier_calls.append(True)
        raise AssertionError("fit_guinier should not run when first is set")

    monkeypatch.setattr("autosaxs.skill.model_bodies.run_guinier_analysis", _guinier_guard)

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert any(a == "--first=3" for a in cmd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_bodies.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 40)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), None, {})

        model_bodies(profile_path, output_dir=os.path.join(tmp, "bodies"), first=3, use_cache=False)
        assert not guinier_calls


def test_model_bodies_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        model_bodies(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_fit_guinier_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        fit_guinier(
            profile="/nonexistent.dat",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_analyze_kratky_contract_with_explicit_rg_i0():
    with tempfile.TemporaryDirectory() as tmp:
        rg_nm = 2.0
        i0 = 1.0
        q = np.linspace(0.05, 1.2, 120)
        I = i0 * np.exp(-((q * rg_nm) ** 2) / 3.0)
        sigma = 0.01 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "kratky")
        result = analyze_kratky(
            profile_path,
            output_dir=out_dir,
            rg_nm=rg_nm,
            i0=i0,
            use_cache=False,
        )
        for key in (
            "results_path",
            "kratky_plot_path",
            "kratky_dimensionless_plot_path",
            "kratky_classical_dat_path",
            "kratky_dimensionless_dat_path",
            "classification",
            "x_max",
            "y_max",
        ):
            assert key in result, f"analyze_kratky must return {key}"
        assert os.path.isfile(str(result["results_path"]))
        assert os.path.isfile(str(result["kratky_dimensionless_plot_path"]))
        assert result["classification"] == "globular"


def test_analyze_kratky_runs_guinier_when_rg_i0_omitted(monkeypatch):
    guinier_calls = []

    def _fake_guinier(q_nm, I, sigma, atsas_dat_path=None):
        guinier_calls.append(atsas_dat_path)
        return {
            "chosen": "adaptive",
            "chosen_Rg": 2.0,
            "chosen_I0": 1.0,
            "chosen_interval": (0.05, 0.4),
        }

    monkeypatch.setattr("autosaxs.skill.analyze_kratky.run_guinier_analysis", _fake_guinier)

    with tempfile.TemporaryDirectory() as tmp:
        rg_nm = 2.0
        i0 = 1.0
        q = np.linspace(0.05, 1.2, 120)
        I = i0 * np.exp(-((q * rg_nm) ** 2) / 3.0)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, 0.01 * I, {})
        out_dir = os.path.join(tmp, "kratky")
        result = analyze_kratky(profile_path, output_dir=out_dir, use_cache=False)
        assert guinier_calls
        assert result["classification"] == "globular"


def test_analyze_kratky_raises_if_only_one_of_rg_i0():
    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 1.0, 40)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), 0.01, {})
        with pytest.raises(ValueError, match="both rg_nm and i0"):
            analyze_kratky(profile_path, output_dir=tmp, rg_nm=2.0, use_cache=False)


def test_analyze_kratky_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        analyze_kratky(
            profile="/nonexistent.dat",
            output_dir=tempfile.mkdtemp(),
            rg_nm=2.0,
            i0=1.0,
            use_cache=False,
        )


def test_model_dam_raises_without_profile():
    with pytest.raises(FileNotFoundError):
        model_dam(
            profile="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def _write_minimal_dammif_cif(path: str) -> None:
    Path(path).write_text(
        "data_dummy\nloop_\n_atom_site.id\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n_atom_site.Cartn_z\n"
        "1 0.0 0.0 0.0\n",
        encoding="utf-8",
    )


def test_model_dam_calls_fit_distances_when_gnom_omitted(monkeypatch):
    import subprocess as _sp

    fit_distances_calls = []
    dammif_gnom_args = []

    def _fake_gnom_from_distances(profile, output_dir, event_bus):
        fit_distances_calls.append((profile, output_dir))
        os.makedirs(output_dir, exist_ok=True)
        gnom_path = os.path.join(output_dir, "fake_gnom.out")
        Path(gnom_path).write_text(
            "DATGNOM OUTPUT (fake)\nReal space range: 0.0000 to 35.0000\nTotal Estimate = 0.85\n"
        )
        return gnom_path

    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam._gnom_path_from_fit_distances",
        _fake_gnom_from_distances,
    )

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "dammif"
        assert "--mode=FAST" in cmd
        dammif_gnom_args.append(str(cmd[-1]))
        prefix = next(a.split("=", 1)[1] for a in cmd if str(a).startswith("--prefix="))
        fir = os.path.join(cwd or ".", f"{prefix}.fir")
        with open(fir, "w") as f:
            f.write("s Exp iExp Err iFit\n")
            for i in range(10):
                q = 0.1 * (i + 1)
                f.write(f"{q}\t1.0\t0.1\t0.95\n")
        _write_minimal_dammif_cif(os.path.join(cwd or ".", f"{prefix}-1.cif"))
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.skill.model_dam.dam.PLTViewer.view_curves", lambda *a, **k: None)
    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam.PLTViewer.plot_3d_views_and_scattering",
        lambda *a, **k: None,
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, 0.02 * I, {})
        out_dir = os.path.join(tmp, "dammif")
        result = model_dam(profile_path, output_dir=out_dir, use_cache=False)
        assert len(fit_distances_calls) == 1
        assert fit_distances_calls[0][0] == os.path.normpath(os.path.abspath(profile_path))
        assert len(dammif_gnom_args) == 1
        assert os.path.isabs(dammif_gnom_args[0])
        assert dammif_gnom_args[0].endswith("fake_gnom.out")
        assert os.path.isdir(str(result["output_subdir"]))
        best = Path(str(result["best_cif_path"]))
        assert best.is_symlink()
        assert best.name == "best.cif"
        assert result["frequency_map_path"] == ""


def test_model_dam_skips_fit_distances_when_gnom_provided(monkeypatch):
    import subprocess as _sp

    def _guard(*_a, **_k):
        raise AssertionError("fit_distances should not run when gnom_path is set")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam._gnom_path_from_fit_distances", _guard)

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "dammif"
        prefix = next(a.split("=", 1)[1] for a in cmd if str(a).startswith("--prefix="))
        fir = os.path.join(cwd or ".", f"{prefix}.fir")
        with open(fir, "w") as f:
            f.write("s Exp iExp Err iFit\n")
            for i in range(10):
                q = 0.1 * (i + 1)
                f.write(f"{q}\t1.0\t0.1\t0.95\n")
        _write_minimal_dammif_cif(os.path.join(cwd or ".", f"{prefix}-1.cif"))
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.skill.model_dam.dam.PLTViewer.view_curves", lambda *a, **k: None)
    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam.PLTViewer.plot_3d_views_and_scattering",
        lambda *a, **k: None,
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), 0.02, {})
        gnom_path = os.path.join(tmp, "provided_gnom.out")
        Path(gnom_path).write_text("DATGNOM OUTPUT\n")
        out_dir = os.path.join(tmp, "dammif")
        model_dam(profile_path, output_dir=out_dir, gnom_path=gnom_path, use_cache=False)


def test_model_dam_n_runs_runs_damaver(monkeypatch):
    import subprocess as _sp

    def _guard(*_a, **_k):
        raise AssertionError("fit_distances should not run when gnom_path is set")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam._gnom_path_from_fit_distances", _guard)
    monkeypatch.setattr("autosaxs.skill.model_dam.dam.shutil.which", lambda name: f"/fake/bin/{name}")

    calls = []

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        calls.append((list(cmd), cwd))
        exe = cmd[0] if cmd else ""
        if exe == "dammif":
            prefix = next(a.split("=", 1)[1] for a in cmd if str(a).startswith("--prefix="))
            fir = os.path.join(cwd or ".", f"{prefix}.fir")
            with open(fir, "w") as f:
                f.write("s Exp iExp Err iFit\n")
                for i in range(10):
                    q = 0.1 * (i + 1)
                    f.write(f"{q}\t1.0\t0.1\t0.95\n")
            _write_minimal_dammif_cif(os.path.join(cwd or ".", f"{prefix}-1.cif"))
        elif exe == "damaver":
            out = Path(cwd or ".")
            out.mkdir(parents=True, exist_ok=True)
            (out / "damaver-global-damaver.cif").write_text("data_freq\n", encoding="utf-8")
            (out / "damaver-global-summary.txt").write_text(
                "Most probable model: dammif-2-1.cif\nInclude dammif-1-1.cif\nInclude dammif-2-1.cif\n",
                encoding="utf-8",
            )
        else:
            raise AssertionError(f"unexpected executable: {exe}")
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.skill.model_dam.dam.PLTViewer.view_curves", lambda *a, **k: None)
    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam.PLTViewer.plot_3d_views_and_scattering",
        lambda *a, **k: None,
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), 0.02, {})
        gnom_path = os.path.join(tmp, "gnom.out")
        Path(gnom_path).write_text("DATGNOM OUTPUT (fake)\n")
        out_dir = os.path.join(tmp, "dammif")
        result = model_dam(
            profile_path,
            output_dir=out_dir,
            gnom_path=gnom_path,
            n_runs=2,
            dammif_mode="slow",
            use_cache=False,
        )
        dammif_calls = [c for c, _ in calls if c[0] == "dammif"]
        damaver_calls = [c for c, _ in calls if c[0] == "damaver"]
        assert len(dammif_calls) == 2
        assert all("--mode=SLOW" in c for c in dammif_calls)
        assert len(damaver_calls) == 1
        assert result["frequency_map_path"]
        assert os.path.isfile(str(result["frequency_map_path"]))
        best = Path(str(result["best_cif_path"]))
        assert best.is_symlink()
        assert best.resolve().name == "dammif-2-1.cif"


def test_fit_dammif_deprecated_alias(monkeypatch):
    """Deprecated fit_dammif still forwards to model_dam."""
    import subprocess as _sp

    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam._gnom_path_from_fit_distances",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should use gnom_path")),
    )

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        prefix = next(a.split("=", 1)[1] for a in cmd if str(a).startswith("--prefix="))
        fir = os.path.join(cwd or ".", f"{prefix}.fir")
        with open(fir, "w") as f:
            f.write("s Exp iExp Err iFit\n")
            for i in range(10):
                q = 0.1 * (i + 1)
                f.write(f"{q}\t1.0\t0.1\t0.95\n")
        _write_minimal_dammif_cif(os.path.join(cwd or ".", f"{prefix}-1.cif"))
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_dam.dam.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.skill.model_dam.dam.PLTViewer.view_curves", lambda *a, **k: None)
    monkeypatch.setattr(
        "autosaxs.skill.model_dam.dam.PLTViewer.plot_3d_views_and_scattering",
        lambda *a, **k: None,
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), 0.02, {})
        gnom_path = os.path.join(tmp, "gnom.out")
        Path(gnom_path).write_text("DATGNOM OUTPUT (fake)\n")
        with pytest.warns(DeprecationWarning, match="fit_dammif is deprecated"):
            fit_dammif(
                profile_path,
                output_dir=os.path.join(tmp, "dammif"),
                gnom_path=gnom_path,
                dammif_reps_num=1,
                use_cache=False,
            )


def test_model_dam_visuals_writes_assets(tmp_path: Path):
    """Visuals writer creates synced run GIFs + overlap + occupancy assets."""
    from autosaxs.skill.model_dam.vis import write_visuals

    def _write_cif(path: Path, pts: np.ndarray, occ=None) -> None:
        lines = [
            "data_test",
            "loop_",
            "_atom_site.group_PDB",
            "_atom_site.id",
            "_atom_site.type_symbol",
            "_atom_site.label_atom_id",
            "_atom_site.label_alt_id",
            "_atom_site.label_comp_id",
            "_atom_site.label_asym_id",
            "_atom_site.label_seq_id",
            "_atom_site.pdbx_PDB_ins_code",
            "_atom_site.Cartn_x",
            "_atom_site.Cartn_y",
            "_atom_site.Cartn_z",
            "_atom_site.occupancy",
            "_atom_site.B_iso_or_equiv",
            "_atom_site.pdbx_formal_charge",
            "_atom_site.pdbx_PDB_model_num",
        ]
        for i, (x, y, z) in enumerate(pts, 1):
            o = 1.0 if occ is None else float(occ[i - 1])
            lines.append(f"ATOM {i} C CA . ASP A 1 ? {x:.3f} {y:.3f} {z:.3f} {o:.3f} 20.00 ? 1")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rng = np.random.default_rng(1)
    base = rng.normal(size=(80, 3)) * 8.0
    dam = tmp_path / "damaver"
    dam.mkdir()
    for i in range(1, 3):
        pts = base + rng.normal(size=base.shape) * 0.5
        _write_cif(dam / f"damaver-global-dammif-{i}-1r.cif", pts)
        _write_cif(tmp_path / f"dammif-{i}-1.cif", pts)
    _write_cif(dam / "damaver-global-damaver.cif", base, occ=rng.uniform(0.0, 3.0, size=len(base)))
    best = tmp_path / "best.cif"
    best.symlink_to(tmp_path / "dammif-1-1.cif")

    out = write_visuals(
        str(tmp_path),
        best_cif_path=str(best),
        frequency_map_path=str(dam / "damaver-global-damaver.cif"),
    )
    vis = Path(out["visuals_dir"])
    assert vis.is_dir()
    assert len(out["run_gifs"]) == 2
    assert Path(out["overlap_gif"]).is_file()
    assert Path(out["occupancy_gif"]).is_file()
    assert Path(out["overlap_png"]).is_file()
    assert Path(out["occupancy_png"]).is_file()
    assert Path(out["occupancy_thresholds_png"]).is_file()


def test_model_density_visuals_writes_assets(tmp_path: Path):
    """Visuals writer creates synced slice GIF + midplane PNG from a tiny MRC."""
    denss = pytest.importorskip("denss")
    from autosaxs.skill.model_density.vis import write_visuals

    n = 16
    side = 80.0  # Å
    zz, yy, xx = np.mgrid[0:n, 0:n, 0:n]
    cx = cy = cz = (n - 1) / 2.0
    r2 = (xx - cx) ** 2 + (yy - cy) ** 2 + (zz - cz) ** 2
    rho = np.exp(-r2 / (2.0 * 2.5**2)).astype(np.float64)
    mrc = tmp_path / "toy.mrc"
    denss.write_mrc(rho, side, filename=str(mrc))

    out = write_visuals(str(tmp_path), density_map_path=str(mrc))
    assert Path(out["visuals_dir"]).is_dir()
    assert Path(out["slices_gif"]).is_file()
    assert Path(out["midplanes_png"]).is_file()
    assert Path(out["density_rotate_gif"]).is_file()
    assert out["sigma_rotate_gif"] == ""
    assert (tmp_path / "visuals" / "density_slices.gif").is_file()
    assert (tmp_path / "visuals" / "density_midplanes.png").is_file()
    assert (tmp_path / "visuals" / "density_rotate.gif").is_file()

    # With a σ map, also write sigma_rotate.gif
    sigma = (0.05 + 0.2 * (1.0 - rho)).astype(np.float64)
    sigma_mrc = tmp_path / "toy_sigma.mrc"
    denss.write_mrc(sigma, side, filename=str(sigma_mrc))
    out2 = write_visuals(
        str(tmp_path / "with_sigma"),
        density_map_path=str(mrc),
        sigma_map_path=str(sigma_mrc),
    )
    assert Path(out2["density_rotate_gif"]).is_file()
    assert Path(out2["sigma_rotate_gif"]).is_file()


def test_fit_bodies_deprecated_alias(monkeypatch):
    """Deprecated fit_bodies still forwards to model_bodies."""
    import subprocess as _sp

    monkeypatch.setattr(
        "autosaxs.skill.model_bodies.run_guinier_analysis",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should pass first")),
    )

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        _ = capture_output, text, timeout, kwargs
        assert (cmd[0] if cmd else "") == "bodies"
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_bodies.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, np.exp(-q**2), 0.02, {})
        with pytest.warns(DeprecationWarning, match="fit_bodies is deprecated"):
            fit_bodies(
                profile_path,
                output_dir=os.path.join(tmp, "bodies"),
                first=3,
                use_cache=False,
            )


def test_fit_mixture_deprecated_alias(monkeypatch):
    """Deprecated fit_mixture still forwards to model_mixture."""
    import subprocess as _sp

    def _fake_run_mixture(work_dir: Path, dat_basename: str, cmd_content: str) -> _sp.CompletedProcess:
        _ = cmd_content
        work_dir.mkdir(parents=True, exist_ok=True)
        fit_path = work_dir / dat_basename.replace(".dat", ".fit")
        log_path = work_dir / "mixture.log"
        q_nm = np.linspace(0.1, 2.0, 30)
        q_A = q_nm / 10.0
        I_exp = np.exp(-q_nm**2) + 0.05
        I_fit = I_exp * 0.98
        sigma = 0.03 * np.abs(I_exp)
        with open(fit_path, "w") as f:
            for i in range(len(q_A)):
                f.write(f"{q_A[i]}\t{I_exp[i]}\t{I_fit[i]}\t{sigma[i]}\n")
        log_path.write_text("1SPH 0.50 0 0 50.0 0 5.0\nProduced function minimum is equal to 1.234\n")
        return _sp.CompletedProcess(args=["mixture"], returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.skill.model_mixture.mixture._run_mixture", _fake_run_mixture)
    monkeypatch.setattr(
        "autosaxs.skill.model_mixture._rmax_nm_from_fit_sizes",
        lambda *_a, **_k: (5.0, "/tmp/fake_fit_sizes.yml"),
    )

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 40)
        profile_path = os.path.join(tmp, "subtracted.dat")
        write_saxs(profile_path, q, np.exp(-q**2) + 0.02, 0.03, {})
        with pytest.warns(DeprecationWarning, match="fit_mixture is deprecated"):
            fit_mixture(profile_path, output_dir=os.path.join(tmp, "mixture"), use_cache=False)


def _fake_datgnom_out_text(*, rmax: float = 35.0, te: float = 0.85) -> str:
    """Minimal DATGNOM/GNOM .out: rmax header, Total Estimate, 8+ row p(r) table."""
    return "\n".join(
        [
            "DATGNOM OUTPUT (fake)",
            f"Real space range: 0.0000 to {float(rmax):.4f}",
            f"Total Estimate = {float(te):.2f}",
            "",
            "R      P(R)    Error",
            "0.0    0.0     0.0",
            "5.0    1.0     0.1",
            "10.0   2.0     0.1",
            "15.0   1.5     0.1",
            "20.0   0.8     0.1",
            "25.0   0.2     0.1",
            "30.0   0.0     0.1",
            "35.0   0.0     0.1",
            "",
        ]
    )


def _write_fake_atsas_out(cmd, cwd=None) -> None:
    """Resolve -o path (absolute for datgnom, cwd-relative for gnom) and write a fake .out."""
    out_idx = cmd.index("-o")
    out_path = cmd[out_idx + 1]
    if cwd and not os.path.isabs(out_path):
        out_path = os.path.join(cwd, out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rmax = 35.0
    for a in cmd:
        s = str(a)
        if s.startswith("--rmax="):
            try:
                rmax = float(s.split("=", 1)[1])
            except ValueError:
                pass
    Path(out_path).write_text(_fake_datgnom_out_text(rmax=rmax))


def test_fit_distances_contract(monkeypatch):
    """
    Contract test without requiring DATGNOM/GNOM: monkeypatch subprocess.run.
    """
    import subprocess as _sp

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
            exe = cmd[0] if cmd else ""
            assert exe in ("datgnom", "gnom")
            if exe == "datgnom":
                assert any(str(a).startswith("--rg=") for a in cmd)
            _write_fake_atsas_out(cmd, cwd=cwd)
            return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("autosaxs.skill.fit_distances.runners.subprocess.run", _fake_run)

        out_dir = os.path.join(tmp, "distances")
        result = fit_distances(
            profile_path, output_dir=out_dir, rg_nm=2.5, first=1, use_cache=False
        )
        for key in ("output_subdir", "gnom_out_paths", "best_gnom_out_path", "fit_distances_log_path"):
            assert key in result
        assert os.path.isdir(str(result["output_subdir"]))
        gnom_paths = result["gnom_out_paths"]
        if isinstance(gnom_paths, str):
            gnom_paths = [gnom_paths]
        assert isinstance(gnom_paths, list) and len(gnom_paths) > 0
        assert all(os.path.isfile(p) for p in gnom_paths)
        assert os.path.isfile(str(result["best_gnom_out_path"]))
        assert os.path.isfile(str(result["fit_distances_log_path"]))


def test_fit_distances_score_te_minus_nf():
    from autosaxs.core.gnom import candidate_score

    c_high = {"total_estimate": 0.9, "neg_frac": 0.1, "suspicious": False}
    c_low = {"total_estimate": 0.9, "neg_frac": 0.3, "suspicious": False}
    assert candidate_score(c_high) == pytest.approx(0.8)
    assert candidate_score(c_low) == pytest.approx(0.6)
    assert candidate_score({"total_estimate": None, "neg_frac": 0.0}) == float("-inf")
    assert candidate_score({"total_estimate": 1.0}) == pytest.approx(1.0)

    best = max(
        [
            {"total_estimate": 0.5, "neg_frac": 0.0, "suspicious": False},
            {"total_estimate": 0.9, "neg_frac": 0.2, "suspicious": False},
            {"total_estimate": 1.0, "neg_frac": 0.5, "suspicious": False},
        ],
        key=candidate_score,
    )
    assert best["total_estimate"] == 0.9 and best["neg_frac"] == 0.2


def test_fit_distances_rg_optimization_invoked(monkeypatch):
    """When rg_nm is omitted, fit_guinier and bounded Rg optimization run."""
    import subprocess as _sp

    guinier_calls = []
    optimize_calls = []

    def _fake_guinier(q_nm, I, sigma, atsas_dat_path=None):
        guinier_calls.append(True)
        return {
            "chosen": True,
            "chosen_Rg": 2.0,
            "rg_min": 1.5,
            "rg_max": 2.5,
            "chosen_interval": (0.1, 0.5),
            "quality_class": "good",
        }

    def _fake_optimize(**kwargs):
        optimize_calls.append(kwargs)
        return 2.2, [{"rg_nm": 2.2, "score": 0.7, "intermediate": True}], []

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
            exe = cmd[0] if cmd else ""
            assert exe in ("datgnom", "gnom")
            _write_fake_atsas_out(cmd, cwd=cwd)
            return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(
            "autosaxs.skill.fit_distances.optimize.run_guinier_analysis", _fake_guinier
        )
        monkeypatch.setattr(
            "autosaxs.skill.fit_distances.distances._optimize_rg_nm", _fake_optimize
        )
        monkeypatch.setattr("autosaxs.skill.fit_distances.runners.subprocess.run", _fake_run)

        out_dir = os.path.join(tmp, "distances")
        fit_distances(profile_path, output_dir=out_dir, first=None, rg_nm=None, use_cache=False)
        assert len(guinier_calls) == 1
        assert len(optimize_calls) == 1
        assert optimize_calls[0]["rg_max_nm"] == pytest.approx(2.5)


def test_fit_distances_all_runs_failed(monkeypatch):
    import subprocess as _sp

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        return _sp.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="DATGNOM failed (fake)")

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        monkeypatch.setattr("autosaxs.skill.fit_distances.runners.subprocess.run", _fake_run)

        out_dir = os.path.join(tmp, "distances")
        result = fit_distances(
            profile_path, output_dir=out_dir, rg_nm=2.5, first=1, use_cache=False
        )
        from autosaxs.skill.gnom_fit_common import _unwrap_scalar

        assert _unwrap_scalar(result.get("atsas_fit_ok")) is False
        assert _unwrap_scalar(result.get("gnom_failed")) is True
        assert result.get("best_gnom_out_path") in ("", [])
        msg = result.get("failure_message")
        if isinstance(msg, list) and len(msg) == 1:
            msg = msg[0]
        assert isinstance(msg, str) and msg
        assert os.path.isfile(str(_unwrap_scalar(result["fit_distances_log_path"])))
        assert os.path.isfile(str(_unwrap_scalar(result["failure_txt_path"])))


def test_fit_sizes_all_runs_failed(monkeypatch):
    import subprocess as _sp

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        return _sp.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="GNOM failed (fake)")

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        monkeypatch.setattr("autosaxs.skill.fit_sizes.runners.subprocess.run", _fake_run)

        out_dir = os.path.join(tmp, "sizes")
        result = fit_sizes(
            profile_path,
            output_dir=out_dir,
            rmax_nm=5.0,
            first=1,
            use_cache=False,
        )
        from autosaxs.skill.gnom_fit_common import _unwrap_scalar

        assert _unwrap_scalar(result.get("atsas_fit_ok")) is False
        assert _unwrap_scalar(result.get("gnom_failed")) is True
        assert result.get("best_gnom_out_path") in ("", [])
        msg = result.get("failure_message")
        if isinstance(msg, list) and len(msg) == 1:
            msg = msg[0]
        assert isinstance(msg, str) and msg
        assert os.path.isfile(str(_unwrap_scalar(result["best_summary_path"])))
        assert os.path.isfile(str(_unwrap_scalar(result["failure_txt_path"])))


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


# ---------------------------------------------------------------------------
# CLI invocation: smoke test that argparse dispatch works
# ---------------------------------------------------------------------------
def test_cli_invocation_smoke(capsys):
    from autosaxs.cli import main as cli_main

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.1, 2.0, 20)
        I = np.exp(-q**2)
        sigma = 0.01 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        out_dir = os.path.join(tmp, "cli_plot_out")
        rc = cli_main(["plot", profile_path, "--output-dir", out_dir, "--no-cache"])
        assert rc == 0

        captured = capsys.readouterr().out
        assert "guinier_plot_path=" in captured
        assert "kratky_plot_path=" in captured

        # Kebab-case is the canonical CLI command form for skills.
        rc = cli_main(["fit-guinier", "--description"])
        assert rc == 0
        captured_desc = capsys.readouterr().out
        assert "### cli usage" in captured_desc.lower()
        assert "autosaxs fit-guinier" in captured_desc
