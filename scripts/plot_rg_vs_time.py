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


def read_subtracted_q_intensity(dat_path: Path) -> tuple[list[float], list[float]] | None:
    """Read q and intensity from a subtracted .dat (YAML + CSV). Returns (q, intensity) or None."""
    if not dat_path.is_file():
        return None
    try:
        content = dat_path.read_text()
        marker = "\n# Data in CSV format\n"
        idx = content.find(marker)
        if idx == -1:
            return None
        csv_text = content[idx + len(marker) :].strip()
        if not csv_text:
            return None
        lines = [l for l in csv_text.splitlines() if l.strip()]
        if not lines:
            return None
        # Header: q,intensity,sigma
        reader = csv.DictReader(lines)
        rows = list(reader)
        if not rows or "q" not in rows[0] or "intensity" not in rows[0]:
            return None
        q = [float(r["q"]) for r in rows]
        intensity = [float(r["intensity"]) for r in rows]
        return (q, intensity)
    except Exception:
        return None


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


def get_tiff_datetime(tif_path: Path, fallback_mtime: Path | None = None) -> datetime | None:
    """Read acquisition time from TIFF metadata (DateTime tag 306). If TIFF is missing and fallback_mtime is set, use that file's modification time (e.g. results file) so that Rg vs time can still be produced when raw/ is absent."""
    try:
        from PIL import Image
        from PIL.TiffTags import TAGS
    except ImportError:
        return None
    if not tif_path.is_file():
        if fallback_mtime and fallback_mtime.is_file():
            return datetime.fromtimestamp(fallback_mtime.stat().st_mtime)
        return None
    try:
        with Image.open(tif_path) as img:
            img.load()
            # TIFF tag 306 = DateTime, format "YYYY:MM:DD HH:MM:SS"
            for tag_id, value in img.tag_v2.items():
                if tag_id == 306 or TAGS.get(tag_id) == "DateTime":
                    s = value if isinstance(value, str) else (value[0] if isinstance(value, (tuple, list)) else None)
                    if not s:
                        continue
                    # Normalize colon-separated date to ISO for parsing
                    s = s.strip().strip("\x00")[:19]
                    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
                        try:
                            return datetime.strptime(s, fmt)
                        except ValueError:
                            continue
                    return None
    except Exception:
        pass
    return None


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
        parsed = read_subtracted_q_intensity(sub_path)
        q_09_11: float | None = None
        q_39_41: float | None = None
        if parsed is not None:
            q_list, I_list = parsed
            q_09_11 = mean_intensity_in_q_range(q_list, I_list, 0.9, 1.1)
            q_39_41 = mean_intensity_in_q_range(q_list, I_list, 3.9, 4.1)
        # Time: from TIFF or descriptor results mtime, else subtracted file mtime
        tif_path = raw_dir / f"{stem}.tif"
        res_path = descriptors_dir / f"{stem}_results.txt"
        fallback = res_path if res_path.is_file() else sub_path
        dt = get_tiff_datetime(tif_path, fallback_mtime=fallback)
        if dt is None:
            dt = datetime.fromtimestamp(sub_path.stat().st_mtime)
        rg: float | None = parse_rg_from_results(res_path) if res_path.is_file() else None
        all_guinier_rg = parse_all_guinier_rg(res_path) if res_path.is_file() else {}
        out.append((dt, rg, stem, q_09_11, q_39_41, all_guinier_rg))
    return sorted(out, key=lambda x: x[0])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
