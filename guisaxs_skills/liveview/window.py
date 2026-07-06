from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
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

from ..core.event_bus import EventBus
from ..core.models import RunRequest
from ..core.paths import latest_stderr_path, runs_dir
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from .executor import LiveviewJobExecutor, LiveviewQueueStatus
from .session_persistence import load_liveview_session_settings, save_liveview_session_settings
from .state import LiveviewSessionState, LiveviewState
from .state import AnalysisMode
from .workdir import save_last_watchdir, select_watchdir
from .watcher import DirectoryWatcher, WatcherConfig
from .poll_watcher import POLL_TRIGGERED_STABILITY, ProcessedTiffPoller, PollWatcherConfig
from .dir_tree_observer import TREE_STABILITY, TreeDirObserver, TreeObserverConfig
from .output_paths import tiff_history_label
from .logic.middle_from_stem import apply_middle_view_from_disk
from .logic.right_from_stem import apply_right_outputs_from_disk
from ..logic.path_display import contracted_path_label
from ..ui.about_dialog import AboutDialog
from ..ui.html_help_dialog import HtmlHelpDialog
from ..ui.update_dialog import request_app_update
from .state import LiveviewWatchMode
from .tiff_revision import TiffRevision, TiffRevisionSource, make_revision
from .stability import FileStatSnapshot
from .ui.left_panel import LiveviewLeftPanel, pick_calibration_curve_image_path
from .ui.middle_panel import LiveviewMiddlePanel
from .ui.right_panel import LiveviewRightPanel
from .ui.subtraction_wizard import SubtractionWizardDialog


class LiveviewMainWindow(QMainWindow):
    def __init__(self, *, bus: EventBus, watchdir: Path) -> None:
        super().__init__()
        self._bus = bus
        self._state = LiveviewSessionState(watchdir=watchdir)
        load_liveview_session_settings(self._state)

        self._runner = SkillRunner(workdir=watchdir)
        self._executor = LiveviewJobExecutor(state=self._state, runner=self._runner)
        self._watcher = DirectoryWatcher(
            directory=watchdir,
            cfg=WatcherConfig(recursive=False),
            on_revision=self._ingest_tiff_revision,
        )
        self._poll_watcher = ProcessedTiffPoller(
            cfg=PollWatcherConfig(),
            on_revision=self._ingest_tiff_revision_from_poll,
        )
        self._tree_observer = TreeDirObserver(
            cfg=TreeObserverConfig(),
            watchdir=watchdir,
            on_revision=self._ingest_tiff_revision_from_tree,
        )
        self._connect_executor(self._executor)

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

        self._last_2d_shown: Optional[Tuple[str, FileStatSnapshot]] = None
        self._history_index: int = 0
        self._watchdir_resolved = watchdir.resolve()
        self._executor.start()
        self._apply_watch_mode_watchers()
        self._wire_ui()
        self._apply_loaded_session_to_ui()
        self._refresh_history_chrome()
        self._sub_wizard: SubtractionWizardDialog | None = None

    def _enforce_column_width_ratio(self) -> None:
        """Keep left:middle:right at 1:3:1 (spec). Right panel children can request huge min widths; cap and setSizes."""
        sp = self._splitter
        total = int(sp.width())
        if total < 320:
            return
        # Integer fifths for 1:3:1
        unit = total // 5
        left_sz = unit
        right_sz = unit
        mid_sz = total - left_sz - right_sz
        if mid_sz < 200:
            return
        # Prevent wide child widgets from stealing horizontal space from the middle column
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
        self._persist_session_settings()
        try:
            self._watcher.stop()
        except Exception:
            pass
        try:
            self._poll_watcher.stop()
        except Exception:
            pass
        try:
            self._tree_observer.stop()
        except Exception:
            pass
        try:
            self._executor.stop()
        except Exception:
            pass
        try:
            self._runner.cancel()
        except Exception:
            pass
        super().closeEvent(event)

    def _connect_executor(self, executor: LiveviewJobExecutor) -> None:
        executor.queue_status.connect(self._on_queue_status)
        executor.error.connect(self._on_error)
        executor.latest_artifacts.connect(self._on_latest_artifacts)
        executor.session_file_completed.connect(self._on_session_file_completed)
        executor.session_file_completed.connect(self._poll_watcher.track_processed_path)
        executor.tiff_revision_pending.connect(self._on_tiff_revision_pending)
        self._poll_watcher.set_idle_check(executor.is_idle)
        self._tree_observer.set_idle_check(executor.is_idle)

    def _init_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("File")
        act_open = QAction("Open watch directory…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._on_change_watchdir)
        file_menu.addAction(act_open)

        file_menu.addSeparator()
        self._act_switch_flat = QAction("Switch to flat directory", self)
        self._act_switch_flat.setToolTip(
            "Watch top-level TIFFs only; outputs under watchdir."
        )
        self._act_switch_flat.triggered.connect(
            lambda: self._set_watch_mode(LiveviewWatchMode.FLAT)
        )
        file_menu.addAction(self._act_switch_flat)

        self._act_switch_tree = QAction("Switch to tree directory", self)
        self._act_switch_tree.setToolTip(
            "Recursive TIFF discovery; outputs beside each TIFF."
        )
        self._act_switch_tree.triggered.connect(
            lambda: self._set_watch_mode(LiveviewWatchMode.TREE)
        )
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
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Update",
                "A skill is still running. Wait for it to finish, then try again.",
            )
            return
        request_app_update(parent=self)

    def _set_watch_mode(self, new_mode: LiveviewWatchMode) -> None:
        if new_mode == self._state.watch_mode:
            return
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Watch mode",
                "A skill is still running. Wait for it to finish, then switch watch mode.",
            )
            return
        self._state.watch_mode = new_mode
        self._persist_session_settings()
        self._sync_watch_mode_menu()
        self._apply_watch_mode_watchers()
        self._refresh_history_chrome()
        if self._executor.session_processed_tiffs:
            self._reload_history_view()

    def _update_watch_mode_button(self) -> None:
        self._sync_watch_mode_menu()

    def _apply_watch_mode_watchers(self) -> None:
        if self._state.watch_mode == LiveviewWatchMode.TREE:
            try:
                self._watcher.stop()
            except Exception:
                pass
            try:
                self._poll_watcher.stop()
            except Exception:
                pass
            self._tree_observer.restart_at(self._watchdir_resolved)
        else:
            try:
                self._tree_observer.stop()
            except Exception:
                pass
            self._tree_observer.clear()
            try:
                self._watcher.restart_at(self._watchdir_resolved)
            except Exception:
                self._watcher.start()
            self._poll_watcher.start()

    def _ingest_tiff_revision(
        self,
        revision: TiffRevision,
        *,
        stability_cfg: object = None,
    ) -> None:
        from .stability import StabilityConfig

        cfg = stability_cfg if isinstance(stability_cfg, StabilityConfig) else None
        self._executor.enqueue_revision(revision, stability_cfg=cfg)

    def _ingest_tiff_revision_from_poll(self, revision: TiffRevision) -> None:
        self._ingest_tiff_revision(revision, stability_cfg=POLL_TRIGGERED_STABILITY)

    def _ingest_tiff_revision_from_tree(self, revision: TiffRevision) -> None:
        self._ingest_tiff_revision(revision, stability_cfg=TREE_STABILITY)

    def _enqueue_manual_tiff(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.MANUAL,
        )
        if rev is not None:
            self._ingest_tiff_revision(rev)

    def _reload_history_view(self) -> None:
        hist = list(self._executor.session_processed_tiffs)
        if not hist:
            return
        self._history_index = max(0, min(self._history_index, len(hist) - 1))
        tiff_path = hist[self._history_index]
        apply_middle_view_from_disk(
            self._middle,
            watchdir=self._watchdir_resolved,
            tiff_path=tiff_path,
            state=self._state,
            subtract_options=self._load_subtract_yaml_options(),
        )
        if tiff_path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(tiff_path)
        self._refresh_right_outputs_for_session_history()

    def _middle_updates_follow_pipeline(self) -> bool:
        hist = self._executor.session_processed_tiffs
        n = len(hist)
        if n == 0:
            return True
        return self._history_index == n - 1

    def _refresh_history_chrome(self) -> None:
        hist = list(self._executor.session_processed_tiffs)
        n = len(hist)
        if n == 0:
            self._middle.set_history_nav_visible(False)
            return
        self._middle.set_history_nav_visible(True)
        if self._history_index >= n:
            self._history_index = n - 1
        if self._history_index < 0:
            self._history_index = 0
        name = tiff_history_label(
            watchdir=self._watchdir_resolved,
            tiff_path=hist[self._history_index],
            mode=self._state.watch_mode,
        )
        self._middle.set_history_label(f"{self._history_index + 1} / {n} · {name}")
        self._middle.set_history_prev_enabled(self._history_index > 0)
        self._middle.set_history_next_enabled(self._history_index < n - 1)
        self._middle.set_process_enabled(True)

    def _on_session_file_completed(self, _path: str) -> None:
        hist = self._executor.session_processed_tiffs
        n = len(hist)
        if n == 0:
            self._refresh_history_chrome()
            return
        # Hist already includes the new path; user was "on live tail" iff they were at the previous last index.
        was_at_previous_tail = n == 1 or self._history_index == n - 2
        if was_at_previous_tail:
            self._history_index = n - 1
        self._refresh_history_chrome()

    def _on_history_step(self, delta: int) -> None:
        hist = list(self._executor.session_processed_tiffs)
        n = len(hist)
        if n == 0 or delta == 0:
            return
        self._history_index = max(0, min(n - 1, self._history_index + int(delta)))
        self._refresh_history_chrome()
        tiff_path = hist[self._history_index]
        apply_middle_view_from_disk(
            self._middle,
            watchdir=self._watchdir_resolved,
            tiff_path=tiff_path,
            state=self._state,
            subtract_options=self._load_subtract_yaml_options(),
        )
        tl = tiff_path.lower()
        if tl.endswith((".tif", ".tiff")):
            self._record_2d_shown(tiff_path)
        self._refresh_right_outputs_for_session_history()

    def _clear_2d_display_cache(self) -> None:
        self._last_2d_shown = None

    def _record_2d_shown(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.MANUAL,
        )
        if rev is not None:
            self._last_2d_shown = (rev.path, rev.stat)

    def _record_2d_revision(self, revision: TiffRevision) -> None:
        self._last_2d_shown = (revision.path, revision.stat)

    def _on_tiff_revision_pending(self, revision: object) -> None:
        if not isinstance(revision, TiffRevision):
            return
        if not self._middle_updates_follow_pipeline():
            return
        if self._last_2d_shown is not None:
            prev_path, prev_snap = self._last_2d_shown
            if prev_path == revision.path and prev_snap == revision.stat:
                return
        self._record_2d_revision(revision)
        self._middle.show_image(revision.path)

    def _refresh_right_outputs_for_session_history(self) -> None:
        hist = list(self._executor.session_processed_tiffs)
        if not hist:
            # No completed session TIFFs yet: keep whatever live pipeline or manual runs showed.
            self._right.sync_modeling_ui_to_session_state()
            return
        idx = max(0, min(self._history_index, len(hist) - 1))
        stem = Path(hist[idx]).stem
        apply_right_outputs_from_disk(
            self._right,
            watchdir=self._watchdir_resolved,
            tiff_stem=stem,
            mode=self._state.analysis_mode,
            tiff_path=hist[idx],
            watch_mode=self._state.watch_mode,
        )
        self._right.sync_modeling_ui_to_session_state()

    def _on_analysis_mode_changed(self, _mode: object) -> None:
        self._refresh_right_outputs_for_session_history()

    def _on_process_history_file_requested(self) -> None:
        hist = list(self._executor.session_processed_tiffs)
        if not hist:
            return
        idx = max(0, min(self._history_index, len(hist) - 1))
        path = hist[idx]
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = path.strip()
        if not key:
            return
        self._enqueue_manual_tiff(key)

    def _on_queue_status(self, status: LiveviewQueueStatus) -> None:
        self._middle.set_queue_status(status)

    def _on_error(self, text: str) -> None:
        # TODO: surface in UI.
        _ = text

    def _persist_session_settings(self) -> None:
        save_liveview_session_settings(self._state)

    def _apply_loaded_session_to_ui(self) -> None:
        self._left.sync_buffer_preview_from_state()
        rp = self._state.calibration_refined_yml_path
        self._left.set_calibration_params_from_path(
            str(rp) if rp is not None and rp.is_file() else None
        )
        cpp = self._state.calibration_curve_plot_path
        if cpp is not None and cpp.is_file():
            self._left.set_calibration_preview_path(str(cpp))
        else:
            self._left.set_calibration_preview_path("")
        st = self._state.current_state()
        if st in (LiveviewState.C, LiveviewState.CD):
            self._middle.show_subtraction_placeholder()

    def _on_change_watchdir(self) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Watch folder",
                "A skill is still running. Wait for it to finish, then change the watch folder.",
            )
            return
        cur = str(self._watchdir_resolved)
        chosen = select_watchdir(parent=self, initial_directory=cur)
        if not chosen:
            return
        new_p = Path(chosen).resolve()
        if new_p == self._watchdir_resolved:
            return
        self._switch_watchdir(new_p)

    def _switch_watchdir(self, new_p: Path) -> None:
        self._persist_session_settings()
        self._runner.cancel()
        if not self._runner.wait_until_idle():
            QMessageBox.warning(
                self,
                "Watch folder",
                "The running subprocess did not stop in time. Try again after it finishes.",
            )
            return
        try:
            self._watcher.stop()
        except Exception:
            pass
        try:
            self._poll_watcher.stop()
        except Exception:
            pass
        try:
            self._tree_observer.stop()
        except Exception:
            pass
        try:
            self._executor.stop()
        except Exception:
            pass

        self._state.reset_for_new_watchdir(new_p)
        load_liveview_session_settings(self._state)
        self._right.reload_configs_from_watchdir()
        try:
            self._runner.set_workdir(new_p)
        except RuntimeError:
            QMessageBox.warning(self, "Watch folder", "Cannot switch while a skill is running.")
            return

        runs_dir(self._state.watchdir).mkdir(parents=True, exist_ok=True)
        self._poll_watcher.clear()
        self._tree_observer.clear()
        # Recreate executor so it uses the updated state + runner workdir cleanly.
        try:
            self._executor.deleteLater()
        except Exception:
            pass
        self._executor = LiveviewJobExecutor(state=self._state, runner=self._runner)
        self._connect_executor(self._executor)
        self._executor.start()
        self._watchdir_resolved = new_p.resolve()
        wd_short, wd_full = contracted_path_label(new_p)
        self._watchdir_label.setText(wd_short)
        self._watchdir_label.setToolTip(f"Watchdir\n{wd_full}")
        save_last_watchdir(str(new_p))
        self._clear_2d_display_cache()
        self._history_index = 0
        self._update_watch_mode_button()
        self._apply_watch_mode_watchers()
        self._right.clear_output_previews()
        self._right.sync_modeling_ui_to_session_state()
        self._apply_loaded_session_to_ui()
        self._refresh_history_chrome()

    def _refresh_middle_for_current_state(self) -> None:
        st = self._state.current_state()
        if st in (LiveviewState.C, LiveviewState.CD):
            self._middle.show_subtraction_placeholder()
        else:
            self._middle.show_curve("", x_label="px" if st == LiveviewState.A else "q (nm$^{-1}$)")
        self._middle.show_image("")
        self._clear_2d_display_cache()

    def _on_reset_calibration(self) -> None:
        self._state.reset_calibration_to_state_a()
        self._persist_session_settings()
        self._left.set_calibration_preview_path("")
        self._left.set_calibration_params_from_path(None)
        self._left.sync_buffer_preview_from_state()
        self._left.reset_calibration_wizard_form()
        self._right.force_analysis_mode_off()
        self._right.clear_output_previews()
        self._right.sync_modeling_ui_to_session_state()
        self._refresh_middle_for_current_state()
        self._refresh_right_outputs_for_session_history()

    def _on_reset_buffer(self) -> None:
        self._state.reset_buffer_to_state_b()
        self._persist_session_settings()
        self._left.sync_buffer_preview_from_state()
        self._left.reset_buffer_wizard_form()
        self._right.force_analysis_mode_off()
        self._right.clear_output_previews()
        self._right.sync_modeling_ui_to_session_state()
        self._refresh_middle_for_current_state()
        self._refresh_right_outputs_for_session_history()

    def _wire_ui(self) -> None:
        self._left.calibration_changed.connect(self._on_run_calibration)
        self._left.calibration_cancel_requested.connect(self._on_cancel_calibration)
        self._left.calibration_reset_requested.connect(self._on_reset_calibration)
        self._left.buffer_reset_requested.connect(self._on_reset_buffer)
        self._left.subtract_config_changed.connect(self._on_subtract_config_changed)
        self._right.modeling_enabled_changed.connect(self._on_modeling_enabled_changed)
        self._right.fit_distances_run_requested.connect(self._on_fit_distances_run)
        self._right.fit_sizes_run_requested.connect(self._on_fit_sizes_run)
        self._right.fit_mixture_run_requested.connect(self._on_fit_mixture_run)
        self._right.fit_bodies_run_requested.connect(self._on_fit_bodies_run)
        self._right.fit_distances_cancel_requested.connect(self._on_cancel_calibration)
        self._middle.tiff_files_dropped.connect(self._on_tiff_files_dropped)
        self._middle.history_step.connect(self._on_history_step)
        self._middle.process_history_file_requested.connect(self._on_process_history_file_requested)
        self._middle.subtraction_wizard_requested.connect(self._open_subtraction_wizard)
        self._right.analysis_mode_changed.connect(self._on_analysis_mode_changed)
        self._runner.started.connect(self._on_runner_started)
        self._runner.finished.connect(self._on_runner_finished)

    def _open_subtraction_wizard(self) -> None:
        st = self._state.current_state()
        if st not in (LiveviewState.C, LiveviewState.CD):
            return
        # While wizard is open, pause queue advancement (but do not cancel current skill).
        self._executor.pause()
        ctx = self._middle.current_subtraction_context()
        sample_dat = str(ctx.get("sample_dat") or "")
        buffer_dat = str(ctx.get("buffer_dat") or "")
        subtracted_dat = str(ctx.get("subtracted_dat") or "")
        subtract_options = ctx.get("subtract_options") if isinstance(ctx.get("subtract_options"), dict) else {}
        if self._sub_wizard is None:
            self._sub_wizard = SubtractionWizardDialog(
                sample_dat=sample_dat,
                buffer_dat=buffer_dat,
                subtracted_dat=subtracted_dat,
                subtract_options=subtract_options,
                parent=self,
            )
            self._sub_wizard.preview_scale_changed.connect(self._middle.preview_manual_subtraction_scale)
            self._sub_wizard.apply_requested.connect(self._on_subtraction_apply_requested)
        else:
            # Recreate dialog to update paths reliably (simpler than mutating internal state).
            try:
                self._sub_wizard.close()
            except Exception:
                pass
            self._sub_wizard = SubtractionWizardDialog(
                sample_dat=sample_dat,
                buffer_dat=buffer_dat,
                subtracted_dat=subtracted_dat,
                subtract_options=subtract_options,
                parent=self,
            )
            self._sub_wizard.preview_scale_changed.connect(self._middle.preview_manual_subtraction_scale)
            self._sub_wizard.apply_requested.connect(self._on_subtraction_apply_requested)
        try:
            self._sub_wizard.finished.connect(lambda _code: self._executor.resume())  # type: ignore[attr-defined]
        except Exception:
            pass
        self._sub_wizard.show()
        self._sub_wizard.raise_()
        self._sub_wizard.activateWindow()

    def _on_subtraction_apply_requested(self, scaling_factor: float) -> None:
        ctx = self._middle.current_subtraction_context()
        sample_dat = str(ctx.get("sample_dat") or "").strip()
        buffer_dat = str(ctx.get("buffer_dat") or "").strip()
        if not sample_dat or not buffer_dat:
            QMessageBox.warning(self, "Subtraction", "Missing sample/buffer curves for the current file.")
            return
        # Stop ASAP, close wizard, enqueue high-priority rerun job, then resume.
        self._executor.cancel_current()
        try:
            if self._sub_wizard is not None:
                self._sub_wizard.close()
        except Exception:
            pass
        job = self._executor.build_rerun_subtraction_job(
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
            scaling_factor=float(scaling_factor),
            priority=100,
        )
        self._executor.enqueue_job(job)
        self._executor.resume()

    def _path_under_watchdir(self, path: Path) -> bool:
        watch = self._watchdir_resolved
        try:
            path.resolve().relative_to(watch)
            return True
        except ValueError:
            return False

    def _resolve_under_watchdir(self, path_str: str) -> str:
        p = Path((path_str or "").strip()).expanduser()
        if not str(p):
            raise ValueError("Empty path")
        if p.is_absolute():
            return str(p.resolve())
        return str((self._watchdir_resolved / p).resolve())

    def _on_tiff_files_dropped(self, paths: object) -> None:
        if not isinstance(paths, list):
            return
        for raw in paths:
            if not isinstance(raw, str):
                continue
            p = Path(raw)
            if not p.is_file():
                continue
            src_r = p.resolve()
            if self._path_under_watchdir(src_r):
                self._enqueue_manual_tiff(str(src_r))
                continue
            dest = self._watchdir_resolved / src_r.name
            shutil.copy2(src_r, dest)
            self._enqueue_manual_tiff(str(dest))

    def _on_run_calibration(self) -> None:
        if self._runner.is_running():
            return
        try:
            req = self._left.build_calibrate_request()
        except Exception:
            return
        self._runner.start(req)

    def _on_cancel_calibration(self) -> None:
        try:
            self._runner.cancel()
        except Exception:
            pass

    def _on_subtract_config_changed(self) -> None:
        self._persist_session_settings()
        self._right.sync_modeling_ui_to_session_state()
        st = self._state.current_state()
        if st in (LiveviewState.C, LiveviewState.CD):
            self._middle.show_subtraction_placeholder()

    def _on_modeling_enabled_changed(self, enabled: bool) -> None:
        _ = enabled

    @staticmethod
    def _resolved_profile_path(profile_arg: str, *, watchdir: Path) -> Path:
        p = Path((profile_arg or "").strip().split(",")[0].strip()).expanduser()
        return p.resolve() if p.is_absolute() else (watchdir / p).resolve()

    def _fit_distances_profile_file_ok(self, req: RunRequest) -> bool:
        if not req.positional:
            return False
        raw = (req.positional[0] or "").strip()
        if not raw:
            return False
        path = self._resolved_profile_path(raw, watchdir=self._watchdir_resolved)
        return path.is_file()

    def _on_fit_distances_run(self) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Busy",
                "Another skill is still running. Wait for it to finish, then try again.",
            )
            return
        wd = self._watchdir_resolved
        has_prof = self._right.fit_distances_wizard_has_existing_profile_file(wd)
        try:
            self._right.save_fit_distances_conf_from_open_wizard(enable_modeling=True)
            self._right.sync_modeling_ui_to_session_state()
            if not has_prof:
                return

            req = self._right.build_fit_distances_request_from_wizard()
            if not self._fit_distances_profile_file_ok(req):
                QMessageBox.warning(
                    self,
                    "fit_distances",
                    "The profile path is not an existing file.",
                )
                return
            opts = dict(req.options)
            opts.pop("use_cache", None)
            od = opts.get("output_dir", "")
            opts["output_dir"] = (
                self._resolve_under_watchdir(str(od))
                if (isinstance(od, str) and od.strip())
                else str((self._watchdir_resolved / "fit_distances").resolve())
            )
            opts["use_cache"] = False
            positional: list[str] = []
            for p in req.positional:
                raw = (p or "").strip()
                if "," in raw:
                    positional.append(raw)
                else:
                    positional.append(self._resolve_under_watchdir(raw))
            req = RunRequest(skill_name=req.skill_name, positional=positional, options=opts)
        except Exception as e:
            QMessageBox.critical(self, "fit_distances", str(e))
            return
        self._runner.start(req)

    def _on_fit_sizes_run(self) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Busy",
                "Another skill is still running. Wait for it to finish, then try again.",
            )
            return
        wd = self._watchdir_resolved
        has_prof = self._right.fit_sizes_wizard_has_existing_profile_file(wd)
        try:
            self._right.save_fit_sizes_conf_from_open_wizard(enable_modeling=True)
            self._right.sync_modeling_ui_to_session_state()
            if not has_prof:
                return
            req = self._right.build_fit_sizes_request_from_wizard()
            if not self._fit_distances_profile_file_ok(req):
                QMessageBox.warning(
                    self,
                    "fit_sizes",
                    "The profile path is not an existing file.",
                )
                return
            opts = dict(req.options)
            opts.pop("use_cache", None)
            od = opts.get("output_dir", "")
            opts["output_dir"] = (
                self._resolve_under_watchdir(str(od))
                if (isinstance(od, str) and od.strip())
                else str((self._watchdir_resolved / "fit_sizes").resolve())
            )
            opts["use_cache"] = False
            positional: list[str] = []
            for p in req.positional:
                raw = (p or "").strip()
                if "," in raw:
                    positional.append(raw)
                else:
                    positional.append(self._resolve_under_watchdir(raw))
            req = RunRequest(skill_name=req.skill_name, positional=positional, options=opts)
        except Exception as e:
            QMessageBox.critical(self, "fit_sizes", str(e))
            return
        self._runner.start(req)

    def _on_fit_mixture_run(self) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Busy",
                "Another skill is still running. Wait for it to finish, then try again.",
            )
            return
        wd = self._watchdir_resolved
        has_prof = self._right.fit_mixture_wizard_has_existing_profile_file(wd)
        try:
            self._right.save_fit_mixture_conf_from_open_wizard(enable_modeling=True)
            self._right.sync_modeling_ui_to_session_state()
            if not has_prof:
                return
            req = self._right.build_fit_mixture_request_from_wizard()
            if not self._fit_distances_profile_file_ok(req):
                QMessageBox.warning(
                    self,
                    "fit_mixture",
                    "The profile path is not an existing file.",
                )
                return
            opts = dict(req.options)
            opts.pop("use_cache", None)
            od = opts.get("output_dir", "")
            opts["output_dir"] = (
                self._resolve_under_watchdir(str(od))
                if (isinstance(od, str) and od.strip())
                else str((self._watchdir_resolved / "mixture").resolve())
            )
            opts["use_cache"] = False
            positional: list[str] = []
            for p in req.positional:
                raw = (p or "").strip()
                if "," in raw:
                    positional.append(raw)
                else:
                    positional.append(self._resolve_under_watchdir(raw))
            req = RunRequest(skill_name=req.skill_name, positional=positional, options=opts)
        except Exception as e:
            QMessageBox.critical(self, "fit_mixture", str(e))
            return
        self._runner.start(req)

    def _on_fit_bodies_run(self) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Busy",
                "Another skill is still running. Wait for it to finish, then try again.",
            )
            return
        try:
            self._right.save_fit_bodies_conf_from_open_wizard(enable_modeling=True)
            self._right.sync_modeling_ui_to_session_state()
        except Exception as e:
            QMessageBox.critical(self, "fit_bodies", str(e))

    def _sync_last_integrated_from_result(self, result: dict) -> None:
        integ = result.get("integrated_1d")
        path_str: Optional[str] = None
        if isinstance(integ, list) and integ:
            last = integ[-1]
            if isinstance(last, str):
                path_str = last
        elif isinstance(integ, str):
            path_str = integ
        if path_str:
            p = Path(path_str)
            if p.is_file():
                self._state.last_integrated_dat_path = p

    def _sync_last_subtracted_from_result(self, result: dict) -> None:
        sub = result.get("subtracted_1d")
        path_str: Optional[str] = None
        if isinstance(sub, str):
            path_str = sub
        elif isinstance(sub, list) and sub:
            last = sub[-1]
            if isinstance(last, str):
                path_str = last
        if path_str:
            p = Path(path_str)
            if p.is_file():
                self._state.last_subtracted_dat_path = p

    def _load_subtract_yaml_options(self) -> Dict[str, Any]:
        # Subtract options are stored in session state (not written to disk).
        try:
            data = self._state.subtract_options
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
        except Exception:
            pass
        return {}

    def _on_latest_artifacts(self, result: dict) -> None:
        # Update state on calibrate success.
        integ_dir = result.get("integrator_dir")
        if isinstance(integ_dir, str) and integ_dir:
            self._state.integrator_dir = Path(integ_dir)

        self._sync_last_integrated_from_result(result)
        self._sync_last_subtracted_from_result(result)

        try:
            st = self._state.current_state()

            if self._middle_updates_follow_pipeline():
                # Middle: §4.4 — States C/CD use two bottom plots (sample+buffer | subtracted), not a single integrated plot.
                if st in (LiveviewState.C, LiveviewState.CD):
                    sub = result.get("subtracted_1d")
                    sub_path = sub.strip() if isinstance(sub, str) else ""
                    if sub_path and os.path.isfile(sub_path):
                        samp = self._state.last_integrated_dat_path
                        buf = self._state.buffer_dat_path
                        self._middle.show_subtraction_views(
                            sample_dat=str(samp) if samp is not None and samp.is_file() else "",
                            buffer_dat=str(buf) if buf is not None and buf.is_file() else "",
                            subtracted_dat=sub_path,
                            subtract_options=self._load_subtract_yaml_options(),
                        )
                    else:
                        integ = result.get("integrated_1d")
                        has_integ = (isinstance(integ, str) and integ.strip()) or (
                            isinstance(integ, list) and integ and isinstance(integ[-1], str) and integ[-1].strip()
                        )
                        if has_integ:
                            self._middle.show_subtraction_placeholder()
                else:
                    # States A, B, BD — single 2D + single 1D curve.
                    integ = result.get("integrated_1d")
                    if isinstance(integ, list) and integ and isinstance(integ[-1], str):
                        xlab = "px" if st == LiveviewState.A else "q (nm$^{-1}$)"
                        self._middle.show_curve(integ[-1], x_label=xlab)
                    elif isinstance(integ, str) and integ:
                        xlab = "px" if st == LiveviewState.A else "q (nm$^{-1}$)"
                        self._middle.show_curve(integ, x_label=xlab)

            if self._middle_updates_follow_pipeline():
                self._right.ingest_skill_result(result)
        finally:
            self._right.sync_modeling_ui_to_session_state()

    @staticmethod
    def _norm_artifact_path(val: object) -> str:
        if not isinstance(val, str):
            return ""
        s = val.strip()
        if not s or s.lower() == "none":
            return ""
        return s

    def _on_runner_started(self, skill_name: str) -> None:
        # Disable Run while any skill is running (matches guisaxs_skills behavior).
        _ = skill_name
        self._left.set_calibration_running(True)
        self._right.set_fit_distances_running(True)

    def _on_runner_finished(self, outcome: RunOutcome) -> None:
        self._left.set_calibration_running(False)
        self._right.set_fit_distances_running(False)
        if outcome.request is not None and not outcome.success:
            if outcome.request.skill_name in (
                "calibrate",
                "fit_distances",
                "fit_dammif",
                "fit_bodies",
                "fit_sizes",
                "fit_mixture",
            ):
                detail = ""
                try:
                    sp = latest_stderr_path(self._watchdir_resolved)
                    if sp.is_file():
                        tail = sp.read_text(encoding="utf-8", errors="replace").strip()
                        if tail:
                            detail = "\n\n" + tail[-4000:]
                except Exception:
                    pass
                QMessageBox.critical(
                    self,
                    outcome.request.skill_name,
                    f"Skill failed (exit code {outcome.exit_code}).{detail}",
                )
        if outcome.request is not None and outcome.success:
            skill = outcome.request.skill_name
            if skill in (
                "fit_distances",
                "fit_dammif",
                "fit_bodies",
                "fit_sizes",
                "fit_mixture",
            ):
                self._right.ingest_skill_result(outcome.result)
            if skill in ("fit_distances", "fit_sizes") and not is_atsas_fit_ok(outcome.result):
                QMessageBox.warning(
                    self,
                    skill,
                    failure_message_from_result(outcome.result, skill_id=skill),
                )
            if skill == "calibrate":
                integ_dir = outcome.result.get("integrator_dir")
                if isinstance(integ_dir, str) and integ_dir.strip():
                    self._state.integrator_dir = Path(integ_dir.strip())
                refined_s = outcome.result.get("refined_path")
                rpath: Optional[Path] = None
                if isinstance(refined_s, str) and refined_s.strip():
                    rp = Path(refined_s.strip())
                    if rp.is_file():
                        rpath = rp.resolve()
                if rpath is None and self._state.integrator_dir is not None:
                    cand = self._state.integrator_dir.parent / "refined.yml"
                    if cand.is_file():
                        rpath = cand.resolve()
                self._state.calibration_refined_yml_path = rpath
                self._left.set_calibration_params_from_path(str(rpath) if rpath is not None else None)
                img = pick_calibration_curve_image_path(outcome.result)
                if img:
                    self._state.calibration_curve_plot_path = Path(img)
                    self._left.set_calibration_preview_path(img)
                else:
                    self._state.calibration_curve_plot_path = None
                    self._left.set_calibration_preview_path("")
                self._persist_session_settings()
        self._right.sync_modeling_ui_to_session_state()

