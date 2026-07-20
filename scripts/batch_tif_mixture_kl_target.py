#!/usr/bin/env python3
"""
Integrate all .tif in a directory (non-recursive), subtract a buffer curve, run MIXTURE
via autosaxs skills, select the best model by BIC_chi2, and compute D_KL(P_mix || Q_target)
numerically on [r_min, r_max] (Å) from the config mixture block. Q_target is Gaussian in
radius with mu = 2.1 nm, sigma = 0.1 nm (21 Å, 1 Å).

Writes data-dir/results.csv with columns: basename, target.

Config (YAML): requires ``mixture:`` as before. Subtraction uses the same keys as the
autosaxs pipeline (``saxs_controller``): optional top-level ``sub:`` with ``q_range_abs:
[q_min, q_max]`` in nm⁻¹ for match-tail scaling (omit or null for default relative tail),
and optional ``method`` (default ``match_tail``).

Usage:
  python batch_tif_mixture_kl_target.py DATA_DIR \\
    --config CONFIG.yml --integrator-dir INTEGRATOR_DIR --buffer BUFFER.dat
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from autosaxs.mixture import N_PARAMS_PER_PHASE, _parse_fit_file
from autosaxs.skill.model_mixture import model_mixture
from autosaxs.skill.integrate import integrate
from autosaxs.skill.subtract import subtract
from autosaxs.utils import gaussian_pdf, load_config, schultz_pdf

# Best row selection: same semantics as scripts/2026_Pt_NPs_kinetic_analysis.py
HIGHER_IS_BETTER = frozenset({"R2", "R2_adj", "R2_log", "R2_adj_log"})

TARGET_MU_NM = 2.1
TARGET_SIGMA_NM = 0.1

# Dense grid for trapezoidal KL on the sphere-radius axis (Å), matching MIXTURE bounds.
KL_GRID_POINTS = 4096


def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    if hasattr(np, "trapezoid"):
        return float(np.trapezoid(y, x))
    return float(np.trapz(y, x))


def _descriptor_stem(stem: str) -> str:
    return stem[4:] if stem.startswith("sub_") else stem


def _parse_float(s: str | None) -> float | None:
    if s is None or (isinstance(s, str) and s.strip() == ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _best_mixture_row(
    run_root: Path, subtracted_stem: str, best_by: str, mixture_subdir: str | None = None
) -> dict[str, Any] | None:
    """Pick best CSV row by best_by (lower better, except HIGHER_IS_BETTER). mixture_subdir: folder name under run_root/mixture (default: descriptor stem)."""
    base = mixture_subdir if mixture_subdir is not None else _descriptor_stem(subtracted_stem)
    csv_path = run_root / "mixture" / base / "mixture_results.csv"
    if not csv_path.is_file():
        return None
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None

        def key_fn(r: dict[str, Any]) -> float:
            if best_by == "BIC_chi2":
                v = r.get("BIC_chi2")
                if v is not None and isinstance(v, str) and v.strip() != "":
                    try:
                        return float(v)
                    except ValueError:
                        pass
                chi2 = r.get("chi2")
                k_s = r.get("k")
                n_fit_s = r.get("n_fit") or r.get("n")
                if k_s is None or (isinstance(k_s, str) and k_s.strip() == ""):
                    n_phases_s = r.get("n_phases")
                    if n_phases_s is None or (isinstance(n_phases_s, str) and n_phases_s.strip() == ""):
                        return float("inf")
                    try:
                        k = int(float(n_phases_s)) * N_PARAMS_PER_PHASE
                    except (ValueError, TypeError):
                        return float("inf")
                else:
                    try:
                        k = int(float(k_s))
                    except (ValueError, TypeError):
                        return float("inf")
                chi2_f = _parse_float(chi2)
                if chi2_f is None:
                    return float("inf")
                n_fit_f = _parse_float(n_fit_s)
                if n_fit_f is None or n_fit_f < 1:
                    label = (r.get("label") or "").strip()
                    mixture_dir = run_root / "mixture" / base
                    if label:
                        fit_path = mixture_dir / label / "exp.fit"
                        if fit_path.is_file():
                            parsed = _parse_fit_file(fit_path)
                            if parsed and len(parsed) >= 2 and parsed[1] is not None:
                                n = len(parsed[1])
                                if n >= 1:
                                    return chi2_f * (n - 1) + k * math.log(n)
                    return float("inf")
                return chi2_f * (n_fit_f - 1) + k * math.log(n_fit_f)

            val = r.get(best_by)
            parsed = _parse_float(val)
            if parsed is None:
                return float("inf")
            if best_by in HIGHER_IS_BETTER:
                return -parsed
            return parsed

        best = min(rows, key=key_fn)
        if key_fn(best) == float("inf"):
            return None
        return best
    except (OSError, csv.Error, ValueError, TypeError, KeyError):
        return None


def _mixture_pdf_on_grid_R_ang(row: dict[str, Any], r_ang: np.ndarray) -> np.ndarray | None:
    """Full mixture PDF P(R) on radii r_ang (Å), volume-weighted and normalized per component then summed (same construction as mixture comparison plots)."""
    try:
        dist_name = (row.get("dist") or "Gauss").strip()
        total = np.zeros_like(r_ang, dtype=float)
        for i in range(1, 4):
            vol_s = row.get(f"vol_{i}", "")
            r_s = row.get(f"Rout_Ang_{i}", "")
            dr_s = row.get(f"dRout_Ang_{i}", "")
            if vol_s == "" or r_s == "" or dr_s == "":
                continue
            try:
                vol, r0, dr = float(vol_s), float(r_s), float(dr_s)
            except ValueError:
                continue
            if dist_name == "Schultz":
                y = schultz_pdf(r_ang, r0, dr)
            else:
                y = gaussian_pdf(r_ang, r0, dr)
            area = _trapz(y, r_ang)
            y = y / (area + 1e-20) * vol
            total += y
        if np.max(total) <= 0:
            return None
        return total
    except (ValueError, TypeError, FloatingPointError, ZeroDivisionError):
        return None


def _normalize_on_grid(p: np.ndarray, r: np.ndarray) -> np.ndarray:
    z = _trapz(p, r)
    if z <= 0 or not math.isfinite(z):
        raise ValueError("non-normalizable distribution")
    return p / z


def kl_divergence_mixture_vs_gaussian_nm(
    row: dict[str, Any],
    r_min_ang: float,
    r_max_ang: float,
    *,
    target_mu_nm: float = TARGET_MU_NM,
    target_sigma_nm: float = TARGET_SIGMA_NM,
) -> float:
    """D_KL(P || Q) with P the fitted mixture PDF on radius (Å), Q = Gaussian in nm converted to Å."""
    r = np.linspace(float(r_min_ang), float(r_max_ang), KL_GRID_POINTS, dtype=np.float64)
    p_raw = _mixture_pdf_on_grid_R_ang(row, r)
    if p_raw is None:
        raise ValueError("empty mixture PDF")
    p = _normalize_on_grid(p_raw, r)
    mu_ang = target_mu_nm * 10.0
    sigma_ang = target_sigma_nm * 10.0
    q_raw = gaussian_pdf(r, mu_ang, sigma_ang)
    q = _normalize_on_grid(q_raw, r)
    pe = np.maximum(p, 1e-300)
    qe = np.maximum(q, 1e-300)
    integrand = p * (np.log(pe) - np.log(qe))
    return _trapz(integrand, r)


def _q_range_nm_from_config(cfg: dict[str, Any]) -> tuple[float, float] | None:
    m = (cfg or {}).get("mixture") or {}
    qr = m.get("q_range_nm")
    if qr is None:
        return None
    if isinstance(qr, (list, tuple)) and len(qr) == 2:
        return float(qr[0]), float(qr[1])
    return None


def _subtract_kwargs_from_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """
    Match autosaxs.saxs_controller / guisaxs ProcessingService: ``sub.q_range_abs`` for
    tail-matching q window (nm⁻¹); optional ``sub.method`` for ``skill.subtract``.
    """
    sub = (cfg or {}).get("sub") or {}
    method = sub.get("method", "match_tail")
    if not isinstance(method, str) or not method.strip():
        method = "match_tail"
    out: dict[str, Any] = {"method": method.strip()}
    qr = sub.get("q_range_abs")
    if qr is None:
        return out
    if not isinstance(qr, (list, tuple)) or len(qr) != 2:
        raise ValueError("config sub.q_range_abs must be absent, null, or a pair [q_min, q_max] (nm^-1)")
    q0, q1 = qr[0], qr[1]
    if q0 is None and q1 is None:
        return out
    if q0 is None or q1 is None:
        raise ValueError("config sub.q_range_abs: set both q_min and q_max, or omit / use null for default tail")
    out["q_min"] = float(q0)
    out["q_max"] = float(q1)
    return out


def _list_tifs_nonrecursive(data_dir: Path) -> list[Path]:
    return sorted(p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".tif")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integrate .tif, subtract buffer, MIXTURE fit, KL vs target Gaussian; write results.csv.",
    )
    parser.add_argument("data_dir", help="Directory containing .tif files (non-recursive)")
    parser.add_argument("--config", required=True, help="Path to autosaxs YAML (must contain mixture: …)")
    parser.add_argument("--integrator-dir", required=True, help="Calibrated integrator directory (from calibrate)")
    parser.add_argument("--buffer", required=True, help="Path to buffer 1D .dat (integrated curve)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        print(f"Not a directory: {data_dir}", file=sys.stderr)
        return 1

    config_path = Path(args.config).resolve()
    if not config_path.is_file():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1

    buffer_path = Path(args.buffer).resolve()
    if not buffer_path.is_file():
        print(f"Buffer file not found: {buffer_path}", file=sys.stderr)
        return 1

    integrator_dir = Path(args.integrator_dir).resolve()
    if not integrator_dir.is_dir():
        print(f"Integrator directory not found: {integrator_dir}", file=sys.stderr)
        return 1

    cfg = load_config(str(config_path))
    mixture_cfg = dict((cfg or {}).get("mixture") or {})
    for key in ("maxit", "r_min", "r_max", "poly_min", "poly_max", "max_nph"):
        if key not in mixture_cfg:
            print(f"Config missing mixture.{key}", file=sys.stderr)
            return 1

    r_min_ang = float(mixture_cfg["r_min"])
    r_max_ang = float(mixture_cfg["r_max"])
    if r_max_ang <= r_min_ang:
        print("mixture.r_max must be > mixture.r_min", file=sys.stderr)
        return 1

    q_range_nm = _q_range_nm_from_config(cfg or {})
    fit_kw: dict[str, Any] = {"config_path": str(config_path), "use_cache": False}
    if q_range_nm is not None:
        fit_kw["q_min_nm"] = q_range_nm[0]
        fit_kw["q_max_nm"] = q_range_nm[1]

    try:
        subtract_kw = _subtract_kwargs_from_config(cfg or {})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    tifs = _list_tifs_nonrecursive(data_dir)
    if not tifs:
        print(f"No .tif files in {data_dir}", file=sys.stderr)
        return 1

    integration_dir = data_dir / "integration"
    subtracted_dir = data_dir / "subtracted"
    mixture_root = data_dir / "mixture"

    print("Integrating…", file=sys.stderr)
    int_out = integrate(
        images=str(data_dir),
        integrator_dir=str(integrator_dir),
        output_dir=str(integration_dir),
        use_cache=False,
    )
    integrated = int_out.get("integrated_1d")
    if isinstance(integrated, str):
        integrated = [integrated]
    if not integrated:
        print("Integration produced no curves.", file=sys.stderr)
        return 1

    print("Subtracting buffer…", file=sys.stderr)
    subtract(
        sample_1d=str(integration_dir),
        buffer_1d=str(buffer_path),
        output_dir=str(subtracted_dir),
        use_cache=False,
        **subtract_kw,
    )

    rows_out: list[tuple[str, str]] = []

    for tif_path in tifs:
        stem = tif_path.stem
        sub_stem = f"sub_{stem}"
        sub_path = subtracted_dir / f"{sub_stem}.dat"
        mix_dir = mixture_root / stem

        if not sub_path.is_file():
            print(f"Missing subtracted curve for {tif_path.name}: {sub_path}", file=sys.stderr)
            rows_out.append((stem, ""))
            continue

        try:
            print(f"MIXTURE: {stem}…", file=sys.stderr)
            model_mixture(profile=str(sub_path), output_dir=str(mix_dir), **fit_kw)
        except Exception as exc:
            print(f"model_mixture failed for {stem}: {exc}", file=sys.stderr)
            rows_out.append((stem, ""))
            continue

        best = _best_mixture_row(data_dir, sub_stem, "BIC_chi2", mixture_subdir=stem)
        if not best:
            print(f"No mixture_results.csv or no valid row for {stem}", file=sys.stderr)
            rows_out.append((stem, ""))
            continue

        try:
            kl = kl_divergence_mixture_vs_gaussian_nm(best, r_min_ang, r_max_ang)
            rows_out.append((stem, f"{kl:.8g}"))
        except Exception as exc:
            print(f"KL failed for {stem}: {exc}", file=sys.stderr)
            rows_out.append((stem, ""))

    out_csv = data_dir / "results.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["basename", "target"])
        w.writerows(rows_out)

    print(f"Wrote {out_csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
