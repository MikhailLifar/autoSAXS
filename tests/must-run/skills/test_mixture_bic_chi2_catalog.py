"""Focused smoke tests for MIXTURE BIC_chi2 selection + catalog (no ATSAS)."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from autosaxs.skill.model_mixture.mixture import (
    component_distribution_curves,
    distribution_curve_for_model,
    select_best_mixture_row,
)
from guisaxs_skills.modeling.catalogs.mixture_models import build_mixture_model_catalog
from guisaxs_skills.ui.passport_format import BIC_CHI2, R2_LOG, format_metric_label, format_metric_label_html


def test_select_best_mixture_row_by_bic_chi2() -> None:
    rows = [
        {"label": "nph1_Gauss", "BIC_chi2": 12.0, "BIC_log": 1.0},
        {"label": "nph2_Gauss", "BIC_chi2": 5.0, "BIC_log": 9.0},
        {"label": "nph1_Schultz", "BIC_chi2": float("nan"), "BIC_log": 0.1},
    ]
    best = select_best_mixture_row(rows)
    assert best is not None
    assert best["label"] == "nph2_Gauss"


def test_passport_labels_bic_chi2_and_r2_log() -> None:
    assert format_metric_label("BIC_chi2") == BIC_CHI2
    assert format_metric_label(BIC_CHI2) == BIC_CHI2
    assert "χ²" in format_metric_label_html("BIC_chi2") or "chi" in format_metric_label_html(
        "BIC_chi2"
    ).lower() or "sub" in format_metric_label_html("BIC_chi2")
    assert format_metric_label("R2_log") == R2_LOG
    assert "<sub>log</sub>" in format_metric_label_html(R2_LOG) or "<sub>log</sub>" in format_metric_label_html(
        "R2_log"
    )


def test_component_distribution_curves_sum_to_total() -> None:
    row = {
        "dist": "Gauss",
        "vol_1": 0.6,
        "Rout_Ang_1": 40.0,
        "dRout_Ang_1": 5.0,
        "vol_2": 0.4,
        "Rout_Ang_2": 80.0,
        "dRout_Ang_2": 10.0,
    }
    comps = component_distribution_curves(row, r_min_ang=5.0, r_max_ang=120.0, n_points=200)
    assert len(comps) == 2
    R_nm, total = distribution_curve_for_model(row, r_min_ang=5.0, r_max_ang=120.0, n_points=200)
    assert R_nm is not None and total is not None
    summed = comps[0]["P"] + comps[1]["P"]
    assert np.allclose(summed, total, rtol=1e-10, atol=1e-12)
    assert comps[0]["Rout_nm"] == pytest.approx(4.0)
    assert comps[1]["dRout_nm"] == pytest.approx(1.0)


def test_fit_residuals_from_mixture_fit(tmp_path: Path) -> None:
    """MIXTURE .fit is 3-col — residual plot must not stay empty (ΔI wiring)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MPLBACKEND", "Agg")
    from PyQt5.QtWidgets import QApplication

    from guisaxs_skills.modeling.plots import FitResidualsPlot

    _ = QApplication.instance() or QApplication([])
    fit = tmp_path / "exp.fit"
    fit.write_text("0.01 100.0 98.0\n0.02 50.0 51.0\n0.03 25.0 24.0\n", encoding="utf-8")
    (tmp_path / "exp.dat").write_text(
        "0.1 100.0 2.0\n0.2 50.0 1.5\n0.3 25.0 1.0\n", encoding="utf-8"
    )
    plot = FitResidualsPlot()
    plot.plot_from_mixture_fit(str(fit))
    assert plot.click_path == str(fit)
    # Dispatch through .fir entry also routes .fit → mixture parser.
    plot2 = FitResidualsPlot()
    plot2.plot_from_fir(str(fit))
    assert plot2.click_path == str(fit)


def test_build_mixture_model_catalog(tmp_path: Path) -> None:
    csv_path = tmp_path / "mixture_results.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "label",
                "n_phases",
                "dist",
                "BIC_chi2",
                "BIC_log",
                "vol_1",
                "Rout_Ang_1",
                "dRout_Ang_1",
            ],
        )
        w.writeheader()
        w.writerow(
            {
                "label": "nph1_Gauss",
                "n_phases": 1,
                "dist": "Gauss",
                "BIC_chi2": 8.0,
                "BIC_log": 1.0,
                "vol_1": 1.0,
                "Rout_Ang_1": 50.0,
                "dRout_Ang_1": 8.0,
            }
        )
        w.writerow(
            {
                "label": "nph2_Schultz",
                "n_phases": 2,
                "dist": "Schultz",
                "BIC_chi2": 3.0,
                "BIC_log": 10.0,
                "vol_1": 0.5,
                "Rout_Ang_1": 40.0,
                "dRout_Ang_1": 5.0,
            }
        )
    nest = tmp_path / "nph2_Schultz"
    nest.mkdir()
    (nest / "exp.fit").write_text("0.1 1.0 1.1\n0.2 0.8 0.75\n", encoding="utf-8")

    cat = build_mixture_model_catalog(tmp_path)
    assert len(cat.entries) == 2
    best = cat.best()
    assert best is not None
    assert best.csv_label == "nph2_Schultz"
    assert best.key == cat.best_key
    assert "Schultz_nph2" in best.label or "nph2" in best.label
    assert best.fit_path and best.fit_path.endswith("exp.fit")
