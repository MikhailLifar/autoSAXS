from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QMainWindow, QMessageBox, QSplitter, QVBoxLayout, QWidget

from ..core.event_bus import EventBus
from ..core.models import RunRequest
from ..core.paths import latest_stderr_path
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from .pipeline import LiveviewPipeline, LiveviewQueueStatus
from .queue import FIFOQueue
from .state import LiveviewSessionState, LiveviewState
from .watcher import DirectoryWatcher, WatcherConfig
from .ui.left_panel import LiveviewLeftPanel, pick_calibration_curve_image_path
from .ui.middle_panel import LiveviewMiddlePanel
from .ui.right_panel import LiveviewRightPanel


class LiveviewMainWindow(QMainWindow):
    def __init__(self, *, bus: EventBus, watchdir: Path) -> None:
        super().__init__()
        self._bus = bus
        self._state = LiveviewSessionState(watchdir=watchdir)

        self._queue = FIFOQueue()
        self._runner = SkillRunner(workdir=watchdir)
        self._watcher = DirectoryWatcher(
            directory=watchdir,
            cfg=WatcherConfig(recursive=False),
            on_new_file=self._on_new_file_detected,
        )
        self._pipeline = LiveviewPipeline(state=self._state, runner=self._runner, queue=self._queue)
        self._pipeline.queue_status.connect(self._on_queue_status)
        self._pipeline.error.connect(self._on_error)
        self._pipeline.latest_artifacts.connect(self._on_latest_artifacts)

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
        self._watchdir_label = QLabel(f"Watchdir: {watchdir}")
        self._watchdir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._watchdir_label)
        layout.addWidget(self._splitter, 1)
        self.setCentralWidget(container)

        self._pipeline.start()
        self._watcher.start()
        self._wire_ui()
        self._last_2d_path: str = ""
        self._watchdir_resolved = watchdir.resolve()
        try:
            self._splitter.setSizes([320, 900, 420])
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._watcher.stop()
        except Exception:
            pass
        try:
            self._pipeline.stop()
        except Exception:
            pass
        try:
            self._runner.cancel()
        except Exception:
            pass
        super().closeEvent(event)

    def _on_new_file_detected(self, path: str, detected_at_monotonic: float) -> None:
        # Called from watchdog thread. Enqueue only; no Qt calls here.
        self._pipeline.enqueue(path=path, detected_at_monotonic=detected_at_monotonic)

    def _on_queue_status(self, status: LiveviewQueueStatus) -> None:
        self._middle.set_queue_status(status)
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

    def _wire_ui(self) -> None:
        self._left.calibration_changed.connect(self._on_run_calibration)
        self._left.calibration_cancel_requested.connect(self._on_cancel_calibration)
        self._left.subtract_config_changed.connect(self._on_subtract_config_changed)
        self._right.modeling_enabled_changed.connect(self._on_modeling_enabled_changed)
        self._right.fit_distances_run_requested.connect(self._on_fit_distances_run)
        self._right.fit_distances_cancel_requested.connect(self._on_cancel_calibration)
        self._middle.tiff_files_dropped.connect(self._on_tiff_files_dropped)
        self._runner.started.connect(self._on_runner_started)
        self._runner.finished.connect(self._on_runner_finished)

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

    def _copy_into_watchdir(self, src: Path) -> Path:
        """Copy src into watchdir if needed; return path to use for the queue (under watchdir)."""
        src_r = src.resolve()
        watch = self._watchdir_resolved
        if self._path_under_watchdir(src_r):
            return src_r
        dest = watch / src_r.name
        shutil.copy2(src_r, dest)
        return dest

    def _on_tiff_files_dropped(self, paths: object) -> None:
        if not isinstance(paths, list):
            return
        for raw in paths:
            if not isinstance(raw, str):
                continue
            p = Path(raw)
            if not p.is_file():
                continue
            final = self._copy_into_watchdir(p)
            self._pipeline.enqueue(path=str(final), detected_at_monotonic=time.monotonic())

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
        self._right.sync_modeling_ui_to_session_state()
        st = self._state.current_state()
        if st in (LiveviewState.C, LiveviewState.CD):
            self._middle.show_subtraction_placeholder()

    def _on_modeling_enabled_changed(self, enabled: bool) -> None:
        _ = enabled

    def _on_fit_distances_run(self) -> None:
        if self._state.current_state() == LiveviewState.A:
            QMessageBox.information(
                self,
                "fit_distances",
                "Modeling is not available in State A. Run calibration first (see liveview spec §4.2).",
            )
            return
        if self._runner.is_running():
            QMessageBox.warning(
                self,
                "Busy",
                "Another skill is still running. Wait for it to finish, then try again.",
            )
            return
        try:
            self._right.save_fit_distances_conf_from_open_wizard()
            req = self._right.build_fit_distances_request_from_wizard()
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
        p = self._state.subtract_conf_path
        if p is None or not p.is_file():
            return {}
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
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
                    return
                integ = result.get("integrated_1d")
                has_integ = (isinstance(integ, str) and integ.strip()) or (
                    isinstance(integ, list) and integ and isinstance(integ[-1], str) and integ[-1].strip()
                )
                if has_integ:
                    self._middle.show_subtraction_placeholder()
                return

            # States A, B, BD — single 2D + single 1D curve.
            integ = result.get("integrated_1d")
            if isinstance(integ, list) and integ and isinstance(integ[-1], str):
                xlab = "px" if st == LiveviewState.A else "q (nm$^{-1}$)"
                self._middle.show_curve(integ[-1], x_label=xlab)
            elif isinstance(integ, str) and integ:
                xlab = "px" if st == LiveviewState.A else "q (nm$^{-1}$)"
                self._middle.show_curve(integ, x_label=xlab)

            # Right: fit_distances plots.
            fit_png = self._norm_artifact_path(result.get("fit_vs_exp_png_path"))
            pr_png = self._norm_artifact_path(result.get("best_pr_png_path"))
            if fit_png or pr_png:
                self._right.show_fit_outputs(fit_png=fit_png, pr_png=pr_png)
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
            if outcome.request.skill_name in ("calibrate", "fit_distances"):
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
        if outcome.success and outcome.request is not None:
            if outcome.request.skill_name == "calibrate":
                integ_dir = outcome.result.get("integrator_dir")
                if isinstance(integ_dir, str) and integ_dir.strip():
                    self._state.integrator_dir = Path(integ_dir.strip())
                img = pick_calibration_curve_image_path(outcome.result)
                if img:
                    self._left.set_calibration_preview_path(img)
            elif outcome.request.skill_name == "fit_distances":
                fit_png = self._norm_artifact_path(outcome.result.get("fit_vs_exp_png_path"))
                pr_png = self._norm_artifact_path(outcome.result.get("best_pr_png_path"))
                self._right.show_fit_outputs(fit_png=fit_png, pr_png=pr_png)
        self._right.sync_modeling_ui_to_session_state()

