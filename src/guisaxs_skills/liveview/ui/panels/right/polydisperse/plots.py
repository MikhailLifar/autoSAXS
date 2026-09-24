from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out

from ..distribution_ylim import clamp_distribution_ylim
from ..gnom_overlays import same_gnom_path


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
        self.plot_from_dat_and_gnom_out("", gnom_out_path)

    def plot_from_dat_and_gnom_out(self, profile_path: str, gnom_out_path: str) -> None:
        from ..gnom_iq_plot import draw_gnom_iq_on_ax

        err = draw_gnom_iq_on_ax(self._ax, gnom_out_path, profile_path=profile_path or "")
        if err:
            self._show_status(err)
            return
        self._click_path = gnom_out_path
        self._click_viewer = "gnom_iq"
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class DrPlot(_BaseMplPlot):
    def plot_from_gnom_out(
        self,
        gnom_out_path: str,
        *,
        overlay_gnom_out: str | None = None,
    ) -> None:
        """
        Plot D(R) from a GNOM ``.out``.

        At most two distribution curves: the primary ``.out`` and an optional
        ``overlay_gnom_out`` (e.g. disk best under an in-memory preview).
        """
        if not gnom_out_path or not os.path.isfile(gnom_out_path):
            self._show_status("No GNOM .out")
            return
        try:
            parsed = parse_gnom_out(gnom_out_path)
            arrays = distribution_arrays(parsed.get("distribution"))
        except Exception:
            self._show_status("D(R) parse error")
            return
        if arrays is None:
            self._show_status("No D(R) in .out")
            return
        r, d, err = arrays
        r = np.asarray(r, dtype=float)
        d = np.asarray(d, dtype=float)
        m = np.isfinite(r) & np.isfinite(d)
        if not m.any():
            self._show_status("Empty D(R)")
            return
        self._click_path = gnom_out_path
        self._click_viewer = "gnom_dr"
        self._ax.clear()

        overlay_path = (overlay_gnom_out or "").strip()
        if overlay_path and same_gnom_path(overlay_path, gnom_out_path):
            overlay_path = ""
        if overlay_path and os.path.isfile(overlay_path):
            try:
                ov_arr = distribution_arrays(parse_gnom_out(overlay_path).get("distribution"))
            except Exception:
                ov_arr = None
            if ov_arr is not None:
                rr, pp, _ee = ov_arr
                self._ax.plot(rr, pp, color="C1", lw=1.0, alpha=0.55, zorder=1, label="best (disk)")

        if err is not None:
            e = np.asarray(err, dtype=float)
            me = m & np.isfinite(e)
            if me.any():
                self._ax.fill_between(
                    r[me],
                    d[me] - e[me],
                    d[me] + e[me],
                    color="C0",
                    alpha=0.25,
                    linewidth=0,
                    zorder=2,
                    label=r"$\pm\sigma$",
                )
        primary_label = "current" if overlay_path else "best"
        self._ax.plot(r[m], d[m], "C0-", lw=1.2, zorder=3, label=primary_label)
        self._ax.set_xlabel("R (nm)")
        self._ax.set_ylabel("D(R)")
        self._ax.grid(True, alpha=0.2)
        handles, _labels = self._ax.get_legend_handles_labels()
        if handles:
            self._ax.legend(fontsize=7, loc="best")
        self._fig.tight_layout()
        clamp_distribution_ylim(self._ax)
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class MixtureFitPlot(_BaseMplPlot):
    def plot_from_fit(self, fit_path: str, *, label: str = "fit") -> None:
        if not fit_path or not os.path.isfile(fit_path):
            self._show_status("No .fit")
            return
        try:
            from autosaxs.skill.model_mixture.mixture import parse_mixture_fit_file

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
            from autosaxs.skill.model_mixture.mixture import distribution_curve_for_model

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
        if label:
            self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        clamp_distribution_ylim(self._ax)
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
