#!/usr/bin/env python3
"""
Build Rg vs acquisition time per run: parse Rg from descriptors/*_results.txt,
read shot time from the matching raw/*_sample.tif metadata, compute average
intensities q_09_11 and q_39_41 from subtracted curves, then plot and save
CSV/PNG (only samples with valid numeric Rg and available time).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from autosaxs.utils import calc_chi2, read_saxs, gaussian_pdf, schultz_pdf

try:
    from autosaxs.mixture import _parse_fit_file as _mixture_parse_fit_file
except ImportError:
    _mixture_parse_fit_file = None

try:
    import numpy as np
except ImportError:
    np = None


def parse_rg_from_results(results_path: Path) -> float | None:
    """Extract Rg (nm) from the 'Descriptors (used downstream)' block. Returns None if N/A or missing."""
    if not results_path.is_file():
        return None
    rg_val = None
    in_descriptors = False
    with open(results_path, "r") as f:
        for line in f:
            if "Descriptors (used downstream):" in line:
                in_descriptors = True
                continue
            if in_descriptors:
                m = re.match(r"\s*Rg\s*=\s*(.+?)\s*nm", line)
                if m:
                    raw = m.group(1).strip()
                    if raw.upper() == "N/A":
                        return None
                    try:
                        return float(raw)
                    except ValueError:
                        return None
                # End of descriptor block (next section)
                if line.strip().startswith(("GNOM", "Porod", "Molecular", "I(0)", "Quality")):
                    break
    return rg_val


def parse_all_guinier_rg(results_path: Path) -> dict[str, float]:
    """Parse 'All Guinier methods' block: method names and their Rg (nm). Returns dict method_name -> Rg; methods with (no result) are omitted."""
    out: dict[str, float] = {}
    if not results_path.is_file():
        return out
    in_block = False
    # Lines like "  first5: Rg=3.9664 nm, ..." or "  autorg: (no result)"
    method_rg_re = re.compile(r"^\s*(\w+):\s*(?:Rg=([\d.]+)\s*nm|\(no result\))")
    with open(results_path, "r") as f:
        for line in f:
            if "All Guinier methods" in line:
                in_block = True
                continue
            if in_block:
                m = method_rg_re.match(line)
                if m:
                    name, rg_str = m.group(1), m.group(2)
                    if rg_str is not None:
                        try:
                            out[name] = float(rg_str)
                        except ValueError:
                            pass
                    continue
                if line.strip().startswith(("Porod", "Descriptors", "GNOM")) or (line.strip() == "" and out):
                    break
    return out


def mean_intensity_in_q_range(
    q: list[float], intensity: list[float], q_min: float, q_max: float
) -> float | None:
    """Average intensity for points with q in [q_min, q_max]. Returns None if no points."""
    if np is None:
        return None
    q_a = np.asarray(q, dtype=float)
    I_a = np.asarray(intensity, dtype=float)
    mask = (q_a >= q_min) & (q_a <= q_max)
    if not np.any(mask):
        return None
    return float(np.mean(I_a[mask]))


def fit_I0(q: list[float], intensity: list[float], q_max: float = 2.0) -> float:
    """Fit I(q) ≈ A * exp(-alpha * q) on q in [0, q_max] nm⁻¹. Returns A (I(0) approximation); if fitted A < 0, returns 0. Raises only if no points in q range."""
    from scipy.optimize import curve_fit
    import numpy as _np
    q_a = _np.asarray(q, dtype=float)
    I_a = _np.asarray(intensity, dtype=float)
    mask = (q_a >= 0) & (q_a <= q_max)
    if not _np.any(mask):
        raise ValueError(f"No points in q range [0, {q_max}]")
    q_fit = q_a[mask]
    I_fit = I_a[mask]
    I0_guess = float(_np.max(_np.abs(I_fit))) if _np.any(_np.isfinite(I_fit)) else 1.0
    alpha_guess = 0.5

    def model(q: Any, A: float, alpha: float) -> Any:
        return A * _np.exp(-alpha * q)

    popt, _pcov = curve_fit(
        model, q_fit, I_fit,
        p0=[I0_guess, alpha_guess],
        bounds=([-_np.inf, 0], [_np.inf, _np.inf]),
        maxfev=5000,
    )
    A = float(popt[0])
    if A < 0:
        A = 0.0
    return A


# DateTime in TIFF raw header: format YYYY:MM:DD HH:MM:SS (e.g. after "II*" in first 300 bytes)
_TIFF_DATETIME_RE = re.compile(rb"(\d{4}:\d{2}:\d{2}\s+\d{2}:\d{2}:\d{2})")


def get_tiff_datetime(tif_path: Path) -> datetime:
    """Read acquisition time from TIFF header. Time is always present; raises if missing or unparseable.
    Tries TIFF tag 306 (DateTime) via PIL, then parses first 300 bytes for YYYY:MM:DD HH:MM:SS (encoding-safe)."""
    if not tif_path.is_file():
        raise FileNotFoundError(f"TIFF file not found: {tif_path}")

    datetime_str = None
    try:
        from PIL import Image
        from PIL.TiffTags import TAGS
        with Image.open(tif_path) as img:
            img.load()
            tag_v2 = getattr(img, "tag_v2", None) or {}
            for tag_id, value in tag_v2.items():
                if tag_id == 306 or TAGS.get(tag_id) == "DateTime":
                    raw = value if isinstance(value, str) else (value[0] if isinstance(value, (tuple, list)) else None)
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("latin-1", errors="replace")
                    s = str(raw).strip().strip("\x00")[:19]
                    if s:
                        datetime_str = s
                        break
    except ImportError:
        pass

    if not datetime_str:
        with open(tif_path, "rb") as f:
            head = f.read(300)
        match = _TIFF_DATETIME_RE.search(head)
        if match:
            datetime_str = match.group(1).decode("ascii")
        if not datetime_str:
            raise ValueError(f"DateTime not found in TIFF header (tag 306 or YYYY:MM:DD HH:MM:SS in first 300 bytes): {tif_path}")

    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"DateTime in TIFF could not be parsed (value={datetime_str!r}); "
        f"expected YYYY:MM:DD HH:MM:SS or YYYY-MM-DD HH:MM:SS: {tif_path}"
    )


def collect_rg_and_time(run_root: Path) -> list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]]:
    """Collect (time, Rg_nm, stem, q_09_11, q_39_41, all_guinier_rg) for every subtracted curve. all_guinier_rg is dict method_name -> Rg from 'All Guinier methods' (new format)."""
    descriptors_dir = run_root / "descriptors"
    raw_dir = run_root / "raw"
    subtracted_dir = run_root / "subtracted"
    if not subtracted_dir.is_dir():
        return []

    out: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]] = []

    for sub_path in sorted(subtracted_dir.glob("*.dat")):
        stem = sub_path.stem
        # Subtracted files are sub_<basename>.dat; raw TIFFs and descriptors use <basename>
        base = stem[4:] if stem.startswith("sub_") else stem
        q_09_11: float | None = None
        q_39_41: float | None = None
        if sub_path.is_file():
            try:
                q_arr, intensity_arr, _sigma, _metadata = read_saxs(str(sub_path))
                q_list = q_arr.tolist()
                I_list = intensity_arr.tolist()
                q_09_11 = mean_intensity_in_q_range(q_list, I_list, 0.9, 1.1)
                q_39_41 = mean_intensity_in_q_range(q_list, I_list, 3.9, 4.1)
            except Exception:
                pass
        # Time: always from TIFF header (raises if missing)
        tif_path = raw_dir / f"{base}.tif"
        res_path = descriptors_dir / f"{stem}_results.txt"
        dt = get_tiff_datetime(tif_path)
        rg: float | None = parse_rg_from_results(res_path) if res_path.is_file() else None
        all_guinier_rg = parse_all_guinier_rg(res_path) if res_path.is_file() else {}
        out.append((dt, rg, stem, q_09_11, q_39_41, all_guinier_rg))
    return sorted(out, key=lambda x: x[0])


def compute_I0_per_sample(
    run_root: Path,
    data: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]],
    q_max: float = 2.0,
) -> list[float]:
    """For each sample load subtracted curve and fit I(q) ≈ A*exp(-alpha*q) on q in [0, q_max]. Return list of A (same order as data). Raises on any failure."""
    subtracted_dir = run_root / "subtracted"
    A_list: list[float] = []
    for _dt, _rg, stem, _q09, _q39, _ in data:
        sub_path = subtracted_dir / f"{stem}.dat"
        if not sub_path.is_file():
            raise FileNotFoundError(f"Subtracted file not found: {sub_path}")
        q_arr, intensity_arr, _sigma, _metadata = read_saxs(str(sub_path))
        A = fit_I0(q_arr.tolist(), intensity_arr.tolist(), q_max=q_max)
        A_list.append(A)
    return A_list


def _get_best_mixture_label(run_root: Path, stem: str) -> str | None:
    """Load mixture_results.csv for sample stem, pick row with lowest BIC_log. Returns best model label (e.g. nph1_Gauss) or None."""
    mixture_dir = run_root / "mixture" / f"mixture_{stem}"
    csv_path = mixture_dir / "mixture_results.csv"
    if not csv_path.is_file():
        return None
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        def bic_log_val(r):
            v = r.get("BIC_log")
            if v == "" or v is None:
                return float("inf")
            try:
                return float(v)
            except ValueError:
                return float("inf")
        best = min(rows, key=bic_log_val)
        if bic_log_val(best) == float("inf"):
            return None
        return (best.get("label") or "").strip() or None
    except Exception:
        return None


def _load_best_fit_curve(
    run_root: Path, stem: str,
) -> tuple[Any, Any, Any] | None:
    """Load (q_nm, I_exp, I_fit) from the best MIXTURE run's .fit file for sample stem. Returns None if missing or parse fails."""
    if _mixture_parse_fit_file is None:
        return None
    best_label = _get_best_mixture_label(run_root, stem)
    if not best_label:
        return None
    mixture_dir = run_root / "mixture" / f"mixture_{stem}"
    fit_path = mixture_dir / best_label / "exp.fit"
    return _mixture_parse_fit_file(fit_path)


def _get_best_fit_metrics(run_root: Path, stem: str) -> dict[str, float] | None:
    """Get chi2, R2, R2_log for the best (BIC_log) fit from mixture_results.csv. If chi2 is absent, recalc from exp.fit and subtracted sigma. Returns None if no results."""
    mixture_dir = run_root / "mixture" / f"mixture_{stem}"
    csv_path = mixture_dir / "mixture_results.csv"
    if not csv_path.is_file() or np is None:
        return None
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        def bic_log_val(r):
            v = r.get("BIC_log")
            if v == "" or v is None:
                return float("inf")
            try:
                return float(v)
            except ValueError:
                return float("inf")
        best = min(rows, key=bic_log_val)
        if bic_log_val(best) == float("inf"):
            return None
        def _f(s: str | None) -> float | None:
            if s is None or (isinstance(s, str) and s.strip() == ""):
                return None
            try:
                return float(s)
            except (ValueError, TypeError):
                return None
        R2 = _f(best.get("R2"))
        R2_log = _f(best.get("R2_log"))
        chi2_val = _f(best.get("chi2"))
        if chi2_val is None:
            parsed = _load_best_fit_curve(run_root, stem)
            if parsed is None:
                return {"chi2": float("nan"), "R2": R2 if R2 is not None else float("nan"), "R2_log": R2_log if R2_log is not None else float("nan")}
            q_fit, I_exp, I_fit = parsed
            if np is None or len(I_exp) < 2:
                return {"chi2": float("nan"), "R2": R2 or float("nan"), "R2_log": R2_log or float("nan")}
            sub_path = run_root / "subtracted" / f"{stem}.dat"
            if not sub_path.is_file():
                return {"chi2": float("nan"), "R2": R2 or float("nan"), "R2_log": R2_log or float("nan")}
            _q_sub, _I_sub, sigma_sub, _meta = read_saxs(str(sub_path))
            q_sub = np.asarray(_q_sub, dtype=float)
            sigma_sub = np.asarray(sigma_sub, dtype=float)
            idx = np.argsort(q_sub)
            q_s, sigma_s = q_sub[idx], sigma_sub[idx]
            sigma_fit = np.interp(np.asarray(q_fit), q_s, sigma_s)
            chi2_val = float(calc_chi2(np.asarray(I_exp), np.asarray(I_fit), sigma_fit))
        return {"chi2": chi2_val, "R2": R2 or float("nan"), "R2_log": R2_log or float("nan")}
    except Exception:
        return None


def _load_best_mixture_pdf(
    run_root: Path, stem: str, r_nm: Any,
) -> Any:
    """Load mixture_results.csv for sample stem, pick row with lowest BIC_log, compute PDF on r_nm (R in nm). Returns P(R) array or None."""
    mixture_dir = run_root / "mixture" / f"mixture_{stem}"
    csv_path = mixture_dir / "mixture_results.csv"
    if not csv_path.is_file() or np is None:
        return None
    r_ang = r_nm * 10.0  # R in Angstrom for utils PDFs
    try:
        with open(csv_path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        if not rows:
            return None
        def bic_log_val(r):
            v = r.get("BIC_log")
            if v == "" or v is None:
                return float("inf")
            try:
                return float(v)
            except ValueError:
                return float("inf")
        best = min(rows, key=bic_log_val)
        if bic_log_val(best) == float("inf"):
            return None
        dist_name = (best.get("dist") or "Gauss").strip()
        total = np.zeros_like(r_ang, dtype=float)
        for i in range(1, 4):
            vol_key = f"vol_{i}"
            r_key = f"Rout_Ang_{i}"
            dr_key = f"dRout_Ang_{i}"
            vol_s = best.get(vol_key, "")
            r_s = best.get(r_key, "")
            dr_s = best.get(dr_key, "")
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
            area = np.trapz(y, r_ang)  # type: ignore[attr-defined]
            y = y / (area + 1e-20) * vol
            total += y
        if np.max(total) <= 0:
            return None
        return total
    except Exception:
        return None


def plot_ridge_curves(
    ax: Any,
    R_nm: Any,
    curves: list[Any],
    y_spacing: float = 0.08,
    curve_scale: float = 1.0,
    colors: list[Any] | None = None,
) -> None:
    """Draw ridge-plot style curves on ax: same axes, constant y-offset between curves, fill_between + line. R_nm and each curve are 1d arrays. colors: optional list of color (one per curve); if None, use viridis by index."""
    if np is None or not curves:
        return
    from matplotlib import cm
    n = len(curves)
    if colors is None:
        colors = [cm.get_cmap("viridis")(i / max(n - 1, 1)) for i in range(n)]
    for i, curve in enumerate(curves):
        y_offset = i * y_spacing
        arr = np.asarray(curve, dtype=float)
        y_curve = y_offset + curve_scale * arr
        color = colors[i % len(colors)]
        ax.fill_between(R_nm, y_offset, y_curve, color=color, alpha=0.5)
        ax.plot(R_nm, y_curve, color=color, lw=1.2, alpha=0.85)
    y_vals = [
        (i * y_spacing + curve_scale * np.asarray(c)) for i, c in enumerate(curves)
    ]
    y_min = min(np.min(yv) for yv in y_vals)
    y_max = max(np.max(yv) for yv in y_vals)
    margin = (y_max - y_min) * 0.02 if y_max > y_min else 1.0
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel(r"P(R) (arb.) + offset")
    ax.set_xlim(0, 13)
    ax.set_ylim(y_min - margin, y_max + margin)
    ax.grid(True, alpha=0.35, linestyle="--")
    ax.tick_params(axis="both", labelsize=9)


I0_SCALE_CONSTANT_C = 50.0


def _save_mixture_ridge_plot(
    run_root: Path,
    data: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]],
    A_list: list[float],
    y_spacing: float = 0.08,
    C: float = I0_SCALE_CONSTANT_C,
) -> None:
    """Ridge plot of best-BIC mixture PDFs for all samples; each PDF scaled by A/C (A from I(0) fit). Same axes, y-offset by time order, color by time. Raises if any sample has no fitted PDF."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import cm
    from matplotlib.colors import Normalize

    if np is None:
        return
    if len(A_list) != len(data):
        raise ValueError("A_list length must match data length")
    R_plot_nm = np.linspace(0.1, 13.0, 400)
    pdfs: list[tuple[datetime, Any, float]] = []
    for i, (dt, _rg, stem, _q09, _q39, _) in enumerate(data):
        pdf = _load_best_mixture_pdf(run_root, stem, R_plot_nm)
        if pdf is None:
            raise FileNotFoundError(f"No fitted mixture PDF for sample {stem}")
        scale = A_list[i] / C
        pdfs.append((dt, pdf, scale))
    times = [p[0] for p in pdfs]
    times_num = mdates.date2num(times)
    t_min, t_max = min(times_num), max(times_num)
    norm = Normalize(vmin=t_min, vmax=t_max)
    cmap = cm.get_cmap("viridis")
    fig, ax = plt.subplots(figsize=(8, 6))
    for i, (dt, pdf, scale) in enumerate(pdfs):
        y_offset = i * y_spacing
        color = cmap(norm(mdates.date2num(dt)))
        y_curve = y_offset + scale * pdf
        ax.fill_between(R_plot_nm, y_offset, y_curve, color=color, alpha=0.5)
        ax.plot(R_plot_nm, y_curve, color=color, lw=1.2, alpha=0.85)
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel(r"P(R) (arb.) + offset")
    ax.set_xlim(0, 13)
    y_max = max(
        (i + 1) * y_spacing + scale * np.max(pdf) for i, (_, pdf, scale) in enumerate(pdfs)
    )
    ax.set_ylim(0, y_max * 1.02)
    ax.grid(True, alpha=0.35, linestyle="--")
    ax.tick_params(axis="both", labelsize=9)
    cbar = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax)
    cbar.set_label("Time")
    cbar.ax.yaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.tight_layout()
    out_path = run_root / "mixture_ridge_PDFs.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_path} ({len(pdfs)} PDFs)")


def _save_mixture_pdfs_txt(
    run_root: Path,
    data: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]],
    A_list: list[float],
) -> None:
    """Save PDF matrix to NumPy-readable .txt: first row R=np.nan and scale factors A per sample; then one row per R value, column 0 = R (nm), columns 1..n = P(R). Raises if any sample has no fitted PDF."""
    if np is None:
        return
    if len(A_list) != len(data):
        raise ValueError("A_list length must match data length")
    R_plot_nm = np.linspace(0.1, 13.0, 400)
    n_samples = len(data)
    pdf_matrix = np.zeros((n_samples, len(R_plot_nm)))
    for i, (_dt, _rg, stem, _q09, _q39, _) in enumerate(data):
        pdf = _load_best_mixture_pdf(run_root, stem, R_plot_nm)
        if pdf is None:
            raise FileNotFoundError(f"No fitted mixture PDF for sample {stem}")
        pdf_matrix[i, :] = pdf
    # First row: R = nan, then A_1, A_2, ... (scale factors stored as A)
    scale_row = np.concatenate([[np.nan], np.asarray(A_list, dtype=float)])
    # Rows 2..: R and PDF columns
    out_block = np.column_stack([R_plot_nm, pdf_matrix.T])
    out_path = run_root / "mixture_ridge_PDFs.txt"
    with open(out_path, "w") as f:
        f.write("# Row 1: R=nan, columns 1..n = I(0) scale factor A per sample. Rows 2..: column 0 = R (nm), columns 1..n = P(R)\n")
        np.savetxt(f, scale_row.reshape(1, -1), fmt="%.6g")
        np.savetxt(f, out_block, fmt="%.6g")
    print(f"Wrote {out_path} (scale row + R + {n_samples} samples)")


def _save_error_ridge_plots(
    run_root: Path,
    data: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]],
    y_spacing: float = 0.08,
    curve_scale: float = 1.0,
) -> None:
    """Save two error ridge plots: (exp - fit) in I vs q and exp(log(I_exp)-log(I_fit)) = I_exp/I_fit in log I vs log q. Per-sample q grid, no A/C scaling. Raises if any sample has no .fit."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import cm
    from matplotlib.colors import Normalize

    if np is None:
        return
    curves_lin: list[tuple[Any, Any]] = []  # (q, residual) per sample
    curves_log: list[tuple[Any, Any]] = []  # (q, ratio) per sample
    times: list[datetime] = []
    for _dt, _rg, stem, _q09, _q39, _ in data:
        parsed = _load_best_fit_curve(run_root, stem)
        if parsed is None:
            raise FileNotFoundError(f"No fitted mixture .fit for sample {stem}")
        q_nm, I_exp, I_fit = parsed
        q_nm = np.asarray(q_nm, dtype=float)
        I_exp = np.asarray(I_exp, dtype=float)
        I_fit = np.asarray(I_fit, dtype=float)
        residual = I_exp - I_fit
        # log-space error exponentiated: exp(log(I_exp)-log(I_fit)) = I_exp/I_fit; avoid div by zero
        ratio = np.where(I_fit > 1e-4, I_exp / I_fit, np.nan)
        curves_lin.append((q_nm, residual))
        curves_log.append((q_nm, ratio))
        times.append(_dt)

    times_num = mdates.date2num(times)
    t_min, t_max = min(times_num), max(times_num)
    norm = Normalize(vmin=t_min, vmax=t_max)
    cmap = cm.get_cmap("viridis")

    # (1) I vs q — linear axes, ridge of (exp - fit)
    fig_lin, ax_lin = plt.subplots()
    for i, (q, y) in enumerate(curves_lin):
        y_offset = i * y_spacing
        y_curve = y_offset + curve_scale * y
        color = cmap(norm(times_num[i]))
        ax_lin.fill_between(q, y_offset, y_curve, color=color, alpha=0.5)
        ax_lin.plot(q, y_curve, color=color, lw=1.2, alpha=0.85)
    ax_lin.set_xlabel(r"$q$ (nm$^{-1}$)")
    ax_lin.set_ylabel(r"$I_{\mathrm{exp}} - I_{\mathrm{fit}}$ (arb.) + offset")
    q_all = np.concatenate([q for q, _ in curves_lin])
    ax_lin.set_xlim(np.nanmin(q_all), np.nanmax(q_all))
    y_vals_lin = [
        i * y_spacing + curve_scale * np.asarray(y) for i, (_, y) in enumerate(curves_lin)
    ]
    y_min_lin = min(np.nanmin(yv) for yv in y_vals_lin)
    y_max_lin = max(np.nanmax(yv) for yv in y_vals_lin)
    margin = (y_max_lin - y_min_lin) * 0.02 if y_max_lin > y_min_lin else 1.0
    ax_lin.set_ylim(y_min_lin - margin, y_max_lin + margin)
    ax_lin.grid(True, alpha=0.35, linestyle="--")
    ax_lin.tick_params(axis="both")
    cbar_lin = fig_lin.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax_lin)
    cbar_lin.set_label("Time")
    cbar_lin.ax.yaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig_lin.tight_layout()
    out_lin = run_root / "mixture_ridge_error_I_vs_q.png"
    fig_lin.savefig(out_lin, dpi=400, bbox_inches="tight")
    plt.close(fig_lin)
    print(f"Wrote {out_lin} ({len(curves_lin)} curves)")

    # (2) log I vs log q — exp(log(I_exp)-log(I_fit)) = I_exp/I_fit; multiplicative offset exp(i*y_spacing) per ridge
    fig_log, ax_log = plt.subplots()
    for i, (q, ratio) in enumerate(curves_log):
        mult = np.exp(i * y_spacing)
        baseline = mult
        y_curve = mult * curve_scale * ratio
        color = cmap(norm(times_num[i]))
        ax_log.fill_between(q, baseline, y_curve, color=color, alpha=0.5)
        ax_log.plot(q, y_curve, color=color, lw=1.2, alpha=0.85)
    ax_log.set_xscale("log")
    ax_log.set_yscale("log")
    ax_log.set_xlabel(r"$q$ (nm$^{-1}$)")
    ax_log.set_ylabel(r"$I_{\mathrm{exp}} / I_{\mathrm{fit}}$ (mult. offset)")
    ax_log.set_xlim(np.nanmin(q_all), np.nanmax(q_all))
    y_vals_log = [
        np.exp(i * y_spacing) * curve_scale * np.asarray(r) for i, (_, r) in enumerate(curves_log)
    ]
    baselines = [np.exp(i * y_spacing) for i in range(len(curves_log))]
    finite_vals = [yv[np.isfinite(yv)] for yv in y_vals_log if np.any(np.isfinite(yv))]
    if finite_vals or baselines:
        all_vals = list(baselines) + [v for fv in finite_vals for v in fv]
        y_min_log = min(all_vals)
        y_max_log = max(all_vals)
        if y_max_log > y_min_log:
            margin = (y_max_log - y_min_log) * 0.05
            ax_log.set_ylim(max(1e-10, y_min_log - margin), y_max_log + margin)
    ax_log.grid(True, alpha=0.35, linestyle="--")
    ax_log.tick_params(axis="both")
    cbar_log = fig_log.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax_log)
    cbar_log.set_label("Time")
    cbar_log.ax.yaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig_log.tight_layout()
    out_log = run_root / "mixture_ridge_error_logI_vs_logq.png"
    fig_log.savefig(out_log, dpi=400, bbox_inches="tight")
    plt.close(fig_log)
    print(f"Wrote {out_log} ({len(curves_log)} curves)")


def _save_fit_quality_plot(run_root: Path, data: list[tuple[Any, ...]]) -> None:
    """Plot chi2 (left), R2 and R2_log (two right y-axes) vs time for best BIC_log fits. Two right axes with same limits [-0.05, 1.05], distinct colors."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    times = [x[0] for x in data]
    stems = [x[2] for x in data]
    points: list[tuple[datetime, float, float, float]] = []
    for t, stem in zip(times, stems):
        m = _get_best_fit_metrics(run_root, stem)
        if m is None:
            continue
        chi2 = m["chi2"]
        R2 = m["R2"]
        R2_log = m["R2_log"]
        if not np.isfinite(chi2) and not np.isfinite(R2) and not np.isfinite(R2_log):
            continue
        points.append((t, chi2, R2, R2_log))
    if not points:
        return
    t_vals, chi2_vals, r2_vals, r2_log_vals = zip(*points)
    t_vals = list(t_vals)
    chi2_arr = np.asarray(chi2_vals, dtype=float)
    r2_arr = np.asarray(r2_vals, dtype=float)
    r2_log_arr = np.asarray(r2_log_vals, dtype=float)
    # For log scale, avoid zero chi2
    chi2_plot = np.maximum(chi2_arr, 1e-10)
    r2_clipped = np.clip(r2_arr, 0.0, None)
    r2_log_clipped = np.clip(r2_log_arr, 0.0, None)
    edge_r2 = ["red" if v < 0 else "white" for v in r2_arr]
    edge_r2_log = ["red" if v < 0 else "white" for v in r2_log_arr]

    color_chi2 = "#1b5e20"   # dark green
    color_r2 = "#0d47a1"    # dark blue
    color_r2_log = "#b71c1c"  # dark red

    fig, ax_left = plt.subplots(figsize=(10, 5.5))
    ax_left.scatter(
        t_vals, chi2_plot,
        c=color_chi2, marker="o", s=52, alpha=0.78, edgecolors="white", linewidths=0.8,
        label=r"$\chi^2$", zorder=3,
    )
    ax_left.set_xlabel("Time")
    ax_left.set_ylabel(r"$\chi^2$ (best fit)", color=color_chi2)
    ax_left.tick_params(axis="y", labelcolor=color_chi2)
    ax_left.set_yscale("log")
    ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_left.xaxis.set_major_locator(mdates.AutoDateLocator())
    plt.setp(ax_left.xaxis.get_majorticklabels(), rotation=45, ha="right")
    ax_left.grid(True, alpha=0.4, linestyle="--")
    ax_left.set_ylim(bottom=None)

    ax_right1 = ax_left.twinx()
    ax_right1.scatter(
        t_vals, r2_clipped,
        c=color_r2, marker="s", s=48, alpha=0.78, edgecolors=edge_r2, linewidths=0.8,
        label=r"$R^2$ (direct)", zorder=3,
    )
    ax_right1.set_ylabel(r"$R^2$ (direct)", color=color_r2)
    ax_right1.tick_params(axis="y", labelcolor=color_r2)
    ax_right1.set_ylim(-0.05, 1.05)
    ax_right1.spines["right"].set_position(("axes", 1.0))

    ax_right2 = ax_left.twinx()
    ax_right2.spines["right"].set_position(("axes", 1.12))
    ax_right2.scatter(
        t_vals, r2_log_clipped,
        c=color_r2_log, marker="^", s=48, alpha=0.78, edgecolors=edge_r2_log, linewidths=0.8,
        label=r"$R^2$ (log)", zorder=3,
    )
    ax_right2.set_ylabel(r"$R^2$ (log)", color=color_r2_log)
    ax_right2.tick_params(axis="y", labelcolor=color_r2_log)
    ax_right2.set_ylim(-0.05, 1.05)

    ax_left.legend(loc="upper left", framealpha=0.92, fontsize=9)
    ax_right1.legend(loc="upper right", framealpha=0.92, fontsize=9)
    ax_right2.legend(loc="upper right", bbox_to_anchor=(1.14, 1.0), framealpha=0.92, fontsize=9)
    ax_left.set_title("Fit quality vs time (best BIC_log model)")
    fig.subplots_adjust(right=0.88)
    out_path = run_root / "mixture_fit_quality_vs_time.png"
    fig.savefig(out_path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_path} ({len(points)} points)")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    parser.add_argument(
        "directory",
        type=Path,
        default=Path("data/260119_PtNPs/Pt_NPs_formatted/Pt_NPs_insitu"),
        nargs="?",
        help="Run root containing descriptors/ and raw/",
    )
    parser.add_argument(
        "--scale-constant",
        "-C",
        type=float,
        default=I0_SCALE_CONSTANT_C,
        metavar="C",
        help="Constant C for PDF scaling: each ridge PDF is scaled by A/C (A = I(0) fit). Default: %(default)s",
    )
    args = parser.parse_args()
    run_root = args.directory.resolve()
    if not run_root.is_dir():
        print(f"Not a directory: {run_root}", file=sys.stderr)
        return 1

    data = collect_rg_and_time(run_root)
    if not data:
        print("No subtracted curves found. Ensure subtracted/*.dat exist.", file=sys.stderr)
        return 0

    # Save CSV
    csv_path = run_root / "Rg_vs_time.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time", "Rg_nm", "q_09_11", "q_39_41", "sample"])
        for dt, rg, stem, q09, q39, _ in data:
            w.writerow([
                dt.isoformat(),
                f"{rg:.6g}" if rg is not None else "",
                f"{q09:.6g}" if q09 is not None else "",
                f"{q39:.6g}" if q39 is not None else "",
                stem,
            ])
    print(f"Wrote {csv_path} ({len(data)} points)")

    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        print("matplotlib not available; skipping plot.", file=sys.stderr)
        return 0

    def _save_all_rg_vs_time(
        out_path: Path,
        times: list[datetime],
        all_guinier_rg_list: list[dict[str, float]],
    ) -> None:
        """Plot all Rg approximations (first5, first10, autorg, manual, etc.) vs time as scatter series."""
        # Stable method order: known names first, then rest sorted
        known_order = ("first5", "first10", "autorg", "manual")
        all_methods = set()
        for d in all_guinier_rg_list:
            all_methods.update(d)
        ordered = [m for m in known_order if m in all_methods]
        ordered += sorted(all_methods - set(known_order))
        if not ordered:
            return
        # Colors and markers for up to ~8 series
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f"]
        markers = ["o", "s", "^", "D", "v", "p", "h", "P"]
        fig, ax = plt.subplots(figsize=(10, 6))
        for i, method in enumerate(ordered):
            t_vals = [times[j] for j in range(len(times)) if method in all_guinier_rg_list[j]]
            r_vals = [all_guinier_rg_list[j][method] for j in range(len(times)) if method in all_guinier_rg_list[j]]
            if not t_vals:
                continue
            c = colors[i % len(colors)]
            m = markers[i % len(markers)]
            ax.scatter(
                t_vals, r_vals,
                c=c, marker=m, s=42, alpha=0.72, edgecolors="black", linewidths=0.4,
                label=method, zorder=2,
            )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha="right")
        ax.set_xlabel("Time")
        ax.set_ylabel("Rg (nm)")
        ax.grid(True, alpha=0.4, linestyle="--")
        ax.legend(loc="best", framealpha=0.92, fontsize=9)
        ax.set_title("All Rg approximations vs time")
        fig.tight_layout()
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    times = [x[0] for x in data]
    rg_nm = [x[1] for x in data]
    q_09_11 = [x[3] for x in data]
    q_39_41 = [x[4] for x in data]
    all_guinier_rg_list = [x[5] for x in data]  # list of dict method_name -> Rg

    def _save_twin_plot(
        out_path: Path,
        left_vals: list[float | None],
        left_label: str,
        title: str,
    ) -> None:
        fig, ax_left = plt.subplots(figsize=(9, 5))
        ax_right = ax_left.twinx()
        # Left axis: all points with intensity (more points when Rg is missing for some curves)
        left_valid = [(t, lv) for t, lv in zip(times, left_vals) if lv is not None]
        if not left_valid:
            plt.close(fig)
            return
        t_left, y_left = zip(*left_valid)
        ax_left.scatter(
            t_left, y_left,
            c="tab:blue", marker="o", s=36, alpha=0.7, edgecolors="navy", linewidths=0.5,
            label=left_label, zorder=2,
        )
        # Right axis: only points with valid Rg (fewer points than left when Rg not calculated for some)
        rg_valid = [(t, r) for t, r in zip(times, rg_nm) if r is not None]
        if rg_valid:
            t_rg, y_rg = zip(*rg_valid)
            ax_right.scatter(
                t_rg, y_rg,
                c="tab:red", marker="s", s=36, alpha=0.7, edgecolors="darkred", linewidths=0.5,
                label="Rg (nm)", zorder=2,
            )
        ax_left.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax_left.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(ax_left.xaxis.get_majorticklabels(), rotation=45, ha="right")
        ax_left.set_xlabel("Time")
        ax_left.set_ylabel(left_label, color="tab:blue")
        ax_right.set_ylabel("Rg (nm)", color="tab:red")
        ax_left.tick_params(axis="y", labelcolor="tab:blue")
        ax_right.tick_params(axis="y", labelcolor="tab:red")
        ax_left.grid(True, alpha=0.35, linestyle="--")
        ax_left.legend(loc="upper left", framealpha=0.9)
        ax_right.legend(loc="upper right", framealpha=0.9)
        ax_left.set_title(title)
        fig.tight_layout()
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

    # Plot 1: q_09_11 vs time (left), Rg vs time (right)
    _save_twin_plot(
        run_root / "Rg_vs_time_q09_11.png",
        q_09_11,
        r"$\langle I \rangle_{q \in [0.9,\,1.1]}$ (a.u.)",
        "Rg and low‑q intensity vs time",
    )
    print(f"Wrote {run_root / 'Rg_vs_time_q09_11.png'}")

    # Plot 2: q_39_41 vs time (left), Rg vs time (right)
    _save_twin_plot(
        run_root / "Rg_vs_time_q39_41.png",
        q_39_41,
        r"$\langle I \rangle_{q \in [3.9,\,4.1]}$ (a.u.)",
        "Rg and high‑q intensity vs time",
    )
    print(f"Wrote {run_root / 'Rg_vs_time_q39_41.png'}")

    # Plot 3: Rg_vs_time.png — all Guinier Rg approximations vs time (new-format results)
    _save_all_rg_vs_time(run_root / "Rg_vs_time.png", times, all_guinier_rg_list)
    print(f"Wrote {run_root / 'Rg_vs_time.png'}")

    # I(0) fit per sample (q in [0, 2] nm⁻¹); raises on failure
    A_list = compute_I0_per_sample(run_root, data, q_max=2.0)

    # Plot 4: Ridge plot of mixture PDFs (all samples, scaled by A/C, color by time)
    _save_mixture_ridge_plot(run_root, data, A_list, y_spacing=0.08, C=args.scale_constant)

    # Save PDF matrix to .txt: first row scale factors A, then R and (A/C)*P(R) per sample
    _save_mixture_pdfs_txt(run_root, data, A_list)

    # Error ridge plots: (exp - fit) in I vs q and I_exp/I_fit in log I vs log q
    _save_error_ridge_plots(run_root, data, y_spacing=1.0, curve_scale=1.0)

    # Fit quality: chi2, R2, R2_log vs time (best BIC_log fit per sample)
    _save_fit_quality_plot(run_root, data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
