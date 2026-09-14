from __future__ import annotations

import glob
import json
import os
from typing import Optional

import fabio
import numpy as np
import pyFAI

from .utils import get_detector


class IntegratorExtended:
    EFFECTIVE_MASK_BASENAME = "effective_mask"
    AUTO_MASK_BASENAME = "auto_mask"
    # Legacy name written by older autosaxs versions.
    LEGACY_MASK_BASENAME = "mask"

    def __init__(self, ai_params, detector_params, mask, auto_mask=None):
        self.detector_params = detector_params
        self.ai_params = ai_params
        self.mask = mask
        self.auto_mask = auto_mask

        self.detector = get_detector(**detector_params)
        self.ai = pyFAI.AzimuthalIntegrator(detector=self.detector, **self.ai_params)

    def to_disk(self, directory):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "detector_params.json"), "w") as fwrite:
            json.dump(self.detector_params, fwrite)
        with open(os.path.join(directory, "ai_params.json"), "w") as fwrite:
            json.dump(self.ai_params, fwrite)
        if self.mask is not None:
            np.save(
                os.path.join(directory, f"{self.EFFECTIVE_MASK_BASENAME}.npy"),
                self.mask,
            )
        if self.auto_mask is not None:
            np.save(
                os.path.join(directory, f"{self.AUTO_MASK_BASENAME}.npy"),
                self.auto_mask,
            )

    @staticmethod
    def read_mask(mask_path):
        _, ext = os.path.splitext(mask_path)
        if ext == ".npy":
            mask = np.load(mask_path).astype("bool")
        elif ext == ".txt":
            mask = np.loadtxt(mask_path).astype("bool")
        elif ext == ".msk":
            mask = fabio.open(mask_path).data.astype("bool")
            mask = np.flip(mask, axis=0)
        else:
            raise RuntimeError(f"Unsupported file extension for mask: {ext}")
        return mask

    @classmethod
    def _resolve_named_mask_path(cls, directory: str, basename: str) -> Optional[str]:
        pattern = os.path.join(directory, f"{basename}.*")
        matches = glob.glob(pattern)
        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        raise RuntimeError(f'Too many files match mask pattern "{pattern}"')

    @classmethod
    def _resolve_mask_path(cls, directory: str) -> Optional[str]:
        for basename in (cls.EFFECTIVE_MASK_BASENAME, cls.LEGACY_MASK_BASENAME):
            hit = cls._resolve_named_mask_path(directory, basename)
            if hit is not None:
                return hit
        return None

    @classmethod
    def resolve_auto_mask_path(cls, directory: str) -> Optional[str]:
        """Path to the auto-only mask written by calibrate, if present."""
        return cls._resolve_named_mask_path(directory, cls.AUTO_MASK_BASENAME)

    @classmethod
    def from_disk(cls, directory):
        with open(os.path.join(directory, "detector_params.json"), "r") as fread:
            detector_params = json.load(fread)
        with open(os.path.join(directory, "ai_params.json"), "r") as fread:
            ai_params = json.load(fread)

        auto_mask = None
        auto_path = cls.resolve_auto_mask_path(directory)
        if auto_path is not None:
            auto_mask = cls.read_mask(auto_path)

        obj = cls(
            ai_params=ai_params,
            detector_params=detector_params,
            mask=None,
            auto_mask=auto_mask,
        )

        mask_path = cls._resolve_mask_path(directory)
        if mask_path is not None:
            obj.set_mask(mask_path)

        return obj

    def set_mask(self, mask_path: str, combine_with_prev=False):
        mask = IntegratorExtended.read_mask(mask_path)
        if combine_with_prev and self.mask is not None:
            self.mask = self.mask | mask
        else:
            self.mask = mask

    def apply_user_mask_override(self, mask_path: str) -> None:
        """
        Apply a per-run user mask for integrate.

        If ``auto_mask`` is available (from calibrate's ``auto_mask.npy``), the
        effective mask becomes ``auto_mask | user``. Otherwise the user mask
        replaces the integrator mask as-is.
        """
        user = self.read_mask(mask_path)
        if self.auto_mask is not None:
            self.mask = np.asarray(self.auto_mask, dtype=bool) | np.asarray(user, dtype=bool)
        else:
            self.mask = np.asarray(user, dtype=bool)

    def integrate1d(self, saxs_2d, npt):
        # Pipeline convention: q in nm^-1, Rg in nm. Explicit unit ensures consistency
        # (pyFAI default is 2th_deg which would break Guinier/Porod analysis).
        q, I, sigma = self.ai.integrate1d(
            saxs_2d, npt=npt, mask=self.mask, error_model="poisson", unit="q_nm^-1"
        )
        return q, I, sigma
