"""Persist polydisperse window params into LiveviewSessionState and YAML confs."""

from __future__ import annotations

from typing import Any

import yaml

from .....session.output_paths import fit_sizes_dir, guinier_poly_dir, mixture_dir
from .....session.state import LiveviewSessionState, PolydisperseMixtureMode


class PolydisperseConfigSync:
    def __init__(self, *, state: LiveviewSessionState, window: Any) -> None:
        self._state = state
        self._window = window

    def sync_params_to_state(self) -> None:
        wp = dict(self._state.polydisperse_window_params or {})
        g_first, g_last = self._window.guinier_pane.first_last()
        if g_first is None or g_last is None:
            wp.pop("guinier_first", None)
            wp.pop("guinier_last", None)
        else:
            wp["guinier_first"] = g_first
            wp["guinier_last"] = g_last
        wp.update(self._window.sizes_pane.sizes_params())
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
        try:
            sopts = dict(self._window.sizes_pane.sizes_params())
        except Exception:
            sopts = {
                k: wp[k]
                for k in ("first", "last", "rmin_nm", "rmax_nm", "alpha")
                if wp.get(k) is not None
            }
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
