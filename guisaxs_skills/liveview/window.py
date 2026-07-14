from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (
    QAction,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..logic.path_display import contracted_path_label
from ..ui.about_dialog import AboutDialog
from ..ui.html_help_dialog import HtmlHelpDialog
from ..ui.update_dialog import request_app_update
from .controller import LiveviewController
from .session.state import LiveviewWatchMode
from .ui.panels import LiveviewLeftPanel, LiveviewMiddlePanel, LiveviewRightPanel
from .ui.wizards.subtraction import SubtractionWizardDialog
from .session.workdir import select_watchdir


class LiveviewMainWindow(QMainWindow):
    def __init__(self, *, watchdir: Path) -> None:
        super().__init__()
        self._controller = LiveviewController(watchdir=watchdir)
        self._state = self._controller.state

        self.setWindowTitle("guisaxs-liveview")

        self._splitter = QSplitter(Qt.Horizontal)
        self._left = LiveviewLeftPanel(state=self._state)
        self._middle = LiveviewMiddlePanel()
        self._right = LiveviewRightPanel(state=self._state)

        self._splitter.addWidget(self._left)
        self._splitter.addWidget(self._middle)
        self._splitter.addWidget(self._right)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 1)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        wd_short, wd_full = contracted_path_label(watchdir)
        self._watchdir_label = QLabel(wd_short)
        self._watchdir_label.setToolTip(f"Watchdir\n{wd_full}")
        self._watchdir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._watchdir_label)
        layout.addWidget(self._splitter, 1)
        self.setCentralWidget(container)

        self._act_switch_flat: QAction | None = None
        self._act_switch_tree: QAction | None = None
        self._init_menu()

        self._controller.bind_panels(left=self._left, middle=self._middle, right=self._right, parent=self)
        self._wire_ui()
        self._sub_wizard: SubtractionWizardDialog | None = None

    def _enforce_column_width_ratio(self) -> None:
        sp = self._splitter
        total = int(sp.width())
        if total < 320:
            return
        unit = total // 5
        left_sz = unit
        right_sz = unit
        mid_sz = total - left_sz - right_sz
        if mid_sz < 200:
            return
        min_side = 240
        max_side = max(min_side, left_sz)
        self._left.setMaximumWidth(max_side)
        self._right.setMaximumWidth(max_side)
        try:
            sp.setSizes([left_sz, mid_sz, right_sz])
        except Exception:
            pass

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._enforce_column_width_ratio)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._enforce_column_width_ratio()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._controller.shutdown()
        super().closeEvent(event)

    def _init_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("File")
        act_open = QAction("Open watch directory…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._on_change_watchdir)
        file_menu.addAction(act_open)

        file_menu.addSeparator()
        self._act_switch_flat = QAction("Switch to flat directory", self)
        self._act_switch_flat.setToolTip("Watch top-level TIFFs only; outputs under watchdir.")
        self._act_switch_flat.triggered.connect(lambda: self._set_watch_mode(LiveviewWatchMode.FLAT))
        file_menu.addAction(self._act_switch_flat)

        self._act_switch_tree = QAction("Switch to tree directory", self)
        self._act_switch_tree.setToolTip("Recursive TIFF discovery; outputs beside each TIFF.")
        self._act_switch_tree.triggered.connect(lambda: self._set_watch_mode(LiveviewWatchMode.TREE))
        file_menu.addAction(self._act_switch_tree)
        self._sync_watch_mode_menu()

        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.setShortcut(QKeySequence.Quit)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        update_menu = mb.addMenu("Update")
        act_update = QAction("Update to latest version…", self)
        act_update.triggered.connect(self._on_update_requested)
        update_menu.addAction(act_update)

        help_menu = mb.addMenu("Help")
        act_help = QAction("guisaxs-liveview Help…", self)
        act_help.setShortcut(QKeySequence.HelpContents)
        act_help.triggered.connect(self._on_help_requested)
        help_menu.addAction(act_help)
        act_about = QAction("About guisaxs-liveview…", self)
        act_about.triggered.connect(self._on_about_requested)
        help_menu.addAction(act_about)

    def _sync_watch_mode_menu(self) -> None:
        tree = self._state.watch_mode == LiveviewWatchMode.TREE
        if self._act_switch_flat is not None:
            self._act_switch_flat.setVisible(tree)
        if self._act_switch_tree is not None:
            self._act_switch_tree.setVisible(not tree)

    def _on_help_requested(self) -> None:
        dlg = HtmlHelpDialog(title="guisaxs-liveview Help", parent=self)
        if dlg.is_ready():
            dlg.exec_()

    def _on_about_requested(self) -> None:
        AboutDialog(parent=self).exec_()

    def _on_update_requested(self) -> None:
        if self._controller.runner.is_running():
            QMessageBox.warning(
                self,
                "Update",
                "A skill is still running. Wait for it to finish, then try again.",
            )
            return
        request_app_update(parent=self)

    def _set_watch_mode(self, new_mode: LiveviewWatchMode) -> None:
        self._controller.set_watch_mode(new_mode)
        self._sync_watch_mode_menu()

    def _on_change_watchdir(self) -> None:
        cur = str(self._controller.watchdir)
        chosen = select_watchdir(parent=self, initial_directory=cur)
        if not chosen:
            return
        new_p = Path(chosen).resolve()
        if new_p == self._controller.watchdir:
            return
        if not self._controller.switch_watchdir(new_p):
            return
        wd_short, wd_full = contracted_path_label(new_p)
        self._watchdir_label.setText(wd_short)
        self._watchdir_label.setToolTip(f"Watchdir\n{wd_full}")
        self._sync_watch_mode_menu()

    def _wire_ui(self) -> None:
        self._left.calibration_changed.connect(self._controller.run_calibration)
        self._left.calibration_cancel_requested.connect(self._controller.cancel_running_skill)
        self._left.calibration_reset_requested.connect(self._controller.reset_calibration)
        self._left.buffer_reset_requested.connect(self._controller.reset_buffer)
        self._left.subtract_config_changed.connect(self._controller.on_subtract_config_changed)
        self._right.fit_sizes_run_requested.connect(self._controller.run_fit_sizes)
        self._right.fit_mixture_run_requested.connect(self._controller.run_fit_mixture)
        self._right.monodisperse_wizard_open_requested.connect(self._controller.on_monodisperse_wizard_open)
        self._right.monodisperse_intervention.connect(self._controller.on_monodisperse_intervention)
        self._right.monodisperse_shape_config.connect(self._controller.on_monodisperse_shape_config)
        self._right.monodisperse_guinier_chain.connect(self._controller.on_monodisperse_guinier_chain)
        self._right.monodisperse_gnom_rerun.connect(self._controller.on_monodisperse_gnom_rerun)
        self._right.monodisperse_shape_rerun.connect(self._controller.on_monodisperse_shape_rerun)
        self._right.monodisperse_resume_queue.connect(self._controller.on_monodisperse_resume_queue)
        self._right.monodisperse_stop_queue.connect(self._controller.on_monodisperse_stop_queue)
        self._middle.tiff_files_dropped.connect(self._on_tiff_files_dropped)
        self._middle.history_step.connect(self._controller.history_step)
        self._middle.process_history_file_requested.connect(self._controller.process_history_file)
        self._middle.subtraction_wizard_requested.connect(self._open_subtraction_wizard)
        self._right.analysis_mode_changed.connect(self._controller.on_analysis_mode_changed)

    def _open_subtraction_wizard(self) -> None:
        from ..session.state import LiveviewState
        from .controller.monodisperse import PAUSE_SOURCE_SUBTRACTION

        st = self._state.current_state()
        if st not in (LiveviewState.C, LiveviewState.CD):
            QMessageBox.information(
                self,
                "Subtraction wizard",
                "Subtraction scaling is available after buffer subtraction is configured "
                "(sample + buffer curves for the current file).",
            )
            return
        ctx = self._controller.middle_subtraction_context()
        sample_dat = str(ctx.get("sample_dat") or "")
        buffer_dat = str(ctx.get("buffer_dat") or "")
        subtracted_dat = str(ctx.get("subtracted_dat") or "")
        subtract_options = ctx.get("subtract_options") if isinstance(ctx.get("subtract_options"), dict) else {}
        if not sample_dat or not buffer_dat:
            QMessageBox.warning(
                self,
                "Subtraction wizard",
                "No sample/buffer curves are loaded for the current file.",
            )
            return
        if self._sub_wizard is not None:
            try:
                self._sub_wizard.close()
            except Exception:
                pass
        self._controller.pause_executor(source=PAUSE_SOURCE_SUBTRACTION)
        self._sub_wizard = SubtractionWizardDialog(
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
            subtracted_dat=subtracted_dat,
            subtract_options=subtract_options,
            parent=self,
        )
        self._sub_wizard.preview_scale_changed.connect(self._controller.preview_manual_subtraction_scale)
        self._sub_wizard.apply_requested.connect(self._on_subtraction_apply_requested)
        try:
            self._sub_wizard.finished.connect(self._on_subtraction_wizard_finished)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._sub_wizard.show()
        self._sub_wizard.raise_()
        self._sub_wizard.activateWindow()

    def _on_subtraction_wizard_finished(self, _code: int) -> None:
        from .controller.monodisperse import PAUSE_SOURCE_SUBTRACTION

        self._controller.resume_executor(source=PAUSE_SOURCE_SUBTRACTION)

    def _on_subtraction_apply_requested(self, scaling_factor: float) -> None:
        ctx = self._controller.middle_subtraction_context()
        sample_dat = str(ctx.get("sample_dat") or "").strip()
        buffer_dat = str(ctx.get("buffer_dat") or "").strip()
        if not sample_dat or not buffer_dat:
            QMessageBox.warning(self, "Subtraction", "Missing sample/buffer curves for the current file.")
            return
        try:
            if self._sub_wizard is not None:
                self._sub_wizard.close()
        except Exception:
            pass
        self._controller.apply_subtraction_rerun(
            scaling_factor=float(scaling_factor),
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
        )

    def _on_tiff_files_dropped(self, paths: object) -> None:
        if not isinstance(paths, list):
            return
        str_paths = [p for p in paths if isinstance(p, str)]
        self._controller.ingest_dropped_tiffs(str_paths)
