from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ....widgets.viewer_3d import LiveviewViewer3D
from .....session.state import DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES
from .plots import ShapeFitPlot

try:
    from autosaxs.skill.model_bodies import BODIES_SHAPES_LIST
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

_DAMMIF_INFO = (
    "Independent DAMMIF reconstructions (n_runs).\n\n"
    "• Value is used on the next Re-run shape or the next automatic TIFF — "
    "changing it does not start a fit.\n"
    "• n_runs = 1: single reconstruction (fast mode).\n"
    "• n_runs ≥ 2: DAMAVER builds an occupancy / stability map; "
    "the data-fitting shape remains the most probable DAMMIF model.\n"
    "• Embedded 3D and I(q) always show the most probable model; "
    "open the dedicated 3D viewer to compare runs and the occupancy map."
)

_DENSS_INFO = (
    "DENSS continuous electron-density reconstruction (model-density).\n\n"
    "• Protocol is used on the next Re-run shape or the next automatic TIFF — "
    "changing it does not start a fit.\n"
    "• pilot (default): single reconstruction (quick look).\n"
    "• average: denss-all (N maps → align → average → FSC) + voxel σ map; "
    "3D surface is colored by σ.\n"
    "• refined: average then denss-refine against the data.\n"
    "• Optional GNOM .out supplies Dmax only; DENSS fits I(q) from the profile."
)


class ShapePane(QWidget):
    mode_changed = pyqtSignal(str)
    n_runs_changed = pyqtSignal(int)
    denss_settings_changed = pyqtSignal()
    rerun_shape_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        mode_row = QHBoxLayout()
        self._grp = QButtonGroup(self)
        self._rb_none = QRadioButton("None")
        self._rb_bodies = QRadioButton("BODIES")
        self._rb_dammif = QRadioButton("DAMMIF")
        self._rb_denss = QRadioButton("DENSS")
        self._rb_none.setChecked(True)
        for rb in (self._rb_none, self._rb_bodies, self._rb_dammif, self._rb_denss):
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
        default_shapes = set(DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES)
        for name in BODIES_SHAPES_LIST:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in default_shapes else Qt.Unchecked)
            self._shapes.addItem(item)
        self._shapes.itemChanged.connect(lambda *_: self.mode_changed.emit(self.shape_mode()))
        ctrl_lay.addWidget(self._shapes, 1)

        n_runs_row = QHBoxLayout()
        self._lbl_n_runs = QLabel("n_runs")
        n_runs_row.addWidget(self._lbl_n_runs)
        self._btn_dammif_info = QPushButton("?")
        self._btn_dammif_info.setObjectName("helpButton")
        self._btn_dammif_info.setFixedSize(22, 22)
        self._btn_dammif_info.setToolTip("DAMMIF / n_runs help")
        self._btn_dammif_info.clicked.connect(self._show_dammif_info)
        n_runs_row.addWidget(self._btn_dammif_info)
        n_runs_row.addStretch(1)
        ctrl_lay.addLayout(n_runs_row)
        self._n_runs = QSpinBox()
        self._n_runs.setRange(1, 20)
        self._n_runs.setValue(1)
        self._n_runs.setToolTip("Number of independent DAMMIF runs (see info).")
        self._n_runs.valueChanged.connect(self._emit_n_runs_changed)
        ctrl_lay.addWidget(self._n_runs)

        denss_proto_row = QHBoxLayout()
        self._lbl_denss_protocol = QLabel("protocol")
        denss_proto_row.addWidget(self._lbl_denss_protocol)
        self._btn_denss_info = QPushButton("?")
        self._btn_denss_info.setObjectName("helpButton")
        self._btn_denss_info.setFixedSize(22, 22)
        self._btn_denss_info.setToolTip("DENSS protocol help")
        self._btn_denss_info.clicked.connect(self._show_denss_info)
        denss_proto_row.addWidget(self._btn_denss_info)
        denss_proto_row.addStretch(1)
        ctrl_lay.addLayout(denss_proto_row)
        self._denss_protocol = QComboBox()
        self._denss_protocol.addItem("pilot", "pilot")
        self._denss_protocol.addItem("average", "average")
        self._denss_protocol.addItem("refined", "refined")
        self._denss_protocol.setCurrentIndex(0)
        self._denss_protocol.currentIndexChanged.connect(self._on_denss_settings_changed)
        ctrl_lay.addWidget(self._denss_protocol)

        self._lbl_denss_mode = QLabel("denss_mode")
        ctrl_lay.addWidget(self._lbl_denss_mode)
        self._denss_mode = QComboBox()
        self._denss_mode.addItem("fast", "fast")
        self._denss_mode.addItem("slow", "slow")
        self._denss_mode.addItem("membrane", "membrane")
        self._denss_mode.setCurrentIndex(0)
        self._denss_mode.currentIndexChanged.connect(self._on_denss_settings_changed)
        ctrl_lay.addWidget(self._denss_mode)

        self._lbl_denss_n_maps = QLabel("n_maps")
        ctrl_lay.addWidget(self._lbl_denss_n_maps)
        self._denss_n_maps = QSpinBox()
        self._denss_n_maps.setRange(2, 100)
        self._denss_n_maps.setValue(20)
        self._denss_n_maps.setToolTip("Number of DENSS maps for average/refined.")
        self._denss_n_maps.valueChanged.connect(self._on_denss_settings_changed)
        ctrl_lay.addWidget(self._denss_n_maps)

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
        self._rb_denss.toggled.connect(lambda on: on and self.mode_changed.emit("denss"))
        self._update_mode_ui()

    def shape_mode(self) -> str:
        if self._rb_dammif.isChecked():
            return "dammif"
        if self._rb_bodies.isChecked():
            return "bodies"
        if self._rb_denss.isChecked():
            return "denss"
        return "none"

    def set_shape_mode(self, mode: str) -> None:
        m = (mode or "none").strip().lower()
        if m == "dammif":
            self._rb_dammif.setChecked(True)
        elif m == "bodies":
            self._rb_bodies.setChecked(True)
        elif m == "denss":
            self._rb_denss.setChecked(True)
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
        """Apply checklist; empty/None → default (ellipsoid only)."""
        want = set(shapes) if shapes else set(DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES)
        for i in range(self._shapes.count()):
            item = self._shapes.item(i)
            item.setCheckState(Qt.Checked if item.text() in want else Qt.Unchecked)

    def set_running(self, running: bool) -> None:
        # Global busy: lock Re-run and BODIES checklist; keep mode radios enabled (config).
        self._rerun.setEnabled(not running and self.shape_mode() != "none")
        self._shapes.setEnabled(not running and self.shape_mode() == "bodies")
        self._n_runs.setEnabled(not running and self.shape_mode() == "dammif")
        denss = self.shape_mode() == "denss" and not running
        self._denss_protocol.setEnabled(denss)
        self._denss_mode.setEnabled(denss)
        self._denss_n_maps.setEnabled(denss and self.denss_protocol() != "pilot")

    def set_rerun_enabled(self, enabled: bool) -> None:
        self._rerun.setEnabled(bool(enabled) and self.shape_mode() != "none")

    def n_runs(self) -> int:
        return int(self._n_runs.value())

    def set_n_runs(self, n: int) -> None:
        try:
            v = int(n)
        except (TypeError, ValueError):
            v = 1
        self._n_runs.blockSignals(True)
        self._n_runs.setValue(max(1, min(20, v)))
        self._n_runs.blockSignals(False)

    def denss_protocol(self) -> str:
        data = self._denss_protocol.currentData()
        return str(data or "pilot")

    def set_denss_protocol(self, mode: str) -> None:
        key = (mode or "pilot").strip().lower()
        idx = self._denss_protocol.findData(key)
        if idx < 0:
            idx = 0
        self._denss_protocol.blockSignals(True)
        self._denss_protocol.setCurrentIndex(idx)
        self._denss_protocol.blockSignals(False)
        self._update_mode_ui()

    def denss_mode(self) -> str:
        data = self._denss_mode.currentData()
        return str(data or "fast")

    def set_denss_mode(self, mode: str) -> None:
        key = (mode or "fast").strip().lower()
        idx = self._denss_mode.findData(key)
        if idx < 0:
            idx = 0
        self._denss_mode.blockSignals(True)
        self._denss_mode.setCurrentIndex(idx)
        self._denss_mode.blockSignals(False)

    def denss_n_maps(self) -> int:
        return int(self._denss_n_maps.value())

    def set_denss_n_maps(self, n: int) -> None:
        try:
            v = int(n)
        except (TypeError, ValueError):
            v = 20
        self._denss_n_maps.blockSignals(True)
        self._denss_n_maps.setValue(max(2, min(100, v)))
        self._denss_n_maps.blockSignals(False)

    def _emit_n_runs_changed(self, value: int) -> None:
        self.n_runs_changed.emit(int(value))

    def _on_denss_settings_changed(self, *_args) -> None:
        self._update_mode_ui()
        self.denss_settings_changed.emit()

    def _show_dammif_info(self) -> None:
        QMessageBox.information(self, "DAMMIF / n_runs", _DAMMIF_INFO)

    def _show_denss_info(self) -> None:
        QMessageBox.information(self, "DENSS / protocol", _DENSS_INFO)

    def set_status(self, text: str) -> None:
        self._lbl_status.setText(text or "—")

    def show_fir(self, fir_path: str, *, label: str = "fit") -> None:
        self._fit_plot.plot_from_fir(fir_path, label=label)

    def show_map_fit(self, fit_path: str, *, label: str = "DENSS") -> None:
        self._fit_plot.plot_from_fir(fit_path, label=label)

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
        dammif = mode == "dammif"
        denss = mode == "denss"
        self._lbl_models.setVisible(bodies)
        self._shapes.setVisible(bodies)
        self._lbl_n_runs.setVisible(dammif)
        self._btn_dammif_info.setVisible(dammif)
        self._n_runs.setVisible(dammif)
        self._n_runs.setEnabled(dammif)
        for w in (
            self._lbl_denss_protocol,
            self._btn_denss_info,
            self._denss_protocol,
            self._lbl_denss_mode,
            self._denss_mode,
            self._lbl_denss_n_maps,
            self._denss_n_maps,
        ):
            w.setVisible(denss)
        show_n_maps = denss and self.denss_protocol() != "pilot"
        self._lbl_denss_n_maps.setVisible(show_n_maps)
        self._denss_n_maps.setVisible(show_n_maps)
        self._denss_n_maps.setEnabled(show_n_maps)
        if mode == "none":
            self.set_status(
                "Select BODIES, DAMMIF, or DENSS for automatic processing; Re-run shape for a manual re-run."
            )
        elif mode == "dammif":
            self.set_status("DAMMIF after GNOM · Re-run shape to apply n_runs.")
        elif mode == "denss":
            self.set_status(
                f"DENSS ({self.denss_protocol()}) · Re-run shape to apply protocol settings."
            )
        else:
            self.set_status("BODIES after GNOM · Re-run shape to re-execute on the current profile.")

    @property
    def viewer(self) -> LiveviewViewer3D:
        return self._viewer

    @property
    def fit_plot(self) -> ShapeFitPlot:
        return self._fit_plot
