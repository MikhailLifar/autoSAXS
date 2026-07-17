from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt

from autosaxs.core.gnom import parse_gnom_out


class _BaseMplPlot(FigureCanvas):
    def __init__(self, *, figsize=(3.2, 2.4)) -> None:
        self._fig = Figure(figsize=figsize, dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._status = ""
        self._click_path: Optional[str] = None
        self._click_viewer: Optional[str] = None
        self._click_payload: Optional[dict[str, Any]] = None

    @property
    def click_path(self) -> Optional[str]:
        return self._click_path

    @property
    def click_viewer(self) -> Optional[str]:
        return self._click_viewer

    @property
    def click_payload(self) -> Optional[dict[str, Any]]:
        return self._click_payload

    def _show_status(self, text: str) -> None:
        self._status = text
        self._click_path = None
        self._click_viewer = None
        self._click_payload = None
        self._ax.clear()
        self._ax.text(0.5, 0.5, text, ha="center", va="center", transform=self._ax.transAxes, fontsize=9)
        self._ax.set_axis_off()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def clear_plot(self) -> None:
        self._show_status("—")


class GnomFitPlot(_BaseMplPlot):
    def plot_from_gnom_out(self, gnom_out_path: str) -> None:
        if not gnom_out_path or not os.path.isfile(gnom_out_path):
            self._show_status("No GNOM .out")
            return
        try:
            parsed = parse_gnom_out(gnom_out_path)
            iq = parsed.get("iq_table")
        except Exception:
            self._show_status("GNOM parse error")
            return
        if not iq or len(iq) != 4:
            self._show_status("No I(q) table in .out")
            return
        q, i_exp, sigma, i_fit = (np.asarray(a, dtype=float) for a in iq)
        m = np.isfinite(q) & np.isfinite(i_exp) & (i_exp > 0) & np.isfinite(i_fit) & (i_fit > 0)
        if not m.any():
            self._show_status("Empty GNOM I(q)")
            return
        self._click_path = gnom_out_path
        self._click_viewer = "gnom_iq"
        self._ax.clear()
        self._ax.scatter(q[m], i_exp[m], s=8, alpha=0.7, label="exp")
        self._ax.plot(q[m], i_fit[m], "r-", lw=1.2, label="GNOM")
        self._ax.set_yscale("log")
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("I")
        self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class DrPlot(_BaseMplPlot):
    def plot_from_dr_csv(self, dr_csv_path: str) -> None:
        if not dr_csv_path or not os.path.isfile(dr_csv_path):
            self._show_status("No D(R) CSV")
            return
        try:
            import pandas as pd

            df = pd.read_csv(dr_csv_path)
            cols = {c.lower(): c for c in df.columns}
            r_col = cols.get("r") or cols.get("r_nm") or list(df.columns)[0]
            d_col = cols.get("d") or cols.get("dr") or cols.get("d_r") or list(df.columns)[1]
            r = np.asarray(df[r_col], dtype=float)
            d = np.asarray(df[d_col], dtype=float)
        except Exception:
            self._show_status("D(R) CSV error")
            return
        m = np.isfinite(r) & np.isfinite(d)
        if not m.any():
            self._show_status("Empty D(R)")
            return
        self._click_path = dr_csv_path
        self._click_viewer = "dr"
        self._ax.clear()
        self._ax.plot(r[m], d[m], "C0-", lw=1.2)
        self._ax.set_xlabel("R (nm)")
        self._ax.set_ylabel("D(R)")
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class MixtureFitPlot(_BaseMplPlot):
    def plot_from_fit(self, fit_path: str, *, label: str = "fit") -> None:
        if not fit_path or not os.path.isfile(fit_path):
            self._show_status("No .fit")
            return
        try:
            from autosaxs.skill.fit_mixture.mixture import parse_mixture_fit_file

            parsed = parse_mixture_fit_file(fit_path)
        except Exception:
            self._show_status(".fit parse error")
            return
        if parsed is None:
            self._show_status("Empty .fit")
            return
        q, i_exp, i_fit = (np.asarray(a, dtype=float) for a in parsed)
        m = np.isfinite(q) & np.isfinite(i_exp) & (i_exp > 0) & np.isfinite(i_fit) & (i_fit > 0)
        if not m.any():
            self._show_status("Empty .fit")
            return
        self._click_path = fit_path
        self._click_viewer = "mixture_iq"
        self._click_payload = None
        self._ax.clear()
        self._ax.scatter(q[m], i_exp[m], s=8, alpha=0.7, label="exp")
        self._ax.plot(q[m], i_fit[m], "r-", lw=1.2, label=label)
        # log I vs q (linear q) — preferred over log–log for MIXTURE fit comparison
        self._ax.set_yscale("log")
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("I")
        self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class MixtureDistPlot(_BaseMplPlot):
    def plot_from_model_row(
        self,
        row: dict[str, Any],
        *,
        r_min_ang: float = 5.0,
        r_max_ang: float = 120.0,
        label: str = "",
    ) -> None:
        try:
            from autosaxs.skill.fit_mixture.mixture import distribution_curve_for_model

            R_nm, total = distribution_curve_for_model(
                row, r_min_ang=r_min_ang, r_max_ang=r_max_ang
            )
        except Exception:
            self._show_status("Distribution error")
            return
        if R_nm is None or total is None:
            self._show_status("No distribution")
            return
        m = np.isfinite(R_nm) & np.isfinite(total)
        if not m.any():
            self._show_status("Empty distribution")
            return
        self._click_path = None
        self._click_viewer = "mixture_dist"
        self._click_payload = {
            "row": dict(row or {}),
            "r_min_ang": float(r_min_ang),
            "r_max_ang": float(r_max_ang),
            "label": str(label or ""),
        }
        self._ax.clear()
        self._ax.plot(R_nm[m], total[m], "C0-", lw=1.4, label=label or None)
        self._ax.set_xlabel("R (nm)")
        self._ax.set_ylabel("P(R)")
        self._ax.set_ylim(0, None)
        if label:
            self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
