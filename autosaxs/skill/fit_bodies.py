"""Deprecated alias for :func:`autosaxs.skill.model_bodies.model_bodies`."""

from __future__ import annotations

import warnings
from typing import Dict, List, Optional, Union

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
)
from .model_bodies import BODIES_SHAPES_LIST, model_bodies

__all__ = ["BODIES_SHAPES_LIST", "fit_bodies"]


def fit_bodies(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    shapes: Optional[List[str]] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    Deprecated alias for ``model_bodies``.

    Prefer ``autosaxs.skill.model_bodies`` / ``autosaxs model-bodies``.
    """
    warnings.warn(
        "fit_bodies is deprecated; use model_bodies instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return model_bodies(
        profile,
        output_dir,
        config_path=config_path,
        shapes=shapes,
        first=first,
        last=last,
        use_cache=use_cache,
    )
