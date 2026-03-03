#!/usr/bin/env python3
"""
Load mixture_ridge_PDFs.txt from two run directories. Compute difference (first_dir - second_dir)
between scaled PDFs: scale = A/C (A from file, C from CLI: C1 for first dir, C2 for second).
Select sample indices where both have non-zero scaled PDFs, and plot as a ridge plot.
Save the plot to the first directory.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Import plotting helper from kinetic analysis script (module name starts with digit, use importlib)
import importlib.util
_script_dir = Path(__file__).resolve().parent
_kinetic_path = _script_dir / "2026_Pt_NPs_kinetic_analysis.py"
_legacy_path = _script_dir / "plot_rg_vs_time.py"
_path = _kinetic_path if _kinetic_path.is_file() else _legacy_path if _legacy_path.is_file() else None
if _path is not None:
    _spec = importlib.util.spec_from_file_location("kinetic_analysis", _path)
    if _spec is not None and _spec.loader is not None:
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        plot_ridge_curves = _mod.plot_ridge_curves
    else:
        raise RuntimeError("Could not load kinetic analysis module")
else:
    raise RuntimeError("Neither 2026_Pt_NPs_kinetic_analysis.py nor plot_rg_vs_time.py found in scripts/")


def load_pdf_matrix(txt_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Load mixture_ridge_PDFs.txt (new format: row 1 = R=nan, A_1..A_n; rows 2.. = R, P(R)).
    Returns (R_nm, A_list, pdf_matrix) where pdf_matrix is (n_samples, n_R), or None."""
    if not txt_path.is_file():
        return None
    data = np.loadtxt(txt_path, comments="#")
    if data.shape[0] < 2:
        return None
    # Row 0: nan, A_1, A_2, ...
    A_list = np.asarray(data[0, 1:], dtype=float)
    R_nm = data[1:, 0]
    pdf_matrix = data[1:, 1:].T  # (n_samples, n_R)
    return R_nm, A_list, pdf_matrix


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
        "--C1",
        type=float,
        metavar="C1",
        help="Scaling constant for first directory: scale = A/C1 (default: 50.0).",
    )
    parser.add_argument(
        "--C2",
        type=float,
        metavar="C2",
        help="Scaling constant for second directory: scale = A/C2 (default: 50.0).",
    )
    parser.add_argument(
        "--y-spacing",
        type=float,
        default=0.08,
        help="Vertical spacing between curves in ridge plot (default: 0.08).",
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
    R_nm, A1, first_pdf = loaded_first
    R_nm_2, A2, second_pdf = loaded_second
    if first_pdf.shape[1] != second_pdf.shape[1] or not np.allclose(R_nm, R_nm_2):
        print("R grids or sizes differ between the two files.", file=sys.stderr)
        return 1

    n_1, n_2 = first_pdf.shape[0], second_pdf.shape[0]
    if n_1 != n_2:
        print("Number of samples in first and second directories must be the same.", file=sys.stderr)
        return 1

    # Scale PDFs: (A/C) * P(R)
    C1, C2 = args.C1, args.C2
    scale1 = (A1 / C1).reshape(-1, 1)
    scale2 = (A2 / C2).reshape(-1, 1)
    first_scaled = first_pdf * scale1
    second_scaled = second_pdf * scale2

    # Difference = scaled first - scaled second
    diff = first_scaled - second_scaled
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
        curve_scale=1.0,
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
