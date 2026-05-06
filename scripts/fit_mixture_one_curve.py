#!/usr/bin/env python3
"""
Fit one subtracted SAXS curve with ATSAS MIXTURE (spheres only):
1-, 2-, and 3-phase mixtures × Gaussian and Schultz-Zimm distributions (6 runs).
Produce comparison plot, distribution plots, and CSV of results.

Input and all outputs use q in nm^-1 (MIXTURE used with scale 2 = 1/nm).

Usage:
  python fit_mixture_one_curve.py [path_to_subtracted.dat]
Default input: ../../temp/260226_to_Konarev/subtracted/sub_Pt_NPs_insitu_110C_00060_sample.dat
Outputs (plots, CSV, MIXTURE run dirs) go to --outdir. Example:
  python fit_mixture_one_curve.py --outdir temp/260226_to_Konarev/mixture_work
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from shutil import which
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

from autosaxs.utils import calc_chi2, gaussian_pdf, schultz_pdf

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DAT = SCRIPT_DIR / "../../temp/260226_to_Konarev/subtracted/sub_Pt_NPs_insitu_110C_00060_sample.dat"
MIXTURE_EXE = "mixture"  # or "Mixture" on Windows
# q: all I/O in nm^-1. Use scale 2 so MIXTURE reads and writes q in 1/nm (best fit in practice).
Q_SCALE = 2  # 2 = 1/nm
MAXIT = 80
SYSTEM_CONCENTRATION = 0.15  # volume fraction of particles for structure factor
# Sphere bounds (in Angstroms for MIXTURE)
R_MIN, R_MAX = 15.0, 120.0
POLY_MIN, POLY_MAX = 0.5, 25.0
RHS_MARGIN = 5.0  # Rhs >= Rout + margin


def load_subtracted_dat(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load q, intensity, sigma from a subtracted .dat (YAML header + CSV)."""
    with open(path) as f:
        raw = f.read()
    if "---" in raw and "q,intensity" in raw:
        parts = raw.split("# Data in CSV format", 1)
        csv_part = parts[1].strip()
        df = pd.read_csv(pd.io.common.StringIO(csv_part))
    else:
        df = pd.read_csv(path, comment="#")
    q = df["q"].to_numpy().astype(np.float64)
    I = df["intensity"].to_numpy().astype(np.float64)
    sigma = df["sigma"].to_numpy().astype(np.float64) if "sigma" in df.columns else 0.03 * np.abs(I)
    return q, I, sigma


def write_atsas_dat(path: Path, q: np.ndarray, I: np.ndarray, sigma: np.ndarray) -> None:
    """Write 3-column (q, I, sigma) no-header file for ATSAS."""
    with open(path, "w") as f:
        for i in range(len(q)):
            f.write(f"{q[i]}\t{I[i]}\t{sigma[i]}\n")


def sphere_block(
    vol: float,
    vol_lo: float,
    vol_hi: float,
    rout: float,
    rout_lo: float,
    rout_hi: float,
    poly: float,
    poly_lo: float,
    poly_hi: float,
    dist_type: int,
    rhs_lo: float,
    rhs_hi: float,
) -> list[str]:
    """One SPHERE phase block (Rin=0, rho_in=0, rho_out=1, tau=0 no interactions)."""
    lines = [
        "SPHERE",
        f"  {vol:.4f}  {vol_lo:.2f}  {vol_hi:.2f}    !! volume fraction",
        "  0.0    0.0    0.0      !! inner shell radius",
        "  0.0    0.0    0.0      !! inner contrast",
        f"  {rout:.2f}  {rout_lo:.2f}  {rout_hi:.2f}   !! outer shell radius (Angstrom)",
        "  1.0    1.0    1.0      !! outer contrast",
        f"  {poly:.2f}  {poly_lo:.2f}  {poly_hi:.2f}   !! polydispersity",
        f"  {rhs_hi:.2f}  {rhs_lo:.2f}  {rhs_hi:.2f}   !! hard sphere radius",
        f"  {dist_type}                        !! 1=Gauss 2=Schultz",
        "  0.0    0.0    0.0      !! sticky (0=no interactions)",
    ]
    return lines


def build_mixture_cmd(
    n_phases: int,
    dist_type: int,
    dat_basename: str,
    work_dir: Path,
) -> str:
    """Build MIXTURE command file content. dist_type: 1=Gaussian, 2=Schultz."""
    dist_name = "Gauss" if dist_type == 1 else "Schultz"
    # Initial volume fractions and radii so they sum to 1 and span range
    vols = np.ones(n_phases) / n_phases
    r_centers = np.linspace(R_MIN + 10, R_MAX - 20, n_phases)
    poly_init = 5.0
    lines = [
        "i                        !! init",
        "!!!!!!!!!!!!!!!!!!       !! init",
        "pro us                   !! problem user",
        f"nph{n_phases}_{dist_name}  !! comment 1",
        f"spheres only            !! comment 2",
        "EXPERIMENT               !! mode",
        f"{n_phases}                        !! number of phases",
        f"{SYSTEM_CONCENTRATION}                     !! system concentration",
    ]
    for k in range(n_phases):
        v = vols[k]
        r = r_centers[k]
        r_lo = max(R_MIN, r - 25)
        r_hi = min(R_MAX, r + 25)
        rhs_lo = r_lo + RHS_MARGIN
        rhs_hi = r_hi + 30
        lines.extend(
            sphere_block(
                vol=v, vol_lo=0.0, vol_hi=1.0,
                rout=r, rout_lo=r_lo, rout_hi=r_hi,
                poly=poly_init, poly_lo=POLY_MIN, poly_hi=POLY_MAX,
                dist_type=dist_type,
                rhs_lo=rhs_lo, rhs_hi=rhs_hi,
            )
        )
    lines.extend([
        "2                        !! data format (2=ASCII)",
        f"{dat_basename}                !! data file",
        f"{Q_SCALE}                        !! q scale (2=1/nm)",
        "1.0                      !! fraction of curve",
        "meth sb                  !! method",
        f"loa maxit {MAXIT}             !! max iterations",
        "run                      !! run",
        "y                        !! confirm",
        "y                        !! confirm",
        "mes 14                   !! save",
        "eva                      !! write",
        "mes 1                    !! next",
        "ex                       !! exit",
        "y                        !! confirm exit",
        "y                        !! confirm exit",
    ])
    return "\n".join(lines)


def run_mixture(work_dir: Path, dat_basename: str, cmd_content: str) -> subprocess.CompletedProcess:
    """Run MIXTURE in work_dir with given .cmd and data file."""
    cmd_path = work_dir / "mixture.cmd"
    with open(cmd_path, "w") as f:
        f.write(cmd_content)
    exe_path = which(MIXTURE_EXE)
    exe = exe_path if exe_path else MIXTURE_EXE
    with open(work_dir / "mixture.cmd") as f:
        proc = subprocess.run(
            [str(exe)],
            cwd=str(work_dir),
            stdin=f,
            capture_output=True,
            text=True,
            timeout=600,
        )
    return proc


def parse_fit_file(fit_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Parse .fit file: one or more header lines (1SPH ..., 2SPH ..., etc.), then columns s, I_exp, I_fit."""
    if not fit_path.exists():
        return None
    # MIXTURE .fit has n_phases header lines then data rows (q, I_exp, I_fit)
    with open(fit_path) as f:
        lines = []
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            first = stripped.split()[0]
            # Phase header lines start with 1SPH, 2SPH, etc.
            if "SPH" in first.upper():
                continue
            # Data rows: first token is a number (e.g. 0.3508653E-01)
            try:
                float(first)
                lines.append(stripped)
            except ValueError:
                continue
        if not lines:
            return None
        data = np.loadtxt(StringIO("\n".join(lines)), dtype=float)
    if data.size == 0 or data.ndim != 2 or data.shape[1] < 3:
        return None
    # MIXTURE .fit writes q in Å^-1; scale to nm^-1 so fit aligns with data (×10 gives correct axis)
    q_nm = data[:, 0] * 10.0
    return q_nm, data[:, 1], data[:, 2]


def parse_mixture_log(log_path: Path, dat_basename: str) -> dict:
    """Parse mixture.log for last run matching dat_basename: params and function value."""
    out = {"n_phases": None, "dist": None, "f_min": np.nan, "params": [], "volumes": [], "radii": [], "polydisp": []}
    if not log_path.exists():
        return out
    text = log_path.read_text()
    # Find "Produced function minimum is equal to  X.XXXXE-XX at the point:"
    m = re.search(r"Produced function minimum is equal to\s+([\d.E+-]+)", text)
    if m:
        out["f_min"] = float(m.group(1))
    # Optimized values: next line after "at the point:"
    m = re.search(r"at the point:\s*\n\s*([\d.\s+-]+)", text)
    if m:
        vals = [float(x) for x in m.group(1).split()]
        out["params"] = vals
    # Infer n_phases from number of SPHERE blocks in last run (we don't have phase count in log easily)
    # So we rely on caller to set n_phases and dist; we only extract f_min and flat params
    return out


def extract_sphere_params_from_log(log_path: Path, n_phases: int) -> list[dict]:
    """From mixture.log get per-sphere: volume fraction, Rout, polydispersity.
    Log lists phase lines like '1SPH vol Rin rho_in Rout rho_out dRout Rhs tau dist scale'.
    Use the last block of n_phases such lines (last run).
    """
    if not log_path.exists():
        return []
    text = log_path.read_text()
    # Collect lines that start with 1SPH, 2SPH, 3SPH etc. (phase params)
    phase_lines = []
    for line in text.splitlines():
        parts = line.strip().split()
        if not parts or len(parts) < 2:
            continue
        if re.match(r"^\dSPH$", parts[0]):
            vals = []
            for x in parts[1:]:
                try:
                    vals.append(float(x))
                except ValueError:
                    break
            if len(vals) >= 6:
                phase_lines.append(vals)
    if len(phase_lines) < n_phases:
        return []
    spheres = []
    for vals in phase_lines[-n_phases:]:
        spheres.append({
            "vol": vals[0],
            "Rout": vals[3],
            "dRout": vals[5],
        })
    return spheres


# 8 fitted parameters per SPHERE phase (vol, Rin, rho_in, Rout, rho_out, dRout, Rhs, tau)
N_PARAMS_PER_PHASE = 8
# Clip log(I) from below to avoid extreme negative values when computing quality on log scale
LOG_I_CLIP = -7.0


def _log_clip(I: np.ndarray, clip: float = LOG_I_CLIP) -> np.ndarray:
    """log(I) with lower clip so that log(I) >= clip (avoids -inf and huge negatives)."""
    return np.log(np.maximum(np.asarray(I, dtype=float), np.exp(clip)))


def calc_R2_and_R2_adj(I_exp: np.ndarray, I_fit: np.ndarray, n_params: int) -> tuple[float, float]:
    """R² and adjusted R². Returns (np.nan, np.nan) if invalid."""
    if I_exp is None or I_fit is None or len(I_exp) != len(I_fit) or len(I_exp) < 2:
        return np.nan, np.nan
    n = len(I_exp)
    if n <= n_params + 1:
        return np.nan, np.nan
    ss_res = np.sum((I_exp - I_fit) ** 2)
    ss_tot = np.sum((I_exp - np.mean(I_exp)) ** 2)
    if ss_tot <= 0:
        return np.nan, np.nan
    R2 = 1.0 - ss_res / ss_tot
    R2_adj = 1.0 - (1.0 - R2) * (n - 1) / (n - n_params - 1)
    return float(R2), float(R2_adj)


def calc_BIC(I_exp: np.ndarray, I_fit: np.ndarray, n_params: int) -> float:
    """Bayesian Information Criterion for regression: BIC = n*ln(SS_res/n) + k*ln(n). Lower is better."""
    if I_exp is None or I_fit is None or len(I_exp) != len(I_fit) or len(I_exp) < 2:
        return np.nan
    n = len(I_exp)
    ss_res = np.sum((I_exp - I_fit) ** 2)
    if ss_res <= 0:
        return np.nan
    bic = n * np.log(ss_res / n) + n_params * np.log(n)
    return float(bic)


def run_all_fits(
    q: np.ndarray,
    I: np.ndarray,
    sigma: np.ndarray,
    work_base: Path,
    dat_basename: str,
) -> list[dict]:
    """Run 6 MIXTURE fits (1/2/3 phase × Gauss/Schultz). Return list of result dicts."""
    results = []
    for n_phases in (1, 2, 3):
        for dist_type in (1, 2):
            dist_name = "Gauss" if dist_type == 1 else "Schultz"
            label = f"nph{n_phases}_{dist_name}"
            work_dir = work_base / label
            work_dir.mkdir(parents=True, exist_ok=True)
            dat_path = work_dir / dat_basename
            write_atsas_dat(dat_path, q, I, sigma)
            cmd_content = build_mixture_cmd(n_phases, dist_type, dat_basename, work_dir)
            proc = run_mixture(work_dir, dat_basename, cmd_content)
            fit_path = work_dir / dat_basename.replace(".dat", ".fit")
            q_fit, I_exp, I_fit = None, None, None
            if fit_path.exists():
                parsed = parse_fit_file(fit_path)
                if parsed:
                    q_fit, I_exp, I_fit = parsed
            log_path = work_dir / "mixture.log"
            spheres = extract_sphere_params_from_log(log_path, n_phases)
            # f_min and params are printed to stdout by MIXTURE/OPTIS, not to log
            f_min = np.nan
            stdout = proc.stdout or ""
            m = re.search(r"Produced function minimum is equal to\s+([\d.E+-]+)", stdout)
            if m:
                f_min = float(m.group(1))
            results.append({
                "label": label,
                "n_phases": n_phases,
                "dist": dist_name,
                "work_dir": work_dir,
                "q_fit": q_fit,
                "I_exp": I_exp,
                "I_fit": I_fit,
                "f_min": f_min,
                "spheres": spheres,
                "stderr": proc.stderr,
                "returncode": proc.returncode,
            })
    return results


def _fit_label(r: dict) -> str:
    """Label with BIC on log(I) when available (format .3f; lower is better)."""
    bic_log = r.get("BIC_log")
    if bic_log is not None and np.isfinite(bic_log):
        return f"{r['label']} (BIC_log={bic_log:.3f})"
    return r["label"]


def plot_comparison(
    q: np.ndarray,
    I: np.ndarray,
    results: list[dict],
    out_path: Path,
) -> None:
    """Plot experimental and all fits: left I vs q, right log(I) vs q. Labels include BIC."""
    import matplotlib.pyplot as plt
    from matplotlib import rcParams

    rcParams["font.family"] = "sans-serif"
    rcParams["font.size"] = 11
    fig, (ax_lin, ax_log) = plt.subplots(1, 2, figsize=(12, 5))
    colors = plt.cm.tab10(np.linspace(0, 1, 10))

    for ax in (ax_lin, ax_log):
        ax.plot(q, I, "k-", lw=1.0, alpha=0.9, label="Experiment")
        for i, r in enumerate(results):
            if r["q_fit"] is None or r["I_fit"] is None:
                continue
            c = colors[i % len(colors)]
            lab = _fit_label(r)
            ax.plot(r["q_fit"], r["I_fit"], "-", color=c, lw=1.5, alpha=0.85, label=lab)
        ax.set_xscale("log")
        ax.set_xlabel(r"$q$ (nm$^{-1}$)")
        ax.legend(loc="best", fontsize=8)
        ax.grid(True, alpha=0.3)

    ax_lin.set_yscale("linear")
    ax_lin.set_ylabel(r"$I(q)$ (a.u.)")
    ax_lin.set_title(r"$I$ vs $q$")
    ax_log.set_yscale("log")
    ax_log.set_ylabel(r"$I(q)$ (a.u.)")
    ax_log.set_title(r"$\log I$ vs $q$")
    fig.suptitle("MIXTURE fits (spheres only) — labels: BIC on log(I)", fontsize=12)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_distributions(results: list[dict], out_path: Path) -> None:
    """Plot all fitted size distributions on the same axes; R in nm. One curve per mixture, labels include R²_adj."""
    import matplotlib.pyplot as plt
    from matplotlib import rcParams

    rcParams["font.family"] = "sans-serif"
    rcParams["font.size"] = 11
    fig, ax = plt.subplots(figsize=(8, 5))
    # R in Å for PDF (MIXTURE params are in Å), then convert to nm for plot
    R_plot_Ang = np.linspace(1, 130, 400)
    R_plot_nm = R_plot_Ang / 10.0
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    for idx, r in enumerate(results):
        spheres = r.get("spheres") or []
        dist_name = r.get("dist", "?")
        total = np.zeros_like(R_plot_Ang)
        for s in spheres:
            R0 = s["Rout"]
            dR = s["dRout"]
            vol = s["vol"]
            if dist_name == "Gauss":
                y = gaussian_pdf(R_plot_Ang, R0, dR)
            else:
                y = schultz_pdf(R_plot_Ang, R0, dR)
            y = y / (np.trapezoid(y, R_plot_Ang) + 1e-20) * vol
            total += y
        c = colors[idx % len(colors)]
        lab = _fit_label(r)
        ax.plot(R_plot_nm, total, "-", color=c, lw=1.8, label=lab)
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel("P(R) (arb.)")
    ax.set_xlim(0, 13)
    ax.set_ylim(0, None)
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.set_title("Fitted size distributions (spheres) — R in nm, labels: BIC on log(I)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def save_results_csv(results: list[dict], out_path: Path) -> None:
    """Save fit quality and parameters to CSV."""
    rows = []
    for r in results:
        base = {
            "label": r["label"],
            "n_phases": r["n_phases"],
            "dist": r["dist"],
            "f_min": r["f_min"],
            "chi2": r.get("chi2", np.nan),
            "k": r.get("k", np.nan),
            "n_fit": r.get("n_fit", np.nan),
            "BIC_chi2": r.get("BIC_chi2", np.nan),
            "R2": r.get("R2", np.nan),
            "R2_adj": r.get("R2_adj", np.nan),
            "BIC": r.get("BIC", np.nan),
            "R2_log": r.get("R2_log", np.nan),
            "R2_adj_log": r.get("R2_adj_log", np.nan),
            "BIC_log": r.get("BIC_log", np.nan),
            "returncode": r["returncode"],
        }
        for i, s in enumerate(r.get("spheres") or []):
            base[f"vol_{i+1}"] = s.get("vol", np.nan)
            base[f"Rout_Ang_{i+1}"] = s.get("Rout", np.nan)
            base[f"dRout_Ang_{i+1}"] = s.get("dRout", np.nan)
        rows.append(base)
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fit one SAXS curve with MIXTURE (spheres only).")
    parser.add_argument(
        "input_dat",
        nargs="?",
        default=str(DEFAULT_DAT.resolve()),
        help="Subtracted .dat file",
    )
    parser.add_argument("--outdir", type=str, required=True, help="Output directory (plots, CSV, MIXTURE run subdirs)")
    args = parser.parse_args()

    input_path = Path(args.input_dat)
    if not input_path.exists():
        print(f"Input not found: {input_path}", file=sys.stderr)
        return 1
    out_dir = Path(args.outdir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work_base = out_dir  # nph1_Gauss, nph1_Schultz, ... and *.png, *.csv all in outdir
    work_base.mkdir(parents=True, exist_ok=True)

    # Load data (q in nm^-1)
    q, I, sigma = load_subtracted_dat(input_path)
    dat_basename = "exp.dat"
    print(f"Loaded {len(q)} points from {input_path}")

    # Run fits
    results = run_all_fits(q, I, sigma, work_base, dat_basename)

    # Parse f_min from log where not already set
    for r in results:
        if np.isnan(r.get("f_min")) and r.get("work_dir"):
            log_path = r["work_dir"] / "mixture.log"
            if log_path.exists():
                text = log_path.read_text()
                m = re.search(r"Produced function minimum is equal to\s+([\d.E+-]+)", text)
                if m:
                    r["f_min"] = float(m.group(1))

    # Quality on I and on log(I) (log clipped at LOG_I_CLIP)
    for r in results:
        k = r["n_phases"] * N_PARAMS_PER_PHASE
        r["k"] = k
        I_exp, I_fit = r.get("I_exp"), r.get("I_fit")
        q_fit = r.get("q_fit")
        R2, R2_adj = calc_R2_and_R2_adj(I_exp, I_fit, k)
        r["R2"] = R2
        r["R2_adj"] = R2_adj
        r["BIC"] = calc_BIC(I_exp, I_fit, k)
        if I_exp is not None and I_fit is not None and q_fit is not None and len(I_exp) >= 2:
            idx = np.argsort(q)
            q_s, sigma_s = q[idx], sigma[idx]
            sigma_fit = np.interp(np.asarray(q_fit), q_s, sigma_s)
            r["chi2"] = float(calc_chi2(I_exp, I_fit, sigma_fit))
            r["n_fit"] = len(I_exp)
            r["BIC_chi2"] = float(r["chi2"]) * (len(I_exp) - 1) + k * np.log(len(I_exp))
        else:
            r["chi2"] = np.nan
            r["n_fit"] = np.nan
            r["BIC_chi2"] = np.nan
        if I_exp is not None and I_fit is not None:
            log_exp = _log_clip(I_exp)
            log_fit = _log_clip(I_fit)
            R2_log, R2_adj_log = calc_R2_and_R2_adj(log_exp, log_fit, k)
            r["R2_log"] = R2_log
            r["R2_adj_log"] = R2_adj_log
            r["BIC_log"] = calc_BIC(log_exp, log_fit, k)
        else:
            r["R2_log"] = r["R2_adj_log"] = r["BIC_log"] = np.nan

    # Outputs
    stem = input_path.stem
    plot_comparison(q, I, results, out_dir / f"{stem}_mixture_comparison.png")
    plot_distributions(results, out_dir / f"{stem}_mixture_distributions.png")
    save_results_csv(results, out_dir / f"{stem}_mixture_results.csv")
    print(f"Comparison plot: {out_dir / f'{stem}_mixture_comparison.png'}")
    print(f"Distributions:   {out_dir / f'{stem}_mixture_distributions.png'}")
    print(f"Results CSV:      {out_dir / f'{stem}_mixture_results.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
