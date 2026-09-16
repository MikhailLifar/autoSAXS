"""Persist monodisperse wizard params into LiveviewSessionState and YAML confs."""

from __future__ import annotations

from typing import Any, Mapping

import yaml

from .....session.output_paths import fit_distances_dir, guinier_mono_dir
from .....session.state import LiveviewSessionState, MonodisperseShapeMode

_GNOM_CONF_KEYS = (
    "rg_nm",
    "first",
    "last",
    "dmax_nm",
    "alpha",
    "force_zero_rmin",
    "force_zero_rmax",
)


class MonodisperseConfigSync:
    def __init__(self, *, state: LiveviewSessionState, wizard: Any) -> None:
        self._state = state
        self._wizard = wizard

    def sync_params_to_state(self) -> None:
        wp = dict(self._state.monodisperse_wizard_params or {})
        g_first, g_last = self._wizard.guinier_pane.first_last()
        if g_first is None or g_last is None:
            wp.pop("guinier_first", None)
            wp.pop("guinier_last", None)
        else:
            wp["guinier_first"] = g_first
            wp["guinier_last"] = g_last
        # Committed GNOM refine params (Confirm) already live in monodisperse_wizard_params.
        gnom = self._gnom_params_from_ui()
        if gnom:
            for k in ("first", "last", "dmax_nm", "alpha", "force_zero_rmin", "force_zero_rmax", "rg_nm"):
                if k in gnom:
                    wp[k] = gnom[k]
                elif k in ("last", "alpha") and k not in gnom:
                    wp.pop(k, None)
        self._state.monodisperse_wizard_params = wp
        mode = self._wizard.shape_pane.shape_mode()
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(mode)
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        shapes = self._wizard.shape_pane.selected_shapes()
        if shapes:
            self._state.model_bodies_shapes = list(shapes)
        self.apply_n_runs(self._wizard.shape_pane.n_runs())
        self.apply_denss_settings()
        self.persist_confs()

    def _gnom_params_from_ui(self) -> dict:
        # Only committed (Confirm) values live in session state — never pull live dirty UI.
        wp = self._state.monodisperse_wizard_params or {}
        return {k: wp[k] for k in _GNOM_CONF_KEYS if wp.get(k) is not None}

    def persist_confs(self) -> None:
        wd = self._state.watchdir
        gdir = guinier_mono_dir(wd)
        gdir.mkdir(parents=True, exist_ok=True)
        gpath = gdir / "guinier.conf"
        dpath = fit_distances_dir(wd) / "fit_distances.conf"
        dpath.parent.mkdir(parents=True, exist_ok=True)
        wp = self._state.monodisperse_wizard_params or {}
        g_first = wp.get("guinier_first")
        g_last = wp.get("guinier_last")
        if g_first is None or g_last is None:
            try:
                g_first, g_last = self._wizard.guinier_pane.first_last()
            except Exception:
                g_first, g_last = None, None
        gopts = {}
        if g_first is not None and g_last is not None:
            gopts["first"] = int(g_first)
            gopts["last"] = int(g_last)
        dopts = self._gnom_params_from_ui()
        refine_opts = dict(dopts)
        # Auto conf must not pin Dmax (that would force GNOM refine on every TIFF).
        # Full refine params (incl. force_zero) are written to fit_distances_refine.conf.
        for k in ("dmax_nm", "alpha", "force_zero_rmin", "force_zero_rmax"):
            dopts.pop(k, None)
        # Drop smooth from persisted conf unless explicitly set for rare DATGNOM re-auto use.
        dopts.pop("smooth", None)
        refine_path = fit_distances_dir(wd) / "fit_distances_refine.conf"
        try:
            gpath.write_text(yaml.safe_dump(gopts, sort_keys=True), encoding="utf-8")
            self._state.fit_guinier_mono_conf_path = gpath
        except OSError:
            pass
        try:
            dpath.write_text(yaml.safe_dump(dopts, sort_keys=True), encoding="utf-8")
            self._state.fit_distances_conf_path = dpath
        except OSError:
            pass
        try:
            if refine_opts:
                refine_path.write_text(yaml.safe_dump(refine_opts, sort_keys=True), encoding="utf-8")
        except OSError:
            pass

    def store_gnom_working(self, params: Mapping[str, Any]) -> None:
        wp = dict(self._state.monodisperse_wizard_params or {})
        for k in _GNOM_CONF_KEYS:
            if k in params:
                if params[k] is None:
                    wp.pop(k, None)
                else:
                    wp[k] = params[k]
        # Clear keys omitted by gnom_params (e.g. last/alpha auto).
        if "last" not in params:
            wp.pop("last", None)
        if "alpha" not in params:
            wp.pop("alpha", None)
        self._state.monodisperse_wizard_params = wp
        self.persist_confs()

    def store_gnom_auto_snapshot(self, params: Mapping[str, Any]) -> None:
        wp = dict(self._state.monodisperse_wizard_params or {})
        auto = {k: params[k] for k in _GNOM_CONF_KEYS if params.get(k) is not None}
        if params.get("force_zero_rmin") is None:
            auto.setdefault("force_zero_rmin", "Y")
        if params.get("force_zero_rmax") is None:
            auto.setdefault("force_zero_rmax", "Y")
        wp["gnom_auto"] = auto
        # Seed working params from auto when first available.
        for k, v in auto.items():
            wp.setdefault(k, v)
        self._state.monodisperse_wizard_params = wp
        self.persist_confs()

    def apply_shape_mode(self, mode: str) -> None:
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(mode)
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        shapes = self._wizard.shape_pane.selected_shapes()
        if shapes:
            self._state.model_bodies_shapes = list(shapes)
        self.apply_n_runs(self._wizard.shape_pane.n_runs())
        self.apply_denss_settings()

    def apply_n_runs(self, n: int) -> None:
        """Persist ``n_runs`` only — does not trigger a shape re-run."""
        try:
            self._state.model_dam_n_runs = max(1, int(n))
        except (TypeError, ValueError):
            self._state.model_dam_n_runs = 1

    def apply_denss_settings(self) -> None:
        """Persist DENSS protocol / denss_mode / n_maps — does not trigger a re-run."""
        pane = self._wizard.shape_pane
        protocol = str(pane.denss_protocol() or "pilot").strip().lower()
        if protocol not in ("pilot", "average", "refined"):
            protocol = "pilot"
        denss_mode = str(pane.denss_mode() or "fast").strip().lower()
        if denss_mode not in ("slow", "fast", "membrane"):
            denss_mode = "fast"
        try:
            n_maps = max(2, int(pane.denss_n_maps()))
        except (TypeError, ValueError):
            n_maps = 20
        self._state.model_density_mode = protocol
        self._state.model_density_denss_mode = denss_mode
        self._state.model_density_n_maps = n_maps

    def store_guinier_interval(self, first: int, last: int) -> None:
        """Persist Guinier spins without touching DATGNOM first/last."""
        wp = dict(self._state.monodisperse_wizard_params or {})
        wp["guinier_first"] = int(first)
        wp["guinier_last"] = int(last)
        self._state.monodisperse_wizard_params = wp
        self.persist_confs()
