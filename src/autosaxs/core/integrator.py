from __future__ import annotations

import glob
import json
import os

import fabio
import numpy as np
import pyFAI

from .utils import get_detector


class IntegratorExtended:
    def __init__(self, ai_params, detector_params, mask):
        self.detector_params = detector_params
        self.ai_params = ai_params
        self.mask = mask

        self.detector = get_detector(**detector_params)
        self.ai = pyFAI.AzimuthalIntegrator(detector=self.detector, **self.ai_params)

    def to_disk(self, directory):
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "detector_params.json"), "w") as fwrite:
            json.dump(self.detector_params, fwrite)
        with open(os.path.join(directory, "ai_params.json"), "w") as fwrite:
            json.dump(self.ai_params, fwrite)
        if self.mask is not None:
            np.save(os.path.join(directory, "mask.npy"), self.mask)

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
    def from_disk(cls, directory):
        with open(os.path.join(directory, "detector_params.json"), "r") as fread:
            detector_params = json.load(fread)
        with open(os.path.join(directory, "ai_params.json"), "r") as fread:
            ai_params = json.load(fread)

        obj = cls(ai_params=ai_params, detector_params=detector_params, mask=None)

        mask_pattern = os.path.join(directory, "mask.*")
        mask_path = glob.glob(mask_pattern)
        if len(mask_path) == 0:
            return obj
        if len(mask_path) == 1:
            mask_path, = mask_path
            obj.set_mask(mask_path)
        else:
            raise RuntimeError(f'Too many files match mask pattern "{mask_pattern}"')

        return obj

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
