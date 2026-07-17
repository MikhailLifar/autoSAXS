from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from ....session.state import LiveviewSessionState
from ...wizards.right import LIVEVIEW_MIXTURE_YML_NAME

try:
    from autosaxs.skill.fit_bodies import BODIES_SHAPES_LIST
except Exception:
    BODIES_SHAPES_LIST = [
        "cylinder",
        "dumbbell",
        "ellipsoid",
        "elliptic-cylinder",
        "hollow-cylinder",
        "hollow-sphere",
        "parallelepiped",
        "rotation-ellipsoid",
    ]


class RightPanelConfigRestore:
    def __init__(self, *, state: LiveviewSessionState) -> None:
        self._state = state
        self._fit_mixture_saved_mixture_params: Optional[dict[str, Any]] = None

    @property
    def fit_mixture_saved_mixture_params(self) -> Optional[dict[str, Any]]:
        return self._fit_mixture_saved_mixture_params

    def reload_all(self) -> None:
        self.restore_fit_guinier()
        self.restore_fit_distances()
        self._scrub_guinier_interval_from_gnom_params()
        self.restore_fit_sizes()
        self.restore_bodies()
        self.restore_mixture()

    def _scrub_guinier_interval_from_gnom_params(self) -> None:
        """Drop DATGNOM last when it equals the Guinier last (legacy shared-key pollution)."""
        wp = dict(self._state.monodisperse_wizard_params or {})
        g_last = wp.get("guinier_last")
        d_last = wp.get("last")
        if g_last is None or d_last is None:
            return
        try:
            if int(g_last) == int(d_last):
                wp.pop("last", None)
                self._state.monodisperse_wizard_params = wp
        except (TypeError, ValueError):
            return

    def restore_fit_guinier(self) -> None:
        wd = self._state.watchdir
        if self._state.fit_guinier_mono_conf_path is None:
            for conf in (
                wd / "guinier_mono" / "guinier.conf",
                wd / "guinier" / "guinier.conf",
                wd / "runs" / "guinier.conf",
            ):
                if conf.is_file():
                    self._state.fit_guinier_mono_conf_path = conf
                    break
        gpath = self._state.fit_guinier_mono_conf_path
        if gpath is not None and gpath.is_file():
            self._merge_monodisperse_conf(
                gpath,
                keys=("first", "last"),
                rename={"first": "guinier_first", "last": "guinier_last"},
            )

        if self._state.fit_guinier_poly_conf_path is None:
            for conf in (wd / "guinier_poly" / "guinier.conf",):
                if conf.is_file():
                    self._state.fit_guinier_poly_conf_path = conf
                    break
        ppath = self._state.fit_guinier_poly_conf_path
        if ppath is not None and ppath.is_file():
            self._merge_polydisperse_guinier_conf(ppath)

    def _merge_polydisperse_guinier_conf(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return
        if not isinstance(data, dict):
            return
        wp = dict(self._state.polydisperse_window_params or {})
        if data.get("first") is not None:
            wp["guinier_first"] = data["first"]
        if data.get("last") is not None:
            wp["guinier_last"] = data["last"]
        if wp:
            self._state.polydisperse_window_params = wp

    def restore_fit_distances(self) -> None:
        if self._state.fit_distances_conf_path is not None:
            return
        wd = self._state.watchdir
        for conf in (wd / "fit_distances" / "fit_distances.conf", wd / "runs" / "fit_distances.conf"):
            if conf.is_file():
                self._state.fit_distances_conf_path = conf
                self._merge_monodisperse_conf(conf, keys=("rg_nm", "first", "last", "smooth"))
                break

    def _merge_monodisperse_conf(
        self,
        path: Path,
        *,
        keys: tuple[str, ...],
        rename: dict[str, str] | None = None,
    ) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return
        if not isinstance(data, dict):
            return
        wp = dict(self._state.monodisperse_wizard_params or {})
        rename = rename or {}
        for key in keys:
            if data.get(key) is not None:
                wp[rename.get(key, key)] = data[key]
        if wp:
            self._state.monodisperse_wizard_params = wp

    def restore_fit_sizes(self) -> None:
        wd = self._state.watchdir
        if self._state.fit_sizes_conf_path is None:
            p = wd / "fit_sizes" / "fit_sizes.conf"
            if p.is_file():
                self._state.fit_sizes_conf_path = p
        spath = self._state.fit_sizes_conf_path
        if spath is not None and spath.is_file():
            self._merge_polydisperse_sizes_conf(spath)

    def _merge_polydisperse_sizes_conf(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            return
        if not isinstance(data, dict):
            return
        wp = dict(self._state.polydisperse_window_params or {})
        for key in ("first", "last", "rmin_nm", "rmax_nm", "alpha"):
            if data.get(key) is not None:
                wp[key] = data[key]
        if wp.get("first") is None:
            wp["first"] = 1
        self._state.polydisperse_window_params = wp

    def restore_bodies(self) -> None:
        if self._state.fit_bodies_conf_path is not None:
            p = self._state.fit_bodies_conf_path
            if p.is_file():
                self._ingest_bodies_conf_file(p)
            return
        p = self._state.watchdir / "fit_bodies" / "fit_bodies.conf"
        if p.is_file():
            self._state.fit_bodies_conf_path = p
            self._ingest_bodies_conf_file(p)

    def restore_mixture(self) -> None:
        wd = self._state.watchdir
        live = (wd / "mixture" / LIVEVIEW_MIXTURE_YML_NAME).resolve()
        if live.is_file():
            self._state.fit_mixture_config_path = live
            self._ingest_mixture_yaml(live)
            return
        p = self._state.fit_mixture_config_path
        if p is not None and p.is_file():
            self._ingest_mixture_yaml(p)
            return
        side = wd / "mixture" / "liveview_mixture_config.txt"
        if side.is_file():
            raw = side.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if raw:
                ext = Path(raw[0].strip()).expanduser()
                path = ext.resolve() if ext.is_absolute() else (wd / ext).resolve()
                if path.is_file():
                    self._state.fit_mixture_config_path = path
                    self._ingest_mixture_yaml(path)

    def _ingest_bodies_conf_file(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            self._state.fit_bodies_shapes = None
            return
        if not isinstance(data, dict):
            self._state.fit_bodies_shapes = None
            return
        sh = data.get("shapes")
        if not isinstance(sh, list) or not sh:
            self._state.fit_bodies_shapes = None
            return
        out: list[str] = []
        for x in sh:
            if not isinstance(x, str):
                continue
            t = x.strip()
            if t in BODIES_SHAPES_LIST and t not in out:
                out.append(t)
        self._state.fit_bodies_shapes = out if out else None

    def _ingest_mixture_yaml(self, yml_path: Path) -> None:
        try:
            raw = yml_path.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                return
            m = data.get("fit_mixture")
            if not isinstance(m, dict):
                m = data.get("mixture")
            if not isinstance(m, dict):
                return
            self._state.fit_mixture_options = {str(k): v for k, v in m.items()}
            # Pane exposes max_nph, optional r_max/poly_max, optional q bounds.
            keys = ("max_nph", "r_max", "poly_max")
            self._fit_mixture_saved_mixture_params = {k: m[k] for k in keys if k in m}
            wp = dict(self._state.polydisperse_window_params or {})
            mix = {k: m[k] for k in (*keys, "q_min_nm", "q_max_nm") if k in m}
            if mix:
                wp["mixture"] = mix
                self._state.polydisperse_window_params = wp
        except Exception:
            pass
