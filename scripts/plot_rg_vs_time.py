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

from autosaxs.utils import read_saxs, gaussian_pdf, schultz_pdf

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
            v = r.get("BIC_log") or r.get("BIC")
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


def _save_mixture_ridge_plot(
    run_root: Path,
    data: list[tuple[datetime, float | None, str, float | None, float | None, dict[str, float]]],
    y_spacing: float = 0.08,
) -> None:
    """Ridge plot of best-BIC mixture PDFs for samples with q_09_11 >= 0.1. Same axes, y-offset by time order, color by time. No legend."""
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib import cm
    from matplotlib.colors import Normalize

    if np is None:
        return
    selected = [(dt, stem, q09) for dt, _rg, stem, q09, _q39, _ in data if q09 is not None and q09 >= 0.1]
    if not selected:
        return
    R_plot_nm = np.linspace(0.1, 13.0, 400)
    pdfs: list[tuple[datetime, Any]] = []
    for dt, stem, _ in selected:
        pdf = _load_best_mixture_pdf(run_root, stem, R_plot_nm)
        if pdf is not None:
            pdfs.append((dt, pdf))
    if not pdfs:
        return
    # Sort by time
    pdfs.sort(key=lambda x: x[0])
    times = [p[0] for p in pdfs]
    times_num = mdates.date2num(times)
    t_min, t_max = min(times_num), max(times_num)
    norm = Normalize(vmin=t_min, vmax=t_max)
    cmap = cm.get_cmap("viridis")
    fig, ax = plt.subplots(figsize=(8, 6))
    pdf_scale = 3.0
    for i, (dt, pdf) in enumerate(pdfs):
        y_offset = i * y_spacing
        color = cmap(norm(mdates.date2num(dt)))
        y_curve = y_offset + pdf_scale * pdf
        ax.fill_between(R_plot_nm, y_offset, y_curve, color=color, alpha=0.5)
        ax.plot(R_plot_nm, y_curve, color=color, lw=1.2, alpha=0.85)
    ax.set_xlabel(r"$R$ (nm)")
    ax.set_ylabel(r"P(R) (arb.) + offset")
    ax.set_xlim(0, 13)
    y_max = max(
        (i + 1) * y_spacing + pdf_scale * np.max(pdf) for i, (_, pdf) in enumerate(pdfs)
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
    pdf_scale: float = 3.0,
) -> None:
    """Save PDF matrix to NumPy-readable .txt: one row per R value, first column R (nm), then one column per sample (data order). Samples with q_09_11 < 0.1 get zeros."""
    if np is None:
        return
    R_plot_nm = np.linspace(0.1, 13.0, 400)
    n_samples = len(data)
    pdf_matrix = np.zeros((n_samples, len(R_plot_nm)))
    for i, (_dt, _rg, stem, q09, _q39, _) in enumerate(data):
        if q09 is not None and q09 >= 0.1:
            pdf = _load_best_mixture_pdf(run_root, stem, R_plot_nm)
            if pdf is not None:
                pdf_matrix[i, :] = pdf_scale * pdf
    # Shape (n_R, 1 + n_samples): column 0 = R, columns 1..n = PDF per sample
    out_array = np.column_stack([R_plot_nm, pdf_matrix.T])
    out_path = run_root / "mixture_ridge_PDFs.txt"
    with open(out_path, "w") as f:
        f.write("# Column 0: R (nm). Columns 1..n: P(R) per sample (data order); zeros where q_09_11 < 0.1. pdf_scale=%g\n" % pdf_scale)
        np.savetxt(f, out_array, fmt="%.6g")
    print(f"Wrote {out_path} (R + {n_samples} samples)")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").strip().split("\n")[0])
    parser.add_argument(
        "directory",
        type=Path,
        default=Path("data/260119_PtNPs/Pt_NPs_formatted/Pt_NPs_insitu"),
        nargs="?",
        help="Run root containing descriptors/ and raw/",
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

    # Plot 4: Ridge plot of mixture PDFs (samples with q_09_11 >= 0.1, best BIC per sample, color by time)
    _save_mixture_ridge_plot(run_root, data, y_spacing=0.08)

    # Save PDF matrix to .txt: all samples (zeros where q_09_11 < 0.1), same scale as plot
    _save_mixture_pdfs_txt(run_root, data, pdf_scale=3.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
