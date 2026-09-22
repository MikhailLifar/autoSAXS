from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt5.QtWidgets import QMessageBox

from ..services.skills import normalize_fit_request, profile_path_exists
from ...logic.runner_qprocess import RunOutcome
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from ...core.paths import latest_stderr_path
from ..ui.panels.left import pick_calibration_curve_image_path

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewSessionHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller

    def apply_loaded_to_ui(self) -> None:
        left, middle = self._c.left, self._c.middle
        if left is None or middle is None:
            return
        state = self._c.state
        left.sync_buffer_preview_from_state()
        left.sync_mask_preview_from_state()
        rp = state.calibration_refined_yml_path
        left.set_calibration_params_from_path(str(rp) if rp is not None and rp.is_file() else None)
        cpp = state.calibration_curve_plot_path
        if cpp is not None and cpp.is_file():
            left.set_calibration_preview_path(str(cpp))
        else:
            left.set_calibration_preview_path("")
        if state.buffer_ready():
            self._c.history.sync_middle(force=True)

    def reset_calibration(self) -> None:
        self._c.state.reset_calibration()
        self._c.persist_session_settings()
        left, right = self._c.left, self._c.right
        if left is not None:
            left.set_calibration_preview_path("")
            left.set_calibration_params_from_path(None)
            left.sync_buffer_preview_from_state()
            left.sync_mask_preview_from_state()
            left.reset_calibration_wizard_form()
            # Re-apply session mask into the reset calibrate form (mask survives calib reset).
            mp = self._c.state.mask_path
            if mp is not None and mp.is_file() and left._cal_wizard is not None:
                left._cal_wizard.set_mask_path(str(mp))
        if right is not None:
            right.force_analysis_disarmed()
            right.clear_output_previews()
            right.sync_modeling_ui_to_session_state()
        self._refresh_middle_for_state()
        self._c.history.refresh_right_outputs()

    def reset_buffer(self) -> None:
        self._c.state.reset_buffer()
        self._c.persist_session_settings()
        left, right = self._c.left, self._c.right
        if left is not None:
            left.sync_buffer_preview_from_state()
            left.reset_buffer_wizard_form()
        if right is not None:
            right.force_analysis_disarmed()
            right.clear_output_previews()
            right.sync_modeling_ui_to_session_state()
        self._refresh_middle_for_state()
        self._c.history.refresh_right_outputs()

    def on_subtract_config_changed(self) -> None:
        self._c.persist_session_settings()
        if self._c.right is not None:
            self._c.right.sync_modeling_ui_to_session_state()
        self._refresh_intake_layout()

    def _refresh_intake_layout(self) -> None:
        self._c.history.sync_middle(force=False)
        left = self._c.left
        if left is not None:
            left.refresh_attention_coach()

    def _refresh_middle_for_state(self) -> None:
        """Reset middle after calib/buffer wipe; then sync empty or current sample."""
        self._c.history.clear_2d_cache()
        if self._c.samples:
            self._c.history.sync_middle(force=True)
        else:
            self._c.history.sync_middle(sample=None, force=True)

class LiveviewSkillRunsHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller

    def run_calibration(self) -> None:
        left = self._c.left
        if left is None or self._c.runner.is_running():
            return
        try:
            req = left.build_calibrate_request()
        except Exception:
            return
        self._c.executor.enqueue_manual_skill(req)

    def cancel_running(self) -> None:
        self._c.executor.cancel_running(requeue=False)

    def apply_subtraction_rerun(self, *, scaling_factor: float, sample_dat: str, buffer_dat: str) -> None:
        # Sync live pane numbers into session before building the after-subtract chain.
        right = self._c.right
        if right is not None:
            if self._c.state.monodisperse_armed:
                right.monodisperse_coordinator.sync_params_to_state()
            if self._c.state.polydisperse_armed:
                right.polydisperse_coordinator.sync_params_to_state()
        self._c.session.stop()
        self._c.executor.cancel_current()
        job = self._c.executor.build_rerun_subtraction_job(
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
            scaling_factor=float(scaling_factor),
            priority=100,
            use_ui_params=True,
        )
        self._c.executor.enqueue_job(job)
        if not (self._c.state.monodisperse_armed or self._c.state.polydisperse_armed):
            self._c.session.resume()

    def _run_manual_fit(
        self,
        *,
        skill_label: str,
        has_profile,
        save_conf,
        build_request,
        default_output_subdir: str,
    ) -> None:
        right = self._c.right
        if right is None:
            return
        if self._c.runner.is_running():
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(
                    parent,
                    "Busy",
                    "Another skill is still running. Wait for it to finish, then try again.",
                )
            return
        wd = self._c.watchdir
        had_prof = has_profile(wd)
        try:
            save_conf()
            right.sync_modeling_ui_to_session_state()
            if not had_prof:
                return
            req = build_request()
            if req is None:
                return
            if not profile_path_exists(req, watchdir=wd):
                parent = self._c.parent_widget
                if parent is not None:
                    QMessageBox.warning(parent, skill_label, "The profile path is not an existing file.")
                return
            req = normalize_fit_request(req, watchdir=wd, default_output_subdir=default_output_subdir)
        except Exception as e:
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.critical(parent, skill_label, str(e))
            return
        self._c.executor.enqueue_manual_skill(req)


class LiveviewSkillOutcomesHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller

    def on_started(self, skill_name: str) -> None:
        if self._c.left is not None:
            self._c.left.set_calibration_running(True)
        if self._c.right is not None:
            self._c.right.set_analysis_busy(True)
            if skill_name:
                self._c.right.log_panel.append_app(f"Started {skill_name}")
        self._c.session.sync_processing_ui()

    def on_finished(self, outcome: RunOutcome) -> None:
        if self._c.left is not None:
            self._c.left.set_calibration_running(False)
        if self._c.right is not None:
            self._c.right.set_analysis_busy(False)
        self._handle_failure(outcome)
        self._handle_success(outcome)
        self._c.session.sync_processing_ui()

    def on_latest_artifacts(self, result: dict) -> None:
        middle, right = self._c.middle, self._c.right
        if middle is None or right is None:
            return
        integ_dir = result.get("integrator_dir")
        if isinstance(integ_dir, str) and integ_dir:
            self._c.state.integrator_dir = Path(integ_dir)

        try:
            if self._c.history.middle_updates_follow_pipeline():
                # One owner for middle content: sample path → disk (TIFF or boarded .dat).
                sample = self._c.executor.current_job_sample_path
                if not sample:
                    cur = self._c.samples.current()
                    if cur is not None:
                        sample = cur.path
                if sample:
                    self._c.history.refresh_middle_for_sample(sample)
                else:
                    # Calibrate-only / no boarded sample: layout via sync, no direct paint.
                    self._c.history.sync_middle(sample=None, force=True)
                from ..services.history.right_artifacts import RightPresentSource, present_right

                present_right(
                    right,
                    state=self._c.state,
                    sample_path=str(sample or ""),
                    stem="",
                    watch_mode=self._c.state.watch_mode,
                    source=RightPresentSource.LIVE,
                    result=result,
                    skill_name=str(result.get("skill_name") or ""),
                    session=self._c.session,
                )
            self._c.monodisperse.update_profile_from_artifacts(result)
            self._c.polydisperse.update_profile_from_artifacts(result)
        finally:
            right.sync_modeling_ui_to_session_state()

    def _handle_failure(self, outcome: RunOutcome) -> None:
        if outcome.request is None or outcome.success:
            return
        if outcome.request.skill_name not in (
            "calibrate",
            "fit_guinier",
            "fit_distances",
            "model_dam",
            "model_density",
            "model_bodies",
            "fit_sizes",
            "model_mixture",
        ):
            return
        detail = ""
        try:
            sp = latest_stderr_path(self._c.watchdir)
            if sp.is_file():
                tail = sp.read_text(encoding="utf-8", errors="replace").strip()
                if tail:
                    detail = "\n\n" + tail[-4000:]
        except Exception:
            pass
        parent = self._c.parent_widget
        msg = f"Skill failed (exit code {outcome.exit_code}).{detail}"
        if self._c.right is not None:
            self._c.right.log_panel.append_app(f"{outcome.request.skill_name}: {msg.strip()}")
        if parent is not None:
            QMessageBox.critical(
                parent,
                outcome.request.skill_name,
                msg,
            )

    def _handle_success(self, outcome: RunOutcome) -> None:
        if outcome.request is None or not outcome.success:
            return
        skill = outcome.request.skill_name
        if skill in (
            "fit_guinier",
            "fit_distances",
            "model_dam",
            "model_density",
            "model_bodies",
            "fit_sizes",
            "model_mixture",
        ):
            self._c.monodisperse.sync_wizard_context_before_ingest(outcome)
            self._c.polydisperse.sync_window_context_before_ingest(outcome)
            if self._c.right is not None:
                from ..services.history.right_artifacts import RightPresentSource, present_right

                present_right(
                    self._c.right,
                    state=self._c.state,
                    sample_path="",
                    stem="",
                    watch_mode=self._c.state.watch_mode,
                    source=RightPresentSource.LIVE,
                    result=outcome.result or {},
                    skill_name=skill,
                    session=self._c.session,
                )
        self._c.session.sync_processing_ui()
        if skill in ("fit_distances", "fit_sizes") and not is_atsas_fit_ok(outcome.result):
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(
                    parent,
                    skill,
                    failure_message_from_result(outcome.result, skill_id=skill),
                )
        if skill == "calibrate":
            self._apply_calibrate_success(outcome.result)

    def _apply_calibrate_success(self, result: dict) -> None:
        left = self._c.left
        if left is None:
            return
        state = self._c.state
        integ_dir = result.get("integrator_dir")
        if isinstance(integ_dir, str) and integ_dir.strip():
            state.integrator_dir = Path(integ_dir.strip())
        refined_s = result.get("refined_path")
        rpath: Optional[Path] = None
        if isinstance(refined_s, str) and refined_s.strip():
            rp = Path(refined_s.strip())
            if rp.is_file():
                rpath = rp.resolve()
        if rpath is None and state.integrator_dir is not None:
            cand = state.integrator_dir.parent / "refined.yml"
            if cand.is_file():
                rpath = cand.resolve()
        state.calibration_refined_yml_path = rpath
        left.set_calibration_params_from_path(str(rpath) if rpath is not None else None)
        img = pick_calibration_curve_image_path(result)
        if img:
            state.calibration_curve_plot_path = Path(img)
            left.set_calibration_preview_path(img)
        else:
            state.calibration_curve_plot_path = None
            left.set_calibration_preview_path("")
        eff = result.get("effective_mask_path")
        if isinstance(eff, str) and eff.strip():
            ep = Path(eff.strip())
            if ep.is_file():
                self._c.session.set_mask_path(ep.resolve())
                if left._cal_wizard is not None:
                    left._cal_wizard.set_mask_path(str(ep.resolve()))
        left.sync_mask_preview_from_state()
        self._c.persist_session_settings()
