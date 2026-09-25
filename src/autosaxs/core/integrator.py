from __future__ import annotations

import json
import os
from typing import Tuple

import fabio
import numpy as np
import pyFAI

from .utils import get_detector


class IntegratorExtended:
    """Calibrated geometry + optional in-memory mask for azimuthal integration.

    On disk, ``integrator/`` holds geometry only. Masks live *alongside* that
    directory as ``effective_mask.npy`` / ``auto_mask.npy`` (written by
    ``calibrate``). ``integrate`` loads the sibling effective mask by default,
    or replaces it entirely when ``--mask`` is given.
    """

    EFFECTIVE_MASK_FILENAME = "effective_mask.npy"
    AUTO_MASK_FILENAME = "auto_mask.npy"

    def __init__(self, ai_params, detector_params, mask, auto_mask=None):
        self.detector_params = detector_params
        self.ai_params = ai_params
        self.mask = mask
        self.auto_mask = auto_mask

        self.detector = get_detector(**detector_params)
        self.ai = pyFAI.AzimuthalIntegrator(detector=self.detector, **self.ai_params)

    def to_disk(self, directory):
        """Write geometry JSON only (no masks)."""
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "detector_params.json"), "w") as fwrite:
            json.dump(self.detector_params, fwrite)
        with open(os.path.join(directory, "ai_params.json"), "w") as fwrite:
            json.dump(self.ai_params, fwrite)

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

    @staticmethod
    def write_mask(path, mask):
        """Write a boolean mask; inverse of :meth:`read_mask` (``.msk`` flips axis 0)."""
        _, ext = os.path.splitext(path)
        arr = np.asarray(mask)
        if ext == ".npy":
            np.save(path, arr.astype(bool))
        elif ext == ".txt":
            np.savetxt(path, arr.astype(int), fmt="%d")
        elif ext == ".msk":
            data = np.flip(arr.astype(np.uint8), axis=0)
            fabio.fit2dmaskimage.Fit2dMaskImage(data=data).write(path)
        else:
            raise RuntimeError(f"Unsupported file extension for mask: {ext}")

    @classmethod
    def sibling_effective_mask_path(cls, integrator_dir: str) -> str:
        """Hardcoded path: ``{parent_of_integrator}/effective_mask.npy``."""
        parent = os.path.dirname(os.path.abspath(integrator_dir))
        return os.path.join(parent, cls.EFFECTIVE_MASK_FILENAME)

    @classmethod
    def sibling_auto_mask_path(cls, integrator_dir: str) -> str:
        """Hardcoded path: ``{parent_of_integrator}/auto_mask.npy``."""
        parent = os.path.dirname(os.path.abspath(integrator_dir))
        return os.path.join(parent, cls.AUTO_MASK_FILENAME)

    @classmethod
    def write_masks_alongside(
        cls,
        integrator_dir: str,
        *,
        effective_mask,
        auto_mask,
    ) -> Tuple[str, str]:
        """Write effective + auto masks next to ``integrator_dir``. Always both."""
        if effective_mask is None or auto_mask is None:
            raise ValueError("write_masks_alongside requires both effective_mask and auto_mask")
        eff_path = cls.sibling_effective_mask_path(integrator_dir)
        auto_path = cls.sibling_auto_mask_path(integrator_dir)
        os.makedirs(os.path.dirname(eff_path) or ".", exist_ok=True)
        np.save(eff_path, np.asarray(effective_mask, dtype=bool))
        np.save(auto_path, np.asarray(auto_mask, dtype=bool))
        return eff_path, auto_path

    @classmethod
    def from_disk(cls, directory):
        """Load geometry only; ``mask`` is None until the caller sets it."""
        with open(os.path.join(directory, "detector_params.json"), "r") as fread:
            detector_params = json.load(fread)
        with open(os.path.join(directory, "ai_params.json"), "r") as fread:
            ai_params = json.load(fread)

        return cls(
            ai_params=ai_params,
            detector_params=detector_params,
            mask=None,
            auto_mask=None,
        )

    def set_mask(self, mask_path: str, combine_with_prev=False):
        mask = IntegratorExtended.read_mask(mask_path)
        if combine_with_prev and self.mask is not None:
            self.mask = self.mask | mask
        else:
            self.mask = mask

    def integrate1d(self, saxs_2d, npt):
        # Pipeline convention: q in nm^-1, Rg in nm. Explicit unit ensures consistency
        # (pyFAI default is 2th_deg which would break Guinier/Porod analysis).
        q, I, sigma = self.ai.integrate1d(
            saxs_2d, npt=npt, mask=self.mask, error_model="poisson", unit="q_nm^-1"
        )
        return q, I, sigma
