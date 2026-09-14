"""Persist polydisperse window params into LiveviewSessionState and YAML confs."""

from __future__ import annotations

from typing import Any, Mapping

import yaml

from .....session.output_paths import fit_sizes_dir, guinier_poly_dir, mixture_dir
from .....session.state import LiveviewSessionState, PolydisperseMixtureMode

_SIZES_CONF_KEYS = (
    "first",
    "last",
    "rmin_nm",
    "rmax_nm",
    "alpha",
    "force_zero_rmin",
    "force_zero_rmax",
)
_SIZES_REFINE_KEYS = ("rmin_nm", "rmax_nm", "alpha", "force_zero_rmin", "force_zero_rmax")


class PolydisperseConfigSync:
    def __init__(self, *, state: LiveviewSessionState, window: Any) -> None:
        self._state = state
        self._window = window
        self._sizes_adjust: Any = None

    def set_sizes_adjust_wizard(self, dlg: Any) -> None:
        self._sizes_adjust = dlg

    def sync_params_to_state(self) -> None:
        wp = dict(self._state.polydisperse_window_params or {})
        g_first, g_last = self._window.guinier_pane.first_last()
        if g_first is None or g_last is None:
            wp.pop("guinier_first", None)
            wp.pop("guinier_last", None)
        else:
            wp["guinier_first"] = g_first
            wp["guinier_last"] = g_last
        sizes = self._sizes_params_from_ui()
        if sizes:
            for k in _SIZES_CONF_KEYS:
                if k in sizes:
                    wp[k] = sizes[k]
                elif k in ("last", "rmin_nm", "alpha") and k not in sizes:
                    wp.pop(k, None)
        mix = self._window.mixture_pane.mixture_params()
        wp["mixture"] = mix
        self._state.polydisperse_window_params = wp
        self._state.model_mixture_options = dict(mix)
        mode = self._window.mixture_pane.mixture_mode()
        try:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode(mode)
        except ValueError:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode.NONE
        self.persist_confs()

    def _sizes_params_from_ui(self) -> dict:
        dlg = self._sizes_adjust
        if dlg is not None and hasattr(dlg, "sizes_params"):
            try:
                return dict(dlg.sizes_params())
            except Exception:
                pass
        wp = self._state.polydisperse_window_params or {}
        return {k: wp[k] for k in _SIZES_CONF_KEYS if wp.get(k) is not None}

    def persist_confs(self) -> None:
        wd = self._state.watchdir
        gdir = guinier_poly_dir(wd)
        gdir.mkdir(parents=True, exist_ok=True)
        gpath = gdir / "guinier.conf"
        spath = fit_sizes_dir(wd) / "fit_sizes.conf"
        spath.parent.mkdir(parents=True, exist_ok=True)
        mdir = mixture_dir(wd)
        mdir.mkdir(parents=True, exist_ok=True)
        mpath = mdir / "liveview_mixture.yml"

        wp = self._state.polydisperse_window_params or {}
        g_first = wp.get("guinier_first")
        g_last = wp.get("guinier_last")
        if g_first is None or g_last is None:
            try:
                g_first, g_last = self._window.guinier_pane.first_last()
            except Exception:
                g_first, g_last = None, None
        gopts = {}
        if g_first is not None and g_last is not None:
            gopts["first"] = int(g_first)
            gopts["last"] = int(g_last)
        sopts = self._sizes_params_from_ui()
        # Auto conf must not pin rmax/alpha/force_zero (that would force refine on every TIFF).
        for k in _SIZES_REFINE_KEYS:
            sopts.pop(k, None)
        sopts["shape"] = "spheres"
        if sopts.get("first") is None:
            sopts["first"] = 1
        try:
            mix = dict(self._window.mixture_pane.mixture_params())
        except Exception:
            mix = dict(wp.get("mixture") or {})

        try:
            gpath.write_text(yaml.safe_dump(gopts, sort_keys=True), encoding="utf-8")
            self._state.fit_guinier_poly_conf_path = gpath
        except OSError:
            pass
        try:
            spath.write_text(yaml.safe_dump(sopts, sort_keys=True), encoding="utf-8")
            self._state.fit_sizes_conf_path = spath
        except OSError:
            pass
        try:
            mpath.write_text(yaml.safe_dump({"model_mixture": mix}, sort_keys=True), encoding="utf-8")
            self._state.model_mixture_config_path = mpath
            self._state.model_mixture_options = mix
        except OSError:
            pass

    def store_sizes_working(self, params: Mapping[str, Any]) -> None:
        wp = dict(self._state.polydisperse_window_params or {})
        for k in _SIZES_CONF_KEYS:
            if k in params:
                if params[k] is None:
                    wp.pop(k, None)
                else:
                    wp[k] = params[k]
        if "last" not in params:
            wp.pop("last", None)
        if "rmin_nm" not in params:
            wp.pop("rmin_nm", None)
        if "alpha" not in params:
            wp.pop("alpha", None)
        self._state.polydisperse_window_params = wp
        self.persist_confs()

    def store_sizes_auto_snapshot(self, params: Mapping[str, Any]) -> None:
        wp = dict(self._state.polydisperse_window_params or {})
        auto = {k: params[k] for k in _SIZES_CONF_KEYS if params.get(k) is not None}
        if params.get("force_zero_rmin") is None:
            auto.setdefault("force_zero_rmin", "Y")
        if params.get("force_zero_rmax") is None:
            auto.setdefault("force_zero_rmax", "Y")
        wp["sizes_auto"] = auto
        for k, v in auto.items():
            wp.setdefault(k, v)
        self._state.polydisperse_window_params = wp
        self.persist_confs()

    def apply_mixture_mode(self, mode: str) -> None:
        try:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode(mode)
        except ValueError:
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode.NONE

    def store_guinier_interval(self, first: int, last: int) -> None:
        wp = dict(self._state.polydisperse_window_params or {})
        wp["guinier_first"] = int(first)
        wp["guinier_last"] = int(last)
        self._state.polydisperse_window_params = wp
        self.persist_confs()
