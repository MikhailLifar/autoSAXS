from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ingest.sample_revision import (
    SampleRevision,
    is_dat_path,
    is_tiff_path,
    normalize_sample_path,
    sample_stem_from_path,
)
from .state import LiveviewIntakeMode


@dataclass(frozen=True)
class Sample:
    """Boarded liveview sample: frame TIFF or curve ``.dat`` with boarding."""

    path: str
    boarding: LiveviewIntakeMode
    stem: str
    revision: SampleRevision | None = None

    @property
    def key(self) -> str:
        return normalize_sample_path(self.path)

    def is_frame(self) -> bool:
        return self.boarding == LiveviewIntakeMode.FRAME_2D or is_tiff_path(self.path)

    def is_curve(self) -> bool:
        return self.boarding in (
            LiveviewIntakeMode.CURVE_1D,
            LiveviewIntakeMode.CURVE_SUB,
        ) or is_dat_path(self.path)

    @classmethod
    def from_path(
        cls,
        path: str,
        *,
        boarding: LiveviewIntakeMode,
        revision: SampleRevision | None = None,
    ) -> Sample:
        key = normalize_sample_path(path)
        return cls(
            path=key,
            boarding=boarding,
            stem=sample_stem_from_path(key),
            revision=revision,
        )

    def as_path(self) -> Path:
        return Path(self.path)
