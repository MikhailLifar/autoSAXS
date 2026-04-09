"""
MIXTURE-based fitting (ATSAS): 6 runs (1-, 2-, 3-phase × Gaussian, Schultz–Zimm; SPHERE-only).
Selects model with lowest BIC_log; writes comparison plot, distribution plot, and results CSV.
Invoked per selected profile in second (slow) processing.
"""

from __future__ import annotations

import re
import subprocess
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .utils import calc_chi2, gaussian_pdf, schultz_pdf

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
MIXTURE_EXE = "mixture"
Q_SCALE = 2  # 2 = 1/nm
SYSTEM_CONCENTRATION = 0.1
RHS_MARGIN = 5.0
N_PARAMS_PER_PHASE = 8
LOG_I_CLIP = -7.0


def _load_curve(path: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load q, intensity, sigma from a subtracted .dat (YAML+CSV or plain CSV)."""
    path = Path(path)
    from .utils import read_data
    df, _, _ = read_data(str(path))
    q = df["q"].to_numpy().astype(np.float64)
    I = df["intensity"].to_numpy().astype(np.float64)
    sigma = df["sigma"].to_numpy().astype(np.float64) if "sigma" in df.columns else 0.03 * np.abs(I)
    return q, I, sigma


def _write_atsas_dat(path: Path, q: np.ndarray, I: np.ndarray, sigma: np.ndarray) -> None:
    """Write 3-column (q, I, sigma) no-header file for ATSAS."""
    with open(path, "w") as f:
        for i in range(len(q)):
            f.write(f"{q[i]}\t{I[i]}\t{sigma[i]}\n")


def _sphere_block(vol, vol_lo, vol_hi, rout, rout_lo, rout_hi, poly, poly_lo, poly_hi, dist_type, rhs_lo, rhs_hi) -> list[str]:
    rhs_init = 0.5 * (rhs_lo + rhs_hi)
    lines = [
        "SPHERE",
        f"  {vol:.4f}  {vol_lo:.2f}  {vol_hi:.2f}    !! volume fraction",
        "  0.0    0.0    0.0      !! inner shell radius",
        "  0.0    0.0    0.0      !! inner contrast",
        f"  {rout:.2f}  {rout_lo:.2f}  {rout_hi:.2f}   !! outer shell radius (Angstrom)",
        "  1.0    1.0    1.0      !! outer contrast",
        f"  {poly:.2f}  {poly_lo:.2f}  {poly_hi:.2f}   !! polydispersity",
        f"  {rhs_init:.2f}  {rhs_lo:.2f}  {rhs_hi:.2f}   !! hard sphere radius",
        f"  {dist_type}                        !! 1=Gauss 2=Schultz",
        "  0.0    0.0    0.0      !! sticky (0=no interactions)",
    ]
    return lines


def _build_mixture_cmd(
    n_phases: int,
    dist_type: int,
    dat_basename: str,
    work_dir: Path,
    *,
    maxit: int,
    r_min: float,
    r_max: float,
    poly_min: float,
    poly_max: float,
) -> str:
    dist_name = "Gauss" if dist_type == 1 else "Schultz"
    vols = np.ones(n_phases) / n_phases
    # Evenly spaced initial radii inside [r_min, r_max].
    # For n_phases=1 this yields the midpoint.
    r_centers = np.linspace(r_min, r_max, n_phases + 2)[1:-1]
    # poly_init = 0.5 * (poly_min + poly_max)
    poly_init = 130.0
    lines = [
        "i                        !! init",
        "!!!!!!!!!!!!!!!!!!       !! init",
        "pro us                   !! problem user",
        f"nph{n_phases}_{dist_name}  !! comment 1",
        "spheres only            !! comment 2",
        "EXPERIMENT               !! mode",
        f"{n_phases}                        !! number of phases",
        f"{SYSTEM_CONCENTRATION}                     !! system concentration",
    ]
    for k in range(n_phases):
        v, r = vols[k], r_centers[k]
        r_lo, r_hi = max(r_min, r - 25.0), min(r_max, r + 25.0)
        rhs_lo, rhs_hi = r_lo + RHS_MARGIN, r_hi + 30
        lines.extend(
            _sphere_block(
                v,
                0.0,
                1.0,
                r,
                r_lo,
                r_hi,
                poly_init,
                poly_min,
                poly_max,
                dist_type,
                rhs_lo,
                rhs_hi,
            )
        )
    lines.extend([
        "2                        !! data format (2=ASCII)",
        f"{dat_basename}                !! data file",
        f"{Q_SCALE}                        !! q scale (2=1/nm)",
        "1.0                      !! fraction of curve",
        "meth sb                  !! method",
        f"loa maxit {maxit}             !! max iterations",
        "run                      !! run", "y                        !! confirm", "y                        !! confirm",
        "mes 14                   !! save", "eva                      !! write", "mes 1                    !! next",
        "ex                       !! exit", "y                        !! confirm exit", "y                        !! confirm exit",
    ])
    return "\n".join(lines)


def _run_mixture(work_dir: Path, dat_basename: str, cmd_content: str) -> subprocess.CompletedProcess:
    """Run MIXTURE (command `mixture` from PATH)."""
    cmd_path = work_dir / "mixture.cmd"
    with open(cmd_path, "w") as f:
        f.write(cmd_content)
    with open(work_dir / "mixture.cmd") as f:
        return subprocess.run([MIXTURE_EXE], cwd=str(work_dir), stdin=f, capture_output=True, text=True, timeout=600)


def _parse_fit_file(fit_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    if not fit_path.exists():
        return None
    with open(fit_path) as f:
        lines = []
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            first = stripped.split()[0]
            if "SPH" in first.upper():
                continue
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
    q_nm = data[:, 0] * 10.0
    return q_nm, data[:, 1], data[:, 2]


def _extract_sphere_params_from_log(log_path: Path, n_phases: int) -> list[dict]:
    if not log_path.exists():
        return []
    text = log_path.read_text()
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
        spheres.append({"vol": vals[0], "Rout": vals[3], "dRout": vals[5]})
    return spheres


def _log_clip(I: np.ndarray, clip: float = LOG_I_CLIP) -> np.ndarray:
    return np.log(np.maximum(np.asarray(I, dtype=float), np.exp(clip)))


def _calc_R2_and_R2_adj(I_exp: np.ndarray, I_fit: np.ndarray, n_params: int) -> tuple[float, float]:
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


def _calc_BIC(I_exp: np.ndarray, I_fit: np.ndarray, n_params: int) -> float:
    if I_exp is None or I_fit is None or len(I_exp) != len(I_fit) or len(I_exp) < 2:
        return np.nan
    n = len(I_exp)
    ss_res = np.sum((I_exp - I_fit) ** 2)
    if ss_res <= 0:
        return np.nan
    return float(n * np.log(ss_res / n) + n_params * np.log(n))


def _run_all_fits(
    q: np.ndarray, I: np.ndarray, sigma: np.ndarray,
    work_base: Path, dat_basename: str,
    *,
    max_nph: int,
    maxit: int,
    r_min: float,
    r_max: float,
    poly_min: float,
    poly_max: float,
) -> list[dict]:
    results = []
    for n_phases in range(1, max_nph + 1):
        for dist_type in (1, 2):
            dist_name = "Gauss" if dist_type == 1 else "Schultz"
            label = f"nph{n_phases}_{dist_name}"
            work_dir = work_base / label
            work_dir.mkdir(parents=True, exist_ok=True)
            dat_path = work_dir / dat_basename
            _write_atsas_dat(dat_path, q, I, sigma)
            cmd_content = _build_mixture_cmd(
                n_phases,
                dist_type,
                dat_basename,
                work_dir,
                maxit=maxit,
                r_min=r_min,
                r_max=r_max,
                poly_min=poly_min,
                poly_max=poly_max,
            )
            proc = _run_mixture(work_dir, dat_basename, cmd_content)
            fit_path = work_dir / dat_basename.replace(".dat", ".fit")
            q_fit, I_exp, I_fit = None, None, None
            if fit_path.exists():
                parsed = _parse_fit_file(fit_path)
                if parsed:
                    q_fit, I_exp, I_fit = parsed
            log_path = work_dir / "mixture.log"
            spheres = _extract_sphere_params_from_log(log_path, n_phases)
            f_min = np.nan
            stdout = proc.stdout or ""
            m = re.search(r"Produced function minimum is equal to\s+([\d.E+-]+)", stdout)
            if m:
                f_min = float(m.group(1))
            results.append({
                "label": label, "n_phases": n_phases, "dist": dist_name,
                "work_dir": work_dir, "q_fit": q_fit, "I_exp": I_exp, "I_fit": I_fit,
                "f_min": f_min, "spheres": spheres, "stderr": proc.stderr, "returncode": proc.returncode,
            })
    return results


def _fit_label(r: dict, *, key: str) -> str:
    val = r.get(key)
    return f"{r['label']} ({key}={val:.3f})"


def _plot_comparison(
    q: np.ndarray, I: np.ndarray, results: list[dict], out_path_lin: Path, out_path_log: Path,
    fit_range_title: str,
) -> None:
    """Save two separate comparison plots: I vs q (linear y) and log I vs q (log y)."""
    import matplotlib.pyplot as plt
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    # Experiment: thin line; fits: thicker with transparency for clarity
    exp_lw = 0.7
    fit_lw = 1.5
    fit_alpha = 0.65

    # (1) I vs q — linear x and y
    fig_lin, ax = plt.subplots()
    ax.plot(q, I, "k-", lw=exp_lw, alpha=0.95, label="Experiment")
    for i, r in enumerate(results):
        if r["q_fit"] is None or r["I_fit"] is None:
            continue
        ax.plot(
            r["q_fit"],
            r["I_fit"],
            "-",
            color=colors[i % len(colors)],
            lw=fit_lw,
            alpha=fit_alpha,
            label=_fit_label(r, key="BIC"),
        )
    ax.set_xscale("linear")
    ax.set_yscale("linear")
    ax.set_xlabel(r"$q$ (nm$^{-1}$)")
    ax.set_ylabel(r"$I(q)$ (a.u.)")
    ax.set_title(r"MIXTURE fits — $I$ vs $q$" + f" ({fit_range_title})")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig_lin.tight_layout()
    fig_lin.savefig(out_path_lin, dpi=400, bbox_inches="tight")
    plt.close(fig_lin)

    # (2) log I vs q — log y
    fig_log, ax = plt.subplots()
    ax.plot(q, I, "k-", lw=exp_lw, alpha=0.95, label="Experiment")
    for i, r in enumerate(results):
        if r["q_fit"] is None or r["I_fit"] is None:
            continue
        ax.plot(
            r["q_fit"],
            r["I_fit"],
            "-",
            color=colors[i % len(colors)],
            lw=fit_lw,
            alpha=fit_alpha,
            label=_fit_label(r, key="BIC_log"),
        )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(r"$q$ (nm$^{-1}$)")
    ax.set_ylabel(r"$I(q)$ (a.u.)")
    ax.set_title(r"MIXTURE fits — $\log I$ vs $q$" + f" ({fit_range_title})")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig_log.tight_layout()
    fig_log.savefig(out_path_log, dpi=400, bbox_inches="tight")
    plt.close(fig_log)


def _plot_distributions(results: list[dict], out_path: Path, *, r_min: float, r_max: float) -> None:
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    r_lo = max(0.0, float(r_min))
    r_hi = max(r_lo + 1.0, float(r_max))
    R_plot_Ang = np.linspace(max(1.0, r_lo), r_hi, 600)
    R_plot_nm = R_plot_Ang / 10.0
    colors = plt.cm.tab10(np.linspace(0, 1, 10))
    for idx, r in enumerate(results):
        spheres = r.get("spheres") or []
        dist_name = r.get("dist", "?")
        total = np.zeros_like(R_plot_Ang)
        for s in spheres:
            R0, dR, vol = s["Rout"], s["dRout"], s["vol"]
            y = gaussian_pdf(R_plot_Ang, R0, dR) if dist_name == "Gauss" else schultz_pdf(R_plot_Ang, R0, dR)
            y = y / (np.trapezoid(y, R_plot_Ang) + 1e-20) * vol
            total += y
        lab = _fit_label(r, key="BIC_chi2")
        ax.plot(R_plot_nm, total, "-", color=colors[idx % len(colors)], lw=1.8, label=lab)
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel("P(R) (arb.)")
    ax.set_xlim(max(0.0, r_lo / 10.0), r_hi / 10.0)
    ax.set_ylim(0, None)
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    ax.set_title("Fitted size distributions (spheres) — R in nm, labels: BIC_chi2")
    fig.tight_layout()
    fig.savefig(out_path, dpi=400, bbox_inches="tight")
    plt.close(fig)


def _save_results_csv(results: list[dict], out_path: Path) -> None:
    rows = []
    for r in results:
        base = {
            "label": r["label"], "n_phases": r["n_phases"], "dist": r["dist"],
            "f_min": r["f_min"], "chi2": r.get("chi2", np.nan),
            "k": r.get("k", np.nan), "n_fit": r.get("n_fit", np.nan), "BIC_chi2": r.get("BIC_chi2", np.nan),
            "R2": r.get("R2", np.nan), "R2_adj": r.get("R2_adj", np.nan),
            "BIC": r.get("BIC", np.nan), "R2_log": r.get("R2_log", np.nan),
            "R2_adj_log": r.get("R2_adj_log", np.nan), "BIC_log": r.get("BIC_log", np.nan),
            "returncode": r["returncode"],
        }
        for i, s in enumerate(r.get("spheres") or []):
            base[f"vol_{i+1}"] = s.get("vol", np.nan)
            base[f"Rout_Ang_{i+1}"] = s.get("Rout", np.nan)
            base[f"dRout_Ang_{i+1}"] = s.get("dRout", np.nan)
        rows.append(base)
    pd.DataFrame(rows).to_csv(out_path, index=False)


def fit_mixtures(
    profile_path: str | Path,
    output_dir: str | Path,
    fast_forward: bool = False,
    q_range_nm: tuple[float, float] | None = None,
    *,
    max_nph: int = 3,
    maxit: int = 100,
    r_min: float = 5.0,
    r_max: float = 120.0,
    poly_min: float = 0.5,
    poly_max: float = 60.0,
) -> dict[str, Any] | None:
    """
    Run 6 MIXTURE fits (1-, 2-, 3-phase × Gaussian, Schultz–Zimm; SPHERE-only),
    select model with lowest BIC_log, write comparison plot, distribution plot, results CSV.
    MIXTURE is invoked as the command `mixture` from the terminal (PATH).

    profile_path: path to 1D subtracted .dat (q in nm⁻¹, intensity, sigma).
    output_dir: directory for this sample's output (caller e.g. apply_batch provides per-sample dir).
    fast_forward: if True and comparison, distribution, CSV exist for this basename, skip and return result dict.
    q_range_nm: (q_min, q_max) in nm⁻¹ to use for fitting; None = use full q range. Only data in this range
        is passed to MIXTURE; comparison plot shows full experiment and fits over the fit range.

    Returns dict with keys: output_subdir (path), best_label, BIC_log, comparison_path, distributions_path, results_csv_path;
    or None on failure.
    """
    output_dir = Path(output_dir)
    profile_path = Path(profile_path)
    basename = profile_path.stem
    work_base = output_dir
    work_base.mkdir(parents=True, exist_ok=True)

    comparison_path = work_base / "mixture_comparison_I_vs_q.png"
    comparison_log_path = work_base / "mixture_comparison_logI_vs_q.png"
    distributions_path = work_base / "mixture_distributions.png"
    results_csv_path = work_base / "mixture_results.csv"
    if fast_forward and comparison_path.exists() and comparison_log_path.exists() and distributions_path.exists() and results_csv_path.exists():
        df = pd.read_csv(results_csv_path)
        if "BIC_log" in df.columns:
            idx = df["BIC_log"].idxmin()
            best_label = str(df.loc[idx, "label"])
            bic_log = float(df.loc[idx, "BIC_log"])
        else:
            best_label = str(df.loc[0, "label"]) if len(df) else ""
            bic_log = np.nan
        return {
            "output_subdir": str(work_base),
            "best_label": best_label,
            "BIC_log": bic_log,
            "comparison_path": str(comparison_path),
            "distributions_path": str(distributions_path),
            "results_csv_path": str(results_csv_path),
        }

    q_full, I_full, sigma_full = _load_curve(profile_path)
    if q_range_nm is not None:
        q_min, q_max = q_range_nm
        mask = (q_full >= q_min) & (q_full <= q_max)
        q, I, sigma = q_full[mask], I_full[mask], sigma_full[mask]
        if len(q) < 2:
            raise ValueError(f"q_range_nm {q_range_nm} yields fewer than 2 points (got {len(q)})")
        fit_range_title = f"fit range: [{q_min}, {q_max}] nm⁻¹"
    else:
        q, I, sigma = q_full, I_full, sigma_full
        fit_range_title = "full q range"
    dat_basename = "exp.dat"
    results = _run_all_fits(
        q,
        I,
        sigma,
        work_base,
        dat_basename,
        max_nph=max_nph,
        maxit=maxit,
        r_min=r_min,
        r_max=r_max,
        poly_min=poly_min,
        poly_max=poly_max,
    )

    for r in results:
        if np.isnan(r.get("f_min")) and r.get("work_dir"):
            log_path = r["work_dir"] / "mixture.log"
            if log_path.exists():
                m = re.search(r"Produced function minimum is equal to\s+([\d.E+-]+)", log_path.read_text())
                if m:
                    r["f_min"] = float(m.group(1))

    for r in results:
        k = r["n_phases"] * N_PARAMS_PER_PHASE
        r["k"] = k
        I_exp, I_fit = r.get("I_exp"), r.get("I_fit")
        q_fit = r.get("q_fit")
        r["R2"], r["R2_adj"] = _calc_R2_and_R2_adj(I_exp, I_fit, k)
        r["BIC"] = _calc_BIC(I_exp, I_fit, k)
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
            r["R2_log"], r["R2_adj_log"] = _calc_R2_and_R2_adj(log_exp, log_fit, k)
            r["BIC_log"] = _calc_BIC(log_exp, log_fit, k)
        else:
            r["R2_log"] = r["R2_adj_log"] = r["BIC_log"] = np.nan

    _plot_comparison(q_full, I_full, results, comparison_path, comparison_log_path, fit_range_title)
    _plot_distributions(results, distributions_path, r_min=r_min, r_max=r_max)
    _save_results_csv(results, results_csv_path)

    best = min(results, key=lambda r: (np.nan if not np.isfinite(r.get("BIC_log", np.nan)) else r["BIC_log"], r["label"]))
    return {
        "output_subdir": str(work_base),
        "best_label": best["label"],
        "BIC_log": best.get("BIC_log", np.nan),
        "comparison_path": str(comparison_path),
        "distributions_path": str(distributions_path),
        "results_csv_path": str(results_csv_path),
    }
