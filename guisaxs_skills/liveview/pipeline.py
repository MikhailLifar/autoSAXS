from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

import yaml
from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from ..core.models import RunRequest
from ..core.paths import runs_dir
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from .queue import FIFOQueue, QueueItem
from .stability import StabilityConfig, StabilityTracker
from .state import (
    AnalysisMode,
    DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES,
    LiveviewSessionState,
    LiveviewState,
)


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
    # Emitted once when a TIFF finishes the per-file pipeline successfully (no skill re-run on browse).
    session_file_completed = pyqtSignal(str)

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

        self._pending_step: Optional[str] = None
        self._latest_integrated_dat: Optional[str] = None
        self._latest_subtracted_dat: Optional[str] = None
        # After fit_distances succeeds, run fit_dammif (DAM mode) or fit_bodies (primitives mode).
        self._chain_fit_dammif_after: bool = False
        self._chain_fit_bodies_after: bool = False
        self._analysis_profile_abs: Optional[str] = None
        self._session_tiff_history: List[str] = []

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
        self._chain_fit_dammif_after = False
        self._chain_fit_bodies_after = False
        self._analysis_profile_abs = None

    @property
    def session_processed_tiffs(self) -> tuple[str, ...]:
        return tuple(self._session_tiff_history)

    @staticmethod
    def _is_tiff_path(path: str) -> bool:
        pl = path.lower()
        return pl.endswith(".tif") or pl.endswith(".tiff")

    def _append_session_tiff(self, path: str) -> None:
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = path.strip()
        if not key or not self._is_tiff_path(key):
            return
        try:
            if key in self._session_tiff_history:
                self._session_tiff_history.remove(key)
            self._session_tiff_history.append(key)
        except Exception:
            return

    def reset(self) -> None:
        self._queue.clear()
        self._current_item = None
        self._current_stability = None
        self._pending_step = None
        self._latest_integrated_dat = None
        self._latest_subtracted_dat = None
        self._last_processed = ""
        self._durations.clear()
        self._chain_fit_dammif_after = False
        self._chain_fit_bodies_after = False
        self._analysis_profile_abs = None
        self._session_tiff_history.clear()

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
            self._chain_fit_dammif_after = False
            self._chain_fit_bodies_after = False
            self._analysis_profile_abs = None

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
        self._runner.start(
            RunRequest(
                skill_name="integrate",
                positional=[self._current_item.path, str(self._state.integrator_dir)],
                options={"output_dir": str(outdir), "use_cache": False},
            )
        )

    def _start_subtract(self) -> None:
        assert self._latest_integrated_dat is not None
        if self._state.buffer_dat_path is None or self._state.subtract_options is None:
            self.error.emit("Cannot subtract: buffer or subtract config is not set.")
            self._finish_current(False)
            return
        outdir = self._state.watchdir / "subtracted"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "subtract"
        opts = {"output_dir": str(outdir), "use_cache": False}
        opts.update(dict(self._state.subtract_options or {}))
        self._runner.start(
            RunRequest(
                skill_name="subtract",
                positional=[self._latest_integrated_dat, str(self._state.buffer_dat_path)],
                options=opts,
            )
        )

    def _resolve_profile_abs(self, profile_path: str) -> str:
        pp = Path(profile_path).expanduser()
        return str(pp.resolve() if pp.is_absolute() else (self._state.watchdir / pp).resolve())

    def _start_analysis_for_profile(self, profile_path: str) -> None:
        mode = self._state.analysis_mode
        if not mode.is_active():
            self._finish_current(True)
            return
        profile_abs = self._resolve_profile_abs(profile_path)
        self._analysis_profile_abs = profile_abs
        self._chain_fit_dammif_after = False
        if mode == AnalysisMode.MONODISPERSE_PR:
            self._start_fit_distances(profile_abs=profile_abs)
            return
        if mode == AnalysisMode.MONODISPERSE_DAM:
            self._chain_fit_dammif_after = True
            self._start_fit_distances(profile_abs=profile_abs)
            return
        if mode == AnalysisMode.MONODISPERSE_BODIES:
            self._start_fit_bodies(profile_abs=profile_abs)
            return
        if mode == AnalysisMode.POLYDISPERSE_DR:
            self._start_fit_sizes(profile_abs=profile_abs)
            return
        if mode == AnalysisMode.POLYDISPERSE_MIXTURE:
            self._start_fit_mixture(profile_abs=profile_abs)
            return
        self._finish_current(True)

    def _start_fit_distances(self, *, profile_abs: str) -> None:
        outdir = self._state.watchdir / "fit_distances"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "fit_distances"
        opts: dict = {}
        if self._state.fit_distances_conf_path is not None:
            opts.update(self._load_yaml_options(self._state.fit_distances_conf_path))
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts["output_dir"] = str(outdir.resolve())
        opts["use_cache"] = False
        self._runner.start(
            RunRequest(
                skill_name="fit_distances",
                positional=[profile_abs],
                options=opts,
            )
        )

    def _start_fit_dammif(self, *, profile_abs: str, gnom_path: str) -> None:
        outdir = self._state.watchdir / "dammif"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "fit_dammif"
        self._runner.start(
            RunRequest(
                skill_name="fit_dammif",
                positional=[profile_abs],
                options={
                    "output_dir": str(outdir.resolve()),
                    "use_cache": False,
                    "gnom_path": gnom_path,
                },
            )
        )

    @staticmethod
    def _resolve_under_watchdir(watchdir: Path, raw: Any) -> Optional[Path]:
        """Turn a skill-returned path (absolute or watchdir-relative) into a resolved file path."""
        if raw is None:
            return None
        s = str(raw).strip()
        if not s or s.lower() == "none":
            return None
        p = Path(s).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path if path.is_file() else None

    @staticmethod
    def _coerce_opt_int(val: Any) -> Optional[int]:
        if val is None:
            return None
        if isinstance(val, bool):
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
            LiveviewPipeline._coerce_opt_int(sel.get("first")),
            LiveviewPipeline._coerce_opt_int(sel.get("last")),
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
            LiveviewPipeline._coerce_opt_int(data.get("first")),
            LiveviewPipeline._coerce_opt_int(data.get("last")),
        )

    @classmethod
    def _parse_first_last_from_fit_distances_result(
        cls, *, watchdir: Path, result: dict
    ) -> tuple[Optional[int], Optional[int]]:
        """
        Read DATGNOM ``--first`` / ``--last`` for chaining into ``fit_bodies``.

        Prefer ``best_summary_path`` (``selected.first`` / ``selected.last``). If that yields no
        integers (missing key, parse failure, or path not in ``result``), fall back to
        ``fit_params_path`` (flat ``first`` / ``last``) — same run, written by ``fit_distances``.
        """
        first_i: Optional[int] = None
        last_i: Optional[int] = None

        bs = cls._resolve_under_watchdir(watchdir, result.get("best_summary_path"))
        if bs is not None:
            first_i, last_i = cls._read_first_last_from_best_summary(bs)

        if first_i is None and last_i is None:
            fp = cls._resolve_under_watchdir(watchdir, result.get("fit_params_path"))
            if fp is not None:
                first_i, last_i = cls._read_first_last_from_fit_params(fp)
        elif first_i is None or last_i is None:
            fp = cls._resolve_under_watchdir(watchdir, result.get("fit_params_path"))
            if fp is not None:
                f2, l2 = cls._read_first_last_from_fit_params(fp)
                if first_i is None:
                    first_i = f2
                if last_i is None:
                    last_i = l2

        return first_i, last_i

    def _start_fit_bodies(
        self,
        *,
        profile_abs: str,
        first: Optional[int] = None,
        last: Optional[int] = None,
    ) -> None:
        outdir = self._state.watchdir / "fit_bodies"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "fit_bodies"
        opts: dict = {"output_dir": str(outdir.resolve()), "use_cache": False}
        shapes = self._state.fit_bodies_shapes
        if shapes is None or len(shapes) == 0:
            shapes = list(DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES)
        opts["shapes"] = list(shapes)
        if first is not None:
            opts["first"] = int(first)
        if last is not None:
            opts["last"] = int(last)
        self._runner.start(
            RunRequest(
                skill_name="fit_bodies",
                positional=[profile_abs],
                options=opts,
            )
        )

    def _start_fit_sizes(self, *, profile_abs: str) -> None:
        outdir = self._state.watchdir / "fit_sizes"
        outdir.mkdir(parents=True, exist_ok=True)
        self._pending_step = "fit_sizes"
        opts: dict = {}
        if self._state.fit_sizes_conf_path is not None:
            opts.update(self._load_yaml_options(self._state.fit_sizes_conf_path))
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts["output_dir"] = str(outdir.resolve())
        opts["use_cache"] = False
        self._runner.start(
            RunRequest(
                skill_name="fit_sizes",
                positional=[profile_abs],
                options=opts,
            )
        )

    def _start_fit_mixture(self, *, profile_abs: str) -> None:
        outdir = self._state.watchdir / "mixture"
        outdir.mkdir(parents=True, exist_ok=True)
        opts: dict = {"output_dir": str(outdir.resolve()), "use_cache": False}
        raw = self._state.fit_mixture_options
        if isinstance(raw, dict):
            skip = frozenset({"output_dir", "use_cache", "config_path"})
            for key, value in raw.items():
                if key in skip or value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                opts[str(key)] = value
        self._pending_step = "fit_mixture"
        self._runner.start(
            RunRequest(
                skill_name="fit_mixture",
                positional=[profile_abs],
                options=opts,
            )
        )

    def _on_skill_finished(self, outcome: RunOutcome) -> None:
        self.latest_artifacts.emit(outcome.result)

        step = self._pending_step
        self._pending_step = None

        if not outcome.success:
            if step is not None:
                if self._current_item is not None:
                    self.error.emit(f"Skill failed ({step}), skipping: {self._current_item.path}")
                self._finish_current(False)
            return

        st = self._state.current_state()

        if step == "integrate_proxy":
            self._finish_current(True)
            return

        if step == "integrate":
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
                self._start_analysis_for_profile(self._latest_integrated_dat)
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

            st_after = self._state.current_state()
            if st_after == LiveviewState.CD and self._latest_subtracted_dat:
                self._start_analysis_for_profile(self._latest_subtracted_dat)
                return

            self._finish_current(True)
            return

        if step == "fit_distances":
            chain_dam = self._chain_fit_dammif_after
            chain_bodies = self._chain_fit_bodies_after
            self._chain_fit_dammif_after = False
            self._chain_fit_bodies_after = False
            prof = self._analysis_profile_abs

            if not is_atsas_fit_ok(outcome.result):
                self.error.emit(
                    failure_message_from_result(outcome.result, skill_id="fit_distances")
                )
                self._analysis_profile_abs = None
                self._finish_current(True)
                return

            if chain_dam:
                gnom = outcome.result.get("best_gnom_out_path")
                gpath = ""
                if isinstance(gnom, str) and gnom.strip():
                    gp = Path(gnom.strip()).expanduser()
                    gpath = str(gp.resolve() if gp.is_absolute() else (self._state.watchdir / gp).resolve())
                if not prof or not gpath or not Path(gpath).is_file():
                    self.error.emit("DAM: missing or invalid best_gnom_out_path after fit_distances; skipping file.")
                    self._analysis_profile_abs = None
                    self._finish_current(False)
                    return
                self._start_fit_dammif(profile_abs=prof, gnom_path=gpath)
                return

            if chain_bodies:
                if not prof:
                    self.error.emit("Primitives: internal error (no profile path after fit_distances); skipping file.")
                    self._analysis_profile_abs = None
                    self._finish_current(False)
                    return
                fi, la = self._parse_first_last_from_fit_distances_result(
                    watchdir=self._state.watchdir, result=outcome.result
                )
                self._start_fit_bodies(profile_abs=prof, first=fi, last=la)
                return

            self._analysis_profile_abs = None
            self._finish_current(True)
            return

        if step == "fit_dammif":
            self._analysis_profile_abs = None
            self._finish_current(True)
            return

        if step in ("fit_bodies", "fit_mixture"):
            self._finish_current(True)
            return

        if step == "fit_sizes":
            if not is_atsas_fit_ok(outcome.result):
                self.error.emit(failure_message_from_result(outcome.result, skill_id="fit_sizes"))
            self._finish_current(True)
            return

    def _finish_current(self, ok: bool) -> None:
        if self._current_item is None:
            return
        completed_path = self._current_item.path
        dt = max(0.0, time.monotonic() - self._current_started_at)
        if ok:
            self._durations.append(dt)
            if len(self._durations) > 50:
                self._durations = self._durations[-50:]
            self._append_session_tiff(completed_path)
            self.session_file_completed.emit(completed_path)
        self._last_processed = completed_path
        self._current_item = None
        self._current_stability = None
        self._pending_step = None
        self._latest_integrated_dat = None
        self._latest_subtracted_dat = None
        self._chain_fit_dammif_after = False
        self._chain_fit_bodies_after = False
        self._analysis_profile_abs = None

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
