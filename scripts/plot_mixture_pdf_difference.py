#!/usr/bin/env python3
"""
Load mixture_ridge_PDFs.txt from two run directories. Compute difference (first_dir - second_dir),
select sample indices where both have non-zero PDFs, and plot as a ridge plot.
Save the plot to the first directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Import plotting helper from plot_rg_vs_time (run from repo root with PYTHONPATH=repos or from repos/)
try:
    from scripts.plot_rg_vs_time import plot_ridge_curves
except ImportError:
    from plot_rg_vs_time import plot_ridge_curves


def load_pdf_matrix(txt_path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Load mixture_ridge_PDFs.txt. Returns (R_nm, pdf_matrix) where pdf_matrix is (n_samples, n_R), or None."""
    if not txt_path.is_file():
        return None
    data = np.loadtxt(txt_path, comments="#")
    R_nm = data[:, 0]
    pdf_matrix = data[:, 1:].T  # (n_samples, n_R)
    return R_nm, pdf_matrix


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plot difference of mixture PDFs (first_dir - second_dir) as ridge plot; save to first_dir.",
    )
    parser.add_argument(
        "first_dir",
        type=Path,
        help="First directory (mixture_ridge_PDFs.txt). Difference = first - second. Plot is saved here.",
    )
    parser.add_argument(
        "second_dir",
        type=Path,
        help="Second directory (mixture_ridge_PDFs.txt). Subtracted from first.",
    )
    parser.add_argument(
        "--y-spacing",
        type=float,
        default=0.08,
        help="Vertical spacing between curves in ridge plot (default: 0.08).",
    )
    parser.add_argument(
        "--curve-scale",
        type=float,
        default=3.0,
        help="Scale factor for curves (default: 3.0, same as main PDF plot).",
    )
    args = parser.parse_args()
    first_dir = args.first_dir.resolve()
    second_dir = args.second_dir.resolve()

    first_txt = first_dir / "mixture_ridge_PDFs.txt"
    second_txt = second_dir / "mixture_ridge_PDFs.txt"
    if not first_txt.is_file():
        print(f"Not found: {first_txt}", file=sys.stderr)
        return 1
    if not second_txt.is_file():
        print(f"Not found: {second_txt}", file=sys.stderr)
        return 1

    loaded_first = load_pdf_matrix(first_txt)
    loaded_second = load_pdf_matrix(second_txt)
    if loaded_first is None or loaded_second is None:
        print("Failed to load PDF data.", file=sys.stderr)
        return 1
    R_nm, first_data = loaded_first
    R_nm_2, second_data = loaded_second
    if first_data.shape[1] != second_data.shape[1] or not np.allclose(R_nm, R_nm_2):
        print("R grids or sizes differ between the two files.", file=sys.stderr)
        return 1

    n_1, n_2 = first_data.shape[0], second_data.shape[0]
    assert n_1 == n_2, "Number of samples in first and second directories must be the same."

    non_zero_first = np.any(first_data != 0, axis=1)
    non_zero_second = np.any(second_data != 0, axis=1)
    selected = non_zero_first & non_zero_second
    indices = np.where(selected)[0]
    if len(indices) == 0:
        print("No sample index has non-zero PDFs in both arrays.", file=sys.stderr)
        return 1

    # Difference = first_dir - second_dir
    diff = first_data[selected] - second_data[selected]
    curves = [diff[i] for i in range(diff.shape[0])]

    import matplotlib.pyplot as plt
    from matplotlib import cm

    fig, ax = plt.subplots(figsize=(8, 6))
    colors = [cm.get_cmap("viridis")(i / max(len(curves) - 1, 1)) for i in range(len(curves))]
    plot_ridge_curves(
        ax,
        R_nm,
        curves,
        y_spacing=args.y_spacing,
        curve_scale=args.curve_scale,
        colors=colors,
    )
    first_name = first_dir.name
    second_name = second_dir.name
    ax.set_title(f"Mixture PDF difference: {first_name} $-$ {second_name}")
    fig.tight_layout()
    out_path = first_dir / "mixture_ridge_PDFs_difference.png"
    fig.savefig(out_path, dpi=400, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out_path} ({len(curves)} curves)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
