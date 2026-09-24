from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

# Default BODIES shapes for liveview “primitives” when no model_bodies.conf exists yet.
DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES: List[str] = ["ellipsoid"]


class LiveviewWatchMode(str, Enum):
    """Filesystem watch layout: flat (top-level) vs recursive tree (frames)."""

    FLAT = "flat"
    TREE = "tree"


class LiveviewIntakeMode(str, Enum):
    """Where new samples board the live pipeline (also Sample.boarding)."""

    FRAME_2D = "frame_2d"
    CURVE_1D = "curve_1d"
    CURVE_SUB = "curve_sub"


class MonodisperseShapeMode(str, Enum):
    NONE = "none"
    DAMMIF = "dammif"
    BODIES = "bodies"
    DENSS = "denss"


class PolydisperseMixtureMode(str, Enum):
    NONE = "none"
    MIXTURE = "mixture"


@dataclass
class LiveviewSessionState:
    """Session facts owner: intake, auto_processing, calib, buffer, analysis."""

    watchdir: Path
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.TREE
    intake_mode: LiveviewIntakeMode = LiveviewIntakeMode.FRAME_2D
    # Auto/Manual gate (in-memory only): True = auto queue may advance; False = manual.
    # Always starts Auto on launch; not written to session.yaml.
    auto_processing: bool = True

    integrator_dir: Optional[Path] = None
    calibration_curve_plot_path: Optional[Path] = None
    calibration_refined_yml_path: Optional[Path] = None
    last_integrated_dat_path: Optional[Path] = None
    last_subtracted_dat_path: Optional[Path] = None

    mask_path: Optional[Path] = None  # applied mask (integrate --mask / UI overlay); not auto-only
    mask_preview_path: Optional[Path] = None

    buffer_dat_path: Optional[Path] = None
    subtract_options: Optional[Dict[str, Any]] = None

    monodisperse_armed: bool = False
    polydisperse_armed: bool = False
    fit_guinier_mono_conf_path: Optional[Path] = None
    fit_guinier_poly_conf_path: Optional[Path] = None
    fit_distances_conf_path: Optional[Path] = None
    fit_sizes_conf_path: Optional[Path] = None
    model_mixture_config_path: Optional[Path] = None
    model_mixture_options: Optional[Dict[str, Any]] = None
    model_bodies_conf_path: Optional[Path] = None
    model_bodies_shapes: Optional[List[str]] = None
    monodisperse_shape_mode: MonodisperseShapeMode = MonodisperseShapeMode.NONE
    model_dam_n_runs: int = 1
    model_density_mode: str = "pilot"
    model_density_denss_mode: str = "fast"
    model_density_n_maps: int = 20
    monodisperse_wizard_params: Optional[Dict[str, Any]] = None
    polydisperse_mixture_mode: PolydisperseMixtureMode = PolydisperseMixtureMode.NONE
    polydisperse_window_params: Optional[Dict[str, Any]] = None

    def is_calibrated(self) -> bool:
        return self.integrator_dir is not None

    def buffer_ready(self) -> bool:
        return self.buffer_dat_path is not None and self.subtract_options is not None

    def analysis_enabled(self) -> bool:
        return bool(self.monodisperse_armed or self.polydisperse_armed)

    def is_auto_processing(self) -> bool:
        return bool(self.auto_processing)

    def set_auto_processing(self, enabled: bool) -> None:
        self.auto_processing = bool(enabled)

    def reset_calibration(self) -> None:
        """Clear calibration and buffer; disarm analysis."""
        self.integrator_dir = None
        self.calibration_curve_plot_path = None
        self.calibration_refined_yml_path = None
        self.buffer_dat_path = None
        self.subtract_options = None
        self.last_integrated_dat_path = None
        self.last_subtracted_dat_path = None
        self._disarm_analysis()

    def reset_buffer(self) -> None:
        """Clear buffer/subtract; disarm analysis; keep calibration."""
        self.buffer_dat_path = None
        self.subtract_options = None
        self.last_subtracted_dat_path = None
        self._disarm_analysis()

    def _disarm_analysis(self) -> None:
        self.monodisperse_armed = False
        self.polydisperse_armed = False
        self.model_bodies_shapes = None
        self.model_bodies_conf_path = None
        self.fit_guinier_mono_conf_path = None
        self.fit_guinier_poly_conf_path = None
        self.monodisperse_shape_mode = MonodisperseShapeMode.NONE
        self.model_dam_n_runs = 1
        self.model_density_mode = "pilot"
        self.model_density_denss_mode = "fast"
        self.model_density_n_maps = 20
        self.monodisperse_wizard_params = None
        self.polydisperse_mixture_mode = PolydisperseMixtureMode.NONE
        self.polydisperse_window_params = None
        self.model_mixture_options = None

    def preferred_profile_path(
        self,
        *,
        boarding: Optional[LiveviewIntakeMode] = None,
    ) -> Optional[Path]:
        """Preferred analysis profile from last_* hints and boarding/buffer."""
        from ..ingest.curve_classify import usable_analysis_curve_path

        prefer_sub = boarding == LiveviewIntakeMode.CURVE_SUB or self.buffer_ready()
        ordered: list[Optional[Path]]
        if prefer_sub:
            ordered = [self.last_subtracted_dat_path, self.last_integrated_dat_path]
        else:
            ordered = [self.last_integrated_dat_path, self.last_subtracted_dat_path]
        for cand in ordered:
            if cand is None:
                continue
            usable = usable_analysis_curve_path(cand)
            if usable:
                return Path(usable)
        return None
