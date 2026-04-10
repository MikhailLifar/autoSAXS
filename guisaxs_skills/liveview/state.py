from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class LiveviewState(str, Enum):
    A = "A"
    B = "B"
    BD = "BD"
    C = "C"
    CD = "CD"


@dataclass
class LiveviewSessionState:
    watchdir: Path

    # Calibration artifacts
    integrator_dir: Optional[Path] = None
    # Last integrated 1D curve from the live pipeline (for fit_distances profile default / hints).
    last_integrated_dat_path: Optional[Path] = None
    # Last subtracted 1D curve (state CD: preferred default profile for fit_distances wizard).
    last_subtracted_dat_path: Optional[Path] = None

    # Buffer + subtraction config
    buffer_dat_path: Optional[Path] = None
    subtract_conf_path: Optional[Path] = None

    # Modeling config
    fit_distances_enabled: bool = False
    fit_distances_conf_path: Optional[Path] = None

    def current_state(self) -> LiveviewState:
        calibrated = self.integrator_dir is not None
        subtraction = self.buffer_dat_path is not None and self.subtract_conf_path is not None
        if not calibrated:
            return LiveviewState.A
        if subtraction:
            return LiveviewState.CD if self.fit_distances_enabled else LiveviewState.C
        return LiveviewState.BD if self.fit_distances_enabled else LiveviewState.B

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

