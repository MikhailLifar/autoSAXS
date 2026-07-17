"""Persist monodisperse wizard params into LiveviewSessionState and YAML confs."""

from __future__ import annotations

from typing import Any

import yaml

from .....session.output_paths import fit_distances_dir, guinier_mono_dir
from .....session.state import LiveviewSessionState, MonodisperseShapeMode


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
        # Drop legacy shared keys so Guinier interval cannot leak into DATGNOM.
        wp.pop("first", None)
        wp.pop("last", None)
        wp.update(self._wizard.gnom_pane.gnom_params())
        self._state.monodisperse_wizard_params = wp
        mode = self._wizard.shape_pane.shape_mode()
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(mode)
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        shapes = self._wizard.shape_pane.selected_shapes()
        if shapes:
            self._state.fit_bodies_shapes = list(shapes)
        self.persist_confs()

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
            # Fall back to live spins if wp not yet keyed.
            try:
                g_first, g_last = self._wizard.guinier_pane.first_last()
            except Exception:
                g_first, g_last = None, None
        gopts = {}
        if g_first is not None and g_last is not None:
            gopts["first"] = int(g_first)
            gopts["last"] = int(g_last)
        # Always take DATGNOM options from the GNOM pane so Guinier interval cannot leak in.
        try:
            dopts = dict(self._wizard.gnom_pane.gnom_params())
        except Exception:
            dopts = {k: wp[k] for k in ("rg_nm", "first", "last", "smooth") if wp.get(k) is not None}
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

    def apply_shape_mode(self, mode: str) -> None:
        try:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode(mode)
        except ValueError:
            self._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        shapes = self._wizard.shape_pane.selected_shapes()
        if shapes:
            self._state.fit_bodies_shapes = list(shapes)

    def store_guinier_interval(self, first: int, last: int) -> None:
        """Persist Guinier spins without touching DATGNOM first/last."""
        wp = dict(self._state.monodisperse_wizard_params or {})
        wp["guinier_first"] = int(first)
        wp["guinier_last"] = int(last)
        self._state.monodisperse_wizard_params = wp
        self.persist_confs()
