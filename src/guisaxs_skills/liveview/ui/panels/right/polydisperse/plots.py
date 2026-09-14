from __future__ import annotations

import os
from typing import Any, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out


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
    def plot_from_gnom_out(
        self,
        gnom_out_path: str,
        *,
        close_fits: bool = True,
        force_zero_off: bool = True,
        overlay_gnom_out: str | None = None,
    ) -> None:
        """
        Plot D(R) from a GNOM ``.out``.

        - ``close_fits``: faint Rmax±10% ensemble overlays (auto pane default).
        - ``force_zero_off``: thin black force-zero-off overlay from ``ensemble/``.
        - ``overlay_gnom_out``: optional second ``.out`` (e.g. auto best) drawn faintly
          under the primary curve; its sibling ``ensemble/`` supplies force-zero-off when
          the primary path has none (adjust-wizard preview temp outs).
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

        primary_dir = os.path.dirname(os.path.abspath(gnom_out_path))
        overlay_path = (overlay_gnom_out or "").strip()
        ens_dirs: list[str] = []
        if close_fits or force_zero_off:
            ens_dirs.append(os.path.join(primary_dir, "ensemble"))
        if overlay_path and os.path.isfile(overlay_path):
            odir = os.path.dirname(os.path.abspath(overlay_path))
            o_ens = os.path.join(odir, "ensemble")
            if o_ens not in ens_dirs:
                ens_dirs.append(o_ens)
            try:
                ov_arr = distribution_arrays(parse_gnom_out(overlay_path).get("distribution"))
            except Exception:
                ov_arr = None
            if ov_arr is not None:
                rr, pp, _ee = ov_arr
                self._ax.plot(rr, pp, color="C1", lw=1.0, alpha=0.55, zorder=1, label="auto best")

        if close_fits:
            for ens_dir in ens_dirs:
                close_dir = os.path.join(ens_dir, "close_fits")
                close_labeled = False
                if not os.path.isdir(close_dir):
                    continue
                for name in sorted(os.listdir(close_dir)):
                    if not name.endswith(".out"):
                        continue
                    cf_path = os.path.join(close_dir, name)
                    try:
                        cf_arr = distribution_arrays(parse_gnom_out(cf_path).get("distribution"))
                    except Exception:
                        continue
                    if cf_arr is None:
                        continue
                    rr, pp, _ee = cf_arr
                    label = "close fits (Rmax±10%)" if not close_labeled else None
                    self._ax.plot(rr, pp, color="0.65", lw=0.8, alpha=0.5, zorder=1, label=label)
                    close_labeled = True

        if force_zero_off:
            fz_labeled = False
            for ens_dir in ens_dirs:
                if not os.path.isdir(ens_dir):
                    continue
                for name in sorted(os.listdir(ens_dir)):
                    if not name.endswith("_force_zero_off.out"):
                        continue
                    fz_path = os.path.join(ens_dir, name)
                    try:
                        fz_arr = distribution_arrays(parse_gnom_out(fz_path).get("distribution"))
                    except Exception:
                        continue
                    if fz_arr is None:
                        continue
                    rr, pp, _ee = fz_arr
                    label = "force-zero-off" if not fz_labeled else None
                    self._ax.plot(rr, pp, color="k", lw=0.8, alpha=1.0, zorder=1, label=label)
                    fz_labeled = True

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
        self._ax.set_ylim(0, None)
        if label:
            self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
