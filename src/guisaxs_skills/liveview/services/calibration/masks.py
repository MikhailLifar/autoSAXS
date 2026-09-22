from __future__ import annotations

from pathlib import Path
from typing import Optional

from autosaxs.core.integrator import IntegratorExtended

from ...session.state import LiveviewSessionState


def applied_mask_path(state: LiveviewSessionState) -> Optional[Path]:
    """Mask that will be applied (session override, else sibling effective_mask.npy)."""
    if state.mask_path is not None and state.mask_path.is_file():
        return state.mask_path
    if state.integrator_dir is not None and state.integrator_dir.is_dir():
        sibling = Path(IntegratorExtended.sibling_effective_mask_path(str(state.integrator_dir)))
        if sibling.is_file():
            return sibling
    return None
