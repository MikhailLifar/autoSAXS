from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QLabel, QHBoxLayout, QVBoxLayout, QWidget

from .curve_plot import CurvePlot


class DataPanel(QWidget):
    """
    Per-skill panel that shows the most relevant outputs/previews for each skill.
    """

    def __init__(self) -> None:
        super().__init__()
        self._title = QLabel("Per-skill panel")

        self._hint = QLabel("")
        self._hint.setWordWrap(True)

        self._imgs_row = QWidget()
        self._imgs_lay = QHBoxLayout(self._imgs_row)
        self._imgs_lay.setContentsMargins(0, 0, 0, 0)
        self._img_a = QLabel()
        self._img_b = QLabel()
        for im in (self._img_a, self._img_b):
            im.setAlignment(Qt.AlignCenter)
            im.setMinimumHeight(120)
            im.setText("")
        self._imgs_lay.addWidget(self._img_a, 1)
        self._imgs_lay.addWidget(self._img_b, 1)

        self._curve = CurvePlot()
        self._curve.setMinimumHeight(180)

        self._keys = QLabel("")
        self._keys.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._title)
        lay.addWidget(self._hint)
        lay.addWidget(self._imgs_row)
        lay.addWidget(self._curve)
        lay.addWidget(self._keys, 1)

    def set_skill(self, skill_name: str) -> None:
        self._title.setText(f"Per-skill panel: {skill_name}")
        self._hint.setText("Run the skill to see previews here.")
        self._img_a.clear()
        self._img_b.clear()
        self._curve.clear()
        self._keys.setText("")

    def set_result(self, skill_name: str, result: Dict[str, Any]) -> None:
        self._title.setText(f"Per-skill panel: {skill_name}")
        res = result or {}

        # Always show keys.
        keys = sorted(res.keys())
        self._keys.setText("Outputs:\n- " + "\n- ".join(keys) if keys else "No outputs parsed.")

        # Reset previews.
        self._img_a.clear()
        self._img_b.clear()
        self._curve.clear()

        if skill_name == "calibrate":
            self._hint.setText("Calibration curve + mask visualization.")
            self._set_img(self._img_a, res.get("calibration_curve_plot_path"))
            self._set_img(self._img_b, res.get("calibration_mask_path"))
            return

        if skill_name == "subtract":
            self._hint.setText("Diff plot + subtracted curve plot.")
            self._set_img(self._img_a, res.get("diff_plot_path"))
            self._set_img(self._img_b, res.get("sub_plot_path"))
            sub = res.get("subtracted_1d")
            if isinstance(sub, str) and os.path.exists(sub):
                self._curve.plot_dat(sub, label="subtracted")
            return

        if skill_name == "plot":
            self._hint.setText("Standard plots (Guinier/Kratky/log-log).")
            self._set_img(self._img_a, res.get("guinier_plot_path"))
            self._set_img(self._img_b, res.get("kratky_plot_path") or res.get("loglog_plot_path"))
            return

        if skill_name in ("integrate", "integrate_proxy"):
            self._hint.setText("Representative integrated curve preview.")
            integ = res.get("integrated_1d")
            if isinstance(integ, list) and integ:
                p = integ[0]
                if isinstance(p, str) and os.path.exists(p):
                    self._curve.plot_dat(p, label="integrated_1d")
            elif isinstance(integ, str) and os.path.exists(integ):
                self._curve.plot_dat(integ, label="integrated_1d")
            return

        # Fits/analysis: for v1 show first PNG-like output if present.
        self._hint.setText("Outputs available; select artifacts on the right to preview.")

    @staticmethod
    def _set_img(target: QLabel, path: Any) -> None:
        if not isinstance(path, str) or not path or not os.path.exists(path):
            target.setText("")
            return
        pix = QPixmap(path)
        if pix.isNull():
            target.setText("")
            return
        target.setPixmap(pix.scaled(target.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Re-scale any already shown pixmaps
        for img in (self._img_a, self._img_b):
            pm = img.pixmap()
            if pm is not None:
                img.setPixmap(pm.scaled(img.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        super().resizeEvent(event)

