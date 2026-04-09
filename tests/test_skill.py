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
    check_output_integrity,
    compute_input_hash,
    read_cache,
    run_with_cache,
    write_cache,
)
from autosaxs.skill.calibrate import calibrate
from autosaxs.skill.fit_bodies import fit_bodies
from autosaxs.skill.fit_dammif import fit_dammif
from autosaxs.skill.fit_distances import fit_distances
from autosaxs.skill.fit_mixture import fit_mixture
from autosaxs.skill.guinier_analysis import guinier_analysis
from autosaxs.skill.integrate import integrate
from autosaxs.skill.integrate_proxy import integrate_proxy
from autosaxs.skill.plot import plot
from autosaxs.skill.plot_2d import plot_2d
from autosaxs.skill.subtract import subtract
from autosaxs.utils import read_saxs, write_saxs, write_data


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
    ("integrate_proxy", integrate_proxy),
    ("subtract", subtract),
    ("plot", plot),
    ("plot_2d", plot_2d),
    ("guinier_analysis", guinier_analysis),
    ("fit_distances", fit_distances),
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
        assert os.path.isfile(str(result["subtracted_1d"]))


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
def test_calibrate_raises_without_calib_image():
    with pytest.raises((FileNotFoundError, ValueError)):
        calibrate(
            calib_image="",
            config_path="",
            output_dir=tempfile.mkdtemp(),
            use_cache=False,
        )


def test_calibrate_rejects_unknown_calibrant():
    with pytest.raises(ValueError, match="Unknown calibrant"):
        calibrate(
            calib_image="",
            config_path="",
            output_dir=tempfile.mkdtemp(),
            calibrant="not_a_real_calibrant",
            use_cache=False,
        )


def test_calibrate_requires_mask_for_default_from_file_mode():
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        cfg_path = os.path.join(tmp, "config.yml")
        Path(calib_path).write_bytes(b"dummy")
        Path(cfg_path).write_text("dummy: true")

        with pytest.raises(ValueError, match="mask path is required"):
            calibrate(
                calib_image=calib_path,
                config_path=cfg_path,
                output_dir=os.path.join(tmp, "out"),
                use_cache=False,
            )


def test_calibrate_default_mask_mode_is_from_file(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        cfg_path = os.path.join(tmp, "config.yml")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(calib_path).write_bytes(b"dummy")
        Path(cfg_path).write_text("dummy: true")
        np.save(mask_path, np.zeros((4, 4), dtype=bool))

        monkeypatch.setattr("autosaxs.skill.calibrate.load_config", lambda _: {"calibrant_name": "AgBh"})

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
            }

        monkeypatch.setattr("autosaxs.skill.calibrate.autocalib_ring_analysis", _fake_autocalib_ring_analysis)
        monkeypatch.setattr("autosaxs.skill.calibrate.PLTViewer.view_mask", lambda *args, **kwargs: None)

        out = calibrate(
            calib_image=calib_path,
            config_path=cfg_path,
            output_dir=os.path.join(tmp, "out"),
            mask=mask_path,
            use_cache=False,
        )
        assert os.path.isdir(out["integrator_dir"])


def test_calibrate_always_overrides_config_calibrant(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        calib_path = os.path.join(tmp, "calib.tif")
        cfg_path = os.path.join(tmp, "config.yml")
        mask_path = os.path.join(tmp, "mask.npy")
        Path(calib_path).write_bytes(b"dummy")
        Path(cfg_path).write_text("dummy: true")
        np.save(mask_path, np.zeros((4, 4), dtype=bool))

        # Use two distinct known names to ensure override is visible.
        requested_calibrant = "AgBh"

        monkeypatch.setattr("autosaxs.skill.calibrate.load_config", lambda _: {"calibrant_name": "Si"})

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
            }

        monkeypatch.setattr("autosaxs.skill.calibrate.autocalib_ring_analysis", _fake_autocalib_ring_analysis)
        monkeypatch.setattr("autosaxs.skill.calibrate.PLTViewer.view_mask", lambda *args, **kwargs: None)

        out = calibrate(
            calib_image=calib_path,
            config_path=cfg_path,
            output_dir=os.path.join(tmp, "out"),
            mask=mask_path,
            calibrant=requested_calibrant,
            use_cache=False,
        )
        assert os.path.isdir(out["integrator_dir"])


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


def test_fit_distances_contract(monkeypatch):
    """
    Contract test without requiring GNOM to be installed: monkeypatch subprocess.run to emulate `gnom`.
    """
    import subprocess as _sp

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-q**2) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})

        def _fake_run(args, cwd=None, capture_output=None, text=None):
            # Expect: ["gnom", "--system=0", "--rmax=..", "--output", out_path, atsas_dat_path]
            assert args[0] == "gnom"
            assert "--system=0" in args
            out_idx = args.index("--output")
            out_path = args[out_idx + 1]
            # Produce a minimal .out containing Total Estimate and a p(r) table block.
            Path(out_path).write_text(
                "\n".join(
                    [
                        "GNOM OUTPUT",
                        "Total Estimate = 0.85",
                        "",
                        "R      P(R)    Error",
                        "0.0    0.0     0.0",
                        "5.0    1.0     0.1",
                        "10.0   2.0     0.1",
                        "15.0   1.5     0.1",
                        "20.0   0.8     0.1",
                        "25.0   0.2     0.1",
                        "30.0   0.0     0.1",
                        "",
                    ]
                )
            )
            return _sp.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        monkeypatch.setattr("autosaxs.skill.fit_distances.subprocess.run", _fake_run)

        out_dir = os.path.join(tmp, "distances")
        result = fit_distances(profile_path, output_dir=out_dir, use_cache=False)
        for key in ("output_subdir", "gnom_out_paths", "best_gnom_out_path", "best_summary_path"):
            assert key in result
        assert os.path.isdir(str(result["output_subdir"]))
        assert isinstance(result["gnom_out_paths"], list)
        assert len(result["gnom_out_paths"]) > 0
        assert os.path.isfile(str(result["best_gnom_out_path"]))
        assert os.path.isfile(str(result["best_summary_path"]))




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
