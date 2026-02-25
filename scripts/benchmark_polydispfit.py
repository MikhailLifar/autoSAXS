#!/usr/bin/env python3
"""
Benchmark for polydispfit: recover known size distributions from synthetic SAXS curves.

Requires sasmodels. Synthetic data are generated with sasmodels (polydisperse sphere);
polydispfit uses sasmodels for fitting. If the fitter recovers distributions well (low L1),
it is consistent with the trusted sasmodels implementation.

Measures:
  - L1 distance between true and fitted PDFs over radius (0 = perfect, 1 = max)
  - Relative parameter errors (mean, std / sigma / z)

Run from repo root or repos/:  python scripts/benchmark_polydispfit.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy.special import gamma

try:
    from tqdm import tqdm
    _out = tqdm.write  # so progress bar is not overwritten by result lines
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable
    _out = print

# Ensure autosaxs is importable (run from repo root or repos/)
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))

from autosaxs.polydispfit import polydispfit
from autosaxs.utils import write_saxs

from sasmodels.core import load_model
from sasmodels.direct_model import call_kernel


# --- PDFs matching polydispfit exactly (for true profile and for distance evaluation) ---

def pdf_gaussian(r: np.ndarray, mean: float, std: float) -> np.ndarray:
    return np.exp(-0.5 * ((r - mean) / std) ** 2) / (std * np.sqrt(2 * np.pi))


def pdf_lognormal(r: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    r = np.maximum(r, np.finfo(float).tiny)
    return np.exp(-(np.log(r) - mu) ** 2 / (2 * sigma ** 2)) / (
        r * sigma * np.sqrt(2 * np.pi)
    )


def pdf_schulz(r: np.ndarray, z: float, r_mean: float) -> np.ndarray:
    r = np.maximum(r, np.finfo(float).tiny)
    prefactor = ((z + 1) ** (z + 1)) / (r_mean * gamma(z + 1))
    return prefactor * (r / r_mean) ** z * np.exp(-(z + 1) * r / r_mean)


def make_distribution_func(name: str, params: dict):
    """Return a callable (R_grid) -> PDF array for the given distribution."""

    def dist_func(*param_grids):
        if len(param_grids) != 1:
            raise ValueError("Expected a single parameter grid.")
        grid = param_grids[0]
        if name in ("gaussian", "normal"):
            return pdf_gaussian(grid, params["mean"], params["std"])
        if name in ("lognormal", "log-normal"):
            return pdf_lognormal(grid, params["mu"], params["sigma"])
        if name in ("schulz", "schultz", "gamma"):
            return pdf_schulz(grid, params["z"], params.get("mean", params.get("r_mean")))
        raise ValueError(f"Unsupported distribution: {name}")

    return dist_func


def pdf_at(r: np.ndarray, name: str, params: dict) -> np.ndarray:
    """Evaluate PDF at radius array for any supported distribution."""
    if name in ("gaussian", "normal"):
        return pdf_gaussian(r, params["mean"], params["std"])
    if name in ("lognormal", "log-normal"):
        return pdf_lognormal(r, params["mu"], params["sigma"])
    if name in ("schulz", "schultz", "gamma"):
        return pdf_schulz(r, params["z"], params.get("mean", params.get("r_mean")))
    raise ValueError(f"Unsupported distribution: {name}")


def l1_distance_pdf(r_grid: np.ndarray, p_true: np.ndarray, p_fit: np.ndarray) -> float:
    """L1 distance between two PDFs; normalized so that 0 = identical, 1 = max (non-overlapping)."""
    # Integrate |p_true - p_fit|; normalize by 2 so that two non-overlapping unit-mass PDFs give 1
    return float(0.5 * np.trapz(np.abs(p_true - p_fit), r_grid))


# --- Sasmodels: generate I(q) in pipeline units (q in 1/nm, params in nm) ---
# Sasmodels uses length in Å and q in 1/Å. Conversion: 1 nm = 10 Å → q_nm = 0.1 * q_A, R_nm = R_A / 10.

def sasmodels_sphere_Iq(
    q_nm: np.ndarray,
    dist_name: str,
    true_params_nm: dict,
    scale: float = 1e6,
    background: float = 10.0,
) -> np.ndarray:
    """
    Generate I(q) for a polydisperse sphere using sasmodels.
    q_nm: q in 1/nm; true_params_nm: distribution params in nm (mean, std or mu, sigma or z, mean).
    Returns intensity at q_nm (same length as q_nm).
    """
    # Sasmodels: q in 1/Å, radius in Å. q_A = q_nm / 10 (since 1 nm^-1 = 0.1 Å^-1)
    q_A = np.asarray(q_nm, dtype=float) / 10.0
    model = load_model("sphere")
    kernel = model.make_kernel([q_A])

    # Map distribution to sasmodels pars. PD for size params = sigma/center (relative width).
    if dist_name in ("gaussian", "normal"):
        mean_nm = true_params_nm["mean"]
        std_nm = true_params_nm["std"]
        radius_A = mean_nm * 10.0
        radius_pd = std_nm / mean_nm if mean_nm > 0 else 0.0
        pd_type = "gaussian"
    elif dist_name in ("lognormal", "log-normal"):
        # sasmodels lognormal: center = median = exp(mu), PD = sigma (width of ln(x))
        mu = true_params_nm["mu"]
        sigma = true_params_nm["sigma"]
        radius_A = np.exp(mu) * 10.0  # median in nm -> Å
        radius_pd = sigma
        pd_type = "lognormal"
    else:
        # schulz: mean in nm, z. PD = sigma/mean, and for Schulz sigma/mean = 1/sqrt(z+1)
        mean_nm = true_params_nm.get("mean", true_params_nm.get("r_mean"))
        z = true_params_nm["z"]
        radius_A = mean_nm * 10.0
        radius_pd = 1.0 / np.sqrt(z + 1.0) if z > -1 else 0.0
        pd_type = "schulz"

    pars = {
        "radius": float(radius_A),
        "radius_pd": float(radius_pd),
        "radius_pd_type": pd_type,
        "radius_pd_n": 40,
        "radius_pd_nsigma": 3,
        "scale": scale,
        "background": background,
        "sld": 1.0,
        "sld_solvent": 0.0,
    }
    Iq = call_kernel(kernel, pars)
    return np.asarray(Iq).squeeze()


def run_one_benchmark(
    dist_name: str,
    true_params: dict,
    q_min: float = 0.05,
    q_max: float = 5.0,
    n_q: int = 200,
    noise_relative: float = 0.02,
    initial_guess_scale: float = 1.0,
    r_grid_size: int = 500,
    optimizer=None,
    case_label: str = "",
) -> dict:
    """
    Generate synthetic curve with sasmodels, fit with polydispfit, return metrics.
    initial_guess_scale: multiply true params by this for the fitter start (1.0 = start at truth).
    optimizer: passed to polydispfit (default: None = use polydispfit default).
    """
    np.random.seed(42)
    q = np.linspace(q_min, q_max, n_q)

    # Theoretical profile from sasmodels (trusted external calculator)
    intensity_true = sasmodels_sphere_Iq(q, dist_name, true_params)

    # Add noise and define sigma
    noise = np.random.normal(0, noise_relative, size=intensity_true.shape)
    intensity = intensity_true * (1 + noise)
    sigma = noise_relative * intensity_true
    sigma = np.maximum(sigma, 1e-10)

    metadata = {"benchmark": "sasmodels", "true_params": true_params, "dist_name": dist_name}
    with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as f:
        data_path = f.name
    try:
        write_saxs(data_path, q, intensity, sigma, metadata)

        if dist_name in ("gaussian", "normal"):
            order = ["mean", "std"]
            x0 = [true_params["mean"] * initial_guess_scale, true_params["std"] * initial_guess_scale]
        elif dist_name in ("lognormal", "log-normal"):
            order = ["mu", "sigma"]
            x0 = [true_params["mu"] * initial_guess_scale, true_params["sigma"] * initial_guess_scale]
        else:
            order = ["z", "mean"]
            x0 = [true_params["z"] * initial_guess_scale, true_params.get("mean", true_params.get("r_mean")) * initial_guess_scale]

        params_guess = dict(zip(order, x0))
        bounds = {}
        if dist_name in ("gaussian", "normal"):
            bounds = {"mean": (0.1, 20.0), "std": (0.05, 10.0)}
        elif dist_name in ("lognormal", "log-normal"):
            bounds = {"mu": (-3.0, 3.0), "sigma": (0.05, 5.0)}
        else:
            bounds = {"z": (0.5, 200.0), "mean": (0.1, 20.0)}
        distribution_config = {
            "name": dist_name,
            "params": params_guess,
            "bounds": bounds,
        }

        kwargs = {"optimizer": optimizer} if optimizer is not None else {}
        result = polydispfit(
            data_path, "sphere", distribution_config, (q_min, q_max), **kwargs
        )
        fitted_params = result["distribution"]["params"]
    finally:
        os.unlink(data_path)

    r_grid = np.linspace(0.05, 8.0, r_grid_size)
    p_true = pdf_at(r_grid, dist_name, true_params)
    p_fit = pdf_at(r_grid, dist_name, fitted_params)
    p_true = p_true / np.trapz(p_true, r_grid)
    p_fit = p_fit / np.trapz(p_fit, r_grid)
    l1_dist = l1_distance_pdf(r_grid, p_true, p_fit)

    # True curve on fit q grid (for plotting)
    q_plot = result["q"]
    intensity_true_plot = sasmodels_sphere_Iq(q_plot, dist_name, true_params)

    param_errors = {}
    for k in true_params:
        if k in fitted_params:
            a, b = true_params[k], fitted_params[k]
            if np.abs(a) > 1e-12:
                param_errors[k] = abs(b - a) / abs(a)
            else:
                param_errors[k] = abs(b - a)

    return {
        "dist_name": dist_name,
        "true_params": true_params,
        "fitted_params": fitted_params,
        "l1_pdf_distance": l1_dist,
        "param_errors": param_errors,
        "chi2": result["chi2"],
        "success": result["optimizer_info"]["success"],
        "q": q_plot,
        "intensity_true": intensity_true_plot,
        "intensity_fitted": result["model"],
        "r_grid": r_grid,
        "p_true": p_true,
        "p_fit": p_fit,
        "source": "sasmodels",
        "case_label": case_label,
        "optimizer_info": result["optimizer_info"],
    }


def _plot_results(results: list, section_name: str, out_dir: Path) -> None:
    """Produce one figure per benchmark case: curves (true vs fitted) and distributions (true vs fitted)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _out("Skipping plots: matplotlib not installed (pip install matplotlib).")
        return

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, r in enumerate(results):
        q = r["q"]
        I_true = r["intensity_true"]
        I_fit = r["intensity_fitted"]
        r_grid = r["r_grid"]
        p_true = r["p_true"]
        p_fit = r["p_fit"]
        dist_name = r["dist_name"]
        case_label = r.get("case_label", "") or ""
        title_suffix = f" ({case_label})" if case_label else ""
        l1 = r["l1_pdf_distance"]
        true_params = r["true_params"]
        fitted_params = r["fitted_params"]
        source = r.get("source", "internal")

        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(7, 7), sharex=False)

        # Curves: I(q) true vs fitted
        ax1.plot(q, I_true, "b-", label="True", lw=2, alpha=0.9)
        ax1.plot(q, I_fit, "r--", label="Fitted", lw=1.5, alpha=0.9)
        ax1.set_xlabel("q (1/nm)")
        ax1.set_ylabel("I(q)")
        ax1.set_yscale("log")
        ax1.legend(loc="best")
        ax1.set_title(f"Scattering: {dist_name}{title_suffix} — {source}")
        ax1.grid(True, alpha=0.3)

        # Distributions: PDF true vs fitted
        ax2.plot(r_grid, p_true, "b-", label="True", lw=2, alpha=0.9)
        ax2.plot(r_grid, p_fit, "r--", label="Fitted", lw=1.5, alpha=0.9)
        ax2.set_xlabel("R (nm)")
        ax2.set_ylabel("PDF")
        ax2.legend(loc="best")
        ax2.set_title(f"Size distribution  (L1 = {l1:.4f})")
        ax2.text(0.98, 0.98, f"true: {true_params}\nfitted: {fitted_params}", transform=ax2.transAxes,
                 fontsize=7, verticalalignment="top", horizontalalignment="right",
                 bbox=dict(boxstyle="round", facecolor="wheat", alpha=0.5))
        ax2.grid(True, alpha=0.3)

        plt.tight_layout()
        safe_name = f"{section_name}_{dist_name}_{i:02d}.png"
        if case_label:
            safe_name = f"{section_name}_{dist_name}_{case_label}_{i:02d}.png"
        fig.savefig(out_dir / safe_name, dpi=150, bbox_inches="tight")
        plt.close(fig)

    _out(f"Saved {len(results)} plot(s) to {out_dir}")


def _to_json_serializable(obj):
    """Convert numpy types to native Python for JSON."""
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _to_json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_json_serializable(x) for x in obj]
    return obj


def _write_results_json(results: list, json_path: Path, l1s: list) -> None:
    """Write benchmark summary to indented JSON (metrics and params only, no arrays)."""
    summary = {
        "summary": {
            "n_cases": len(results),
            "l1_pdf_distance": {"mean": float(np.mean(l1s)), "max": float(np.max(l1s))},
        },
        "cases": [],
    }
    for r in results:
        oi = r.get("optimizer_info") or {}
        case = {
            "dist_name": r["dist_name"],
            "case_label": r.get("case_label") or "",
            "true_params": _to_json_serializable(r["true_params"]),
            "fitted_params": _to_json_serializable(r["fitted_params"]),
            "l1_pdf_distance": float(r["l1_pdf_distance"]),
            "param_errors": _to_json_serializable(r["param_errors"]),
            "chi2": float(r["chi2"]),
            "success": bool(r["success"]),
            "optimizer_info": _to_json_serializable({
                "method": oi.get("method"),
                "nfev": oi.get("nfev"),
                "parameterization_used": oi.get("parameterization_used"),
                "n_starts_linear_run": oi.get("n_starts_linear_run"),
                "n_starts_log_run": oi.get("n_starts_log_run"),
                "time_budget_linear_sec": oi.get("time_budget_linear_sec"),
                "time_budget_log_sec": oi.get("time_budget_log_sec"),
                "elapsed_linear_sec": oi.get("elapsed_linear_sec"),
                "elapsed_log_sec": oi.get("elapsed_log_sec"),
                "elapsed_sec": oi.get("elapsed_sec"),
            }),
        }
        summary["cases"].append(case)
    path = Path(json_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    _out(f"Results written to {path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Benchmark polydispfit recovery of known distributions.")
    parser.add_argument("--noise", type=float, default=0.02, help="Relative noise level (default 0.02)")
    parser.add_argument("--guess-scale", type=float, default=1.0, help="Initial guess = true * scale (default 1.0)")
    parser.add_argument("--list-only", action="store_true", help="Only list test cases and exit")
    parser.add_argument("--quick", action="store_true", help="Fewer q points and cases for fast run")
    parser.add_argument(
        "--trf",
        action="store_true",
        help="Use local optimizer (trf) instead of global; faster and avoids LAPACK warnings.",
    )
    parser.add_argument(
        "--bo",
        action="store_true",
        help="Use Bayesian optimization (efficient for 2D; requires scikit-optimize).",
    )
    parser.add_argument(
        "--sobol-trf",
        action="store_true",
        dest="sobol_trf",
        help="Use Sobol multi-start TRF (Sobol-sampled starts + local TRF).",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Do not save comparison plots.",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="polydispfit_benchmarks",
        help="Directory for both plots and JSON results (default: polydispfit_benchmarks).",
    )
    parser.add_argument(
        "--indices",
        type=str,
        default=None,
        metavar="I,J,...",
        help="Run only these test case indices (0-based), e.g. --indices 9 or --indices 0,6,9.",
    )
    args = parser.parse_args()
    do_plot = not args.no_plot
    output_dir = Path(args.output_dir)

    # Moderate cases (baseline)
    test_cases = [
        ("schulz", {"z": 8.0, "mean": 2.0}),
        ("schulz", {"z": 3.0, "mean": 1.5}),
        ("lognormal", {"mu": np.log(2.0), "sigma": 0.25}),
        ("lognormal", {"mu": np.log(1.0), "sigma": 0.4}),
        ("gaussian", {"mean": 2.0, "std": 0.4}),
        ("gaussian", {"mean": 1.0, "std": 0.2}),
        # Wide distributions (large spread)
        ("gaussian", {"mean": 2.0, "std": 0.9}, "wide"),
        ("lognormal", {"mu": np.log(2.0), "sigma": 0.65}, "wide"),
        ("schulz", {"z": 2.0, "mean": 1.5}, "wide"),
        # Narrow distributions (near-monodisperse)
        ("gaussian", {"mean": 2.0, "std": 0.12}, "narrow"),
        ("lognormal", {"mu": np.log(2.0), "sigma": 0.12}, "narrow"),
        ("schulz", {"z": 60.0, "mean": 2.0}, "narrow"),
        # Strongly skewed (heavy-tail / asymmetric)
        ("lognormal", {"mu": np.log(1.2), "sigma": 0.55}, "skewed"),
        ("schulz", {"z": 1.8, "mean": 1.2}, "skewed"),
    ]
    if args.quick:
        # One moderate per type + one wide, one narrow, one skewed
        test_cases = [
            test_cases[0],
            test_cases[2],
            test_cases[4],
            test_cases[6],
            test_cases[9],
            test_cases[12],
        ]
    if args.indices is not None:
        idx_list = [int(s.strip()) for s in args.indices.split(",") if s.strip()]
        all_indices = set(range(len(test_cases)))
        for i in idx_list:
            if i not in all_indices:
                raise SystemExit(f"Invalid --indices: {i} not in [0, {len(test_cases) - 1}]")
        test_cases = [test_cases[i] for i in sorted(idx_list)]

    def _unpack_case(case):
        if len(case) == 3:
            return case[0], case[1], case[2]
        return case[0], case[1], ""

    if args.list_only:
        for case in test_cases:
            dist_name, params, label = _unpack_case(case)
            suffix = f"  [{label}]" if label else ""
            print(f"  {dist_name}: {params}{suffix}")
        return

    optimizer_arg = (
        "bo"
        if args.bo
        else ("trf" if args.trf else ("sobol_trf" if args.sobol_trf else None))
    )

    def run_section(title: str, run_fn, results_list: list) -> None:
        print(title)
        print("=" * 60)
        n_curves = len(test_cases)
        pbar = tqdm(
            total=n_curves,
            desc="Curves",
            unit="curve",
            leave=True,
        )
        for case in test_cases:
            dist_name, true_params, case_label = _unpack_case(case)
            r = run_fn(
                dist_name,
                true_params,
                noise_relative=args.noise,
                initial_guess_scale=args.guess_scale,
                optimizer=optimizer_arg,
                case_label=case_label,
            )
            results_list.append(r)
            pbar.update(1)
            pbar.set_postfix_str(f"{r['dist_name']} done")
            tag = f" [{case_label}]" if case_label else ""
            _out(f"\n{r['dist_name']}{tag}  true={true_params}")
            _out(f"  fitted={r['fitted_params']}")
            _out(f"  L1(PDF distance) = {r['l1_pdf_distance']:.4f}  (0=perfect)")
            _out(f"  param rel. errors: {r['param_errors']}")
            _out(f"  chi2 = {r['chi2']:.2f}  success = {r['success']}")
            oi = r.get("optimizer_info") or {}
            _out(f"  optimizer: method={oi.get('method', '?')} nfev={oi.get('nfev')} param_used={oi.get('parameterization_used')} "
                 f"n_starts_linear={oi.get('n_starts_linear_run')} n_starts_log={oi.get('n_starts_log_run')} "
                 f"budget_linear={oi.get('time_budget_linear_sec')}s budget_log={oi.get('time_budget_log_sec')}s "
                 f"elapsed_linear={oi.get('elapsed_linear_sec')}s elapsed_log={oi.get('elapsed_log_sec')}s")
        pbar.close()

    results = []
    run_section("Polydispfit benchmark (sasmodels)", run_one_benchmark, results)
    l1s = [x["l1_pdf_distance"] for x in results]
    print("\n" + "=" * 60)
    print(f"L1 PDF distance  mean = {np.mean(l1s):.4f}  max = {np.max(l1s):.4f}")
    if np.max(l1s) < 0.15:
        print(
            "Conclusion: polydispfit recovers sasmodels distributions well. "
            "Fitter is consistent with the trusted sasmodels implementation."
        )
    elif np.max(l1s) < 0.35:
        print(
            "Conclusion: polydispfit is reasonably consistent with sasmodels (L1 in [0.15, 0.35]). "
            "Minor differences may be due to weighting or distribution conventions."
        )
    else:
        print(
            "Conclusion: large L1 — check units and distribution "
            "conventions (e.g. volume- vs number-weighted, PD definition)."
        )
    if do_plot and results:
        _plot_results(results, "sasmodels", output_dir)
    if results:
        _write_results_json(results, output_dir / "benchmark_results.json", l1s)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
