"""Deprecated alias for :func:`autosaxs.skill.model_mixture.model_mixture`."""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Union

from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
)
from ..model_mixture import (
    _resolve_mixture_radius_params,
    _rmax_nm_from_fit_sizes,
    model_mixture,
)
from ..model_mixture import _model_mixture_paths as _fit_mixture_paths  # noqa: F401

__all__ = [
    "fit_mixture",
    "_resolve_mixture_radius_params",
    "_rmax_nm_from_fit_sizes",
    "_fit_mixture_paths",
]


def fit_mixture(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    maxit: Optional[int] = None,
    r_min: Optional[float] = None,
    r_max: Optional[float] = None,
    poly_min: Optional[float] = None,
    poly_max: Optional[float] = None,
    max_nph: Optional[int] = None,
    plot_I_q: Optional[bool] = None,
    plot_logI_logq: Optional[bool] = None,
    plot_logI_q: Optional[bool] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    Deprecated alias for ``model_mixture``.

    Prefer ``autosaxs.skill.model_mixture`` / ``autosaxs model-mixture``.
    """
    warnings.warn(
        "fit_mixture is deprecated; use model_mixture instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return model_mixture(
        profile,
        output_dir,
        config_path=config_path,
        q_min_nm=q_min_nm,
        q_max_nm=q_max_nm,
        maxit=maxit,
        r_min=r_min,
        r_max=r_max,
        poly_min=poly_min,
        poly_max=poly_max,
        max_nph=max_nph,
        plot_I_q=plot_I_q,
        plot_logI_logq=plot_logI_logq,
        plot_logI_q=plot_logI_q,
        use_cache=use_cache,
    )
