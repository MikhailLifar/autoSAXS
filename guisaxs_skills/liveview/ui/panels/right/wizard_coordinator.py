from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import yaml
from PyQt5.QtWidgets import QWidget

from .....core.models import RunRequest
from .....logic.path_normalize import normalize_pathish
from .....logic.session_state import SessionPathHints
from .....ui.path_field import PathField
from ....session.state import AnalysisMode, LiveviewSessionState, LiveviewState
from ...wizards.right import (
    FitBodiesWizardDialog,
    FitDistancesWizardDialog,
    FitMixtureWizardDialog,
    FitSizesWizardDialog,
    fit_mixture_run_options_from_wizard,
)
from .config_restore import BODIES_SHAPES_LIST
from .form_helpers import strip_fit_distances_profile_from_saved_form
from .mode_selector import AnalysisModeSelector


class FitWizardCoordinator:
    def __init__(
        self,
        *,
        state: LiveviewSessionState,
        parent: QWidget,
        meta_fit: Any,
        meta_sizes: Any,
        meta_mixture: Any,
        mode_selector: AnalysisModeSelector,
        on_config_changed: Callable[[], None],
        on_modeling_enabled: Callable[[bool], None],
        config_restore: Any,
    ) -> None:
        self._state = state
        self._parent = parent
        self._meta_fit = meta_fit
        self._meta_sizes = meta_sizes
        self._meta_mixture = meta_mixture
        self._mode = mode_selector
        self._on_config_changed = on_config_changed
        self._on_modeling_enabled = on_modeling_enabled
        self._config_restore = config_restore

        self._fit_wizard: FitDistancesWizardDialog | None = None
        self._fit_sizes_wizard: FitSizesWizardDialog | None = None
        self._fit_mixture_wizard: FitMixtureWizardDialog | None = None
        self._fit_bodies_wizard: FitBodiesWizardDialog | None = None
        self._fit_distances_saved_form: Optional[dict[str, Any]] = None
        self._fit_sizes_saved_form: Optional[dict[str, Any]] = None
        self._fit_mixture_saved_form: Optional[dict[str, Any]] = None

    def set_running(self, running: bool) -> None:
        for w in (
            self._fit_wizard,
            self._fit_sizes_wizard,
            self._fit_mixture_wizard,
            self._fit_bodies_wizard,
        ):
            if w is not None:
                w.set_running(bool(running))

    def open_fit_distances(self) -> None:
        if self._meta_fit is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_wizard is None:
            self._fit_wizard = FitDistancesWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=strip_fit_distances_profile_from_saved_form(
                    self._fit_distances_saved_form, self._meta_fit
                ),
                parent=self._parent,
            )
            self._fit_wizard.finished.connect(self._persist_fit_wizard_form)
        else:
            self._fit_wizard.rebuild(hints)
        self._fit_wizard.show()
        self._fit_wizard.raise_()
        self._fit_wizard.activateWindow()

    def open_fit_sizes(self) -> None:
        if self._meta_sizes is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_sizes_wizard is None:
            self._fit_sizes_wizard = FitSizesWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=self._fit_sizes_saved_form,
                parent=self._parent,
            )
            self._fit_sizes_wizard.finished.connect(self._persist_fit_sizes_wizard_form)
        else:
            self._fit_sizes_wizard.rebuild(hints)
        self._fit_sizes_wizard.show()
        self._fit_sizes_wizard.raise_()
        self._fit_sizes_wizard.activateWindow()

    def open_fit_mixture(self) -> None:
        if self._meta_mixture is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_mixture_wizard is None:
            self._fit_mixture_wizard = FitMixtureWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=self._fit_mixture_saved_form,
                saved_mixture_params=self._config_restore.fit_mixture_saved_mixture_params,
                parent=self._parent,
            )
            self._fit_mixture_wizard.finished.connect(self._persist_fit_mixture_wizard_form)
        else:
            self._fit_mixture_wizard.rebuild(hints)
        self._fit_mixture_wizard.show()
        self._fit_mixture_wizard.raise_()
        self._fit_mixture_wizard.activateWindow()

    def open_fit_bodies(self) -> None:
        if self._fit_bodies_wizard is None:
            self._fit_bodies_wizard = FitBodiesWizardDialog(
                watchdir=self._state.watchdir,
                saved_shapes=self._state.fit_bodies_shapes,
                parent=self._parent,
            )
        else:
            self._fit_bodies_wizard.rebuild(saved_shapes=self._state.fit_bodies_shapes)
        self._fit_bodies_wizard.show()
        self._fit_bodies_wizard.raise_()
        self._fit_bodies_wizard.activateWindow()

    def build_fit_distances_request(self) -> RunRequest:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        return self._fit_wizard.build_fit_request()

    def build_fit_sizes_request(self) -> RunRequest:
        if self._fit_sizes_wizard is None:
            raise RuntimeError("Open the fit_sizes wizard first")
        return self._fit_sizes_wizard.build_fit_sizes_request()

    def build_fit_mixture_request(self) -> RunRequest:
        if self._fit_mixture_wizard is None:
            raise RuntimeError("Open the fit_mixture wizard first")
        req = self._fit_mixture_wizard.build_fit_mixture_request()
        opts = dict(req.options)
        saved = self._state.fit_mixture_options
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k not in ("output_dir", "use_cache", "config_path") and v is not None:
                    if not (isinstance(v, str) and not str(v).strip()):
                        opts[k] = v
        opts.pop("config_path", None)
        opts["use_cache"] = False
        return RunRequest(skill_name=req.skill_name, positional=list(req.positional), options=opts)

    def _profile_text(self, wizard, meta, skill_name: str) -> str:
        if wizard is None or meta is None:
            return ""
        form = wizard._form  # type: ignore[attr-defined]
        widgets = getattr(form, "_pos_widgets", [])
        for i, p in enumerate(meta.positional_params):
            if p.name != "profile" or i >= len(widgets):
                continue
            w = widgets[i]
            if isinstance(w, PathField):
                parts = [normalize_pathish(x) for x in w.paths() if normalize_pathish(x)]
                if parts:
                    return parts[0].split(",")[0].strip()
                t = (w.text() or "").strip()
                return t.split(",")[0].strip() if t else ""
        return ""

    def wizard_has_profile_file(self, wizard, meta, watchdir: Path) -> bool:
        t = self._profile_text(wizard, meta, "").strip()
        if not t:
            return False
        p = Path(t).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path.is_file()

    def fit_distances_has_profile(self, watchdir: Path) -> bool:
        return self.wizard_has_profile_file(self._fit_wizard, self._meta_fit, watchdir)

    def fit_sizes_has_profile(self, watchdir: Path) -> bool:
        return self.wizard_has_profile_file(self._fit_sizes_wizard, self._meta_sizes, watchdir)

    def fit_mixture_has_profile(self, watchdir: Path) -> bool:
        return self.wizard_has_profile_file(self._fit_mixture_wizard, self._meta_mixture, watchdir)

    def save_fit_distances_conf(self, *, enable_modeling: bool = True) -> None:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        self._persist_fit_distances_conf(self._fit_wizard, enable_modeling=enable_modeling)

    def save_fit_sizes_conf(self, *, enable_modeling: bool = True) -> None:
        if self._fit_sizes_wizard is None:
            raise RuntimeError("Open the fit_sizes wizard first")
        self._persist_fit_sizes_conf(self._fit_sizes_wizard, enable_modeling=enable_modeling)

    def save_fit_mixture_conf(self, *, enable_modeling: bool = True) -> None:
        if self._fit_mixture_wizard is None:
            raise RuntimeError("Open the fit_mixture wizard first")
        self._persist_fit_mixture_conf(self._fit_mixture_wizard, enable_modeling=enable_modeling)

    def save_fit_bodies_conf(self, *, enable_modeling: bool = True) -> None:
        if self._fit_bodies_wizard is None:
            raise RuntimeError("Open the fit_bodies (shapes) wizard first")
        w = self._fit_bodies_wizard
        shapes = w.selected_shapes()
        if not shapes:
            raise ValueError("Select at least one body model.")
        for s in shapes:
            if s not in BODIES_SHAPES_LIST:
                raise ValueError(f"Unknown bodies shape {s!r}")
        conf_dir = self._state.watchdir / "fit_bodies"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_bodies.conf"
        conf_path.write_text(
            yaml.safe_dump({"shapes": shapes}, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        self._state.fit_bodies_conf_path = conf_path
        self._state.fit_bodies_shapes = list(shapes)
        self._on_config_changed()
        if enable_modeling:
            self._enable_mode_if_off(AnalysisMode.MONODISPERSE)

    def _enable_mode_if_off(self, mode: AnalysisMode) -> None:
        prev_ae = self._state.analysis_enabled()
        if self._state.analysis_mode == AnalysisMode.OFF:
            self._state.analysis_mode = mode
            self._mode._set_combo_to_state()
        if not prev_ae and self._state.analysis_enabled():
            self._on_modeling_enabled(True)

    def _build_fit_path_hints(self) -> SessionPathHints:
        h = SessionPathHints()
        wd = self._state.watchdir
        if self._state.integrator_dir is not None:
            h.integrator_dir = str(self._state.integrator_dir.resolve())
        st = self._state.current_state()
        profile_file = self._state.default_fit_distances_profile_path()
        if profile_file is not None and profile_file.is_file():
            h.preferred_profile_dat_path = str(profile_file.resolve())
            h.one_d_profile_dir = str(profile_file.parent.resolve())
        else:
            profile_parent = None
            if st in (LiveviewState.C, LiveviewState.CD):
                ls = self._state.last_subtracted_dat_path
                if ls is not None and ls.is_file():
                    profile_parent = ls.parent
            if profile_parent is None and st in (LiveviewState.C, LiveviewState.CD):
                lip = self._state.last_integrated_dat_path
                if lip is not None and lip.is_file():
                    profile_parent = lip.parent
            if profile_parent is None and st in (LiveviewState.B, LiveviewState.BD):
                lip = self._state.last_integrated_dat_path
                if lip is not None and lip.is_file():
                    profile_parent = lip.parent
            if profile_parent is not None:
                h.one_d_profile_dir = str(profile_parent.resolve())
        av = wd / "averaged"
        if av.is_dir():
            h.integrate_output_dir = str(av.resolve())
        sub = wd / "subtracted"
        if sub.is_dir() and self._state.buffer_dat_path is not None:
            h.subtract_output_dir = str(sub.resolve())
        return h

    def _persist_fit_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_wizard
        if w is None or self._meta_fit is None:
            return
        try:
            self._fit_distances_saved_form = strip_fit_distances_profile_from_saved_form(
                w._form.state(),  # type: ignore[attr-defined]
                self._meta_fit,
            )
        except Exception:
            pass

    def _persist_fit_sizes_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_sizes_wizard
        if w is None or self._meta_sizes is None:
            return
        try:
            self._fit_sizes_saved_form = w._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _persist_fit_mixture_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_mixture_wizard
        if w is None or self._meta_mixture is None:
            return
        try:
            self._fit_mixture_saved_form = w._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _persist_fit_distances_conf(self, wizard: FitDistancesWizardDialog, *, enable_modeling: bool) -> None:
        if self._meta_fit is None:
            return
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts = {k: v for k, v in opts.items() if not (isinstance(v, str) and not v.strip())}
        conf_dir = self._state.watchdir / "fit_distances"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_distances.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_distances_conf_path = conf_path
        self._on_config_changed()
        if enable_modeling:
            self._enable_mode_if_off(AnalysisMode.MONODISPERSE)
        try:
            self._fit_distances_saved_form = strip_fit_distances_profile_from_saved_form(
                wizard._form.state(),  # type: ignore[attr-defined]
                self._meta_fit,
            )
        except Exception:
            pass

    def _persist_fit_sizes_conf(self, wizard: FitSizesWizardDialog, *, enable_modeling: bool) -> None:
        if self._meta_sizes is None:
            return
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts = {k: v for k, v in opts.items() if not (isinstance(v, str) and not v.strip())}
        conf_dir = self._state.watchdir / "fit_sizes"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_sizes.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_sizes_conf_path = conf_path
        self._on_config_changed()
        if enable_modeling:
            self._enable_mode_if_off(AnalysisMode.POLYDISPERSE_DR)
        try:
            self._fit_sizes_saved_form = wizard._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _persist_fit_mixture_conf(self, wizard: FitMixtureWizardDialog, *, enable_modeling: bool) -> None:
        if self._meta_mixture is None:
            return
        self._state.fit_mixture_options = fit_mixture_run_options_from_wizard(wizard)
        cfg_path = wizard.write_mixture_config_yaml()
        self._state.fit_mixture_config_path = cfg_path
        self._write_mixture_sidecar(cfg_path)
        self._on_config_changed()
        if enable_modeling:
            self._enable_mode_if_off(AnalysisMode.POLYDISPERSE_MIXTURE)
        try:
            self._fit_mixture_saved_form = wizard._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _write_mixture_sidecar(self, path: Path) -> None:
        try:
            d = self._state.watchdir / "mixture"
            d.mkdir(parents=True, exist_ok=True)
            (d / "liveview_mixture_config.txt").write_text(str(path.resolve()), encoding="utf-8")
        except Exception:
            pass
