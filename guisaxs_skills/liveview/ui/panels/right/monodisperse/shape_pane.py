from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....widgets.viewer_3d import LiveviewViewer3D
from .plots import ShapeFitPlot

try:
    from autosaxs.skill.fit_bodies import BODIES_SHAPES_LIST
except Exception:
    BODIES_SHAPES_LIST = [
        "cylinder",
        "dumbbell",
        "ellipsoid",
        "elliptic-cylinder",
        "hollow-cylinder",
        "hollow-sphere",
        "parallelepiped",
        "rotation-ellipsoid",
    ]


class ShapePane(QWidget):
    mode_changed = pyqtSignal(str)
    rerun_shape_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        mode_row = QHBoxLayout()
        self._grp = QButtonGroup(self)
        self._rb_none = QRadioButton("None")
        self._rb_bodies = QRadioButton("BODIES")
        self._rb_dammif = QRadioButton("DAMMIF")
        self._rb_none.setChecked(True)
        for rb in (self._rb_none, self._rb_bodies, self._rb_dammif):
            self._grp.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        self._rerun = QPushButton("Re-run shape")
        self._rerun.clicked.connect(self.rerun_shape_requested.emit)
        mode_row.addWidget(self._rerun)

        body = QHBoxLayout()
        body.setSpacing(10)
        fit_box = QGroupBox("I(q) fit")
        fit_lay = QVBoxLayout(fit_box)
        fit_lay.setContentsMargins(6, 8, 6, 6)
        self._fit_plot = ShapeFitPlot()
        self._fit_plot.setMinimumHeight(120)
        self._fit_plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        fit_lay.addWidget(self._fit_plot, 1)
        view_box = QGroupBox("3D")
        view_lay = QVBoxLayout(view_box)
        view_lay.setContentsMargins(6, 8, 6, 6)
        self._viewer = LiveviewViewer3D()
        self._viewer.setMinimumHeight(120)
        view_lay.addWidget(self._viewer, 1)
        ctrl_box = QGroupBox("Controls")
        ctrl_lay = QVBoxLayout(ctrl_box)
        ctrl_lay.setContentsMargins(6, 8, 6, 6)
        self._lbl_models = QLabel("Body models")
        ctrl_lay.addWidget(self._lbl_models)
        self._shapes = QListWidget()
        for name in BODIES_SHAPES_LIST:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._shapes.addItem(item)
        self._shapes.itemChanged.connect(lambda *_: self.mode_changed.emit(self.shape_mode()))
        ctrl_lay.addWidget(self._shapes, 1)
        self._lbl_status = QLabel("—")
        self._lbl_status.setWordWrap(True)
        ctrl_lay.addWidget(self._lbl_status)
        self._fit_box = fit_box
        self._view_box = view_box
        self._ctrl_box = ctrl_box
        body.addWidget(fit_box, 2)
        body.addWidget(view_box, 2)
        body.addWidget(ctrl_box, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(mode_row)
        lay.addLayout(body, 1)

        self._rb_none.toggled.connect(lambda on: on and self.mode_changed.emit("none"))
        self._rb_bodies.toggled.connect(lambda on: on and self.mode_changed.emit("bodies"))
        self._rb_dammif.toggled.connect(lambda on: on and self.mode_changed.emit("dammif"))
        self._update_mode_ui()

    def shape_mode(self) -> str:
        if self._rb_dammif.isChecked():
            return "dammif"
        if self._rb_bodies.isChecked():
            return "bodies"
        return "none"

    def set_shape_mode(self, mode: str) -> None:
        m = (mode or "none").strip().lower()
        if m == "dammif":
            self._rb_dammif.setChecked(True)
        elif m == "bodies":
            self._rb_bodies.setChecked(True)
        else:
            self._rb_none.setChecked(True)
        self._update_mode_ui()

    def selected_shapes(self) -> List[str]:
        out: List[str] = []
        for i in range(self._shapes.count()):
            item = self._shapes.item(i)
            if item.checkState() == Qt.Checked:
                out.append(item.text())
        return out

    def set_selected_shapes(self, shapes: Optional[List[str]]) -> None:
        want = set(shapes or [])
        for i in range(self._shapes.count()):
            item = self._shapes.item(i)
            item.setCheckState(Qt.Checked if not want or item.text() in want else Qt.Unchecked)

    def set_running(self, running: bool) -> None:
        # Global busy: lock Re-run and BODIES checklist; keep mode radios enabled (config).
        self._rerun.setEnabled(not running and self.shape_mode() != "none")
        self._shapes.setEnabled(not running and self.shape_mode() == "bodies")

    def set_rerun_enabled(self, enabled: bool) -> None:
        self._rerun.setEnabled(bool(enabled) and self.shape_mode() != "none")

    def set_status(self, text: str) -> None:
        self._lbl_status.setText(text or "—")

    def show_fir(self, fir_path: str, *, label: str = "fit") -> None:
        self._fit_plot.plot_from_fir(fir_path, label=label)

    def clear_view(self) -> None:
        self._fit_plot.clear_plot()
        self._viewer.clear()
        self.set_status("—")

    def _update_mode_ui(self) -> None:
        mode = self.shape_mode()
        active = mode != "none"
        self._fit_box.setVisible(active)
        self._view_box.setVisible(active)
        bodies = mode == "bodies"
        self._lbl_models.setVisible(bodies)
        self._shapes.setVisible(bodies)
        if mode == "none":
            self.set_status("Select BODIES or DAMMIF to include in automatic processing; Re-run shape for a manual re-run.")
        elif mode == "dammif":
            self.set_status("DAMMIF runs automatically after GNOM when processing TIFFs. Use Re-run shape to re-execute on the current profile.")
        else:
            self.set_status("BODIES runs automatically after GNOM when processing TIFFs. Use Re-run shape to re-execute on the current profile.")

    @property
    def viewer(self) -> LiveviewViewer3D:
        return self._viewer

    @property
    def fit_plot(self) -> ShapeFitPlot:
        return self._fit_plot
