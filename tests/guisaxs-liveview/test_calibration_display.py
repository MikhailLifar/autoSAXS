from __future__ import annotations

import json
from pathlib import Path

from guisaxs_skills.liveview.services.calibration import (
    empty_refined_yml_display_rows,
    refined_yml_display_rows,
)


def _write_detector_params(integ: Path) -> None:
    integ.mkdir(parents=True, exist_ok=True)
    (integ / "detector_params.json").write_text(
        json.dumps({"detector_name": "Pilatus1M", "pixel_size": [1.0e-4, 1.0e-4]}),
        encoding="utf-8",
    )


def test_empty_refined_yml_display_rows() -> None:
    rows = empty_refined_yml_display_rows()
    assert rows
    assert all(val == "" for _, val in rows)
    assert rows[0][0] == "Sample-detector distance (m)"
    assert "cy (px)" in {lab for lab, _ in rows}


def test_refined_yml_display_rows_prefers_fit2d_center_px(tmp_path: Path) -> None:
    integ = tmp_path / "integrator"
    _write_detector_params(integ)
    yml = tmp_path / "refined.yml"
    yml.write_text(
        "dist: 0.5\n"
        "poni1: 0.1\n"
        "poni2: 0.2\n"
        "center_y_px: 321.5\n"
        "center_x_px: 456.25\n"
        "rot1: 0.01\n"
        "rot2: 0.02\n"
        "rot3: 3.14159\n"
        "wavelength: 1.54e-10\n",
        encoding="utf-8",
    )
    rows = refined_yml_display_rows(yml)
    labels = [r[0] for r in rows]
    vals = dict(rows)
    assert "Sample-detector distance (m)" in labels
    assert "cy (px)" in labels and "cx (px)" in labels
    assert vals["cy (px)"] == "321.500"
    assert vals["cx (px)"] == "456.250"
    assert "Wavelength (nm)" in labels
    assert "Wavelength (m)" not in labels
    assert "PONI1 (m)" not in labels
    assert any("Rotation 1" in L for L in labels)


def test_refined_yml_display_rows_legacy_poni_fallback(tmp_path: Path) -> None:
    integ = tmp_path / "integrator"
    _write_detector_params(integ)
    yml = tmp_path / "refined.yml"
    yml.write_text(
        "dist: 0.5\n"
        "poni1: 0.1\n"
        "poni2: 0.2\n"
        "rot1: 0.01\n"
        "rot2: 0.02\n"
        "rot3: 3.14159\n"
        "wavelength: 1.54e-10\n",
        encoding="utf-8",
    )
    rows = refined_yml_display_rows(yml)
    vals = dict(rows)
    assert vals["cy (px)"] == "1000.000"
    assert vals["cx (px)"] == "2000.000"
