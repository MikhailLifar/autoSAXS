"""McSAS3 library orchestration for form-free volume-weighted D(R)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any


def _ensure_sas_opencl_none() -> None:
    os.environ.setdefault("SAS_OPENCL", "none")


def _as_1d(arr: Any) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    return np.ravel(a)


def _clip_and_rebin(
    q: np.ndarray,
    I: np.ndarray,
    sigma: np.ndarray,
    *,
    q_min: Optional[float],
    q_max: Optional[float],
    nbins: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Tuple[float, float]]:
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    mask = np.isfinite(q) & np.isfinite(I) & np.isfinite(sigma) & (sigma > 0) & (q > 0)
    q, I, sigma = q[mask], I[mask], sigma[mask]
    if q.size < 4:
        raise ValueError("model_dr_mc: fewer than 4 valid (q, I, sigma) points after filtering")
    q_lo = float(np.nanmin(q) if q_min is None else q_min)
    q_hi = float(np.nanmax(q) if q_max is None else q_max)
    if not (q_lo < q_hi):
        raise ValueError(f"model_dr_mc: invalid q range [{q_lo}, {q_hi}]")
    clip = (q >= q_lo) & (q <= q_hi)
    q, I, sigma = q[clip], I[clip], sigma[clip]
    if q.size < 4:
        raise ValueError("model_dr_mc: fewer than 4 points in requested q range")
    # McData1D also rebins; we pass nbins/dataRange so McSAS owns the binning.
    return q, I, sigma, (q_lo, q_hi)


def _local_maxima(r: np.ndarray, d: np.ndarray) -> List[float]:
    r = np.asarray(r, dtype=float)
    d = np.asarray(d, dtype=float)
    if r.size < 3:
        return []
    peaks: List[float] = []
    for i in range(1, len(d) - 1):
        if d[i] > d[i - 1] and d[i] >= d[i + 1] and np.isfinite(d[i]) and d[i] > 0:
            peaks.append(float(r[i]))
    if not peaks and np.any(np.isfinite(d) & (d > 0)):
        peaks.append(float(r[int(np.nanargmax(d))]))
    return peaks


def run_mcsas3_dr(
    profile_path: str | Path,
    output_dir: str | Path,
    *,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    n_rep: int = 5,
    n_contrib: int = 300,
    conv_crit: float = 1.0,
    n_cores: int = 0,
    nbins: int = 100,
    n_bin: int = 50,
    max_iter: int = 20000,
    sld: float = 33.4,
    sld_solvent: float = 0.0,
) -> Dict[str, Any]:
    """
    Run McSAS3 optimize + histogram on a 1D profile.

    Returns a dict with arrays/tables needed for artifacts and plots (no files written
    except the McSAS HDF5 state under ``output_dir``).
    """
    _ensure_sas_opencl_none()
    from mcsas3.mc_analysis import McAnalysis
    from mcsas3.mc_data_1d import McData1D
    from mcsas3.mc_hat import McHat

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_path = Path(profile_path)

    q, I, sigma = load_saxs_1d_any(str(profile_path))
    q, I, sigma = ensure_q_nm(q, I, sigma)
    if sigma is None:
        sigma = 0.03 * np.maximum(np.abs(I), 1e-30)
    sigma = np.asarray(sigma, dtype=float)
    sigma = np.where(np.isfinite(sigma) & (sigma > 0), sigma, 0.03 * np.maximum(np.abs(I), 1e-30))

    q, I, sigma, q_range = _clip_and_rebin(
        q, I, sigma, q_min=q_min_nm, q_max=q_max_nm, nbins=int(nbins)
    )
    q_lo, q_hi = q_range

    df = pd.DataFrame({"Q": q, "I": I, "ISigma": sigma})
    state_path = output_dir / "mcsas_state.nxs"
    if state_path.is_file():
        state_path.unlink()

    mds = McData1D(df=df, nbins=int(nbins), dataRange=[q_lo, q_hi])
    mds.store(state_path)
    meas = mds.measData.copy()

    hat = McHat(
        seed=None,
        modelName="mcsas_sphere",
        nContrib=int(n_contrib),
        fitParameterLimits={"radius": "auto"},
        staticParameters={"sld": float(sld), "sld_solvent": float(sld_solvent)},
        maxIter=int(max_iter),
        convCrit=float(conv_crit),
        nRep=int(n_rep),
        nCores=int(n_cores),
    )
    hat.run(meas, state_path)

    # Resolved radius limits after auto-fill (π/qmax … π/qmin).
    r_limits = hat._modelArgs.get("fitParameterLimits", {}).get("radius")
    if isinstance(r_limits, (list, tuple)) and len(r_limits) == 2:
        r_min_nm = float(r_limits[0])
        r_max_nm = float(r_limits[1])
    else:
        q_meas = _as_1d(meas["Q"])
        r_min_nm = float(np.pi / np.max(q_meas))
        r_max_nm = float(np.pi / np.min(q_meas))

    hist_ranges = pd.DataFrame(
        [
            {
                "parameter": "radius",
                "nBin": int(n_bin),
                "binScale": "log",
                "presetRangeMin": r_min_nm,
                "presetRangeMax": r_max_nm,
                "binWeighting": "vol",
                "autoRange": True,
            }
        ]
    )
    mcres = McAnalysis(state_path, meas, hist_ranges, store=True)

    hist = mcres._averagedHistograms[0]
    r = np.asarray(hist["xMean"], dtype=float)
    dr = np.asarray(hist["xWidth"], dtype=float)
    d = np.asarray(hist["yMean"], dtype=float)
    d_std = np.asarray(hist["yStd"], dtype=float)

    q_fit = _as_1d(meas["Q"])
    I_exp = _as_1d(meas["I"])
    sigma_fit = _as_1d(meas["ISigma"])
    I_fit = np.asarray(mcres._averagedI["modelIMean"], dtype=float)
    I_fit_std = np.asarray(mcres._averagedI["modelIStd"], dtype=float)

    peaks = _local_maxima(r, d)
    mode_mean = None
    mode_mean_std = None
    mode_total = None
    if mcres._averagedModes is not None and len(mcres._averagedModes):
        row = mcres._averagedModes.iloc[0]
        try:
            mode_mean = float(row[("mean", "valMean")])
            mode_mean_std = float(row[("mean", "valStd")])
            mode_total = float(row[("totalValue", "valMean")])
        except Exception:
            pass

    gof_mean = None
    gof_std = None
    if mcres._averagedOpts is not None and "gof" in mcres._averagedOpts.index:
        gof_mean = float(mcres._averagedOpts.loc["gof", "valMean"])
        gof_std = float(mcres._averagedOpts.loc["gof", "valStd"])

    n_components = max(1, min(3, len(peaks))) if peaks else 1

    return {
        "state_path": str(state_path),
        "mcres": mcres,
        "meas": meas,
        "q_nm": q_fit,
        "I_exp": I_exp,
        "sigma": sigma_fit,
        "I_fit": I_fit,
        "I_fit_std": I_fit_std,
        "r_nm": r,
        "dr_nm": dr,
        "D": d,
        "D_std": d_std,
        "peaks_nm": peaks,
        "n_components_suggested": int(n_components),
        "mode_mean_nm": mode_mean,
        "mode_mean_std_nm": mode_mean_std,
        "mode_total": mode_total,
        "gof_mean": gof_mean,
        "gof_std": gof_std,
        "q_min_nm": q_lo,
        "q_max_nm": q_hi,
        "r_min_nm": r_min_nm,
        "r_max_nm": r_max_nm,
        "n_rep": int(n_rep),
        "n_contrib": int(n_contrib),
        "conv_crit": float(conv_crit),
        "n_cores": int(n_cores),
        "nbins": int(nbins),
        "n_bin": int(n_bin),
        "sld": float(sld),
        "sld_solvent": float(sld_solvent),
        "max_iter": int(max_iter),
        "debug_run_report": mcres.debugRunReport(),
        "debug_hist_report": mcres.debugReport(0),
    }
