"""Liveview-side modeling child process manager (paths in / Confirm prefs out)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QWidget

from ..modeling.context import ModelingContext
from ..modeling.ipc import ModelingChildHandle
from ..modeling.launch import start_modeling_child
from .session.output_paths import (
    dammif_dir,
    denss_dir,
    mixture_dir,
    model_bodies_dir,
)
from .session.state import (
    LiveviewSessionState,
    MonodisperseShapeMode,
    PolydisperseMixtureMode,
)


class ModelingChildManager(QObject):
    """Owns at most one shape and one DR child; builds ModelingContext from session."""

    preview_refresh_requested = pyqtSignal(str)  # "shape" | "dr"

    def __init__(self, *, state: LiveviewSessionState, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._state = state
        self._shape: Optional[ModelingChildHandle] = None
        self._dr: Optional[ModelingChildHandle] = None
        self._shape_push: Optional[Dict[str, Any]] = None
        self._dr_push: Optional[Dict[str, Any]] = None

    def shape_child(self) -> Optional[ModelingChildHandle]:
        """Active guisaxs-shape handle (tests / diagnostics)."""
        return self._shape

    def start_shape(
        self,
        *,
        profile_path: str,
        gnom_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
        parent_widget: Optional[QWidget] = None,
    ) -> None:
        self._shape_push = {
            "profile_path": profile_path,
            "gnom_path": gnom_path,
            "output_root": Path(output_root),
            "stem": stem,
            "sample_id": sample_id,
        }
        ctx = self.build_shape_context(
            profile_path=profile_path,
            gnom_path=gnom_path,
            output_root=output_root,
            stem=stem,
            sample_id=sample_id,
        )
        if self._shape is not None and self._shape.is_running():
            self._shape.send_context(ctx)
            self._shape.send_focus()
            self._shape.send_confirm()
            return
        self._shape = start_modeling_child(
            app="shape",
            context=ctx,
            parent=self,
            cwd=self._state.watchdir,
        )
        self._shape.confirmed.connect(self._on_shape_confirmed)
        self._shape.finished_run.connect(lambda _m: self.preview_refresh_requested.emit("shape"))
        self._shape.ready_for_context.connect(self._retry_shape_context)
        self._shape.process_exited.connect(lambda _c: setattr(self, "_shape", None))
        # After child's ready → launch sends context; then auto-Confirm.
        self._shape.ready.connect(self._auto_confirm_shape)

    def start_dr(
        self,
        *,
        profile_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
        parent_widget: Optional[QWidget] = None,
    ) -> None:
        self._dr_push = {
            "profile_path": profile_path,
            "output_root": Path(output_root),
            "stem": stem,
            "sample_id": sample_id,
        }
        ctx = self.build_dr_context(
            profile_path=profile_path,
            output_root=output_root,
            stem=stem,
            sample_id=sample_id,
        )
        if self._dr is not None and self._dr.is_running():
            self._dr.send_context(ctx)
            self._dr.send_focus()
            self._dr.send_confirm()
            return
        self._dr = start_modeling_child(
            app="dr",
            context=ctx,
            parent=self,
            cwd=self._state.watchdir,
        )
        self._dr.confirmed.connect(self._on_dr_confirmed)
        self._dr.finished_run.connect(lambda _m: self.preview_refresh_requested.emit("dr"))
        self._dr.ready_for_context.connect(self._retry_dr_context)
        self._dr.process_exited.connect(lambda _c: setattr(self, "_dr", None))
        self._dr.ready.connect(self._auto_confirm_dr)

    def shutdown(self) -> None:
        """Stop supervised modeling children before liveview tears down Qt objects."""
        shape = self._shape
        dr = self._dr
        self._shape = None
        self._dr = None
        self._shape_push = None
        self._dr_push = None
        for handle in (shape, dr):
            if handle is None:
                continue
            try:
                handle.shutdown()
            except Exception:
                pass

    def push_shape_context(
        self,
        *,
        profile_path: str,
        gnom_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
    ) -> None:
        self._shape_push = {
            "profile_path": profile_path,
            "gnom_path": gnom_path,
            "output_root": Path(output_root),
            "stem": stem,
            "sample_id": sample_id,
        }
        if self._shape is None or not self._shape.is_running():
            return
        self._shape.send_context(
            self.build_shape_context(
                profile_path=profile_path,
                gnom_path=gnom_path,
                output_root=output_root,
                stem=stem,
                sample_id=sample_id,
            )
        )
        self._shape.send_confirm()

    def push_dr_context(
        self,
        *,
        profile_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
    ) -> None:
        self._dr_push = {
            "profile_path": profile_path,
            "output_root": Path(output_root),
            "stem": stem,
            "sample_id": sample_id,
        }
        if self._dr is None or not self._dr.is_running():
            return
        self._dr.send_context(
            self.build_dr_context(
                profile_path=profile_path,
                output_root=output_root,
                stem=stem,
                sample_id=sample_id,
            )
        )
        self._dr.send_confirm()

    def _auto_confirm_shape(self) -> None:
        if self._shape is not None and self._shape.is_running():
            self._shape.send_confirm()

    def _auto_confirm_dr(self) -> None:
        if self._dr is not None and self._dr.is_running():
            self._dr.send_confirm()

    def _retry_shape_context(self) -> None:
        args = self._shape_push
        if not args:
            return
        self.push_shape_context(
            profile_path=str(args.get("profile_path") or ""),
            gnom_path=str(args.get("gnom_path") or ""),
            output_root=Path(args["output_root"]),
            stem=str(args.get("stem") or ""),
            sample_id=str(args.get("sample_id") or ""),
        )

    def _retry_dr_context(self) -> None:
        args = self._dr_push
        if not args:
            return
        self.push_dr_context(
            profile_path=str(args.get("profile_path") or ""),
            output_root=Path(args["output_root"]),
            stem=str(args.get("stem") or ""),
            sample_id=str(args.get("sample_id") or ""),
        )

    def build_shape_context(
        self,
        *,
        profile_path: str,
        gnom_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
    ) -> ModelingContext:
        from .ingest.curve_classify import usable_analysis_curve_path
        from ..modeling.run_params import (
            apply_disk_params_to_session_state,
            infer_n_runs_from_disk,
            infer_shape_mode_from_disk,
            profile_sample_stem,
            read_run_params,
            sample_modeling_dir_for_family,
        )

        profile_path = usable_analysis_curve_path(profile_path)
        st = (stem or profile_sample_stem(profile_path) or "").strip()
        mode = self._state.monodisperse_shape_mode
        mode_s = str(getattr(mode, "value", mode) or "none").lower()
        root = output_root.expanduser().resolve()

        if mode_s not in ("bodies", "dammif", "denss"):
            for cand_mode, family in (
                ("dammif", dammif_dir(root)),
                ("bodies", model_bodies_dir(root)),
                ("denss", denss_dir(root)),
            ):
                if (
                    st
                    and infer_shape_mode_from_disk(family, profile_path=profile_path, stem=st)
                    == cand_mode
                ):
                    mode_s = cand_mode
                    break
        if mode_s == "bodies":
            family = model_bodies_dir(root)
        elif mode_s == "denss":
            family = denss_dir(root)
        else:
            family = dammif_dir(root)
        # Always family/<stem> when stem known — never newest-sibling under family.
        out = sample_modeling_dir_for_family(family, profile_path=profile_path, stem=st)
        if st:
            apply_disk_params_to_session_state(
                self._state, output_dir=out, profile_path=profile_path, stem=st
            )
        opts: Dict[str, Any] = {
            "n_runs": int(self._state.model_dam_n_runs or 1),
            "mode": str(self._state.model_density_mode or "pilot"),
            "denss_mode": str(self._state.model_density_denss_mode or "fast"),
            "n_maps": int(self._state.model_density_n_maps or 20),
        }
        if self._state.model_bodies_shapes:
            opts["shapes"] = list(self._state.model_bodies_shapes)
        disk = read_run_params(out, profile_path=profile_path, stem=st) if st else {}
        if disk:
            if "n_runs" in disk:
                try:
                    opts["n_runs"] = max(1, int(disk["n_runs"]))
                except (TypeError, ValueError):
                    pass
            if "mode" in disk:
                opts["mode"] = str(disk["mode"])
            if "denss_mode" in disk:
                opts["denss_mode"] = str(disk["denss_mode"]).lower()
            if "n_maps" in disk:
                try:
                    opts["n_maps"] = max(2, int(disk["n_maps"]))
                except (TypeError, ValueError):
                    pass
            if isinstance(disk.get("shapes"), list):
                opts["shapes"] = [str(s) for s in disk["shapes"]]
        elif st and mode_s in ("none", "dammif"):
            n_legacy = infer_n_runs_from_disk(out, profile_path=profile_path, stem=st)
            if n_legacy is not None:
                opts["n_runs"] = n_legacy
        if "n_runs" in opts:
            try:
                self._state.model_dam_n_runs = int(opts["n_runs"])
            except (TypeError, ValueError):
                pass
        gnom = str(gnom_path or "").strip()
        if gnom and not Path(gnom).is_file():
            gnom = ""
        return ModelingContext(
            profile_path=str(profile_path or ""),
            gnom_path=gnom,
            output_dir=str(out.resolve()),
            mode=mode_s if mode_s in ("bodies", "dammif", "denss") else "dammif",
            options=opts,
            require_gnom_for_dam=True,
            sample_id=str(sample_id or ""),
        )

    def build_dr_context(
        self,
        *,
        profile_path: str,
        output_root: Path,
        stem: str = "",
        sample_id: str = "",
    ) -> ModelingContext:
        from .ingest.curve_classify import usable_analysis_curve_path
        from ..modeling.run_params import (
            apply_disk_params_to_session_state,
            profile_sample_stem,
            read_run_params,
            sample_modeling_dir_for_family,
        )

        profile_path = usable_analysis_curve_path(profile_path)
        st = (stem or profile_sample_stem(profile_path) or "").strip()
        mode = self._state.polydisperse_mixture_mode
        mode_s = str(getattr(mode, "value", mode) or "none").lower()
        family = mixture_dir(output_root.expanduser().resolve())
        out = sample_modeling_dir_for_family(family, profile_path=profile_path, stem=st)
        if st:
            apply_disk_params_to_session_state(
                self._state, output_dir=out, profile_path=profile_path, stem=st
            )
        opts = dict(self._state.model_mixture_options or {})
        disk = read_run_params(out, profile_path=profile_path, stem=st) if st else {}
        if disk:
            for key in ("max_nph", "r_max_nm", "poly_max_nm", "q_min", "q_max"):
                if key in disk and disk[key] is not None:
                    opts[key] = disk[key]
            if mode_s == "none" and str(disk.get("skill") or "") == "model_mixture":
                mode_s = "mixture"
        elif st and (out / "mixture_results.csv").is_file() and mode_s == "none":
            mode_s = "mixture"
        self._state.model_mixture_options = dict(opts)
        return ModelingContext(
            profile_path=str(profile_path or ""),
            output_dir=str(out.resolve()),
            mode="mixture",
            options=opts,
            sample_id=str(sample_id or ""),
        )

    def _on_shape_confirmed(self, msg: dict) -> None:
        mode = str(msg.get("mode") or "none").lower()
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(mode)
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        opts = msg.get("options") if isinstance(msg.get("options"), dict) else {}
        if "n_runs" in opts:
            try:
                self._state.model_dam_n_runs = max(1, int(opts["n_runs"]))
            except (TypeError, ValueError):
                pass
        if "mode" in opts:
            self._state.model_density_mode = str(opts["mode"])
        if "denss_mode" in opts:
            self._state.model_density_denss_mode = str(opts["denss_mode"])
        if "n_maps" in opts:
            try:
                self._state.model_density_n_maps = max(2, int(opts["n_maps"]))
            except (TypeError, ValueError):
                pass
        if isinstance(opts.get("shapes"), list):
            self._state.model_bodies_shapes = [str(s) for s in opts["shapes"]]
        # Arm upstream fast analysis only (no modeling in liveview plan).
        self._state.monodisperse_armed = True

    def _on_dr_confirmed(self, msg: dict) -> None:
        mode = str(msg.get("mode") or "none").lower()
        try:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode(mode)
        except ValueError:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode.NONE
        opts = msg.get("options") if isinstance(msg.get("options"), dict) else {}
        self._state.model_mixture_options = dict(opts)
        self._state.polydisperse_armed = True
