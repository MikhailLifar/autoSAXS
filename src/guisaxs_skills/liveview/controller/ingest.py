from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..ingest.curve_classify import try_classify_curve_boarding
from ..ingest.dir_tree_observer import TreeDirObserver, TreeObserverConfig
from ..ingest.ingress import RevisionIngress
from ..ingest.poll_watcher import ProcessedTiffPoller, PollWatcherConfig
from ..ingest.settle import RevisionSettler
from ..ingest.sample_revision import (
    SampleRevision,
    SampleRevisionSource,
    is_dat_path,
    is_sample_dat_path,
    is_tiff_path,
    make_revision,
)
from ..ingest.watcher import DirectoryWatcher, WatcherConfig
from ..services.calibration.masks import applied_mask_path
from ..session.state import LiveviewIntakeMode, LiveviewWatchMode

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewIngestHandler:
    """Watchers, watch mode, sample ingest (TIFF / curve), dropped files."""

    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller
        wd = controller.watchdir
        self._ingress = RevisionIngress(
            is_owned_output=controller.executor.is_owned_output,
            remember_boarding=controller.samples.remember_boarding,
            enqueue_revision=self._enqueue_to_executor,
            infer_boarding=self._infer_boarding,
            current_intake=lambda: controller.state.intake_mode,
            frame_2d_boarding=LiveviewIntakeMode.FRAME_2D,
            acknowledge_stat=self._acknowledge_sample_stat,
            admit_revision=self._admit_revision_reason,
            on_reject=self._on_revision_rejected,
        )
        self._settler = RevisionSettler(
            on_stable=self._on_settled,
            on_timeout=lambda path: controller.error.emit(
                f"File did not become stable (timeout), skipping: {path}"
            ),
            parent=controller,
        )
        self._watcher = DirectoryWatcher(
            directory=wd,
            cfg=WatcherConfig(recursive=False),
            on_revision=self._observe_detected,
        )
        self._poll_watcher = ProcessedTiffPoller(
            cfg=PollWatcherConfig(),
            on_revision=self._observe_detected,
        )
        self._tree_observer = TreeDirObserver(
            cfg=TreeObserverConfig(),
            watchdir=wd,
            on_revision=self._observe_detected,
        )
        controller.executor.session_file_completed.connect(self._poll_watcher.track_processed_path)
        self._settler.start()
        self.apply_watch_mode_watchers()

    def stop_all(self) -> None:
        try:
            self._settler.stop()
        except Exception:
            pass
        for stop in (self._watcher.stop, self._poll_watcher.stop, self._tree_observer.stop):
            try:
                stop()
            except Exception:
                pass
        if hasattr(self, "_dat_root_watcher"):
            try:
                self._dat_root_watcher.stop()
            except Exception:
                pass

    def on_intake_changed(self, _mode: LiveviewIntakeMode) -> None:
        self.apply_watch_mode_watchers()
        left = self._c.left
        right = self._c.right
        if left is not None and hasattr(left, "apply_intake_visibility"):
            left.apply_intake_visibility(_mode)
        # One owner: layout + content for current sample (fixes 2d↔1d↔sub residue).
        self._c.history.sync_middle(force=False)
        if right is not None and hasattr(right, "sync_intake_toggles"):
            right.sync_intake_toggles(_mode)
        if left is not None:
            left.refresh_attention_coach()

    def set_watch_mode(self, new_mode: LiveviewWatchMode) -> None:
        if new_mode == self._c.state.watch_mode:
            return
        if not self._c.require_idle(
            "Watch mode",
            "A skill is still running. Wait for it to finish, then switch watch mode.",
        ):
            return
        self._c.state.watch_mode = new_mode
        self._c.persist_session_settings()
        self.apply_watch_mode_watchers()
        self._c.history.refresh_chrome()
        if self._c.samples:
            self._c.history.reload_view()

    def apply_watch_mode_watchers(self) -> None:
        wd = self._c.watchdir
        intake = self._c.state.intake_mode

        def _dat_under_watchdir_ok(path: str, *, allow_dirs: tuple[str, ...]) -> bool:
            if not is_sample_dat_path(path):
                return False
            try:
                rel = Path(path).resolve().relative_to(wd.resolve())
            except (ValueError, OSError):
                return False
            parts = rel.parts
            if not parts:
                return False
            low_parts = tuple(p.lower() for p in parts)
            if "averaged_proxy" in low_parts:
                return False
            if len(parts) == 1:
                return True  # watchdir root
            if len(parts) == 2 and low_parts[0] in allow_dirs:
                return True
            return False

        # Curve intake: .dat under root + averaged/ or subtracted/, plus TIFF detection
        # for Option A switch back to 2D (mirror aux .dat while intake is 2D).
        if intake == LiveviewIntakeMode.CURVE_1D:
            self._apply_curve_intake_watchers(
                wd,
                allow_dirs=("averaged",),
                mkdir_name="averaged",
                _dat_under_watchdir_ok=_dat_under_watchdir_ok,
            )
            return
        if intake == LiveviewIntakeMode.CURVE_SUB:
            self._apply_curve_intake_watchers(
                wd,
                allow_dirs=("subtracted",),
                mkdir_name="subtracted",
                _dat_under_watchdir_ok=_dat_under_watchdir_ok,
            )
            return

        # FRAME_2D: TIFF watch as today, plus root *.dat (auto-switch like a drop).
        if self._c.state.watch_mode == LiveviewWatchMode.TREE:
            try:
                self._watcher.stop()
            except Exception:
                pass
            try:
                self._poll_watcher.stop()
            except Exception:
                pass
            if hasattr(self, "_tiff_option_a_watcher"):
                try:
                    self._tiff_option_a_watcher.stop()
                except Exception:
                    pass
            self._tree_observer.restart_at(wd)
            self._ensure_dat_root_watcher_2d(wd, _dat_under_watchdir_ok)
            return

        try:
            self._tree_observer.stop()
        except Exception:
            pass
        self._tree_observer.clear()
        if hasattr(self, "_tiff_option_a_watcher"):
            try:
                self._tiff_option_a_watcher.stop()
            except Exception:
                pass
        try:
            self._watcher.restart_at(
                wd,
                cfg=WatcherConfig(recursive=False, patterns=("*.tif", "*.tiff"), allow_dat=False),
            )
        except Exception:
            self._watcher.start()
        self._poll_watcher.start()
        self._ensure_dat_root_watcher_2d(wd, _dat_under_watchdir_ok)

    def _apply_curve_intake_watchers(
        self,
        wd: Path,
        *,
        allow_dirs: tuple[str, ...],
        mkdir_name: str,
        _dat_under_watchdir_ok,
    ) -> None:
        """``.dat`` watch for curve boarding + TIFF watch for Option A → 2D."""
        (wd / mkdir_name).mkdir(parents=True, exist_ok=True)
        if hasattr(self, "_dat_root_watcher"):
            try:
                self._dat_root_watcher.stop()
            except Exception:
                pass

        if self._c.state.watch_mode == LiveviewWatchMode.TREE:
            # Final-scan stop then restart: flush TIFFs dropped under 2D, keep TREE
            # so a later .tif can Option-A switch back to 2D.
            try:
                self._tree_observer.stop()
            except Exception:
                pass
            try:
                self._tree_observer.start()
            except Exception:
                pass
            if hasattr(self, "_tiff_option_a_watcher"):
                try:
                    self._tiff_option_a_watcher.stop()
                except Exception:
                    pass
        else:
            try:
                self._tree_observer.stop()
            except Exception:
                pass
            self._tree_observer.clear()
            self._ensure_tiff_option_a_watcher_flat(wd)

        self._watcher.restart_at(
            wd,
            cfg=WatcherConfig(
                recursive=True,
                patterns=("*.dat",),
                allow_dat=True,
                path_ok=lambda p: _dat_under_watchdir_ok(p, allow_dirs=allow_dirs),
            ),
        )
        self._poll_watcher.start()

    def _ensure_tiff_option_a_watcher_flat(self, wd: Path) -> None:
        """FLAT aux: top-level TIFFs while intake is 1D/Sub (Option A → 2D)."""
        cfg = WatcherConfig(
            recursive=False,
            patterns=("*.tif", "*.tiff"),
            allow_dat=False,
        )
        if not hasattr(self, "_tiff_option_a_watcher"):
            self._tiff_option_a_watcher = DirectoryWatcher(
                directory=wd,
                cfg=cfg,
                on_revision=self._observe_detected,
            )
        try:
            self._tiff_option_a_watcher.restart_at(wd, cfg=cfg)
        except Exception:
            self._tiff_option_a_watcher.start()

    def _ensure_dat_root_watcher_2d(self, wd: Path, path_ok_fn) -> None:
        """Aux watcher: *.dat in watchdir root (and averaged/subtracted if recursive were on)."""
        cfg = WatcherConfig(
            recursive=False,
            patterns=("*.dat",),
            allow_dat=True,
            path_ok=lambda p: path_ok_fn(p, allow_dirs=("averaged", "subtracted")),
        )
        if not hasattr(self, "_dat_root_watcher"):
            self._dat_root_watcher = DirectoryWatcher(
                directory=wd,
                cfg=cfg,
                on_revision=self._observe_detected,
            )
        try:
            self._dat_root_watcher.restart_at(wd, cfg=cfg)
        except Exception:
            self._dat_root_watcher.start()

    def _stop_tiff_watchers(self) -> None:
        try:
            self._tree_observer.stop()
        except Exception:
            pass
        self._tree_observer.clear()
        try:
            self._poll_watcher.stop()
        except Exception:
            pass
        if hasattr(self, "_dat_root_watcher"):
            try:
                self._dat_root_watcher.stop()
            except Exception:
                pass
        if hasattr(self, "_tiff_option_a_watcher"):
            try:
                self._tiff_option_a_watcher.stop()
            except Exception:
                pass

    def _on_dat_while_2d(self, revision: SampleRevision) -> None:
        """Incoming .dat while intake is 2D: same as a middle-column drop (may auto-switch)."""
        if self._c.state.intake_mode != LiveviewIntakeMode.FRAME_2D:
            return
        if self._c.executor.is_owned_output(revision.path):
            return
        self._ingest_one_dropped(Path(revision.path))

    def _on_tiff_while_curve(self, revision: SampleRevision) -> None:
        """Incoming .tif while intake is 1D/Sub: Option A → 2D (same as a middle drop)."""
        if self._c.state.intake_mode not in (
            LiveviewIntakeMode.CURVE_1D,
            LiveviewIntakeMode.CURVE_SUB,
        ):
            return
        if self._c.executor.is_owned_output(revision.path):
            return
        self._ingest_one_dropped(Path(revision.path))

    def enqueue_manual_sample(
        self,
        path: str,
        *,
        boarding: Optional[LiveviewIntakeMode] = None,
    ) -> None:
        allow_dat = True
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=SampleRevisionSource.MANUAL,
            allow_dat=allow_dat,
        )
        if rev is None:
            return
        mode = boarding or self._infer_boarding(rev.path)
        self._ingress.accept_manual(rev, boarding=mode)

    def ingest_dropped_files(self, paths: list[str]) -> None:
        """Drop intake with Option A auto-switch; manual toggle wins within 1D/Sub."""
        for raw in paths:
            p = Path(raw)
            if not p.is_file():
                continue
            src_r = p.resolve()
            self._ingest_one_dropped(src_r)

    def ingest_dropped_tiffs(self, paths: list[str]) -> None:
        self.ingest_dropped_files(paths)

    def _ingest_one_dropped(self, src_r: Path) -> None:
        intake = self._c.state.intake_mode
        path_s = str(src_r)

        if is_tiff_path(path_s):
            if intake != LiveviewIntakeMode.FRAME_2D:
                self._c.session.set_intake(LiveviewIntakeMode.FRAME_2D)
            dest = self._copy_tiff_into_watchdir(src_r)
            self.enqueue_manual_sample(dest, boarding=LiveviewIntakeMode.FRAME_2D)
            return

        if not is_sample_dat_path(path_s):
            self._c.error.emit(f"Unsupported drop (need .tif/.tiff/.dat): {src_r.name}")
            return

        boarding_cls, err = try_classify_curve_boarding(path_s)
        if err:
            self._c.error.emit(err)
            return
        assert boarding_cls is not None

        classified = boarding_cls

        if intake == LiveviewIntakeMode.FRAME_2D:
            # Auto-switch from 2D based on classification.
            self._c.session.set_intake(classified)
            boarding = classified
        else:
            # Already in 1D or Sub: manual toggle wins.
            boarding = intake
            if boarding != classified:
                self._app_log(
                    f"Drop classified as {classified.value} but intake is {boarding.value}; "
                    f"keeping manual toggle for {src_r.name}"
                )

        dest = self._copy_curve_into_watchdir(src_r, boarding=boarding)
        self.enqueue_manual_sample(dest, boarding=boarding)

    def _copy_tiff_into_watchdir(self, src_r: Path) -> str:
        wd = self._c.watchdir
        if self._path_under_watchdir(src_r):
            return str(src_r)
        dest = wd / src_r.name
        shutil.copy2(src_r, dest)
        return str(dest.resolve())

    def _copy_curve_into_watchdir(self, src_r: Path, *, boarding: LiveviewIntakeMode) -> str:
        wd = self._c.watchdir
        sub = "subtracted" if boarding == LiveviewIntakeMode.CURVE_SUB else "averaged"
        dest_dir = wd / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        # Already in watchdir root or the correct intake folder → enqueue in place.
        try:
            rel = src_r.resolve().relative_to(wd.resolve())
            if len(rel.parts) == 1:
                return str(src_r.resolve())
            if len(rel.parts) >= 2 and rel.parts[0] == sub:
                return str(src_r.resolve())
        except ValueError:
            pass
        dest = dest_dir / src_r.name
        if src_r.resolve() != dest.resolve():
            shutil.copy2(src_r, dest)
        return str(dest.resolve())

    def _infer_boarding(self, path: str) -> LiveviewIntakeMode:
        remembered = self._c.samples.boarding_for(path)
        if remembered is not None:
            return remembered
        if is_tiff_path(path):
            return LiveviewIntakeMode.FRAME_2D
        if is_dat_path(path):
            boarding_cls, _err = try_classify_curve_boarding(path)
            if boarding_cls is not None:
                return boarding_cls
            return LiveviewIntakeMode.CURVE_1D
        return self._c.state.intake_mode

    def _app_log(self, text: str) -> None:
        self._c.append_app_log(text)

    def _path_under_watchdir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._c.watchdir)
            return True
        except ValueError:
            return False

    def _enqueue_to_executor(self, revision: SampleRevision) -> None:
        if revision.source != SampleRevisionSource.MANUAL:
            if self._c.executor.is_owned_output(revision.path):
                return
        self._c.executor.enqueue_revision(revision)

    def _admit_revision_reason(self, revision: SampleRevision) -> Optional[str]:
        """
        Pre-enqueue gate for 2D frames.

        - Unreadable / non-2D TIFF → reject (toast).
        - Applied mask set and shape ≠ frame → reject (toast).
        - No mask → shape-vs-mask check inactive.
        """
        path = revision.path
        if not is_tiff_path(path):
            return None
        name = Path(path).name
        from autosaxs.core.detector_shape import frame_shape_hw, mask_shape_hw

        try:
            fh = frame_shape_hw(path)
        except Exception:
            return f"Invalid TIFF (cannot read): {name}"
        mask = applied_mask_path(self._c.state)
        if mask is None:
            return None
        try:
            mh = mask_shape_hw(str(mask))
        except Exception:
            return f"Invalid mask (cannot read): {mask.name}"
        if fh != mh:
            return (
                f"TIFF shape {fh[0]}×{fh[1]} does not match mask "
                f"{mh[0]}×{mh[1]}: {name}"
            )
        return None

    def _on_revision_rejected(self, reason: str) -> None:
        self._c.show_toast(reason)

    def _acknowledge_sample_stat(self, path: str, snap=None) -> None:
        try:
            self._settler.discard(path)
        except Exception:
            pass
        for w in (self._watcher, getattr(self, "_dat_root_watcher", None)):
            if w is None:
                continue
            try:
                w.note_path_stat(path, snap)
            except Exception:
                pass
        try:
            self._poll_watcher.note_path_stat(path, snap)
        except Exception:
            pass
        try:
            self._tree_observer.note_path_stat(path, snap)
        except Exception:
            pass

    def _observe_detected(self, revision: SampleRevision) -> None:
        """Detector callback: settle before ingress."""
        if revision.source != SampleRevisionSource.MANUAL:
            if self._c.executor.is_owned_output(revision.path):
                return
        self._settler.observe(revision)

    def _on_settled(self, revision: SampleRevision) -> None:
        """Stable revision → ingress (or Option A intake auto-switch)."""
        if is_tiff_path(revision.path) and self._c.state.intake_mode in (
            LiveviewIntakeMode.CURVE_1D,
            LiveviewIntakeMode.CURVE_SUB,
        ):
            self._on_tiff_while_curve(revision)
            return
        if (
            is_sample_dat_path(revision.path)
            and self._c.state.intake_mode == LiveviewIntakeMode.FRAME_2D
        ):
            self._on_dat_while_2d(revision)
            return
        if revision.source == SampleRevisionSource.TREE:
            boarding_from = "frame_2d"
        elif revision.source == SampleRevisionSource.POLL:
            boarding_from = "infer"
        else:
            boarding_from = "intake"
        self._ingress.accept(revision, boarding_from=boarding_from)
