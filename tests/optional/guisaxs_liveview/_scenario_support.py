"""Shared bootstrap for optional liveview full-scenario attacks."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_FINDINGS_PATH = Path(__file__).resolve().parent / "_findings_accum.jsonl"
_PIPELINE_DIAG_PATH = Path(__file__).resolve().parent / "_pipeline_diag.jsonl"

# Last uncaught exception from a Qt slot / excepthook (attack harness only).
_LAST_SLOT_EXC: Optional[Tuple[str, str]] = None
_ORIG_EXCEPTHOOK = None


def load_liveview_lib():
    _lib_path = (
        Path(__file__).resolve().parents[2] / "must-run" / "guisaxs_liveview" / "_lib.py"
    )
    _spec = importlib.util.spec_from_file_location("liveview_test_lib_opt", _lib_path)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


def record_finding(
    *,
    launch: str,
    phase: str,
    observed: str,
    expected: str,
    likely_cause: str = "",
    candidate_fix: str = "",
    severity: str = "unknown",
) -> None:
    row = {
        "launch": launch,
        "phase": phase,
        "observed": observed,
        "expected": expected,
        "likely_cause": likely_cause,
        "candidate_fix": candidate_fix,
        "severity": severity,
    }
    with _FINDINGS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
    print(f"[FINDING:{severity}] {launch}/{phase}: {observed}", flush=True)


def install_slot_exception_guard() -> None:
    """Record Qt-slot exceptions instead of letting the interpreter abort when possible."""
    global _ORIG_EXCEPTHOOK, _LAST_SLOT_EXC
    if _ORIG_EXCEPTHOOK is not None:
        return
    _ORIG_EXCEPTHOOK = sys.excepthook
    _LAST_SLOT_EXC = None

    def _hook(etype, value, tb):  # noqa: ANN001
        global _LAST_SLOT_EXC
        name = getattr(etype, "__name__", str(etype))
        msg = f"{name}: {value}"
        _LAST_SLOT_EXC = (name, str(value))
        print(f"[ATTACK-GUARD] {msg}", flush=True)
        traceback.print_exception(etype, value, tb)
        # Do not re-raise / abort — attack must continue to later phases when possible.

    sys.excepthook = _hook


def consume_slot_exception() -> Optional[Tuple[str, str]]:
    global _LAST_SLOT_EXC
    cur = _LAST_SLOT_EXC
    _LAST_SLOT_EXC = None
    return cur


def peek_slot_exception() -> Optional[Tuple[str, str]]:
    return _LAST_SLOT_EXC


def soft_patch_show_tiff_for_attack(*, launch: str = "ingest") -> None:
    """
    Attack-only: swallow middle-panel imshow failures on corrupt TIFF so Qt does
    not Fatal-Abort the interpreter. Still records a crash finding — not a product fix.
    """
    from guisaxs_skills.liveview.ui.widgets.plots import Image2DPlot

    if getattr(Image2DPlot.show_tiff, "_attack_soft_patch", False):
        return
    _orig = Image2DPlot.show_tiff

    def _wrapped(self, path):  # noqa: ANN001
        try:
            return _orig(self, path)
        except Exception as exc:  # noqa: BLE001
            record_finding(
                launch=launch,
                phase="show_tiff soft-catch",
                observed=f"Image2DPlot.show_tiff raised {type(exc).__name__}: {exc} path={path!r}",
                expected="No crash; toast that TIFF is invalid; skip sample; continue",
                likely_cause="sync_middle paints unreadable TIFF → imshow Invalid shape",
                candidate_fix="Guard show_tiff for empty/non-2D arrays; reject before paint; toast",
                severity="crash",
            )
            try:
                self.clear()
            except Exception:
                pass
            return None

    _wrapped._attack_soft_patch = True  # type: ignore[attr-defined]
    Image2DPlot.show_tiff = _wrapped  # type: ignore[method-assign]


def clear_findings_accum() -> None:
    if _FINDINGS_PATH.is_file():
        _FINDINGS_PATH.unlink()


def read_findings() -> List[Dict[str, Any]]:
    if not _FINDINGS_PATH.is_file():
        return []
    rows = []
    for line in _FINDINGS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def clear_pipeline_diag() -> None:
    if _PIPELINE_DIAG_PATH.is_file():
        _PIPELINE_DIAG_PATH.unlink()


def _stem_of(path: str) -> str:
    try:
        return Path(path).stem
    except Exception:
        return path


def _path_mentions(path: str, interest: Sequence[str]) -> bool:
    if not interest:
        return True
    low = path.lower()
    name = Path(path).name.lower()
    stem = Path(path).stem.lower()
    for token in interest:
        t = token.lower()
        if t in low or t == name or t == stem or name.startswith(t) or stem.startswith(t):
            return True
    return False


class PipelineDiag:
    """Attack-only spies: TREE emit → settle → admit → promote (no product changes)."""

    def __init__(
        self,
        win: Any,
        *,
        interest: Sequence[str] = (),
        log_path: Optional[Path] = None,
        tick_every: int = 25,
    ) -> None:
        self.win = win
        self.interest = tuple(interest)
        self.log_path = log_path or _PIPELINE_DIAG_PATH
        self.tick_every = max(1, int(tick_every))
        self.t0 = time.monotonic()
        self.events: List[Dict[str, Any]] = []
        self._tick_n = 0
        self._installed = False

    def _ctrl(self) -> Any:
        return self.win._controller  # noqa: SLF001

    def _rel_t(self) -> float:
        return round(time.monotonic() - self.t0, 3)

    def log(self, kind: str, **payload: Any) -> None:
        row = {"t": self._rel_t(), "kind": kind, **payload}
        self.events.append(row)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            f.flush()
        # Compact stdout for live tail
        extra = {k: v for k, v in payload.items() if k in ("path", "stem", "reason", "ok", "auto", "intake", "n", "accepted")}
        print(f"[DIAG {row['t']:8.3f}] {kind} {extra}", flush=True)

    def mark(self, label: str, **payload: Any) -> None:
        self.log("mark", label=label, **payload)

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        clear_pipeline_diag()
        c = self._ctrl()
        ingest = c.ingest
        tree = ingest._tree_observer  # noqa: SLF001
        settler = ingest._settler  # noqa: SLF001
        ingress = ingest._ingress  # noqa: SLF001
        executor = c.executor
        session = c.session

        interest = self.interest

        def _care(path: str) -> bool:
            return _path_mentions(path, interest)

        # --- TREE ---
        _tree_start = tree.start
        _tree_stop = tree.stop
        _tree_restart = tree.restart_at
        _tree_emit = tree._emit_changed  # noqa: SLF001

        def _wrap_engine_baseline() -> None:
            eng = tree._engine  # noqa: SLF001
            if getattr(eng.baseline, "_diag_wrapped", False):
                return
            _orig_bl = eng.baseline

            def baseline_w() -> None:
                before = len(tree._cache.files)  # noqa: SLF001
                _orig_bl()
                after = len(tree._cache.files)  # noqa: SLF001
                swallowed = [Path(k).name for k in tree._cache.files if _care(k)]  # noqa: SLF001
                self.log(
                    "tree.baseline",
                    files_before=before,
                    files_after=after,
                    interest_in_cache=swallowed,
                )

            baseline_w._diag_wrapped = True  # type: ignore[attr-defined]
            eng.baseline = baseline_w  # type: ignore[method-assign]

        def start_w() -> None:
            self.log(
                "tree.start",
                watchdir=str(tree._watchdir),  # noqa: SLF001
                running=tree._running,  # noqa: SLF001
            )
            _wrap_engine_baseline()
            return _tree_start()

        def stop_w() -> None:
            self.log(
                "tree.stop",
                running=tree._running,  # noqa: SLF001
                n_files=len(tree._cache.files),  # noqa: SLF001
            )
            return _tree_stop()

        def restart_w(watchdir: Path) -> None:
            self.log("tree.restart_at", watchdir=str(watchdir))
            return _tree_restart(watchdir)

        def emit_w(paths: List[str]) -> None:
            cared = [p for p in paths if _care(p)]
            if cared or (paths and not interest):
                self.log(
                    "tree.emit",
                    n=len(paths),
                    paths=[Path(p).name for p in (cared or paths)[:12]],
                    stems=[_stem_of(p) for p in (cared or paths)[:12]],
                )
            return _tree_emit(paths)

        tree.start = start_w  # type: ignore[method-assign]
        tree.stop = stop_w  # type: ignore[method-assign]
        tree.restart_at = restart_w  # type: ignore[method-assign]
        tree._emit_changed = emit_w  # type: ignore[method-assign]
        _wrap_engine_baseline()

        # --- apply_watch_mode (intake flip restarts TREE) ---
        _apply = ingest.apply_watch_mode_watchers

        def apply_w() -> None:
            mode = getattr(c.state.intake_mode, "value", c.state.intake_mode)
            wm = getattr(c.state.watch_mode, "value", c.state.watch_mode)
            self.log("watchers.apply", intake=str(mode), watch_mode=str(wm), tree_running=bool(tree._running))
            return _apply()

        ingest.apply_watch_mode_watchers = apply_w  # type: ignore[method-assign]

        # --- settle ---
        _observe = settler.observe
        _on_stable = settler._on_stable  # noqa: SLF001

        def observe_w(revision: Any) -> None:
            if _care(revision.path):
                self.log(
                    "settle.observe",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    source=str(getattr(revision.source, "value", revision.source)),
                    in_flight=len(settler._entries),  # noqa: SLF001
                )
            return _observe(revision)

        def on_stable_w(revision: Any) -> None:
            if _care(revision.path) or not interest:
                self.log(
                    "settle.stable",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    source=str(getattr(revision.source, "value", revision.source)),
                )
            return _on_stable(revision)

        settler.observe = observe_w  # type: ignore[method-assign]
        settler._on_stable = on_stable_w  # type: ignore[method-assign]

        # --- ingress (observe only; never alter control flow) ---
        _accept = ingress.accept
        _try_admit = ingress._try_admit  # noqa: SLF001
        _enqueue = ingress._enqueue
        _is_owned = ingress._is_owned

        def try_admit_w(revision: Any) -> bool:
            ok = _try_admit(revision)
            if _care(revision.path):
                self.log(
                    "ingress.try_admit",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    ok=ok,
                )
            return ok

        def accept_w(revision: Any, *, boarding: Any = None, boarding_from: str = "intake") -> None:
            if _care(revision.path):
                owned = False
                try:
                    owned = bool(_is_owned(revision.path))
                except Exception as exc:  # noqa: BLE001
                    self.log("ingress.owned_err", path=revision.path, err=str(exc))
                self.log(
                    "ingress.accept_enter",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    boarding_from=boarding_from,
                    owned=owned,
                    source=str(getattr(revision.source, "value", revision.source)),
                )
            return _accept(revision, boarding=boarding, boarding_from=boarding_from)

        def enqueue_from_ingress_w(revision: Any) -> None:
            if _care(revision.path):
                self.log(
                    "ingress.enqueue",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    source=str(getattr(revision.source, "value", revision.source)),
                )
            return _enqueue(revision)

        ingress._try_admit = try_admit_w  # type: ignore[method-assign]
        ingress.accept = accept_w  # type: ignore[method-assign]
        ingress._enqueue = enqueue_from_ingress_w  # type: ignore[method-assign]

        # --- executor ---
        _enq_rev = executor.enqueue_revision
        _accept_in = executor._accept_incoming_revision  # noqa: SLF001
        _promote = executor._promote_incoming_to_jobs  # noqa: SLF001
        _tick = executor._tick  # noqa: SLF001
        _start_job = executor._start_job  # noqa: SLF001

        def enq_rev_w(revision: Any) -> None:
            if _care(revision.path):
                self.log(
                    "exec.enqueue_revision",
                    path=revision.path,
                    stem=_stem_of(revision.path),
                    auto=bool(c.state.is_auto_processing()),
                    incoming=len(executor._incoming),  # noqa: SLF001
                    jobs=len(executor._jobs),  # noqa: SLF001
                )
            return _enq_rev(revision)

        def accept_in_w(item: Any, *, source: Any = None) -> bool:
            from guisaxs_skills.liveview.ingest.sample_revision import SampleRevisionSource

            src = source if source is not None else SampleRevisionSource.INOTIFY
            accepted = _accept_in(item, source=src)
            if _care(item.path):
                key = item.path
                try:
                    from guisaxs_skills.liveview.ingest.sample_revision import normalize_sample_path

                    key = normalize_sample_path(item.path)
                except Exception:
                    pass
                prev = executor._last_accepted_stat.get(key)  # noqa: SLF001
                self.log(
                    "exec.accept_incoming",
                    path=item.path,
                    stem=_stem_of(item.path),
                    accepted=accepted,
                    had_prev_stat=prev is not None,
                    incoming_after=len(executor._incoming),  # noqa: SLF001
                )
            return accepted

        def promote_w() -> None:
            before = list(executor._incoming)  # noqa: SLF001
            before_paths = [it.path for it in before]
            cared_before = [p for p in before_paths if _care(p)]
            if cared_before or (before_paths and not interest):
                self.log(
                    "exec.promote_enter",
                    n=len(before_paths),
                    paths=[Path(p).name for p in (cared_before or before_paths)[:12]],
                    auto=bool(c.state.is_auto_processing()),
                )
            _promote()
            after = list(executor._incoming)  # noqa: SLF001
            after_paths = [it.path for it in after]
            if cared_before or (before_paths and not interest):
                self.log(
                    "exec.promote_exit",
                    remaining=len(after_paths),
                    remaining_stems=[_stem_of(p) for p in after_paths[:12]],
                    jobs=len(executor._jobs),  # noqa: SLF001
                )

        def tick_w() -> None:
            self._tick_n += 1
            if self._tick_n % self.tick_every == 0:
                auto = bool(c.state.is_auto_processing())
                cur = executor._current_job  # noqa: SLF001
                cur_path = ""
                if cur is not None:
                    cur_path = str(
                        cur.context.get("source_path") or cur.context.get("tiff_path") or ""
                    )
                self.log(
                    "exec.tick",
                    auto=auto,
                    runner=bool(executor._runner.is_running()),  # noqa: SLF001
                    handling=bool(executor._handling_skill_outcome),  # noqa: SLF001
                    incoming=len(executor._incoming),  # noqa: SLF001
                    jobs=len(executor._jobs),  # noqa: SLF001
                    current=_stem_of(cur_path) if cur_path else "",
                    suspended=bool(executor.queue_suspended),
                )
            return _tick()

        def start_job_w(job: Any) -> None:
            src = str(job.context.get("source_path") or job.context.get("tiff_path") or "")
            if _care(src) or not interest:
                steps = [getattr(s, "name", "?") for s in (job.steps or [])]
                self.log(
                    "exec.start_job",
                    path=src,
                    stem=_stem_of(src),
                    steps=steps,
                    manual=bool(job.context.get("manual")),
                    auto=bool(c.state.is_auto_processing()),
                )
            return _start_job(job)

        executor.enqueue_revision = enq_rev_w  # type: ignore[method-assign]
        executor._accept_incoming_revision = accept_in_w  # type: ignore[method-assign]
        executor._promote_incoming_to_jobs = promote_w  # type: ignore[method-assign]
        executor._tick = tick_w  # type: ignore[method-assign]
        executor._start_job = start_job_w  # type: ignore[method-assign]

        # Wrap plan errors inside promote by wrapping plan_for call site — already logged via error signal
        def _on_exec_error(msg: str) -> None:
            if any(t.lower() in msg.lower() for t in interest) or not interest:
                self.log("exec.error", msg=msg[:500])

        try:
            executor.error.connect(_on_exec_error)
        except Exception:
            pass

        # --- session stop/resume ---
        _stop = session.stop
        _resume = session.resume

        def stop_w() -> None:
            self.log(
                "session.stop",
                auto_before=bool(c.state.is_auto_processing()),
                processing_idle=bool(executor.is_processing_idle()),
                incoming=len(executor._incoming),
                jobs=len(executor._jobs),
            )
            return _stop()

        def resume_w() -> None:
            idle = bool(executor.is_processing_idle())
            auto_before = bool(c.state.is_auto_processing())
            self.log(
                "session.resume_enter",
                processing_idle=idle,
                auto_before=auto_before,
                incoming=len(executor._incoming),
                jobs=len(executor._jobs),
                will_noop=(executor is None or not idle),
            )
            out = _resume()
            self.log(
                "session.resume_exit",
                auto_after=bool(c.state.is_auto_processing()),
                processing_idle=bool(executor.is_processing_idle()),
                incoming=len(executor._incoming),
                jobs=len(executor._jobs),
            )
            return out

        session.stop = stop_w  # type: ignore[method-assign]
        session.resume = resume_w  # type: ignore[method-assign]

        # --- reject toast ---
        _reject = ingest._on_revision_rejected  # noqa: SLF001

        def reject_w(reason: str) -> None:
            self.log("ingress.reject", reason=reason)
            return _reject(reason)

        ingest._on_revision_rejected = reject_w  # type: ignore[method-assign]

        self.log("diag.installed", interest=list(interest))

    def probe_stem(self, watchdir: Path, stem: str, *, label: str = "") -> Dict[str, Any]:
        """Snapshot A/B/C evidence for one sample stem."""
        c = self._ctrl()
        ingest = c.ingest
        tree = ingest._tree_observer  # noqa: SLF001
        settler = ingest._settler  # noqa: SLF001
        executor = c.executor
        sample_dir = watchdir / stem
        tiff = sample_dir / f"{stem}.tif"
        if not tiff.is_file():
            tiff = sample_dir / f"{stem}.tiff"
        int_dat = sample_dir / "averaged" / f"int_{stem}.dat"
        sub_dat = sample_dir / "subtracted" / f"sub_{stem}.dat"

        tiff_key = ""
        in_tree_cache = False
        tree_snap = None
        if tiff.is_file():
            try:
                tiff_key = str(tiff.resolve())
                tree_snap = tree._cache.files.get(tiff_key)  # noqa: SLF001
                in_tree_cache = tree_snap is not None
            except OSError:
                pass

        settle_keys = list(settler._entries.keys())  # noqa: SLF001
        in_settle = any(stem in k for k in settle_keys)

        incoming_paths = [it.path for it in list(executor._incoming)]  # noqa: SLF001
        in_incoming = any(stem in p for p in incoming_paths)

        job_paths = []
        for _pri, _seq, job in list(getattr(executor._jobs, "_heap", [])):  # noqa: SLF001
            src = str(job.context.get("source_path") or job.context.get("tiff_path") or "")
            job_paths.append(src)
        in_jobs = any(stem in p for p in job_paths)

        last_keys = [k for k in executor._last_accepted_stat if stem in k]  # noqa: SLF001
        boarding = None
        if tiff_key:
            try:
                boarding = c.samples.boarding_for(tiff_key)
            except Exception:
                boarding = None

        cur = executor._current_job  # noqa: SLF001
        cur_src = ""
        if cur is not None:
            cur_src = str(cur.context.get("source_path") or cur.context.get("tiff_path") or "")

        # Classify A/B/C-ish
        cls = "unknown"
        if int_dat.is_file():
            cls = "DONE"
        elif not tiff.is_file():
            cls = "NO_TIFF"
        elif not in_tree_cache and not in_settle and not in_incoming and not in_jobs and stem not in cur_src:
            # File on disk but TREE never recorded it post-baseline → likely A (never emit) OR baseline swallow
            cls = "A_or_baseline_swallow"
        elif in_settle and not in_incoming:
            cls = "A_settle_stuck"
        elif in_incoming:
            cls = "C_admitted_not_promoted" if not c.state.is_auto_processing() else "C_admitted_awaiting_promote"
        elif in_jobs or stem in cur_src:
            cls = "C_promoted_running_or_queued"
        elif last_keys and not int_dat.is_file():
            cls = "B_or_C_acked_no_output"
        elif in_tree_cache and not last_keys:
            cls = "A_cached_no_emit_or_pending_scan"

        snap = {
            "label": label,
            "stem": stem,
            "class": cls,
            "tiff_exists": tiff.is_file(),
            "tiff_path": str(tiff) if tiff.is_file() else "",
            "int_exists": int_dat.is_file(),
            "sub_exists": sub_dat.is_file(),
            "tree_running": bool(tree._running),  # noqa: SLF001
            "tree_baselined": bool(tree._engine.baselined),  # noqa: SLF001
            "in_tree_cache": in_tree_cache,
            "in_settle": in_settle,
            "settle_n": len(settle_keys),
            "in_incoming": in_incoming,
            "incoming_n": len(incoming_paths),
            "incoming_stems": [_stem_of(p) for p in incoming_paths[:20]],
            "in_jobs": in_jobs,
            "jobs_n": len(job_paths),
            "job_stems": [_stem_of(p) for p in job_paths[:20]],
            "last_accepted_keys": [Path(k).name for k in last_keys],
            "boarding": str(getattr(boarding, "value", boarding)),
            "auto": bool(c.state.is_auto_processing()),
            "runner": bool(executor._runner.is_running()),  # noqa: SLF001
            "current": _stem_of(cur_src) if cur_src else "",
            "intake": str(getattr(c.state.intake_mode, "value", c.state.intake_mode)),
        }
        self.log("probe", **snap)
        return snap

    def events_for_stem(self, stem: str) -> List[Dict[str, Any]]:
        out = []
        s = stem.lower()
        for ev in self.events:
            blob = json.dumps(ev, default=str).lower()
            if s in blob:
                out.append(ev)
        return out

    def summarize_stem(self, stem: str) -> Dict[str, Any]:
        evs = self.events_for_stem(stem)
        kinds = [e.get("kind") for e in evs]
        return {
            "stem": stem,
            "n_events": len(evs),
            "kinds": kinds,
            "saw_tree_emit": "tree.emit" in kinds,
            "saw_settle_observe": "settle.observe" in kinds,
            "saw_settle_stable": "settle.stable" in kinds,
            "saw_ingress_accept": any(k and k.startswith("ingress.") for k in kinds),
            "saw_exec_enqueue": "exec.enqueue_revision" in kinds,
            "saw_exec_accept": "exec.accept_incoming" in kinds,
            "saw_promote": any(k and k.startswith("exec.promote") for k in kinds),
            "saw_start_job": "exec.start_job" in kinds,
            "saw_reject": "ingress.reject" in kinds,
        }
