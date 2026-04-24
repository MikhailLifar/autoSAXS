from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Union

from ..core.path_expression import (
    ConfigPathExpression,
    DatPathExpression,
    PathExpression,
    SingletonDatPathExpression,
    SingletonMaskPathExpression,
    SingletonPathExpression,
    SingletonTiffPathExpression,
    TiffPathExpression,
)

PathExpressionArg = Union[str, Path, PathExpression, List[str], List[Path], tuple]
SingletonPathExpressionArg = Union[str, Path, SingletonPathExpression]
ConfigPathExpressionArg = Union[str, Path, ConfigPathExpression]
TiffPathExpressionArg = Union[str, Path, TiffPathExpression]
SingletonTiffPathExpressionArg = Union[str, Path, SingletonTiffPathExpression]
DatPathExpressionArg = Union[str, Path, DatPathExpression]
SingletonDatPathExpressionArg = Union[str, Path, SingletonDatPathExpression]
SingletonMaskPathExpressionArg = Union[str, Path, SingletonMaskPathExpression]


def coerce_path_expression(value: PathExpressionArg) -> PathExpression:
    if isinstance(value, PathExpression):
        return value
    if isinstance(value, Path):
        return PathExpression(str(value))
    if isinstance(value, (list, tuple)):
        parts: List[str] = [str(x) for x in value]
        return PathExpression(", ".join(parts))
    return PathExpression(str(value))


def coerce_singleton_path_expression(value: SingletonPathExpressionArg) -> SingletonPathExpression:
    if isinstance(value, SingletonPathExpression):
        return value
    if isinstance(value, Path):
        return SingletonPathExpression(str(value))
    return SingletonPathExpression(str(value))


def coerce_config_path_expression(value: ConfigPathExpressionArg) -> ConfigPathExpression:
    if isinstance(value, ConfigPathExpression):
        return value
    if isinstance(value, Path):
        return ConfigPathExpression(str(value))
    return ConfigPathExpression(str(value))


def coerce_tiff_path_expression(value: TiffPathExpressionArg) -> TiffPathExpression:
    if isinstance(value, TiffPathExpression):
        return value
    if isinstance(value, Path):
        return TiffPathExpression(str(value))
    return TiffPathExpression(str(value))


def coerce_dat_path_expression(value: DatPathExpressionArg) -> DatPathExpression:
    if isinstance(value, DatPathExpression):
        return value
    if isinstance(value, Path):
        return DatPathExpression(str(value))
    return DatPathExpression(str(value))


def coerce_singleton_tiff_path_expression(value: SingletonTiffPathExpressionArg) -> SingletonTiffPathExpression:
    if isinstance(value, SingletonTiffPathExpression):
        return value
    if isinstance(value, Path):
        return SingletonTiffPathExpression(str(value))
    return SingletonTiffPathExpression(str(value))


def coerce_singleton_dat_path_expression(value: SingletonDatPathExpressionArg) -> SingletonDatPathExpression:
    if isinstance(value, SingletonDatPathExpression):
        return value
    if isinstance(value, Path):
        return SingletonDatPathExpression(str(value))
    return SingletonDatPathExpression(str(value))


def coerce_singleton_mask_expression(value: SingletonMaskPathExpressionArg) -> SingletonMaskPathExpression:
    if isinstance(value, SingletonMaskPathExpression):
        return value
    if isinstance(value, Path):
        return SingletonMaskPathExpression(str(value))
    return SingletonMaskPathExpression(str(value))


def coerce_optional_singleton_path_expression(
    value: Optional[SingletonPathExpressionArg],
) -> Optional[SingletonPathExpression]:
    if value is None:
        return None
    return coerce_singleton_path_expression(value)


def expand_files_from_unwrapped(items: List[str], *, kind: str) -> List[str]:
    """
    Expand an already-unwrapped PathExpression list into concrete file paths.

    kind:
      - "2d_tif": directories expand to '*.tif' (non-recursive)
      - "1d_dat": directories expand to '*.dat' (non-recursive)
    """
    out: List[str] = []
    for p in items:
        if os.path.isdir(p):
            if kind == "2d_tif":
                out.extend(
                    str(x)
                    for x in sorted(Path(p).iterdir())
                    if x.is_file() and x.suffix.lower() == ".tif"
                )
            elif kind == "1d_dat":
                out.extend(
                    str(x)
                    for x in sorted(Path(p).iterdir())
                    if x.is_file() and x.suffix.lower() == ".dat"
                )
            else:
                raise ValueError(f"Unknown kind: {kind!r}")
        else:
            if os.path.isfile(p):
                out.append(p)
    # Stable de-dupe while preserving order
    seen = set()
    deduped: List[str] = []
    for x in out:
        if x in seen:
            continue
        seen.add(x)
        deduped.append(x)
    if not deduped:
        raise FileNotFoundError("No input files found after expansion")
    return deduped

