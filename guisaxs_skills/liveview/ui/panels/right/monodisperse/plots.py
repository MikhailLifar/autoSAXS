from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import yaml
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

    @property
    def click_path(self) -> Optional[str]:
        return self._click_path

    @property
    def click_viewer(self) -> Optional[str]:
        return self._click_viewer

    def _show_status(self, text: str) -> None:
        self._status = text
        self._click_path = None
        self._click_viewer = None
        self._ax.clear()
        self._ax.text(0.5, 0.5, text, ha="center", va="center", transform=self._ax.transAxes, fontsize=9)
        self._ax.set_axis_off()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def clear_plot(self) -> None:
        self._show_status("—")


class GuinierCurvePlot(_BaseMplPlot):
    def __init__(self, *, figsize=(2.14, 1.61)) -> None:
        super().__init__(figsize=figsize)

    def plot_from_profile_and_region(self, profile_path: str, region_yaml_path: str) -> None:
        if not profile_path or not os.path.isfile(profile_path):
            self._show_status("No profile")
            return
        if not region_yaml_path or not os.path.isfile(region_yaml_path):
            self._show_status("No Guinier region")
            return
        try:
            from autosaxs.core.utils import load_saxs_1d_any, ensure_q_nm

            q, I, sigma = load_saxs_1d_any(profile_path)
            q, I, sigma = ensure_q_nm(q, I, sigma)
            data = yaml.safe_load(Path(region_yaml_path).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            self._show_status("Guinier plot error")
            return
        if not isinstance(data, dict):
            self._show_status("Invalid region YAML")
            return
        rg = data.get("rg")
        if rg is None:
            rg = data.get("Rg")
        i0 = data.get("i0")
        if i0 is None:
            i0 = data.get("I0")
        q_min = data.get("q_min")
        q_max = data.get("q_max")
        if rg is None or i0 is None:
            self._show_status("Incomplete Guinier fit")
            return
        q = np.asarray(q, dtype=float)
        I = np.asarray(I, dtype=float)
        lp = data.get("last_point_1based")
        if lp is None:
            fp, lp = None, None
            try:
                from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

                fp, lp = guinier_point_range_1based(data)
            except Exception:
                pass
        if lp is not None:
            try:
                end_excl = min(len(q), int(lp) + 5)
                q = q[:end_excl]
                I = I[:end_excl]
            except (TypeError, ValueError):
                pass
        m = np.isfinite(q) & np.isfinite(I) & (I > 0)
        if not m.any():
            self._show_status("Empty profile")
            return
        q = q[m]
        I = I[m]
        if q_min is None or q_max is None:
            fp = data.get("first_point_1based")
            lp = data.get("last_point_1based")
            if fp is not None and lp is not None:
                try:
                    i1 = max(0, int(fp) - 1)
                    i2 = min(len(q) - 1, int(lp) - 1)
                    if i2 >= i1:
                        q_min = float(q[i1])
                        q_max = float(q[i2])
                except (TypeError, ValueError):
                    pass
        if q_min is not None and q_max is not None:
            band = (q >= float(q_min)) & (q <= float(q_max))
        else:
            band = np.ones_like(q, dtype=bool)
        x = q ** 2
        y = np.log(I)
        y_fit = np.log(float(i0)) - (float(rg) ** 2 / 3.0) * x
        self._click_path = profile_path
        self._ax.clear()
        self._ax.scatter(x[~band], y[~band], s=8, alpha=0.35, c="0.6", label="out")
        self._ax.scatter(x[band], y[band], s=10, alpha=0.9, c="C0", label="fit region")
        self._ax.plot(x[band], y_fit[band], "r-", lw=1.5, label="Guinier")
        self._ax.set_xlabel("q² (nm⁻²)")
        self._ax.set_ylabel("ln I")
        self._ax.legend(fontsize=7, loc="best")
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


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
        self._ax.set_xscale("log")
        self._ax.set_yscale("log")
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("I")
        self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)

    def plot_from_dat_and_gnom_out(self, profile_path: str, gnom_out_path: str) -> None:
        if not profile_path or not os.path.isfile(profile_path):
            self.plot_from_gnom_out(gnom_out_path)
            return
        self.plot_from_gnom_out(gnom_out_path)


class PrPlot(_BaseMplPlot):
    def plot_from_gnom_out(self, gnom_out_path: str) -> None:
        if not gnom_out_path or not os.path.isfile(gnom_out_path):
            self._show_status("No GNOM .out")
            return
        try:
            parsed = parse_gnom_out(gnom_out_path)
            dist = parsed.get("distribution")
        except Exception:
            self._show_status("P(r) parse error")
            return
        if not dist or len(dist) != 2:
            self._show_status("No P(r) in .out")
            return
        r, pr = (np.asarray(a, dtype=float) for a in dist)
        m = np.isfinite(r) & np.isfinite(pr)
        if not m.any():
            self._show_status("Empty P(r)")
            return
        self._click_path = gnom_out_path
        self._click_viewer = "gnom_pr"
        self._ax.clear()
        self._ax.plot(r[m], pr[m], "C0-", lw=1.2)
        self._ax.set_xlabel("r (nm)")
        self._ax.set_ylabel("P(r)")
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class ShapeFitPlot(_BaseMplPlot):
    def plot_from_fir(self, fir_path: str, *, label: str = "fit") -> None:
        if not fir_path or not os.path.isfile(fir_path):
            self._show_status("No .fir")
            return
        try:
            from autosaxs.core.utils import ensure_q_nm

            # ATSAS .fir: one header line, then sExp | iExp | Err | iFit(+Const)
            data = np.loadtxt(fir_path, skiprows=1)
            if data.ndim == 1:
                data = data.reshape(1, -1)
            if data.shape[1] < 2:
                self._show_status("Invalid .fir")
                return
            q = data[:, 0]
            i_exp = data[:, 1]
            if data.shape[1] >= 4:
                i_fit = data[:, 3]
            elif data.shape[1] >= 3:
                i_fit = data[:, 2]
            else:
                i_fit = data[:, 1]
            # DAMMIF .fir s-values are Å^-1; BODIES .fir is typically already nm^-1.
            q, i_exp, _ = ensure_q_nm(q, i_exp, None)
        except Exception:
            self._show_status("FIR read error")
            return
        m = np.isfinite(q) & np.isfinite(i_exp) & (i_exp > 0) & np.isfinite(i_fit) & (i_fit > 0)
        if not m.any():
            self._show_status("Empty FIR")
            return
        self._click_path = fir_path
        self._ax.clear()
        self._ax.scatter(q[m], i_exp[m], s=8, alpha=0.7, label="exp")
        self._ax.plot(q[m], i_fit[m], "r-", lw=1.2, label=label)
        self._ax.set_xscale("log")
        self._ax.set_yscale("log")
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("I")
        self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
