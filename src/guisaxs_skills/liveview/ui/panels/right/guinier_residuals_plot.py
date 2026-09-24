"""Embedded Guinier residual plot canvas for the Guinier adjust wizard."""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt


def draw_guinier_residuals_on_ax(
    ax,
    q,
    I,
    sigma,
    *,
    rg: float,
    i0: float,
    first_point_1based: int,
    last_point_1based: int,
) -> Optional[str]:
    """
    Draw ``(I − I_fit) / (σ + 0.1)`` vs q on the Guinier fit window.

    ``I_fit = I0 * exp(-(Rg²/3) * q²)``. Returns an error status string, or None.
    """
    try:
        rg_f = float(rg)
        i0_f = float(i0)
        first = int(first_point_1based)
        last = int(last_point_1based)
    except (TypeError, ValueError):
        return "Incomplete Guinier fit"
    if first < 1 or last < first:
        return "Invalid first/last"
    q_arr = np.asarray(q, dtype=float)
    I_arr = np.asarray(I, dtype=float)
    if q_arr.size == 0 or I_arr.size == 0:
        return "Empty profile"
    i0z = first - 1
    i1z = min(len(q_arr) - 1, last - 1)
    if i1z < i0z:
        return "Invalid first/last"
    qq = q_arr[i0z : i1z + 1]
    ye = I_arr[i0z : i1z + 1]
    if sigma is not None:
        sig_arr = np.asarray(sigma, dtype=float)
        if sig_arr.size == I_arr.size:
            sig = sig_arr[i0z : i1z + 1]
        else:
            sig = None
    else:
        sig = None
    yf = i0_f * np.exp(-(rg_f ** 2 / 3.0) * (qq ** 2))
    m = np.isfinite(qq) & np.isfinite(ye) & np.isfinite(yf)
    if not m.any():
        return "Empty residuals"
    qq, ye, yf = qq[m], ye[m], yf[m]
    if sig is not None:
        sig = sig[m]
        if np.any(np.isfinite(sig) & (sig > 0)):
            denom = np.abs(np.where(np.isfinite(sig), sig, 0.0)) + 0.1
            ylabel = r"$(I-I_{\mathrm{fit}})/(\sigma+0.1)$"
        else:
            denom = np.abs(ye) + 0.1
            ylabel = r"$(I-I_{\mathrm{fit}})/(|I|+0.1)$"
    else:
        denom = np.abs(ye) + 0.1
        ylabel = r"$(I-I_{\mathrm{fit}})/(|I|+0.1)$"
    with np.errstate(divide="ignore", invalid="ignore"):
        resid = (ye - yf) / denom
    ax.clear()
    ax.axhline(0.0, color="0.5", lw=0.8)
    ax.plot(qq, resid, "C1-", lw=1.0)
    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    return None


class GuinierResidualsPlot(FigureCanvas):
    def __init__(self, *, figsize=(3.2, 2.4)) -> None:
        self._fig = Figure(figsize=figsize, dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._click_path: Optional[str] = None
        self._click_viewer: Optional[str] = None
        self._last_payload: Optional[dict[str, Any]] = None

    @property
    def click_path(self) -> Optional[str]:
        return self._click_path

    @property
    def click_viewer(self) -> Optional[str]:
        return self._click_viewer

    @property
    def last_payload(self) -> Optional[dict[str, Any]]:
        return self._last_payload

    def _show_status(self, text: str) -> None:
        self._click_path = None
        self._click_viewer = None
        self._last_payload = None
        self._ax.clear()
        self._ax.text(0.5, 0.5, text, ha="center", va="center", transform=self._ax.transAxes, fontsize=9)
        self._ax.set_axis_off()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def clear_plot(self) -> None:
        self._show_status("—")

    def plot_from_arrays(
        self,
        q,
        I,
        sigma,
        *,
        rg: float,
        i0: float,
        first_point_1based: int,
        last_point_1based: int,
        click_path: str = "",
    ) -> None:
        err = draw_guinier_residuals_on_ax(
            self._ax,
            q,
            I,
            sigma,
            rg=rg,
            i0=i0,
            first_point_1based=first_point_1based,
            last_point_1based=last_point_1based,
        )
        if err:
            self._show_status(err)
            return
        self._last_payload = {
            "q": np.asarray(q, dtype=float),
            "I": np.asarray(I, dtype=float),
            "sigma": None if sigma is None else np.asarray(sigma, dtype=float),
            "rg": float(rg),
            "i0": float(i0),
            "first_point_1based": int(first_point_1based),
            "last_point_1based": int(last_point_1based),
        }
        path = str(click_path or "").strip()
        self._click_path = path if path and os.path.isfile(path) else None
        self._click_viewer = "guinier_residuals" if self._click_path else None
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor if self._click_path else Qt.ArrowCursor)

    def plot_from_payload(self, payload: Mapping[str, Any], *, click_path: str = "") -> None:
        if not payload:
            self._show_status("—")
            return
        self.plot_from_arrays(
            payload.get("q"),
            payload.get("I"),
            payload.get("sigma"),
            rg=float(payload["rg"]),
            i0=float(payload["i0"]),
            first_point_1based=int(payload["first_point_1based"]),
            last_point_1based=int(payload["last_point_1based"]),
            click_path=click_path,
        )
