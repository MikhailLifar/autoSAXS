"""Deprecated alias for :func:`autosaxs.skill.model_dam.model_dam`."""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Union

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonPathExpressionArg,
)
from .model_dam import model_dam


def fit_dammif(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    gnom_path: Optional[SingletonPathExpressionArg] = None,
    dammif_reps_num: int = 1,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    Deprecated alias for ``model_dam``.

    Prefer ``autosaxs.skill.model_dam`` / ``autosaxs model-dam``.
    ``dammif_reps_num`` maps to ``n_runs``; mode defaults to ``fast``.
    """
    warnings.warn(
        "fit_dammif is deprecated; use model_dam (n_runs=..., dammif_mode=...) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return model_dam(
        profile,
        output_dir,
        config_path=config_path,
        gnom_path=gnom_path,
        n_runs=int(dammif_reps_num),
        dammif_mode="fast",
        use_cache=use_cache,
    )
