from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ...core.models import RunRequest
from ...logic.runner_qprocess import RunOutcome, SkillRunner
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from ..ingest.stability import StabilityConfig, StabilityTracker
from ..ingest.tiff_revision import (
    TiffRevision,
    TiffRevisionSource,
    is_newer_than,
    is_tiff_path,
    make_revision,
    normalize_tiff_path,
)
from ..session.output_paths import (
    averaged_dir,
    averaged_proxy_dir,
    integrated_dat_path,
    subtracted_dat_path,
    subtracted_dir,
    tiff_output_root,
)
from ..session.state import (
    LiveviewSessionState,
    LiveviewState,
    LiveviewWatchMode,
    MonodisperseShapeMode,
    PolydisperseMixtureMode,
)
from ..services.artifacts import merge_fit_distances_quality_fields
from .jobs import Job, JobStep, PlaceholderError, is_manual_job, resolve_request_placeholders
from .monodisperse_pipeline import (
    FIT_GUINIER_MONO_STEP,
    FIT_GUINIER_POLY_STEP,
    MonodispersePipelineParts,
    build_monodisperse_steps,
    job_includes_shape,
    profile_sample_stem,
)
from .polydisperse_pipeline import (
    PolydispersePipelineParts,
    build_polydisperse_steps,
    job_includes_mixture,
)
from .queue import FIFOQueue, JobQueue, QueueItem, RevisionEnqueueResult


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
    - tracks incoming TIFFs until stable
    - converts them into Jobs based on current LiveviewSessionState
    - executes Jobs step-by-step using SkillRunner
    """

    queue_status = pyqtSignal(object)  # LiveviewQueueStatus
    latest_artifacts = pyqtSignal(object)  # dict
    error = pyqtSignal(str)
    session_file_completed = pyqtSignal(str)  # tiff path
    tiff_revision_pending = pyqtSignal(object)  # TiffRevision — queued or stabilizing
    skill_started = pyqtSignal(str)
    skill_finished = pyqtSignal(object)  # RunOutcome

    def __init__(self, *, state: LiveviewSessionState, runner: SkillRunner) -> None:
        super().__init__()
        self._state = state
        self._runner = runner

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._tick)

        self._paused: bool = False
        self._pause_sources: set[str] = set()

        self._incoming = FIFOQueue()
        self._stability_cfg = StabilityConfig()
        self._current_incoming: Optional[QueueItem] = None
        self._current_stability: Optional[StabilityTracker] = None

        self._jobs = JobQueue()
        self._current_job: Optional[Job] = None
        self._job_step_idx: int = 0
        self._pending_step_name: Optional[str] = None
        self._step_results: Dict[str, Dict[str, Any]] = {}

        self._last_processed: str = ""
        self._durations: List[float] = []
        self._job_started_at: float = 0.0

        self._session_tiff_history: List[str] = []

        self._runner.finished.connect(self._on_skill_finished)
        self._requeue_cancelled_job: bool = False
        self._requeue_priority: int = 50
        # True while handling a skill result (incl. modal UI in skill_finished slots).
        # Nested Qt event loops must not let _tick restart the same job step.
        self._handling_skill_outcome: bool = False

    def start(self) -> None:
        if self._tick_timer.isActive():
            return
        self._tick_timer.start()

    def stop(self) -> None:
        self._tick_timer.stop()
        self._paused = False
        self._pause_sources.clear()
        self._current_incoming = None
        self._current_stability = None
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None
        self._step_results.clear()

    def pause(self, *, source: str = "default") -> None:
        self._pause_sources.add(str(source))
        self._paused = bool(self._pause_sources)

    def resume(self, *, source: Optional[str] = None) -> None:
        if source is None:
            self._pause_sources.clear()
        else:
            self._pause_sources.discard(str(source))
        self._paused = bool(self._pause_sources)

    def cancel_current(self) -> None:
        # Cancellation policy: by default we requeue the current job so users don't lose the file.
        # Call sites that don't want this can toggle `_requeue_cancelled_job` before cancelling.
        self.cancel_running(requeue=True)

    @property
    def paused(self) -> bool:
        return bool(self._paused)

    @property
    def session_processed_tiffs(self) -> tuple[str, ...]:
        return tuple(self._session_tiff_history)

    def is_idle(self) -> bool:
        """True when not paused and no skill, job, or incoming TIFF work is active."""
        if self._paused:
            return False
        if self._runner.is_running():
            return False
        if self._current_job is not None:
            return False
        if self._current_incoming is not None:
            return False
        if len(self._incoming) > 0 or len(self._jobs) > 0:
            return False
        return True

    def is_processing_idle(self) -> bool:
        """True when no autosaxs skill subprocess is currently running."""
        return not self._runner.is_running()

    @property
    def queue_suspended(self) -> bool:
        return bool(self._paused)

    def enqueue_revision(
        self,
        revision: TiffRevision,
        *,
        stability_cfg: Optional[StabilityConfig] = None,
    ) -> None:
        """Accept an observed TIFF revision into the incoming pipeline."""
        item = QueueItem.from_revision(revision, stability_cfg=stability_cfg)
        accepted = self._accept_incoming_revision(item)
        if accepted:
            self._jobs.drop_jobs_for_tiff_path(revision.path)
            self.tiff_revision_pending.emit(revision)

    def enqueue_tiff(
        self,
        *,
        path: str,
        detected_at_monotonic: float,
        stability_cfg: Optional[StabilityConfig] = None,
        stat: Optional[object] = None,
    ) -> None:
        from .stability import FileStatSnapshot

        snap = stat if isinstance(stat, FileStatSnapshot) else None
        rev = make_revision(
            path=path,
            detected_at=detected_at_monotonic,
            source=TiffRevisionSource.MANUAL,
            stat=snap,
        )
        if rev is None:
            return
        self.enqueue_revision(rev, stability_cfg=stability_cfg)

    def _accept_incoming_revision(self, item: QueueItem) -> bool:
        """Queue or upgrade a revision; return True when the pending work item changed."""
        cur = self._current_incoming
        if cur is not None and normalize_tiff_path(cur.path) == normalize_tiff_path(item.path):
            if cur.observed_stat == item.observed_stat:
                return False
            if not is_newer_than(item.observed_stat, cur.observed_stat):
                return False
            cfg = item.stability_cfg or cur.stability_cfg or self._stability_cfg
            self._current_incoming = QueueItem(
                path=item.path,
                detected_at_monotonic=item.detected_at_monotonic,
                observed_stat=item.observed_stat,
                stability_cfg=cfg,
            )
            self._current_stability = StabilityTracker(path=item.path, cfg=cfg)
            return True

        result = self._incoming.put_revision(item)
        return result in (RevisionEnqueueResult.ADDED, RevisionEnqueueResult.REPLACED)

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
        """
        Build a high-priority job to rerun subtraction for a single displayed file and optionally rerun analysis.

        `sample_dat` is expected to be `averaged/int_<stem>.dat`.
        Output overwrites `subtracted/sub_<stem>.dat` by autosaxs naming convention.

        When ``use_ui_params`` is True, analysis steps use pane/session values already synced
        from the open analysis windows (explicit Guinier interval when set; omit when auto).
        """
        sp = (sample_dat or "").strip()
        bp = (buffer_dat or "").strip()
        stem = Path(sp).stem
        if stem.startswith("int_"):
            stem = stem[len("int_") :]
        root = self._subtraction_output_root(sample_dat=sp)
        subdir = subtracted_dir(root)
        subdir.mkdir(parents=True, exist_ok=True)
        opts = {"output_dir": str(subdir.resolve()), "use_cache": False}
        opts.update(dict(self._state.subtract_options or {}))
        opts["scaling_factor"] = float(scaling_factor)
        steps: List[JobStep] = [
            JobStep(
                name="subtract",
                request=RunRequest(
                    skill_name="subtract",
                    positional=[sp, bp],
                    options=opts,
                ),
            )
        ]
        profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
        steps.extend(
            self._analysis_steps_for_profile(
                profile,
                output_root=root,
                use_ui_params=bool(use_ui_params),
            )
        )
        return Job(
            id=f"rerun_sub:{stem}:{time.time_ns()}",
            priority=int(priority),
            steps=steps,
            context={"manual": True, "tiff_stem": stem, "profile_path": profile},
        )

    def _append_session_tiff(self, path: str) -> None:
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = (path or "").strip()
        if not key:
            return
        try:
            if key in self._session_tiff_history:
                self._session_tiff_history.remove(key)
            self._session_tiff_history.append(key)
        except Exception:
            return

    def _emit_status(self) -> None:
        avg = (sum(self._durations) / len(self._durations)) if self._durations else 0.0
        qn = len(self._incoming) + len(self._jobs)
        cur_path = ""
        if self._runner.is_running():
            cur_path = self._pending_step_name or ""
        elif self._current_incoming is not None:
            cur_path = self._current_incoming.path
        elif self._current_job is not None:
            cur_path = str(self._current_job.context.get("tiff_path") or "")
        rem = qn + (1 if (self._runner.is_running() or self._current_job is not None or self._current_incoming is not None) else 0)
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

        # If a job is active and no step is pending, advance.
        if self._current_job is not None:
            if self._job_step_idx >= len(self._current_job.steps):
                self._finish_job(ok=True)
                return
            # When auto-processing is suspended, only manual jobs may advance.
            if not self._paused or is_manual_job(self._current_job):
                self._start_next_job_step()
            return

        if self._paused:
            # Suspended: run queued manual jobs only; hold TIFF intake and auto jobs.
            nxt = self._jobs.get_nowait_manual()
            if nxt is not None:
                self._start_job(nxt)
            return

        # No current job: promote stable incoming TIFFs into jobs.
        self._advance_incoming_until_job_ready()

        # Start next job if available.
        nxt = self._jobs.get_nowait()
        if nxt is None:
            return
        self._start_job(nxt)

    def _advance_incoming_until_job_ready(self) -> None:
        # If already tracking a file, keep polling stability.
        if self._current_incoming is None:
            item = self._incoming.get_nowait()
            if item is None:
                return
            self._current_incoming = item
            cfg = item.stability_cfg or self._stability_cfg
            self._current_stability = StabilityTracker(path=item.path, cfg=cfg)

        if self._current_stability is None or self._current_incoming is None:
            return

        stable = self._current_stability.tick()
        if stable is None:
            self.error.emit(f"File did not become stable (timeout), skipping: {self._current_incoming.path}")
            self._current_incoming = None
            self._current_stability = None
            return
        if stable is False:
            return

        # Stable -> build and enqueue processing job if this is a TIFF; else ignore.
        tiff = self._current_incoming.path
        self._current_incoming = None
        self._current_stability = None
        if not self._is_tiff_path(tiff):
            return
        try:
            job = self._build_process_tiff_job(tiff_path=tiff)
        except Exception as e:
            self.error.emit(f"Cannot build job for TIFF: {tiff}\n{e}")
            return
        self._jobs.drop_jobs_for_tiff_path(tiff)
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

    def _start_job(self, job: Job) -> None:
        self._current_job = job
        self._job_step_idx = 0
        self._pending_step_name = None
        self._step_results = {}
        self._job_started_at = time.monotonic()

    def _finish_job(self, *, ok: bool) -> None:
        job = self._current_job
        if job is None:
            return
        dt = max(0.0, time.monotonic() - self._job_started_at)
        if ok:
            self._durations.append(dt)
            if len(self._durations) > 50:
                self._durations = self._durations[-50:]
        tiff_path = str(job.context.get("tiff_path") or "").strip()
        if ok and tiff_path and self._is_tiff_path(tiff_path):
            self._append_session_tiff(tiff_path)
            self.session_file_completed.emit(tiff_path)
            self._last_processed = tiff_path
        followup = self._shape_followup_job_if_needed(job=job, ok=ok)
        if followup is None:
            followup = self._mixture_followup_job_if_needed(job=job, ok=ok)
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None
        self._step_results.clear()
        if followup is not None:
            self._jobs.put(followup)

    def _shape_followup_job_if_needed(self, *, job: Job, ok: bool) -> Optional[Job]:
        """
        If an auto job finished without a shape step but shape mode is now set
        (e.g. user selected DAMMIF mid-run), enqueue a shape-only follow-up.
        """
        if not ok or is_manual_job(job):
            return None
        if not self._state.monodisperse_armed:
            return None
        if self._state.monodisperse_shape_mode == MonodisperseShapeMode.NONE:
            return None
        if job_includes_shape(list(job.steps)):
            return None
        fd = self._step_results.get("fit_distances")
        if not isinstance(fd, dict) or not is_atsas_fit_ok(fd):
            return None
        prof = str(job.context.get("profile_path") or "").strip()
        if not prof:
            for step in job.steps:
                if step.name in (FIT_GUINIER_MONO_STEP, "fit_distances") and step.request.positional:
                    prof = str(step.request.positional[0]).strip()
                    break
        if not prof:
            return None
        try:
            root_raw = job.context.get("output_root")
            root = (
                Path(str(root_raw)).expanduser().resolve()
                if root_raw
                else self._state.watchdir.expanduser().resolve()
            )
        except OSError:
            root = self._state.watchdir.expanduser().resolve()
        gnom_hint = ""
        if isinstance(fd.get("best_gnom_out_path"), str):
            gnom_hint = fd["best_gnom_out_path"].strip()
        steps = build_monodisperse_steps(
            prof,
            output_root=root,
            state=self._state,
            parts=MonodispersePipelineParts.SHAPE_ONLY,
            load_yaml=self._load_yaml_options,
            gnom_out_path=gnom_hint or None,
        )
        if not steps:
            return None
        stem = profile_sample_stem(prof)
        return Job(
            id=f"mono_shape_followup:{stem}:{time.time_ns()}",
            priority=0,
            steps=steps,
            context={
                "monodisperse": True,
                "shape_followup": True,
                "profile_path": str(Path(prof).expanduser().resolve()),
                "tiff_stem": stem,
                "output_root": str(root),
                "tiff_path": str(job.context.get("tiff_path") or "").strip(),
            },
        )

    def _mixture_followup_job_if_needed(self, *, job: Job, ok: bool) -> Optional[Job]:
        """
        If an auto job finished without a mixture step but mixture mode is now set
        (e.g. user enabled Mixture mid-run), enqueue a mixture-only follow-up.
        """
        if not ok or is_manual_job(job):
            return None
        if not self._state.polydisperse_armed:
            return None
        if self._state.polydisperse_mixture_mode == PolydisperseMixtureMode.NONE:
            return None
        if job_includes_mixture(list(job.steps)):
            return None
        fs = self._step_results.get("fit_sizes")
        if not isinstance(fs, dict) or not is_atsas_fit_ok(fs):
            return None
        prof = str(job.context.get("profile_path") or "").strip()
        if not prof:
            for step in job.steps:
                if step.name in (FIT_GUINIER_POLY_STEP, FIT_GUINIER_MONO_STEP, "fit_sizes") and step.request.positional:
                    prof = str(step.request.positional[0]).strip()
                    break
        if not prof:
            return None
        try:
            root_raw = job.context.get("output_root")
            root = (
                Path(str(root_raw)).expanduser().resolve()
                if root_raw
                else self._state.watchdir.expanduser().resolve()
            )
        except OSError:
            root = self._state.watchdir.expanduser().resolve()
        steps = build_polydisperse_steps(
            prof,
            output_root=root,
            state=self._state,
            parts=PolydispersePipelineParts.MIXTURE_ONLY,
            load_yaml=self._load_yaml_options,
        )
        if not steps:
            return None
        stem = profile_sample_stem(prof)
        return Job(
            id=f"poly_mixture_followup:{stem}:{time.time_ns()}",
            priority=0,
            steps=steps,
            context={
                "polydisperse": True,
                "mixture_followup": True,
                "profile_path": str(Path(prof).expanduser().resolve()),
                "tiff_stem": stem,
                "output_root": str(root),
                "tiff_path": str(job.context.get("tiff_path") or "").strip(),
            },
        )

    def _start_next_job_step(self) -> None:
        assert self._current_job is not None
        step = self._current_job.steps[self._job_step_idx]
        try:
            req = resolve_request_placeholders(step.request, results_by_step=self._step_results)
        except PlaceholderError as e:
            if step.name == "fit_distances" and "fit_guinier.rg" in str(e):
                guinier = self._step_results.get("fit_guinier")
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
        self.skill_started.emit(req.skill_name)
        self._runner.start(req)

    @staticmethod
    def _coerce_opt_int(val: Any) -> Optional[int]:
        if val is None or isinstance(val, bool):
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _read_first_last_from_best_summary(path: Path) -> Tuple[Optional[int], Optional[int]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        sel = data.get("selected")
        if not isinstance(sel, dict):
            return None, None
        return (
            LiveviewJobExecutor._coerce_opt_int(sel.get("first")),
            LiveviewJobExecutor._coerce_opt_int(sel.get("last")),
        )

    @staticmethod
    def _read_first_last_from_fit_params(path: Path) -> Tuple[Optional[int], Optional[int]]:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return None, None
        if not isinstance(data, dict):
            return None, None
        return (
            LiveviewJobExecutor._coerce_opt_int(data.get("first")),
            LiveviewJobExecutor._coerce_opt_int(data.get("last")),
        )

    def _enrich_fit_distances_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(result or {})
        watchdir = self._state.watchdir
        bs = out.get("fit_distances_log_path")
        fp = out.get("fit_params_path")
        first_i: Optional[int] = None
        last_i: Optional[int] = None
        try:
            if isinstance(bs, str) and bs.strip():
                p = Path(bs.strip()).expanduser()
                p = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
                if p.is_file():
                    first_i, last_i = self._read_first_last_from_best_summary(p)
        except Exception:
            first_i, last_i = None, None
        if first_i is None and last_i is None:
            try:
                if isinstance(fp, str) and fp.strip():
                    p2 = Path(fp.strip()).expanduser()
                    p2 = p2.resolve() if p2.is_absolute() else (watchdir / p2).resolve()
                    if p2.is_file():
                        first_i, last_i = self._read_first_last_from_fit_params(p2)
            except Exception:
                first_i, last_i = None, None
        if first_i is not None:
            out["selected_first"] = int(first_i)
        if last_i is not None:
            out["selected_last"] = int(last_i)
        return merge_fit_distances_quality_fields(out, watchdir=watchdir)

    def _resolve_artifact_path(
        self, path_str: str, *, resolve_bases: Optional[Sequence[Path]] = None
    ) -> Path:
        p = Path(path_str.strip()).expanduser()
        if p.is_absolute():
            return p.resolve()
        bases = [b.expanduser().resolve() for b in (resolve_bases or [])]
        if not bases:
            bases = [self._state.watchdir.expanduser().resolve()]
        for base in bases:
            cand = (base / p).resolve()
            if cand.is_file():
                return cand
        return (bases[0] / p).resolve()

    def _artifact_resolve_bases_for_job(self) -> List[Path]:
        bases: List[Path] = []
        job = self._current_job
        if job is None:
            return [self._state.watchdir.expanduser().resolve()]
        or_raw = job.context.get("output_root")
        if isinstance(or_raw, str) and or_raw.strip():
            bases.append(Path(or_raw.strip()).expanduser().resolve())
        for step in job.steps:
            if step.name not in (FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP):
                continue
            od = (step.request.options or {}).get("output_dir")
            if isinstance(od, str) and od.strip():
                bases.append(Path(od.strip()).expanduser().resolve())
        bases.append(self._state.watchdir.expanduser().resolve())
        seen: set[str] = set()
        out: List[Path] = []
        for b in bases:
            key = str(b)
            if key in seen:
                continue
            seen.add(key)
            out.append(b)
        return out

    def _enrich_fit_guinier_result(
        self, result: Dict[str, Any], *, resolve_bases: Optional[Sequence[Path]] = None
    ) -> Dict[str, Any]:
        from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

        out = dict(result or {})
        grp_raw = out.get("guinier_region_path")
        if isinstance(grp_raw, list) and len(grp_raw) == 1:
            grp_raw = grp_raw[0]
        if not isinstance(grp_raw, str) or not grp_raw.strip():
            return out
        try:
            p = self._resolve_artifact_path(grp_raw.strip(), resolve_bases=resolve_bases)
            if not p.is_file():
                return out
            data = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return out
        if not isinstance(data, dict):
            return out
        for key in (
            "rg",
            "i0",
            "first_point_1based",
            "last_point_1based",
            "classification",
            "quality_class",
        ):
            if key in data and data[key] is not None:
                out[key] = data[key]
        fp, lp = guinier_point_range_1based(data)
        if fp is not None:
            out["first_point_1based"] = fp
        if lp is not None:
            out["last_point_1based"] = lp
        return out

    def build_polydisperse_manual_job(
        self,
        *,
        profile_abs: str,
        steps: List[JobStep],
        output_root: Optional[Path] = None,
        priority: int = 150,
    ) -> Job:
        prof = str(Path(profile_abs).expanduser().resolve())
        stem = profile_sample_stem(prof)
        root = (output_root or self._state.watchdir).expanduser().resolve()
        return Job(
            id=f"poly_manual:{stem}:{time.time_ns()}",
            priority=int(priority),
            steps=steps,
            context={
                "manual": True,
                "polydisperse": True,
                "profile_path": prof,
                "tiff_stem": stem,
                "output_root": str(root),
            },
        )

    def build_monodisperse_manual_job(
        self,
        *,
        profile_abs: str,
        steps: List[JobStep],
        output_root: Optional[Path] = None,
        priority: int = 150,
    ) -> Job:
        prof = str(Path(profile_abs).expanduser().resolve())
        stem = profile_sample_stem(prof)
        root = (output_root or self._state.watchdir).expanduser().resolve()
        return Job(
            id=f"mono_manual:{stem}:{time.time_ns()}",
            priority=int(priority),
            steps=steps,
            context={
                "manual": True,
                "monodisperse": True,
                "profile_path": prof,
                "tiff_stem": stem,
                "output_root": str(root),
            },
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
        # Prefer explicit mode passed by caller; temporarily sync session if needed.
        prev = self._state.monodisperse_shape_mode
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(str(shape_mode).lower())
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        try:
            steps = build_monodisperse_steps(
                profile_abs,
                output_root=output_root,
                state=self._state,
                parts=MonodispersePipelineParts.SHAPE_ONLY,
                load_yaml=self._load_yaml_options,
                gnom_out_path=gnom_out_path,
            )
        finally:
            self._state.monodisperse_shape_mode = prev
        return steps[0] if steps else None

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

            # Record step result for placeholder substitution.
            res = dict(outcome.result or {})
            if step_name == "fit_distances":
                res = self._enrich_fit_distances_result(res)
            if step_name in (FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP):
                res = self._enrich_fit_guinier_result(res, resolve_bases=self._artifact_resolve_bases_for_job())
            if step_name:
                self._step_results[step_name] = res

            if not outcome.success:
                # Job failed/cancelled.
                if self._requeue_cancelled_job and self._current_job is not None:
                    try:
                        j = self._current_job
                        retry = Job(
                            id=f"{j.id}:retry:{time.time_ns()}",
                            priority=int(self._requeue_priority),
                            steps=list(j.steps),
                            context=dict(j.context),
                        )
                        self._jobs.put(retry)
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

            # Advance to next step.
            self._job_step_idx += 1
        finally:
            self._handling_skill_outcome = False

    def _subtraction_output_root(self, *, sample_dat: str) -> Path:
        wd = self._state.watchdir.resolve()
        if self._state.watch_mode != LiveviewWatchMode.TREE:
            return wd
        sp = Path((sample_dat or "").strip()).expanduser().resolve()
        if sp.parent.name in ("averaged", "averaged_proxy"):
            return sp.parent.parent
        return sp.parent

    def _build_process_tiff_job(self, *, tiff_path: str) -> Job:
        """
        Build a Job for a stable incoming TIFF based on current session state.

        Uses deterministic output paths based on TIFF stem (watchdir conventions).
        """
        tp = (tiff_path or "").strip()
        stem = Path(tp).stem
        wd = self._state.watchdir
        root = tiff_output_root(watchdir=wd, tiff_path=tp, mode=self._state.watch_mode)

        st = self._state.current_state()
        steps: List[JobStep] = []

        # Always disable caching for live runs.
        if st == LiveviewState.A:
            outdir = averaged_proxy_dir(root)
            outdir.mkdir(parents=True, exist_ok=True)
            steps.append(
                JobStep(
                    name="integrate_proxy",
                    request=RunRequest(
                        skill_name="integrate_proxy",
                        positional=[tp],
                        options={"output_dir": str(outdir), "use_cache": False},
                    ),
                )
            )
            return Job(
                id=f"tiff:{stem}:{time.time_ns()}",
                priority=0,
                steps=steps,
                context={"tiff_path": tp, "tiff_stem": stem, "output_root": str(root.resolve())},
            )

        # Calibrated paths (B/BD/C/CD)
        if self._state.integrator_dir is None:
            raise RuntimeError("Missing integrator_dir (not calibrated)")
        outdir = averaged_dir(root)
        outdir.mkdir(parents=True, exist_ok=True)
        steps.append(
            JobStep(
                name="integrate",
                request=RunRequest(
                    skill_name="integrate",
                    positional=[tp, str(self._state.integrator_dir)],
                    options={"output_dir": str(outdir), "use_cache": False},
                ),
            )
        )

        integrated_dat = str(integrated_dat_path(root=root, stem=stem, integrator_ready=True).resolve())

        if st in (LiveviewState.C, LiveviewState.CD):
            if self._state.buffer_dat_path is None or self._state.subtract_options is None:
                raise RuntimeError("State C requires buffer_dat_path and subtract_options")
            subdir = subtracted_dir(root)
            subdir.mkdir(parents=True, exist_ok=True)
            opts = {"output_dir": str(subdir), "use_cache": False}
            opts.update(dict(self._state.subtract_options or {}))
            steps.append(
                JobStep(
                    name="subtract",
                    request=RunRequest(
                        skill_name="subtract",
                        positional=[integrated_dat, str(self._state.buffer_dat_path)],
                        options=opts,
                    ),
                )
            )
            profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
            steps.extend(self._analysis_steps_for_profile(profile, output_root=root))
            return Job(
                id=f"tiff:{stem}:{time.time_ns()}",
                priority=0,
                steps=steps,
                context={"tiff_path": tp, "tiff_stem": stem, "output_root": str(root.resolve())},
            )

        # State B/BD
        profile = integrated_dat
        steps.extend(self._analysis_steps_for_profile(profile, output_root=root))
        return Job(
            id=f"tiff:{stem}:{time.time_ns()}",
            priority=0,
            steps=steps,
            context={"tiff_path": tp, "tiff_stem": stem, "output_root": str(root.resolve())},
        )

    def _analysis_steps_for_profile(
        self,
        profile_abs: str,
        *,
        output_root: Path,
        use_ui_params: bool = False,
    ) -> List[JobStep]:
        if not self._state.analysis_enabled():
            return []
        prof = str(Path(profile_abs).expanduser().resolve())
        steps: List[JobStep] = []
        # When driven from open analysis panes, pass fixed_guinier_interval so
        # explicit first/last from session are used; omit when still (auto).
        fixed = bool(use_ui_params)
        if self._state.monodisperse_armed:
            steps.extend(
                build_monodisperse_steps(
                    prof,
                    output_root=output_root,
                    state=self._state,
                    parts=MonodispersePipelineParts.FULL,
                    load_yaml=self._load_yaml_options,
                    guinier_handoff=None,
                    fixed_guinier_interval=fixed,
                )
            )
        if self._state.polydisperse_armed:
            steps.extend(
                build_polydisperse_steps(
                    prof,
                    output_root=output_root,
                    state=self._state,
                    parts=PolydispersePipelineParts.FULL,
                    load_yaml=self._load_yaml_options,
                    fixed_guinier_interval=fixed,
                )
            )
        return steps

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

