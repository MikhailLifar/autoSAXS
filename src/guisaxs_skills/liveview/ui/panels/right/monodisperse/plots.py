from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out

from ..distribution_ylim import clamp_distribution_ylim
from ..gnom_overlays import resolve_force_zero_off_path, same_gnom_path


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

    def plot_from_profile_and_results(self, profile_path: str, results_txt_path: str) -> None:
        if not results_txt_path or not os.path.isfile(results_txt_path):
            self._show_status("No Guinier results")
            return
        try:
            from autosaxs.core.guinier import parse_guinier_results_txt

            data = parse_guinier_results_txt(results_txt_path)
        except Exception:
            self._show_status("Guinier plot error")
            return
        if not isinstance(data, dict) or not data:
            self._show_status("Invalid Guinier results")
            return
        # Prefer the profile that was actually fit (avoids I0 mismatch vs preferred_profile).
        fit_prof = str(data.get("input_file") or "").strip()
        if fit_prof:
            if not os.path.isabs(fit_prof):
                fit_prof = os.path.normpath(
                    os.path.join(os.path.dirname(os.path.abspath(results_txt_path)), fit_prof)
                )
            if os.path.isfile(fit_prof):
                profile_path = fit_prof
        if not profile_path or not os.path.isfile(profile_path):
            self._show_status("No profile")
            return
        try:
            from autosaxs.core.utils import load_saxs_1d_any, ensure_q_nm

            q, I, _sigma = load_saxs_1d_any(profile_path)
            q, I, _sigma = ensure_q_nm(q, I, _sigma)
        except Exception:
            self._show_status("Guinier plot error")
            return
        self.plot_from_profile_and_data(profile_path, data, q=q, I=I)

    def plot_from_profile_and_data(
        self,
        profile_path: str,
        data: dict,
        *,
        q=None,
        I=None,
    ) -> None:
        if q is None or I is None:
            if not profile_path or not os.path.isfile(profile_path):
                self._show_status("No profile")
                return
            try:
                from autosaxs.core.utils import load_saxs_1d_any, ensure_q_nm

                q, I, _sigma = load_saxs_1d_any(profile_path)
                q, I, _sigma = ensure_q_nm(q, I, _sigma)
            except Exception:
                self._show_status("Guinier plot error")
                return
        if not isinstance(data, dict):
            self._show_status("Invalid Guinier data")
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
        q_full = np.asarray(q, dtype=float)
        I_full = np.asarray(I, dtype=float)
        # Derive q band from 1-based indices on the *unfiltered* curve when needed.
        if (q_min is None or q_max is None) and q_full.size:
            fp = data.get("first_point_1based")
            lp = data.get("last_point_1based")
            if fp is None or lp is None:
                try:
                    from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

                    fp, lp = guinier_point_range_1based(data)
                except Exception:
                    fp, lp = None, None
            if fp is not None and lp is not None:
                try:
                    i1 = max(0, int(fp) - 1)
                    i2 = min(len(q_full) - 1, int(lp) - 1)
                    if i2 >= i1:
                        q_min = float(q_full[i1])
                        q_max = float(q_full[i2])
                except (TypeError, ValueError):
                    pass
        # Display window: a few points past the Guinier last index (optional).
        lp_disp = data.get("last_point_1based")
        if lp_disp is None:
            try:
                from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

                _fp, lp_disp = guinier_point_range_1based(data)
            except Exception:
                lp_disp = None
        if lp_disp is not None:
            try:
                end_excl = min(len(q_full), int(lp_disp) + 5)
                q_full = q_full[:end_excl]
                I_full = I_full[:end_excl]
            except (TypeError, ValueError):
                pass
        m = np.isfinite(q_full) & np.isfinite(I_full) & (I_full > 0)
        if not m.any():
            self._show_status("Empty profile")
            return
        q = q_full[m]
        I = I_full[m]
        if q_min is not None and q_max is not None:
            band = (q >= float(q_min)) & (q <= float(q_max))
        else:
            band = np.ones_like(q, dtype=bool)
        x = q ** 2
        y = np.log(I)
        # Continuous fit line on the Guinier q-window (same as PLTViewer.view_guinier_fit).
        if q_min is not None and q_max is not None and float(q_max) > float(q_min):
            q_line = np.linspace(float(q_min), float(q_max), 200)
        elif band.any():
            q_line = q[band]
        else:
            q_line = q
        y_fit_line = np.log(float(i0)) - (float(rg) ** 2 / 3.0) * (q_line ** 2)
        self._click_path = None
        self._click_viewer = None
        self._ax.clear()
        self._ax.scatter(x[~band], y[~band], s=8, alpha=0.35, c="0.6", label="out")
        self._ax.scatter(x[band], y[band], s=10, alpha=0.9, c="C0", label="fit region")
        if q_line.size:
            self._ax.plot(q_line ** 2, y_fit_line, "r-", lw=1.5, label="Guinier")
        self._ax.set_xlabel("q² (nm⁻²)")
        self._ax.set_ylabel("ln I")
        x_hi = float(np.nanmax(x)) if x.size else 1.0
        self._ax.set_xlim(0.0, x_hi * 1.05 if x_hi > 0 else 1.0)
        y_parts = [y]
        if q_line.size:
            y_parts.append(y_fit_line)
        y_all = np.concatenate(y_parts)
        y_all = y_all[np.isfinite(y_all)]
        if y_all.size:
            ymin = float(np.nanmin(y_all))
            ymax = float(np.nanmax(y_all))
            pad = 0.08 * (ymax - ymin) if ymax > ymin else 0.5
            self._ax.set_ylim(ymin - pad, ymax + pad)
        self._ax.legend(fontsize=7, loc="best")
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)


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

    def plot_from_dat_and_gnom_out(self, profile_path: str, gnom_out_path: str) -> None:
        if not profile_path or not os.path.isfile(profile_path):
            self.plot_from_gnom_out(gnom_out_path)
            return
        self.plot_from_gnom_out(gnom_out_path)


class PrPlot(_BaseMplPlot):
    def plot_from_gnom_out(
        self,
        gnom_out_path: str,
        *,
        close_fits: bool = True,
        force_zero_off: bool = True,
        overlay_gnom_out: str | None = None,
    ) -> None:
        """
        Plot P(r) from a GNOM ``.out``.

        - ``close_fits``: faint Dmax±10% ensemble overlays (auto pane default).
        - ``force_zero_off``: thin black force-zero-off overlay from ``ensemble/``.
        - ``overlay_gnom_out``: optional second ``.out`` (e.g. disk best) drawn faintly
          under the primary curve; its sibling ``ensemble/`` supplies force-zero-off /
          close-fits when the primary path has none (adjust-wizard preview temp outs).
        """
        if not gnom_out_path or not os.path.isfile(gnom_out_path):
            self._show_status("No GNOM .out")
            return
        try:
            parsed = parse_gnom_out(gnom_out_path)
            arrays = distribution_arrays(parsed.get("distribution"))
        except Exception:
            self._show_status("P(r) parse error")
            return
        if arrays is None:
            self._show_status("No P(r) in .out")
            return
        r, pr, err = arrays
        r = np.asarray(r, dtype=float)
        pr = np.asarray(pr, dtype=float)
        m = np.isfinite(r) & np.isfinite(pr)
        if not m.any():
            self._show_status("Empty P(r)")
            return
        self._click_path = gnom_out_path
        self._click_viewer = "gnom_pr"
        self._ax.clear()

        primary_dir = os.path.dirname(os.path.abspath(gnom_out_path))
        overlay_path = (overlay_gnom_out or "").strip()
        if overlay_path and same_gnom_path(overlay_path, gnom_out_path):
            overlay_path = ""
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
                self._ax.plot(rr, pp, color="C1", lw=1.0, alpha=0.55, zorder=1, label="best (disk)")

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
                    label = "close fits (Dmax±10%)" if not close_labeled else None
                    self._ax.plot(rr, pp, color="0.65", lw=0.8, alpha=0.5, zorder=1, label=label)
                    close_labeled = True

        if force_zero_off:
            fz_drawn = False
            for ens_dir in ens_dirs:
                fz_path = resolve_force_zero_off_path(ens_dir)
                if not fz_path or same_gnom_path(fz_path, gnom_out_path):
                    continue
                if overlay_path and same_gnom_path(fz_path, overlay_path):
                    continue
                try:
                    fz_arr = distribution_arrays(parse_gnom_out(fz_path).get("distribution"))
                except Exception:
                    continue
                if fz_arr is None:
                    continue
                rr, pp, _ee = fz_arr
                label = "force-zero-off" if not fz_drawn else None
                self._ax.plot(rr, pp, color="k", lw=0.8, alpha=1.0, zorder=1, label=label)
                fz_drawn = True
                break  # one overlay only

        if err is not None:
            e = np.asarray(err, dtype=float)
            me = m & np.isfinite(e)
            if me.any():
                self._ax.fill_between(
                    r[me],
                    pr[me] - e[me],
                    pr[me] + e[me],
                    color="C0",
                    alpha=0.25,
                    linewidth=0,
                    zorder=2,
                    label=r"$\pm\sigma$",
                )
        primary_label = "current" if overlay_path else "best"
        self._ax.plot(r[m], pr[m], "C0-", lw=1.2, zorder=3, label=primary_label)
        self._ax.set_xlabel("r (nm)")
        self._ax.set_ylabel("P(r)")
        self._ax.grid(True, alpha=0.2)
        handles, _labels = self._ax.get_legend_handles_labels()
        if handles:
            self._ax.legend(fontsize=7, loc="best")
        self._fig.tight_layout()
        clamp_distribution_ylim(self._ax)
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class ShapeFitPlot(_BaseMplPlot):
    def plot_from_fir(self, fir_path: str, *, label: str = "fit") -> None:
        if not fir_path or not os.path.isfile(fir_path):
            self._show_status("No fit file")
            return
        try:
            from autosaxs.core.utils import ensure_q_nm

            # ATSAS .fir: one header line, then sExp | iExp | Err | iFit(+Const)
            # DENSS *_map.fit: '#' header, then q | I | err | Icalc (Å⁻¹)
            try:
                data = np.loadtxt(fir_path, comments="#")
            except Exception:
                data = np.loadtxt(fir_path, skiprows=1)
            if data.ndim == 1:
                data = data.reshape(1, -1)
            if data.shape[1] < 2:
                self._show_status("Invalid fit file")
                return
            q = data[:, 0]
            i_exp = data[:, 1]
            if data.shape[1] >= 4:
                i_fit = data[:, 3]
            elif data.shape[1] >= 3:
                i_fit = data[:, 2]
            else:
                i_fit = data[:, 1]
            # DAMMIF .fir / DENSS .fit s-values are Å^-1; BODIES .fir is typically already nm^-1.
            q, i_exp, _ = ensure_q_nm(q, i_exp, None)
        except Exception:
            self._show_status("Fit read error")
            return
        m = np.isfinite(q) & np.isfinite(i_exp) & (i_exp > 0) & np.isfinite(i_fit) & (i_fit > 0)
        if not m.any():
            self._show_status("Empty fit")
            return
        self._click_path = fir_path
        self._ax.clear()
        self._ax.scatter(q[m], i_exp[m], s=8, alpha=0.7, label="exp")
        self._ax.plot(q[m], i_fit[m], "r-", lw=1.2, label=label)
        self._ax.set_yscale("log")
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("I")
        self._ax.legend(fontsize=7)
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
