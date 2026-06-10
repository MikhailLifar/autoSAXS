from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ..core.models import RunRequest
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from .jobs import Job, JobStep, PlaceholderError, resolve_request_placeholders
from .queue import FIFOQueue, JobQueue, QueueItem
from .stability import StabilityConfig, StabilityTracker
from .state import AnalysisMode, DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES, LiveviewSessionState, LiveviewState


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

    def __init__(self, *, state: LiveviewSessionState, runner: SkillRunner) -> None:
        super().__init__()
        self._state = state
        self._runner = runner

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._tick)

        self._paused: bool = False

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

    def start(self) -> None:
        if self._tick_timer.isActive():
            return
        self._tick_timer.start()

    def stop(self) -> None:
        self._tick_timer.stop()
        self._paused = False
        self._current_incoming = None
        self._current_stability = None
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None
        self._step_results.clear()

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def cancel_current(self) -> None:
        # Cancellation policy: by default we requeue the current job so users don't lose the file.
        # Call sites that don't want this can toggle `_requeue_cancelled_job` before cancelling.
        if self._current_job is not None:
            self._requeue_cancelled_job = True
        try:
            self._runner.cancel()
        except Exception:
            pass

    @property
    def paused(self) -> bool:
        return bool(self._paused)

    @property
    def session_processed_tiffs(self) -> tuple[str, ...]:
        return tuple(self._session_tiff_history)

    def enqueue_tiff(self, *, path: str, detected_at_monotonic: float) -> None:
        cur = self._current_incoming.path if self._current_incoming else None
        self._incoming.put_if_absent(
            QueueItem(path=path, detected_at_monotonic=float(detected_at_monotonic)),
            current_path=cur,
        )

    def enqueue_job(self, job: Job) -> None:
        self._jobs.put(job)

    def build_rerun_subtraction_job(
        self,
        *,
        sample_dat: str,
        buffer_dat: str,
        scaling_factor: float,
        priority: int = 100,
    ) -> Job:
        """
        Build a high-priority job to rerun subtraction for a single displayed file and optionally rerun analysis.

        `sample_dat` is expected to be `averaged/int_<stem>.dat`.
        Output overwrites `subtracted/sub_<stem>.dat` by autosaxs naming convention.
        """
        sp = (sample_dat or "").strip()
        bp = (buffer_dat or "").strip()
        stem = Path(sp).stem
        if stem.startswith("int_"):
            stem = stem[len("int_") :]
        wd = self._state.watchdir
        subdir = wd / "subtracted"
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
        profile = str((wd / "subtracted" / f"sub_{stem}.dat").resolve())
        steps.extend(self._analysis_steps_for_profile(profile))
        return Job(
            id=f"rerun_sub:{stem}:{time.time_ns()}",
            priority=int(priority),
            steps=steps,
            context={"tiff_stem": stem, "profile_path": profile},
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
        pl = (path or "").lower()
        return pl.endswith(".tif") or pl.endswith(".tiff")

    def _tick(self) -> None:
        self._emit_status()

        # Never start a new subprocess while one is running.
        if self._runner.is_running():
            return

        # When paused: do not advance to next step/job/incoming item.
        if self._paused:
            return

        # If a job is active and no step is pending, advance.
        if self._current_job is not None:
            if self._job_step_idx >= len(self._current_job.steps):
                self._finish_job(ok=True)
                return
            self._start_next_job_step()
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
            self._current_stability = StabilityTracker(path=item.path, cfg=self._stability_cfg)

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
        self._jobs.put(job)

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
        self._current_job = None
        self._job_step_idx = 0
        self._pending_step_name = None
        self._step_results.clear()

    def _start_next_job_step(self) -> None:
        assert self._current_job is not None
        step = self._current_job.steps[self._job_step_idx]
        try:
            req = resolve_request_placeholders(step.request, results_by_step=self._step_results)
        except PlaceholderError as e:
            self.error.emit(f"Job placeholder resolution failed ({step.name}): {e}")
            self._finish_job(ok=False)
            return
        self._pending_step_name = step.name
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
        bs = out.get("best_summary_path")
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
        return out

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

        # Push raw result to UI for job-driven steps.
        self.latest_artifacts.emit(outcome.result)
        self._sync_last_paths_from_result(outcome.result)

        # Record step result for placeholder substitution.
        res = dict(outcome.result or {})
        if step_name == "fit_distances":
            res = self._enrich_fit_distances_result(res)
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

        # Advance to next step.
        self._job_step_idx += 1

    def _build_process_tiff_job(self, *, tiff_path: str) -> Job:
        """
        Build a Job for a stable incoming TIFF based on current session state.

        Uses deterministic output paths based on TIFF stem (watchdir conventions).
        """
        tp = (tiff_path or "").strip()
        stem = Path(tp).stem
        wd = self._state.watchdir

        st = self._state.current_state()
        steps: List[JobStep] = []

        # Always disable caching for live runs.
        if st == LiveviewState.A:
            outdir = wd / "averaged_proxy"
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
            return Job(id=f"tiff:{stem}:{time.time_ns()}", priority=0, steps=steps, context={"tiff_path": tp, "tiff_stem": stem})

        # Calibrated paths (B/BD/C/CD)
        if self._state.integrator_dir is None:
            raise RuntimeError("Missing integrator_dir (not calibrated)")
        outdir = wd / "averaged"
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

        integrated_dat = str((wd / "averaged" / f"int_{stem}.dat").resolve())

        if st in (LiveviewState.C, LiveviewState.CD):
            if self._state.buffer_dat_path is None or self._state.subtract_options is None:
                raise RuntimeError("State C requires buffer_dat_path and subtract_options")
            subdir = wd / "subtracted"
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
            profile = str((wd / "subtracted" / f"sub_{stem}.dat").resolve())
            steps.extend(self._analysis_steps_for_profile(profile))
            return Job(id=f"tiff:{stem}:{time.time_ns()}", priority=0, steps=steps, context={"tiff_path": tp, "tiff_stem": stem})

        # State B/BD
        profile = integrated_dat
        steps.extend(self._analysis_steps_for_profile(profile))
        return Job(id=f"tiff:{stem}:{time.time_ns()}", priority=0, steps=steps, context={"tiff_path": tp, "tiff_stem": stem})

    def _analysis_steps_for_profile(self, profile_abs: str) -> List[JobStep]:
        mode = self._state.analysis_mode
        if not mode.is_active():
            return []
        wd = self._state.watchdir
        prof = str(Path(profile_abs).expanduser().resolve())

        if mode == AnalysisMode.MONODISPERSE_PR:
            outdir = wd / "fit_distances"
            outdir.mkdir(parents=True, exist_ok=True)
            opts: dict = {}
            if self._state.fit_distances_conf_path is not None:
                opts.update(self._load_yaml_options(self._state.fit_distances_conf_path))
            opts.pop("output_dir", None)
            opts.pop("use_cache", None)
            opts["output_dir"] = str(outdir.resolve())
            opts["use_cache"] = False
            return [JobStep(name="fit_distances", request=RunRequest("fit_distances", [prof], opts))]

        if mode == AnalysisMode.MONODISPERSE_DAM:
            outdir = wd / "fit_distances"
            outdir.mkdir(parents=True, exist_ok=True)
            opts: dict = {}
            if self._state.fit_distances_conf_path is not None:
                opts.update(self._load_yaml_options(self._state.fit_distances_conf_path))
            opts.pop("output_dir", None)
            opts.pop("use_cache", None)
            opts["output_dir"] = str(outdir.resolve())
            opts["use_cache"] = False
            # DAMMIF needs gnom_path from fit_distances result.
            damdir = wd / "dammif"
            damdir.mkdir(parents=True, exist_ok=True)
            return [
                JobStep(name="fit_distances", request=RunRequest("fit_distances", [prof], opts)),
                JobStep(
                    name="fit_dammif",
                    request=RunRequest(
                        "fit_dammif",
                        [prof],
                        {
                            "output_dir": str(damdir.resolve()),
                            "use_cache": False,
                            "gnom_path": "${fit_distances.best_gnom_out_path}",
                        },
                    ),
                ),
            ]

        if mode == AnalysisMode.MONODISPERSE_BODIES:
            bodies_dir = wd / "fit_bodies"
            bodies_dir.mkdir(parents=True, exist_ok=True)
            shapes = self._state.fit_bodies_shapes
            if not shapes:
                shapes = list(DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES)
            return [
                JobStep(
                    name="fit_bodies",
                    request=RunRequest(
                        "fit_bodies",
                        [prof],
                        {
                            "output_dir": str(bodies_dir.resolve()),
                            "use_cache": False,
                            "shapes": list(shapes),
                        },
                    ),
                ),
            ]

        if mode == AnalysisMode.POLYDISPERSE_DR:
            outdir = wd / "fit_sizes"
            outdir.mkdir(parents=True, exist_ok=True)
            opts: dict = {}
            if self._state.fit_sizes_conf_path is not None:
                opts.update(self._load_yaml_options(self._state.fit_sizes_conf_path))
            opts.pop("output_dir", None)
            opts.pop("use_cache", None)
            opts["output_dir"] = str(outdir.resolve())
            opts["use_cache"] = False
            return [JobStep(name="fit_sizes", request=RunRequest("fit_sizes", [prof], opts))]

        if mode == AnalysisMode.POLYDISPERSE_MIXTURE:
            outdir = wd / "mixture"
            outdir.mkdir(parents=True, exist_ok=True)
            opts: dict = {"output_dir": str(outdir.resolve()), "use_cache": False}
            opts.update(self._fit_mixture_run_options())
            return [
                JobStep(
                    name="fit_mixture",
                    request=RunRequest("fit_mixture", [prof], opts),
                )
            ]

        return []

    def _fit_mixture_run_options(self) -> dict:
        """Skill options from wizard Apply; omit empty values and persistence-only keys."""
        raw = self._state.fit_mixture_options
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

