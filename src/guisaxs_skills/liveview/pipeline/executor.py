from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ...core.models import RunRequest
from ...logic.runner_qprocess import RunOutcome, SkillRunner
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from ..ingest.stability import FileStatSnapshot
from ..ingest.sample_revision import (
    SampleRevision,
    SampleRevisionSource,
    is_dat_path,
    is_tiff_path,
    normalize_sample_path,
)
from ..session.output_paths import (
    subtracted_dat_path,
    subtracted_dir,
)
from ..session.sample import Sample
from ..session.sample_store import SampleStore
from ..session.state import (
    LiveviewIntakeMode,
    LiveviewSessionState,
)
from ..services.artifacts import merge_fit_distances_quality_fields
from .jobs import Job, JobStep, PlaceholderError, is_manual_job, resolve_request_placeholders
from .monodisperse_pipeline import (
    FIT_GUINIER_MONO_STEP,
    FIT_GUINIER_POLY_STEP,
    MonodispersePipelineParts,
    build_monodisperse_steps,
    profile_sample_stem,
)
from .plan import plan_for
from .polydisperse_pipeline import (
    PolydispersePipelineParts,
    build_polydisperse_steps,
)
from .queue import FIFOQueue, JobQueue, QueueItem, RevisionEnqueueResult
from .report_pipeline import report_individual_step
from . import artifact_enrichment as _artifacts
from . import manual_jobs as _manual_jobs


@dataclass(frozen=True)
class LiveviewQueueStatus:
    queue_size: int
    current_path: str
    last_processed_path: str
    avg_seconds_per_item: float
    remaining: int


class LiveviewJobExecutor(QObject):
    """
    Single orchestrator for liveview:
    - queues settled revisions (``_incoming``) and promotes them to Jobs via ``plan_for``
    - re-plans the *current* auto job at start and after each successful step
      (``plan_for(..., completed=)``); queued jobs are untouched until they start
    - executes Jobs step-by-step using SkillRunner
    """

    queue_status = pyqtSignal(object)  # LiveviewQueueStatus
    latest_artifacts = pyqtSignal(object)  # dict
    error = pyqtSignal(str)
    session_file_completed = pyqtSignal(str)  # sample path
    job_started = pyqtSignal(str)  # sample path (TIFF or .dat) when a pipeline job begins
    sample_revision_pending = pyqtSignal(object)  # SampleRevision — queued for plan/run
    skill_started = pyqtSignal(str)
    skill_finished = pyqtSignal(object)  # RunOutcome

    def __init__(
        self,
        *,
        state: LiveviewSessionState,
        sample_store: SampleStore,
        runner: SkillRunner,
    ) -> None:
        super().__init__()
        self._state = state
        self._samples = sample_store
        self._runner = runner

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._tick)

        self._incoming = FIFOQueue()
        self._incoming_lock = threading.Lock()
        # Dedupe inotify+poll (and drop+watch) for the same path+stat; manual re-Process bypasses.
        self._last_accepted_stat: Dict[str, FileStatSnapshot] = {}

        self._jobs = JobQueue()
        self._current_job: Optional[Job] = None
        self._job_step_idx: int = 0
        self._pending_step_name: Optional[str] = None
        # Completed progress (phases + step results) lives only on Job.completed.

        self._last_processed: str = ""
        self._durations: List[float] = []
        self._job_started_at: float = 0.0

        self._owned_output_paths: set[str] = set()

        self._runner.finished.connect(self._on_skill_finished)
        self._requeue_cancelled_job: bool = False
        self._requeue_priority: int = 50
        # True while handling a skill result (incl. modal UI in skill_finished slots).
        # Nested Qt event loops must not let _tick restart the same job step.
        self._handling_skill_outcome: bool = False

    def register_owned_output(self, path: str) -> None:
        key = normalize_sample_path(path)
        if key:
            self._owned_output_paths.add(key)

    def is_owned_output(self, path: str) -> bool:
        return normalize_sample_path(path) in self._owned_output_paths

    def _register_job_outputs(self, job: Job) -> None:
        """Mark skill destinations so watchers do not re-ingest pipeline products."""
        for step in job.steps:
            opts = step.request.options or {}
            out_dir = opts.get("output_dir")
            if not isinstance(out_dir, str) or not out_dir.strip():
                continue
            name = step.name
            stem = str(job.context.get("sample_stem") or job.context.get("tiff_stem") or "").strip()
            root = Path(out_dir.strip())
            if name in ("integrate", "integrate_proxy") and stem:
                self.register_owned_output(str((root / f"int_{stem}.dat").resolve()))
            elif name == "subtract" and stem:
                self.register_owned_output(str((root / f"sub_{stem}.dat").resolve()))
                self.register_owned_output(str((root / f"diff_log_{stem}.dat").resolve()))
            # Also own the whole output dir files that match when we know profile path
        profile = str(job.context.get("profile_path") or "").strip()
        if profile:
            self.register_owned_output(profile)
        sub_out = str(job.context.get("subtracted_path") or "").strip()
        if sub_out:
            self.register_owned_output(sub_out)

    def start(self) -> None:
        if self._tick_timer.isActive():
            return
        self._tick_timer.start()

    def stop(self) -> None:
        self._tick_timer.stop()
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None

    def cancel_current(self) -> None:
        # Cancellation policy: by default we requeue the current job so users don't lose the file.
        # Call sites that don't want this can toggle `_requeue_cancelled_job` before cancelling.
        self.cancel_running(requeue=True)

    def is_idle(self) -> bool:
        """True when auto-processing and no skill, job, or incoming sample work is active."""
        if not self._state.is_auto_processing():
            return False
        if self._runner.is_running():
            return False
        if self._current_job is not None:
            return False
        if len(self._incoming) > 0 or len(self._jobs) > 0:
            return False
        return True

    def is_processing_idle(self) -> bool:
        """True when no autosaxs skill subprocess is currently running."""
        return not self._runner.is_running()

    @property
    def queue_suspended(self) -> bool:
        return not self._state.is_auto_processing()

    def sync_auto_processing_from_session(self) -> None:
        """API hook; queue suspension follows ``session.auto_processing``."""
        return

    def enqueue_revision(self, revision: SampleRevision) -> None:
        """Accept a settled sample revision into the incoming (admit) queue."""
        item = QueueItem.from_revision(revision)
        with self._incoming_lock:
            accepted = self._accept_incoming_revision(
                item,
                source=revision.source,
            )
        if accepted:
            self._jobs.drop_jobs_for_tiff_path(revision.path)
            self.sample_revision_pending.emit(revision)

    def _accept_incoming_revision(
        self,
        item: QueueItem,
        *,
        source: SampleRevisionSource = SampleRevisionSource.INOTIFY,
    ) -> bool:
        """Queue or replace a settled revision; return True when the admit queue changed."""
        key = normalize_sample_path(item.path)
        # Non-manual: ignore identical re-notifications (inotify + poll, or drop + watch).
        if source != SampleRevisionSource.MANUAL:
            prev = self._last_accepted_stat.get(key)
            if prev is not None and prev == item.observed_stat:
                return False

        result = self._incoming.put_revision(item)
        if result in (RevisionEnqueueResult.ADDED, RevisionEnqueueResult.REPLACED):
            self._last_accepted_stat[key] = item.observed_stat
            return True
        if result == RevisionEnqueueResult.UNCHANGED:
            self._last_accepted_stat[key] = item.observed_stat
        return False

    def enqueue_job(self, job: Job) -> None:
        self._jobs.put(job)

    def enqueue_manual_skill(self, request: RunRequest, *, priority: int = 150) -> None:
        """Enqueue a single-step skill run (calibration, manual fit, …) ahead of normal TIFF jobs."""
        job = Job(
            id=f"manual:{request.skill_name}:{time.time_ns()}",
            priority=int(priority),
            steps=[JobStep(name=request.skill_name, request=request)],
            context={"manual": True, "skill_name": request.skill_name},
        )
        self.enqueue_job(job)

    def enqueue_report_individual_for_sample(
        self,
        *,
        output_root: Path,
        basename: str,
        tiff_path: str = "",
        priority: int = 160,
    ) -> None:
        """Manual one-shot report for a sample (e.g. on Resume auto-processing)."""
        if not self._state.analysis_enabled():
            return
        stem = (basename or "").strip()
        if not stem:
            return
        root = output_root.expanduser().resolve()
        step = report_individual_step(output_root=root, basename=stem)
        self.enqueue_job(
            Job(
                id=f"manual:report_individual:{stem}:{time.time_ns()}",
                priority=int(priority),
                steps=[step],
                context={
                    "manual": True,
                    "skill_name": "report_individual",
                    "sample_stem": stem,
                    "output_root": str(root),
                    "source_path": (tiff_path or "").strip(),
                },
            )
        )

    def cancel_running(self, *, requeue: bool = False) -> None:
        self._requeue_cancelled_job = bool(requeue) and self._current_job is not None
        try:
            self._runner.cancel()
        except Exception:
            pass

    def build_rerun_subtraction_job(
        self,
        *,
        sample_dat: str,
        buffer_dat: str,
        scaling_factor: float,
        priority: int = 100,
        use_ui_params: bool = False,
    ) -> Job:
        return _manual_jobs.build_rerun_subtraction_job(
            state=self._state,
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
            scaling_factor=scaling_factor,
            load_yaml=self._load_yaml_options,
            priority=priority,
            use_ui_params=use_ui_params,
        )

    def _append_session_sample(self, path: str, *, boarding: LiveviewIntakeMode) -> None:
        try:
            sample = Sample.from_path(path, boarding=boarding)
        except Exception:
            return
        try:
            self._samples.append_history(sample)
        except Exception:
            return

    def _append_session_tiff(self, path: str, *, boarding: Optional[LiveviewIntakeMode] = None) -> None:
        mode = boarding if boarding is not None else self._state.intake_mode
        self._append_session_sample(path, boarding=mode)

    def _boarding_from_job_context(self, job: Job) -> LiveviewIntakeMode:
        raw = str(job.context.get("boarding") or "").strip()
        if raw:
            try:
                return LiveviewIntakeMode(raw)
            except ValueError:
                pass
        source = str(job.context.get("source_path") or job.context.get("tiff_path") or "").strip()
        remembered = self._samples.boarding_for(source) if source else None
        return remembered if remembered is not None else self._state.intake_mode

    def _emit_status(self) -> None:
        avg = (sum(self._durations) / len(self._durations)) if self._durations else 0.0
        qn = len(self._incoming) + len(self._jobs)
        cur_path = ""
        if self._runner.is_running():
            cur_path = self._pending_step_name or ""
        elif self._current_job is not None:
            cur_path = str(
                self._current_job.context.get("source_path")
                or self._current_job.context.get("tiff_path")
                or ""
            )
        rem = qn + (1 if (self._runner.is_running() or self._current_job is not None) else 0)
        self.queue_status.emit(
            LiveviewQueueStatus(
                queue_size=qn,
                current_path=cur_path,
                last_processed_path=self._last_processed,
                avg_seconds_per_item=avg,
                remaining=rem,
            )
        )

    @staticmethod
    def _is_tiff_path(path: str) -> bool:
        return is_tiff_path(path)

    def _tick(self) -> None:
        self._emit_status()

        # Never start a new subprocess while one is running.
        if self._runner.is_running():
            return
        # skill_finished slots may show modal dialogs (nested event loop). Do not
        # advance/restart the current job until outcome handling has finished.
        if self._handling_skill_outcome:
            return

        auto = self._state.is_auto_processing()

        # If a job is active and no step is pending, advance.
        if self._current_job is not None:
            if self._job_step_idx >= len(self._current_job.steps):
                self._finish_job(ok=True)
                return
            # When auto-processing is off, only manual jobs may advance.
            if auto or is_manual_job(self._current_job):
                self._start_next_job_step()
            return

        if not auto:
            # Manual mode: run queued manual jobs only; hold incoming and auto jobs.
            nxt = self._jobs.get_nowait_manual()
            if nxt is not None:
                self._start_job(nxt)
            return

        # No current job: promote settled incoming samples into jobs.
        self._promote_incoming_to_jobs()

        # Start next job if available.
        nxt = self._jobs.get_nowait()
        if nxt is None:
            return
        self._start_job(nxt)

    def _promote_incoming_to_jobs(self) -> None:
        """Move settled admit-queue items into ``_jobs`` via ``plan_for``."""
        while True:
            with self._incoming_lock:
                item = self._incoming.get_nowait()
            if item is None:
                return
            sample_path = item.path
            boarding = self._samples.boarding_for(sample_path) or self._state.intake_mode
            try:
                sample = Sample.from_path(sample_path, boarding=boarding)
                self._samples.remember(sample)
                plan = plan_for(self._state, sample, load_yaml=self._load_yaml_options)
                job = plan.to_job()
            except Exception as e:
                self.error.emit(f"Cannot build job for sample: {sample_path}\n{e}")
                continue
            self._register_job_outputs(job)
            self._jobs.drop_jobs_for_tiff_path(sample_path)
            self._jobs.put(job)

    @property
    def current_job_output_root(self) -> Optional[Path]:
        job = self._current_job
        if job is None:
            return None
        or_raw = job.context.get("output_root")
        if isinstance(or_raw, str) and or_raw.strip():
            try:
                return Path(or_raw.strip()).expanduser().resolve()
            except OSError:
                return None
        return None

    @property
    def current_job_sample_path(self) -> str:
        """TIFF or boarded ``.dat`` path for the active job (empty if idle)."""
        job = self._current_job
        if job is None:
            return ""
        raw = str(job.context.get("source_path") or job.context.get("tiff_path") or "").strip()
        if not raw:
            return ""
        try:
            return str(Path(raw).expanduser().resolve())
        except OSError:
            return raw

    def _start_job(self, job: Job) -> None:
        self._current_job = job
        self._job_step_idx = 0
        self._pending_step_name = None
        self._job_started_at = time.monotonic()
        # Fresh plan at start so arming/session changes since enqueue apply.
        # Progress comes from Job.completed (preserved across cancel-requeue).
        # Never touches the queue — only mutates ``_current_job``.
        if not is_manual_job(job):
            if not self._replan_current_job():
                self._finish_job(ok=False)
                return
            job = self._current_job or job
        self._sync_last_paths_from_job_context(job)
        sample = self.current_job_sample_path
        if sample:
            self.job_started.emit(sample)

    def _replan_current_job(self) -> bool:
        """
        Replace remaining steps of the active auto job via ``plan_for(..., completed=)``.

        Does not enqueue, dequeue, or mutate any queued job.
        Returns False if replanning failed (caller should abort the job).
        """
        job = self._current_job
        if job is None or is_manual_job(job):
            return True
        source = str(job.context.get("source_path") or job.context.get("tiff_path") or "").strip()
        if not source:
            self.error.emit("Cannot replan current job: missing source_path")
            return False
        boarding = self._boarding_from_job_context(job)
        try:
            sample = Sample.from_path(source, boarding=boarding)
            plan = plan_for(
                self._state,
                sample,
                load_yaml=self._load_yaml_options,
                completed=job.completed,
            )
        except Exception as e:
            self.error.emit(f"Cannot replan current job for sample: {source}\n{e}")
            return False
        ctx = dict(job.context)
        ctx["profile_path"] = plan.profile_path
        ctx["output_root"] = str(plan.output_root.resolve())
        if plan.subtracted_path:
            ctx["subtracted_path"] = plan.subtracted_path
        updated = job.with_remaining_steps(list(plan.steps), context=ctx)
        self._register_job_outputs(updated)
        self._current_job = updated
        self._job_step_idx = 0
        return True

    def _sync_last_paths_from_job_context(self, job: Job) -> None:
        """Curve boarding never emits integrate/subtract artifacts — seed last_* from context."""
        boarding = str(job.context.get("boarding") or "").strip()
        source = str(job.context.get("source_path") or job.context.get("tiff_path") or "").strip()
        profile = str(job.context.get("profile_path") or "").strip()
        subtracted = str(job.context.get("subtracted_path") or "").strip()

        def _set_if_file(attr: str, raw: str) -> None:
            if not raw:
                return
            try:
                p = Path(raw).expanduser().resolve()
            except OSError:
                return
            if p.is_file():
                setattr(self._state, attr, p)

        if boarding == LiveviewIntakeMode.CURVE_1D.value:
            _set_if_file("last_integrated_dat_path", source)
            # Profile may already be the subtracted path when buffer is configured.
            if subtracted:
                _set_if_file("last_subtracted_dat_path", subtracted)
            elif profile and profile != source and Path(profile).name.lower().startswith("sub_"):
                _set_if_file("last_subtracted_dat_path", profile)
        elif boarding == LiveviewIntakeMode.CURVE_SUB.value:
            _set_if_file("last_subtracted_dat_path", source or profile)
        elif boarding == LiveviewIntakeMode.FRAME_2D.value and profile:
            # Keep last_* in sync when a TIFF job already knows its profile path.
            if "subtracted" in str(Path(profile).parent).lower() or Path(profile).name.lower().startswith(
                "sub_"
            ):
                _set_if_file("last_subtracted_dat_path", profile)
            else:
                _set_if_file("last_integrated_dat_path", profile)

    def _finish_job(self, *, ok: bool) -> None:
        job = self._current_job
        if job is None:
            return
        dt = max(0.0, time.monotonic() - self._job_started_at)
        if ok:
            self._durations.append(dt)
            if len(self._durations) > 50:
                self._durations = self._durations[-50:]
        source_path = str(job.context.get("source_path") or job.context.get("tiff_path") or "").strip()
        if ok and source_path and (self._is_tiff_path(source_path) or is_dat_path(source_path)):
            boarding = self._boarding_from_job_context(job)
            if not is_manual_job(job):
                self._append_session_sample(source_path, boarding=boarding)
                self.session_file_completed.emit(source_path)
                self._last_processed = source_path
            elif job.context.get("append_history"):
                self._append_session_sample(source_path, boarding=boarding)
                self.session_file_completed.emit(source_path)
                self._last_processed = source_path
        # Shape / mixture arming mid-job is handled by phase-boundary replan
        # (``plan_for(..., completed=job.completed)``). No post-job followup path.
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None

    def _start_next_job_step(self) -> None:
        assert self._current_job is not None
        step = self._current_job.steps[self._job_step_idx]
        step_results = self._current_job.completed.results
        try:
            req = resolve_request_placeholders(step.request, results_by_step=step_results)
        except PlaceholderError as e:
            if step.name in ("fit_distances", "analyze_kratky") and "fit_guinier." in str(e):
                guinier = step_results.get("fit_guinier")
                if isinstance(guinier, dict):
                    guinier = self._enrich_fit_guinier_result(
                        dict(guinier), resolve_bases=self._artifact_resolve_bases_for_job()
                    )
                if not isinstance(guinier, dict) or guinier.get("rg") is None:
                    self.error.emit(
                        "Guinier fit produced no Rg for this curve (empty or invalid interval). "
                        "Open the monodisperse wizard and adjust the Guinier range, or clear guinier.conf."
                    )
                    self._finish_job(ok=False)
                    return
            self.error.emit(f"Job placeholder resolution failed ({step.name}): {e}")
            self._finish_job(ok=False)
            return
        self._pending_step_name = step.name
        skill = str(req.skill_name or "").strip()
        from ...modeling.skills import MODELING_SKILLS

        if skill in MODELING_SKILLS:
            self.error.emit(
                f"Liveview refused modeling skill {skill!r} "
                "(run Confirm in guisaxs-shape / guisaxs-dr instead)."
            )
            self._finish_job(ok=False)
            return
        self.skill_started.emit(req.skill_name)
        self._runner.start(req)

    def _enrich_fit_distances_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        return _artifacts.enrich_fit_distances_result(result, watchdir=self._state.watchdir)

    def _resolve_artifact_path(
        self, path_str: str, *, resolve_bases: Optional[Sequence[Path]] = None
    ) -> Path:
        return _artifacts.resolve_artifact_path(
            path_str, resolve_bases=resolve_bases, watchdir=self._state.watchdir
        )

    def _artifact_resolve_bases_for_job(self) -> List[Path]:
        return _artifacts.artifact_resolve_bases_for_job(
            self._current_job, watchdir=self._state.watchdir
        )

    def _enrich_fit_guinier_result(
        self, result: Dict[str, Any], *, resolve_bases: Optional[Sequence[Path]] = None
    ) -> Dict[str, Any]:
        return _artifacts.enrich_fit_guinier_result(
            result, watchdir=self._state.watchdir, resolve_bases=resolve_bases
        )

    def build_polydisperse_manual_job(
        self,
        *,
        profile_abs: str,
        steps: List[JobStep],
        output_root: Optional[Path] = None,
        priority: int = 150,
    ) -> Job:
        return _manual_jobs.build_polydisperse_manual_job(
            profile_abs=profile_abs,
            steps=steps,
            output_root=output_root,
            watchdir=self._state.watchdir,
            priority=priority,
        )

    def build_monodisperse_manual_job(
        self,
        *,
        profile_abs: str,
        steps: List[JobStep],
        output_root: Optional[Path] = None,
        priority: int = 150,
    ) -> Job:
        return _manual_jobs.build_monodisperse_manual_job(
            profile_abs=profile_abs,
            steps=steps,
            output_root=output_root,
            watchdir=self._state.watchdir,
            priority=priority,
        )

    def monodisperse_steps_guinier_and_distances(
        self,
        profile_abs: str,
        *,
        output_root: Path,
        guinier_handoff: Optional[dict] = None,
        fixed_guinier_interval: bool = False,
        guinier_interval_first: Optional[int] = None,
        guinier_interval_last: Optional[int] = None,
    ) -> List[JobStep]:
        return build_monodisperse_steps(
            profile_abs,
            output_root=output_root,
            state=self._state,
            parts=MonodispersePipelineParts.GUINIER_AND_DISTANCES,
            load_yaml=self._load_yaml_options,
            guinier_handoff=guinier_handoff,
            fixed_guinier_interval=fixed_guinier_interval,
            guinier_interval_first=guinier_interval_first,
            guinier_interval_last=guinier_interval_last,
        )

    def monodisperse_step_fit_distances(
        self, profile_abs: str, *, output_root: Path, guinier_handoff: Optional[dict] = None
    ) -> JobStep:
        steps = build_monodisperse_steps(
            profile_abs,
            output_root=output_root,
            state=self._state,
            parts=MonodispersePipelineParts.DISTANCES_ONLY,
            load_yaml=self._load_yaml_options,
            guinier_handoff=guinier_handoff,
        )
        return steps[0]

    def monodisperse_step_shape(
        self,
        profile_abs: str,
        *,
        output_root: Path,
        shape_mode: str,
        gnom_out_path: Optional[str] = None,
    ) -> Optional[JobStep]:
        return _manual_jobs.monodisperse_step_shape(
            state=self._state,
            profile_abs=profile_abs,
            output_root=output_root,
            shape_mode=shape_mode,
            load_yaml=self._load_yaml_options,
            gnom_out_path=gnom_out_path,
        )

    def _sync_last_paths_from_result(self, result: Dict[str, Any]) -> None:
        integ = result.get("integrated_1d")
        integ_path: Optional[str] = None
        if isinstance(integ, list) and integ and isinstance(integ[-1], str):
            integ_path = integ[-1]
        elif isinstance(integ, str):
            integ_path = integ
        if integ_path:
            try:
                p = Path(integ_path)
                if p.is_file():
                    self._state.last_integrated_dat_path = p
            except Exception:
                pass

        sub = result.get("subtracted_1d")
        sub_path: Optional[str] = None
        if isinstance(sub, str):
            sub_path = sub
        elif isinstance(sub, list) and sub and isinstance(sub[-1], str):
            sub_path = sub[-1]
        if sub_path:
            try:
                p = Path(sub_path)
                if p.is_file():
                    self._state.last_subtracted_dat_path = p
            except Exception:
                pass

    def _on_skill_finished(self, outcome: RunOutcome) -> None:
        # Only handle runner completions that were started by this executor.
        if self._current_job is None or not self._pending_step_name:
            return
        step_name = self._pending_step_name
        self._pending_step_name = None

        # Guard _tick for the whole handler: skill_finished slots may open modal
        # dialogs (nested event loop). Without this, _tick can restart the same
        # step while _current_job is still set, leaving the wizard stuck busy.
        self._handling_skill_outcome = True
        try:
            # Push raw result to UI for job-driven steps.
            ui_result = dict(outcome.result or {})
            if step_name:
                ui_result["_liveview_step"] = step_name
            self.latest_artifacts.emit(ui_result)
            self._sync_last_paths_from_result(outcome.result)
            self.skill_finished.emit(outcome)

            # Record step result on the Job (sole progress owner), then advance.
            res = dict(outcome.result or {})
            if step_name == "fit_distances":
                res = self._enrich_fit_distances_result(res)
            if step_name in (FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP):
                res = self._enrich_fit_guinier_result(res, resolve_bases=self._artifact_resolve_bases_for_job())

            if not outcome.success:
                # Job failed/cancelled.
                if self._requeue_cancelled_job and self._current_job is not None:
                    try:
                        # Job.completed (phases + results) is preserved on retry.
                        self._jobs.put(
                            self._current_job.as_retry(priority=int(self._requeue_priority))
                        )
                    except Exception:
                        pass
                self._requeue_cancelled_job = False
                self._finish_job(ok=False)
                return

            if step_name == "fit_distances" and not is_atsas_fit_ok(res):
                self.error.emit(failure_message_from_result(res, skill_id="fit_distances"))
                self._finish_job(ok=True)
                return

            if step_name == "fit_sizes" and not is_atsas_fit_ok(res):
                self.error.emit(failure_message_from_result(res, skill_id="fit_sizes"))
                self._finish_job(ok=True)
                return

            # Mark progress on the Job, then replan remaining steps (auto only).
            # Manual jobs keep a frozen step list (index advance).
            self._current_job = self._current_job.mark_step_done(step_name, result=res)
            if is_manual_job(self._current_job):
                self._job_step_idx += 1
            else:
                if not self._replan_current_job():
                    self._finish_job(ok=False)
        finally:
            self._handling_skill_outcome = False

    def _subtraction_output_root(self, *, sample_dat: str) -> Path:
        return _manual_jobs.subtraction_output_root(state=self._state, sample_dat=sample_dat)

    def _analysis_steps_for_profile(
        self,
        profile_abs: str,
        *,
        output_root: Path,
        use_ui_params: bool = False,
    ) -> List[JobStep]:
        return _manual_jobs.analysis_steps_for_profile(
            self._state,
            profile_abs,
            output_root=output_root,
            load_yaml=self._load_yaml_options,
            use_ui_params=use_ui_params,
        )

    def _model_mixture_run_options(self) -> dict:
        """Skill options from window Apply; omit empty values and persistence-only keys."""
        raw = self._state.model_mixture_options
        if not isinstance(raw, dict):
            return {}
        skip = frozenset({"output_dir", "use_cache", "config_path"})
        out: dict = {}
        for key, value in raw.items():
            if key in skip:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            out[str(key)] = value
        return out

    @staticmethod
    def _load_yaml_options(path: Optional[Path]) -> dict:
        if path is None:
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
        except Exception:
            return {}
        return {}

