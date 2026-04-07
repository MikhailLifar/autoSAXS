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

    def clear(self) -> None:
        self._ax.clear()
        self._ax.set_xlabel("q")
        self._ax.set_ylabel("I")
        self.draw_idle()

    def plot_dat(self, path: str, *, label: Optional[str] = None) -> None:
        from autosaxs.utils import read_saxs

        q, I, sigma, _meta = read_saxs(path)
        self._ax.clear()
        self._ax.plot(q, I, label=label or path)
        if label:
            self._ax.legend(fontsize=8)
        self._ax.set_xlabel("q")
        self._ax.set_ylabel("I")
        self._ax.grid(True, alpha=0.2)
        self.draw_idle()

