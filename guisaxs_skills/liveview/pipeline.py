from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ..core.models import RunRequest
from ..core.paths import latest_request_path, latest_result_path, latest_stderr_path, latest_stdout_path, runs_dir
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from .queue import FIFOQueue, QueueItem
from .stability import StabilityConfig, StabilityTracker
from .state import LiveviewSessionState, LiveviewState


@dataclass(frozen=True)
class LiveviewQueueStatus:
    queue_size: int
    current_path: str
    last_processed_path: str
    avg_seconds_per_item: float
    # Items not yet fully processed: FIFO backlog plus the one currently in the pipeline (if any).
    remaining: int


class LiveviewPipeline(QObject):
    queue_status = pyqtSignal(object)  # LiveviewQueueStatus
    latest_artifacts = pyqtSignal(object)  # dict (skill result)
    error = pyqtSignal(str)

    def __init__(self, *, state: LiveviewSessionState, runner: SkillRunner, queue: FIFOQueue) -> None:
        super().__init__()
        self._state = state
        self._runner = runner
        self._queue = queue
        runs_dir(self._state.watchdir).mkdir(parents=True, exist_ok=True)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(100)
        self._tick_timer.timeout.connect(self._tick)

        self._stability_cfg = StabilityConfig()
        self._current_item: Optional[QueueItem] = None
        self._current_stability: Optional[StabilityTracker] = None
        self._current_started_at: float = 0.0
        self._last_processed: str = ""
        self._durations: list[float] = []

        self._pending_step: Optional[str] = None  # integrate_proxy|integrate|subtract|fit_distances
        self._pending_run_dir: Optional[Path] = None
        self._latest_integrated_dat: Optional[str] = None
        self._latest_subtracted_dat: Optional[str] = None

        self._runner.finished.connect(self._on_skill_finished)

    def start(self) -> None:
        if self._tick_timer.isActive():
            return
        self._tick_timer.start()

    def stop(self) -> None:
        self._tick_timer.stop()
        self._current_item = None
        self._current_stability = None
        self._pending_step = None

    def reset(self) -> None:
        self._queue.clear()
        self._current_item = None
        self._current_stability = None
        self._pending_step = None
        self._latest_integrated_dat = None
        self._latest_subtracted_dat = None
        self._last_processed = ""
        self._durations.clear()

    def enqueue(self, *, path: str, detected_at_monotonic: float) -> None:
        cur = self._current_item.path if self._current_item else None
        self._queue.put_if_absent(
            QueueItem(path=path, detected_at_monotonic=float(detected_at_monotonic)),
            current_path=cur,
        )

    def _emit_status(self) -> None:
        avg = (sum(self._durations) / len(self._durations)) if self._durations else 0.0
        qn = len(self._queue)
        cur = 1 if self._current_item is not None else 0
        self.queue_status.emit(
            LiveviewQueueStatus(
                queue_size=qn,
                current_path=self._current_item.path if self._current_item else "",
                last_processed_path=self._last_processed,
                avg_seconds_per_item=avg,
                remaining=qn + cur,
            )
        )

    def _tick(self) -> None:
        self._emit_status()

        if self._runner.is_running():
            return

        if self._current_item is None:
            item = self._queue.get_nowait()
            if item is None:
                return
            self._current_item = item
            self._current_stability = StabilityTracker(path=item.path, cfg=self._stability_cfg)
            self._current_started_at = time.monotonic()
            self._pending_step = None
            self._latest_integrated_dat = None
            self._latest_subtracted_dat = None

        # Wait until current file is stable.
        if self._current_stability is not None:
            stable = self._current_stability.tick()
            if stable is None:
                self.error.emit(f"File did not become stable (timeout), skipping: {self._current_item.path}")
                self._finish_current(False)
                return
            if stable is False:
                return
            self._current_stability = None

        # Start (or continue) per-file pipeline by running the next skill step.
        if self._pending_step is None:
            st = self._state.current_state()
            if st == LiveviewState.A:
                self._start_integrate_proxy()
                return
            if st in (LiveviewState.B, LiveviewState.BD, LiveviewState.C, LiveviewState.CD):
                self._start_integrate()
                return

        # If no pending step and runner idle, it means this item finished, but we didn't clean up.
        if self._pending_step is None:
            self._finish_current(True)

    def _start_integrate_proxy(self) -> None:
        assert self._current_item is not None
        outdir = self._state.watchdir / "averaged_proxy"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "integrate_proxy"
        self._pending_run_dir = self._new_run_dir(skill_name="integrate_proxy")
        self._runner.start(
            RunRequest(
                skill_name="integrate_proxy",
                positional=[self._current_item.path],
                options={"output_dir": str(outdir), "use_cache": False},
            )
        )

    def _start_integrate(self) -> None:
        assert self._current_item is not None
        if self._state.integrator_dir is None:
            self.error.emit("Cannot integrate: missing integrator_dir (not calibrated).")
            self._finish_current(False)
            return
        outdir = self._state.watchdir / "averaged"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "integrate"
        self._pending_run_dir = self._new_run_dir(skill_name="integrate")
        self._runner.start(
            RunRequest(
                skill_name="integrate",
                positional=[self._current_item.path, str(self._state.integrator_dir)],
                options={"output_dir": str(outdir), "use_cache": False},
            )
        )

    def _start_subtract(self) -> None:
        assert self._latest_integrated_dat is not None
        if self._state.buffer_dat_path is None or self._state.subtract_conf_path is None:
            self.error.emit("Cannot subtract: buffer or subtract config is not set.")
            self._finish_current(False)
            return
        outdir = self._state.watchdir / "subtracted"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "subtract"
        self._pending_run_dir = self._new_run_dir(skill_name="subtract")
        opts = {"output_dir": str(outdir), "use_cache": False}
        opts.update(self._load_yaml_options(self._state.subtract_conf_path))
        self._runner.start(
            RunRequest(
                skill_name="subtract",
                positional=[self._latest_integrated_dat, str(self._state.buffer_dat_path)],
                options=opts,
            )
        )

    def _start_fit_distances(self, *, profile_path: str) -> None:
        outdir = self._state.watchdir / "fit_distances"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "fit_distances"
        self._pending_run_dir = self._new_run_dir(skill_name="fit_distances")
        opts: dict = {}
        if self._state.fit_distances_conf_path is not None:
            opts.update(self._load_yaml_options(self._state.fit_distances_conf_path))
        # Saved YAML must not override liveview output location or caching policy.
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts["output_dir"] = str(outdir.resolve())
        opts["use_cache"] = False
        pp = Path(profile_path).expanduser()
        profile_abs = str(pp.resolve() if pp.is_absolute() else (self._state.watchdir / pp).resolve())
        self._runner.start(
            RunRequest(
                skill_name="fit_distances",
                positional=[profile_abs],
                options=opts,
            )
        )

    def _on_skill_finished(self, outcome: RunOutcome) -> None:
        self._snapshot_latest_run()
        # Always emit artifacts for UI to update (even on failure).
        self.latest_artifacts.emit(outcome.result)

        step = self._pending_step
        self._pending_step = None
        self._pending_run_dir = None

        if not outcome.success:
            # Manual runs (calibrate / fit_distances wizard) do not set _pending_step; never drop a queued file.
            if step is not None:
                if self._current_item is not None:
                    self.error.emit(f"Skill failed ({step}), skipping: {self._current_item.path}")
                self._finish_current(False)
            return

        st = self._state.current_state()

        if step == "integrate_proxy":
            # Done for State A.
            self._finish_current(True)
            return

        if step == "integrate":
            # Capture the newest integrated curve path.
            integrated = outcome.result.get("integrated_1d")
            if isinstance(integrated, list) and integrated and isinstance(integrated[-1], str):
                self._latest_integrated_dat = integrated[-1]
            elif isinstance(integrated, str):
                self._latest_integrated_dat = integrated
            if self._latest_integrated_dat:
                try:
                    ip = Path(self._latest_integrated_dat)
                    if ip.is_file():
                        self._state.last_integrated_dat_path = ip
                except Exception:
                    pass

            if st in (LiveviewState.C, LiveviewState.CD):
                if self._latest_integrated_dat is None:
                    self.error.emit("Integrate succeeded but integrated_1d was not found in result.")
                    self._finish_current(False)
                    return
                self._start_subtract()
                return

            if st == LiveviewState.BD and self._latest_integrated_dat:
                self._start_fit_distances(profile_path=self._latest_integrated_dat)
                return

            self._finish_current(True)
            return

        if step == "subtract":
            sub = outcome.result.get("subtracted_1d")
            if isinstance(sub, str):
                self._latest_subtracted_dat = sub
            elif isinstance(sub, list) and sub and isinstance(sub[-1], str):
                self._latest_subtracted_dat = sub[-1]
            if self._latest_subtracted_dat:
                try:
                    sp = Path(self._latest_subtracted_dat)
                    if sp.is_file():
                        self._state.last_subtracted_dat_path = sp
                except Exception:
                    pass

            if st == LiveviewState.CD and self._latest_subtracted_dat:
                self._start_fit_distances(profile_path=self._latest_subtracted_dat)
                return

            self._finish_current(True)
            return

        if step == "fit_distances":
            self._finish_current(True)
            return

    def _finish_current(self, ok: bool) -> None:
        if self._current_item is None:
            return
        dt = max(0.0, time.monotonic() - self._current_started_at)
        if ok:
            self._durations.append(dt)
            if len(self._durations) > 50:
                self._durations = self._durations[-50:]
        self._last_processed = self._current_item.path
        self._current_item = None
        self._current_stability = None
        self._pending_step = None
        self._latest_integrated_dat = None
        self._latest_subtracted_dat = None

    def _new_run_dir(self, *, skill_name: str) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        base = f"{ts}_{skill_name}"
        d = runs_dir(self._state.watchdir) / base
        # De-dup in case multiple starts within same second.
        if d.exists():
            for i in range(1, 1000):
                cand = runs_dir(self._state.watchdir) / f"{base}_{i:03d}"
                if not cand.exists():
                    d = cand
                    break
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _snapshot_latest_run(self) -> None:
        """
        Copy `runs/latest/*` into the per-run directory for traceability.
        """
        if self._pending_run_dir is None:
            return
        try:
            dst = self._pending_run_dir
            # Best-effort copies; ignore failures.
            for src in (
                latest_request_path(self._state.watchdir),
                latest_stdout_path(self._state.watchdir),
                latest_stderr_path(self._state.watchdir),
                latest_result_path(self._state.watchdir),
            ):
                try:
                    if src.exists():
                        (dst / src.name).write_bytes(src.read_bytes())
                except Exception:
                    pass
        except Exception:
            return

    def _load_yaml_options(self, path: Optional[Path]) -> dict:
        if path is None:
            return {}
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
        except Exception:
            pass
        return {}

