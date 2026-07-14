from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default BODIES shapes for liveview “primitives” when no fit_bodies.conf exists yet.
DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES: List[str] = ["ellipsoid"]


class LiveviewState(str, Enum):
    A = "A"
    B = "B"
    BD = "BD"
    C = "C"
    CD = "CD"


class LiveviewWatchMode(str, Enum):
    """Filesystem watch layout: flat (top-level) vs recursive tree."""

    FLAT = "flat"
    TREE = "tree"


class AnalysisMode(str, Enum):
    """Right-column analysis mode (spec §6.4). First combo item is OFF."""

    OFF = "off"
    MONODISPERSE = "monodisperse"
    POLYDISPERSE_DR = "polydisperse_dr"
    POLYDISPERSE_MIXTURE = "polydisperse_mixture"

    def is_active(self) -> bool:
        return self != AnalysisMode.OFF

    @classmethod
    def from_legacy_value(cls, raw: str) -> AnalysisMode:
        """Map removed monodisperse mode ids to ``MONODISPERSE``."""
        legacy = {
            "monodisperse_pr": cls.MONODISPERSE,
            "monodisperse_dam": cls.MONODISPERSE,
            "monodisperse_bodies": cls.MONODISPERSE,
        }
        try:
            return cls(str(raw))
        except ValueError:
            return legacy.get(str(raw).strip().lower(), cls.OFF)


class MonodisperseShapeMode(str, Enum):
    NONE = "none"
    DAMMIF = "dammif"
    BODIES = "bodies"


@dataclass
class LiveviewSessionState:
    watchdir: Path
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT

    # Calibration artifacts
    integrator_dir: Optional[Path] = None
    # Last calibration curve PNG (for left-panel preview; persisted with session file).
    calibration_curve_plot_path: Optional[Path] = None
    # Refined geometry YAML (``refined.yml`` from calibrate); used for parameter table.
    calibration_refined_yml_path: Optional[Path] = None
    # Last integrated 1D curve from the live pipeline (for analysis wizard hints).
    last_integrated_dat_path: Optional[Path] = None
    # Last subtracted 1D curve (state CD: preferred default profile for wizards).
    last_subtracted_dat_path: Optional[Path] = None

    # Buffer + subtraction config
    buffer_dat_path: Optional[Path] = None
    # Subtract options as a dict (method, q_min/q_max, forms, etc.); also persisted under watchdir.
    subtract_options: Optional[Dict[str, Any]] = None

    # Analysis config
    analysis_mode: AnalysisMode = AnalysisMode.OFF
    fit_guinier_conf_path: Optional[Path] = None
    fit_distances_conf_path: Optional[Path] = None
    fit_sizes_conf_path: Optional[Path] = None
    # Optional ``mixture/liveview_mixture.yml`` from wizard Apply (persistence only).
    fit_mixture_config_path: Optional[Path] = None
    # CLI options for ``fit_mixture`` (q range, MIXTURE params). None → bundled defaults, full q.
    fit_mixture_options: Optional[Dict[str, Any]] = None
    # Written by the primitives wizard to fit_bodies/fit_bodies.conf (shape subset).
    fit_bodies_conf_path: Optional[Path] = None
    # Subset of BODIES model names; None or [] means pipeline uses DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES.
    fit_bodies_shapes: Optional[List[str]] = None
    monodisperse_shape_mode: MonodisperseShapeMode = MonodisperseShapeMode.NONE
    monodisperse_wizard_params: Optional[Dict[str, Any]] = None

    def analysis_enabled(self) -> bool:
        return self.analysis_mode.is_active()

    def current_state(self) -> LiveviewState:
        calibrated = self.integrator_dir is not None
        subtraction = self.buffer_dat_path is not None and self.subtract_options is not None
        ae = self.analysis_enabled()
        if not calibrated:
            return LiveviewState.A
        if subtraction:
            return LiveviewState.CD if ae else LiveviewState.C
        return LiveviewState.BD if ae else LiveviewState.B

    def reset_calibration_to_state_a(self) -> None:
        """Clear calibration (and buffer); session becomes state A; call sites should set analysis to Off."""
        self.integrator_dir = None
        self.calibration_curve_plot_path = None
        self.calibration_refined_yml_path = None
        self.buffer_dat_path = None
        self.subtract_options = None
        self.last_integrated_dat_path = None
        self.last_subtracted_dat_path = None
        self.analysis_mode = AnalysisMode.OFF
        self.fit_bodies_shapes = None
        self.fit_bodies_conf_path = None
        self.fit_guinier_conf_path = None
        self.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        self.monodisperse_wizard_params = None
        self.fit_mixture_options = None

    def reset_buffer_to_state_b(self) -> None:
        """Clear buffer/subtract settings; analysis Off; remain calibrated (state B if integrator is set)."""
        self.buffer_dat_path = None
        self.subtract_options = None
        self.last_subtracted_dat_path = None
        self.analysis_mode = AnalysisMode.OFF
        self.fit_bodies_shapes = None
        self.fit_bodies_conf_path = None
        self.fit_guinier_conf_path = None
        self.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        self.monodisperse_wizard_params = None

    def reset_for_new_watchdir(self, watchdir: Path) -> None:
        """Point session at a new folder and clear in-memory state (then load ``.guisaxs_liveview/`` if present)."""
        self.watchdir = watchdir.expanduser().resolve()
        self.integrator_dir = None
        self.calibration_curve_plot_path = None
        self.calibration_refined_yml_path = None
        self.last_integrated_dat_path = None
        self.last_subtracted_dat_path = None
        self.buffer_dat_path = None
        self.subtract_options = None
        self.analysis_mode = AnalysisMode.OFF
        self.fit_distances_conf_path = None
        self.fit_guinier_conf_path = None
        self.fit_sizes_conf_path = None
        self.fit_mixture_config_path = None
        self.fit_mixture_options = None
        self.fit_bodies_shapes = None
        self.fit_bodies_conf_path = None
        self.fit_guinier_conf_path = None
        self.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        self.monodisperse_wizard_params = None

    def default_fit_distances_profile_path(self) -> Optional[Path]:
        """State B/BD: last integrated .dat. State C/CD: last subtracted .dat (else last integrated)."""
        st = self.current_state()
        if st in (LiveviewState.C, LiveviewState.CD):
            ls = self.last_subtracted_dat_path
            if ls is not None and ls.is_file():
                return ls
            li = self.last_integrated_dat_path
            if li is not None and li.is_file():
                return li
            return None
        if st in (LiveviewState.B, LiveviewState.BD):
            li = self.last_integrated_dat_path
            if li is not None and li.is_file():
                return li
        return None
