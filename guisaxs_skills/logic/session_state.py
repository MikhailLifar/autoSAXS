from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import Artifact, RunState, SkillMeta


@dataclass
class SessionPathHints:
    """
    Session-wide path hints: a small set of semantic locations (2D image area, 1D profile area,
    integrator, mask file, config file, etc.). Any skill may read them when filling the form and
    update them after a successful run. There is no fixed pipeline order—only last-known-good
    values under the workdir.
    """

    integrator_dir: Optional[str] = None
    integrate_output_dir: Optional[str] = None
    subtract_output_dir: Optional[str] = None
    # Shared directory for 2D .tif workflows (calibrate / integrate / integrate_proxy / plot_2d); last valid primary path wins
    two_d_tif_dir: Optional[str] = None
    # Shared directory for 1D .dat profiles (subtract / plot / guinier / fit_*); last valid primary path wins
    one_d_profile_dir: Optional[str] = None
    # When set to an existing .dat file, analysis skills use it as the session default for ``profile``
    # before falling back to ``one_d_profile_dir`` (liveview: latest integrated in B/BD, subtracted in C/CD).
    preferred_profile_dat_path: Optional[str] = None
    # Liveview: path to the latest integrated sample .dat (buffer file-picker hint / browse anchor).
    last_integrated_dat_path: Optional[str] = None
    # Last mask file used (calibrate / integrate_proxy / any skill with optional mask)
    mask_file_path: Optional[str] = None
    # Last config file used (.conf for calibrate, YAML for model_mixture, etc.)
    config_file_path: Optional[str] = None


@dataclass
class SessionState:
    workdir: Path
    selected_skill: Optional[SkillMeta] = None
    run_state: RunState = RunState.IDLE
    stdout: str = ""
    stderr: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Artifact] = field(default_factory=list)
    selected_artifact_path: Optional[str] = None
    # Per-skill persisted form state (session-only)
    form_state_by_skill: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # Shared semantic path hints (updated on successful runs; read by any skill's form defaults)
    path_hints: SessionPathHints = field(default_factory=SessionPathHints)

