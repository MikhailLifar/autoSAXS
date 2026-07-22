"""Plots for model_dr_mc (fit and D(R)±σ)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np


def write_fit_png(
    *,
    q_nm: np.ndarray,
    I_exp: np.ndarray,
    I_fit: np.ndarray,
    I_fit_std: Optional[np.ndarray],
    out_path: str | Path,
    sigma: Optional[np.ndarray] = None,
    title: str = "McSAS3 fit",
) -> str:
    from autosaxs.core.viewer import write_iq_fit_comparison_png

    out_path = Path(out_path)
    return write_iq_fit_comparison_png(
        str(out_path),
        np.asarray(q_nm, dtype=float),
        np.asarray(I_exp, dtype=float),
        [(np.asarray(I_fit, dtype=float), "McSAS mean fit")],
        sigma=sigma,
        title=title,
        primary_fit_std=None if I_fit_std is None else np.asarray(I_fit_std, dtype=float),
    )


def write_dr_png(
    *,
    r_nm: np.ndarray,
    dr_nm: np.ndarray,
    D: np.ndarray,
    D_std: np.ndarray,
    out_path: str | Path,
    peaks_nm: Optional[list] = None,
    mode_mean_nm: Optional[float] = None,
    title: str = "McSAS3 volume-weighted D(R)",
) -> str:
    out_path = Path(out_path)
    r = np.asarray(r_nm, dtype=float)
    w = np.asarray(dr_nm, dtype=float)
    y = np.asarray(D, dtype=float)
    e = np.asarray(D_std, dtype=float)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.bar(
        r,
        y,
        width=np.maximum(w, 1e-12),
        align="center",
        color="C0",
        alpha=0.55,
        edgecolor="C0",
        linewidth=0.4,
        label="volume-weighted D(R)",
    )
    ax.errorbar(r, y, yerr=e, fmt="none", ecolor="0.2", elinewidth=0.9, capsize=1.5, label=r"$\pm\sigma$ (reps)")
    if peaks_nm:
        for i, pk in enumerate(peaks_nm):
            ax.axvline(float(pk), color="C3", ls="--", lw=1.0, alpha=0.8, label="peak" if i == 0 else None)
    if mode_mean_nm is not None and np.isfinite(mode_mean_nm):
        ax.axvline(float(mode_mean_nm), color="C2", ls=":", lw=1.4, label=fr"mode mean={mode_mean_nm:.3g} nm")
    ax.set_xscale("log")
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel(r"$D(R)$ (vol. weighted)")
    ax.set_ylim(0, None)
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


def write_all_plots(result: Dict[str, Any], output_dir: str | Path, base: str) -> Dict[str, str]:
    output_dir = Path(output_dir)
    fit_path = output_dir / f"{base}_mcsas_fit.png"
    dr_path = output_dir / f"{base}_mcsas_dr.png"

    gof = result.get("gof_mean")
    title_fit = "McSAS3 fit"
    if gof is not None and np.isfinite(float(gof)):
        title_fit = f"McSAS3 fit (gof={float(gof):.3g})"

    write_fit_png(
        q_nm=result["q_nm"],
        I_exp=result["I_exp"],
        I_fit=result["I_fit"],
        I_fit_std=result.get("I_fit_std"),
        sigma=result.get("sigma"),
        out_path=fit_path,
        title=title_fit,
    )
    write_dr_png(
        r_nm=result["r_nm"],
        dr_nm=result["dr_nm"],
        D=result["D"],
        D_std=result["D_std"],
        out_path=dr_path,
        peaks_nm=result.get("peaks_nm") or [],
        mode_mean_nm=result.get("mode_mean_nm"),
    )
    return {
        "fit_png_path": str(fit_path),
        "dr_png_path": str(dr_path),
    }
