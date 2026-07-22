from __future__ import annotations

from typing import Optional, Tuple

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class CurvePlot(FigureCanvas):
    def __init__(self) -> None:
        self._fig = Figure(figsize=(4, 3), dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._ax.set_xlabel("q")
        self._ax.set_ylabel("I")
        self._ax.set_yscale("log")

    def clear(self) -> None:
        self._ax.clear()
        self._ax.set_xlabel("q")
        self._ax.set_ylabel("I")
        self._ax.set_yscale("log")
        self.draw_idle()

    def plot_dat(self, path: str, *, label: Optional[str] = None) -> None:
        from autosaxs.core.utils import read_saxs

        q, I, sigma, _meta = read_saxs(path)
        self._ax.clear()
        try:
            import numpy as np

            q = np.asarray(q)
            I = np.asarray(I)
            sigma = np.asarray(sigma) if sigma is not None else None
            m = np.isfinite(q) & np.isfinite(I) & (I > 0)
            if sigma is not None:
                m = m & np.isfinite(sigma) & (sigma >= 0)
            q = q[m]
            I = I[m]
            if sigma is not None:
                sigma = sigma[m]
        except Exception:
            pass

        if "np" in locals():
            if sigma is not None and len(I):
                sigma = np.minimum(sigma, 0.99 * I)

        if sigma is not None:
            self._ax.errorbar(
                q,
                I,
                yerr=sigma,
                fmt="o",
                markersize=3.0,
                linewidth=0,
                elinewidth=0.8,
                capsize=0,
                alpha=0.9,
                label=label or path,
            )
        else:
            self._ax.scatter(q, I, s=10, alpha=0.9, label=label or path)
        if label:
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel("q")
        self._ax.set_ylabel("I")
        self._ax.set_yscale("log")
        self._ax.grid(True, alpha=0.2)
        self.draw_idle()

    def plot_two_series(
        self,
        q1,
        y1,
        q2,
        y2,
        *,
        label1: str = "exp",
        label2: str = "fit",
        xlabel: str = "q (nm$^{-1}$)",
        ylabel: str = "I",
    ) -> None:
        """Overlay two 1D series (e.g. experimental vs best BODIES fit from CSV columns)."""
        self._ax.clear()
        self._ax.plot(q1, y1, label=label1, linewidth=1.2)
        self._ax.plot(q2, y2, label=label2, linewidth=1.0, alpha=0.85)
        self._ax.set_xlabel(xlabel)
        self._ax.set_ylabel(ylabel)
        self._ax.legend(fontsize=8)
        self._ax.grid(True, alpha=0.2)
        self.draw_idle()

