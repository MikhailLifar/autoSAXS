from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
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
from .state import LiveviewWatchMode
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
            on_new_file=self._on_new_file_detected,
        )
        self._poll_watcher = ProcessedTiffPoller(
            cfg=PollWatcherConfig(),
            on_update=self._on_polled_file_detected,
        )
        self._tree_observer = TreeDirObserver(
            cfg=TreeObserverConfig(),
            watchdir=watchdir,
            on_update=self._on_tree_file_detected,
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
        top = QHBoxLayout()
        top.addWidget(self._watchdir_label, 1)
        self._watch_mode_btn = QPushButton()
        self._watch_mode_btn.setCheckable(True)
        self._watch_mode_btn.clicked.connect(self._on_watch_mode_toggled)
        self._update_watch_mode_button()
        self._watch_mode_btn.setToolTip(
            "Flat dir: watch top-level TIFFs only; outputs under watchdir.\n"
            "Tree dir: recursive TIFF discovery; outputs beside each TIFF."
        )
        top.addWidget(self._watch_mode_btn, 0, Qt.AlignRight)
        self._change_watchdir_btn = QPushButton("Change watch folder…")
        self._change_watchdir_btn.setToolTip("Same folder picker as guisaxs_skills working directory")
        self._change_watchdir_btn.clicked.connect(self._on_change_watchdir)
        top.addWidget(self._change_watchdir_btn, 0, Qt.AlignRight)
        layout.addLayout(top)
        layout.addWidget(self._splitter, 1)
        self.setCentralWidget(container)

        self._last_2d_path: str = ""
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
        self._poll_watcher.set_idle_check(executor.is_idle)
        self._tree_observer.set_idle_check(executor.is_idle)

    def _update_watch_mode_button(self) -> None:
        tree = self._state.watch_mode == LiveviewWatchMode.TREE
        self._watch_mode_btn.blockSignals(True)
        self._watch_mode_btn.setChecked(tree)
        self._watch_mode_btn.setText("Tree dir" if tree else "Flat dir")
        self._watch_mode_btn.blockSignals(False)

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

    def _on_watch_mode_toggled(self, checked: bool) -> None:
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Watch mode",
                "A skill is still running. Wait for it to finish, then switch watch mode.",
            )
            self._update_watch_mode_button()
            return
        new_mode = LiveviewWatchMode.TREE if checked else LiveviewWatchMode.FLAT
        if new_mode == self._state.watch_mode:
            return
        self._state.watch_mode = new_mode
        self._persist_session_settings()
        self._update_watch_mode_button()
        self._apply_watch_mode_watchers()
        self._refresh_history_chrome()
        if self._executor.session_processed_tiffs:
            self._reload_history_view()

    def _on_tree_file_detected(self, path: str, detected_at_monotonic: float) -> None:
        self._executor.enqueue_tiff(
            path=path,
            detected_at_monotonic=detected_at_monotonic,
            stability_cfg=TREE_STABILITY,
        )

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
            self._last_2d_path = tiff_path
        self._refresh_right_outputs_for_session_history()

    def _on_new_file_detected(self, path: str, detected_at_monotonic: float) -> None:
        # Called from watchdog thread. Enqueue only; no Qt calls here.
        self._executor.enqueue_tiff(path=path, detected_at_monotonic=detected_at_monotonic)

    def _on_polled_file_detected(self, path: str, detected_at_monotonic: float) -> None:
        # NFS overwrite fallback: faster poll cadence and shorter stability only here.
        self._executor.enqueue_tiff(
            path=path,
            detected_at_monotonic=detected_at_monotonic,
            stability_cfg=POLL_TRIGGERED_STABILITY,
        )

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
            self._last_2d_path = tiff_path
        self._refresh_right_outputs_for_session_history()

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
        self._executor.enqueue_tiff(path=key, detected_at_monotonic=time.monotonic())

    def _on_queue_status(self, status: LiveviewQueueStatus) -> None:
        self._middle.set_queue_status(status)
        if not self._middle_updates_follow_pipeline():
            return
        # Update 2D view from current/last path.
        p = status.current_path or status.last_processed_path
        if isinstance(p, str) and p.lower().endswith((".tif", ".tiff")):
            # Avoid re-rendering the same image on every status tick (can visually \"shrink\" due to repeated layout).
            if p != self._last_2d_path:
                self._last_2d_path = p
                self._middle.show_image(p)

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
        self._last_2d_path = ""
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
        self._last_2d_path = ""

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
                self._executor.enqueue_tiff(path=str(src_r), detected_at_monotonic=time.monotonic())
                continue
            dest = self._watchdir_resolved / src_r.name
            shutil.copy2(src_r, dest)
            # Enqueue immediately; if the watcher also fires for this copy, put_if_absent drops the duplicate.
            self._executor.enqueue_tiff(path=str(dest), detected_at_monotonic=time.monotonic())

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

