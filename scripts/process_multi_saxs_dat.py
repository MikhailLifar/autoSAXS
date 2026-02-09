#!/usr/bin/env python3
"""
Process multi-dataset SAXS .dat files (Pankin-style):
- First column "Common" = q, other columns = intensities per dataset.
- Plot raw and tail-aligned curves, save aligned data to .dat.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Allow importing autosaxs when run from repo root or from repos/
_repo = Path(__file__).resolve().parent.parent
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))
from autosaxs.foreign.supervised_ml.whittaker_smooth import whittaker_smooth


def extract_dataset_number(col_name: str) -> int:
    """Extract dataset index from column name. E.g. 0002_002 -> 2, 0002.chi -> 2."""
    if col_name == "Common":
        return 0
    # 0000.chi, 0002.chi, ... -> leading digits
    m = re.match(r"^(\d+)\.", col_name)
    if m:
        return int(m.group(1))
    # 0002_000, 0002_002, ... -> part after underscore
    if "_" in col_name:
        return int(col_name.split("_")[-1])
    # fallback: first group of digits
    m = re.search(r"\d+", col_name)
    return int(m.group(0)) if m else 0


def load_multi_saxs_dat(path: str) -> Tuple[pd.DataFrame, np.ndarray, List[str], List[int]]:
    """Load tab-separated .dat with comma decimal. Returns (df, q, intensity_cols, dataset_numbers)."""
    df = pd.read_csv(path, sep="\t", decimal=",")
    if "Common" not in df.columns:
        raise ValueError(f"Expected column 'Common' (q) in {path}")
    q = df["Common"].to_numpy(dtype=float)
    intensity_cols = [c for c in df.columns if c != "Common"]
    dataset_numbers = [extract_dataset_number(c) for c in intensity_cols]
    return df, q, intensity_cols, dataset_numbers


def align_tails(
    q: np.ndarray,
    columns_data: List[Tuple[str, np.ndarray]],
    q_range_abs: Optional[Tuple[float, float]] = None,
    q_range_rel: Tuple[Optional[float], Optional[float]] = (0.8, None),
    approach_factor: float = 0.98,
    whittaker_lmbd: float = 1.0e10,
    whittaker_d: int = 3,
) -> List[Tuple[str, np.ndarray]]:
    """
    Align high-q tails of multiple datasets to the first dataset (reference).
    Same idea as processor.subtract_buffer match_tail: use tail region, Whittaker smooth, scale by min ratio.
    If q_range_abs is given (e.g. (4, 6)), use that q window; else use q_range_rel as fraction of q_max.
    """
    if not columns_data:
        return []
    if q_range_abs is not None:
        q0, q1 = q_range_abs[0], q_range_abs[1]
    else:
        q_max = np.max(q)
        q0_rel, q1_rel = q_range_rel
        q0 = (q0_rel or 0.0) * q_max
        q1 = (q1_rel if q1_rel is not None else 1.0) * q_max
    idx = (q >= q0) & (q <= q1)
    if not np.any(idx):
        return [(name, I.copy()) for name, I in columns_data]

    ref_name, ref_I = columns_data[0]
    I_ref_tail = whittaker_smooth(ref_I[idx].astype(float), lmbd=whittaker_lmbd, d=whittaker_d)

    result = [(ref_name, ref_I.copy())]
    for name, I in columns_data[1:]:
        I_tail = whittaker_smooth(I[idx].astype(float), lmbd=whittaker_lmbd, d=whittaker_d)
        # Scale so that I_scaled matches ref in tail: I_scaled = I * scale => scale = I_ref / I
        ratios = np.where(I_tail > 1e-30, I_ref_tail / I_tail, np.nan)
        valid = np.isfinite(ratios) & (ratios > 0)
        if not np.any(valid):
            result.append((name, I.copy()))
            continue
        scaling = float(np.nanmin(ratios[valid])) * approach_factor
        result.append((name, I.astype(float) * scaling))
    return result


PlotKind = str  # "loglog" | "log_linear" | "linear"


def plot_scatter_by_dataset(
    q: np.ndarray,
    columns_data: List[Tuple[str, np.ndarray]],
    dataset_numbers: List[int],
    out_path: str,
    title: str,
    plot_kind: PlotKind = "loglog",
) -> None:
    """Plot each dataset as scatter with color = dataset number. plot_kind: loglog, log_linear, or linear."""
    fig, ax = plt.subplots()
    nums = np.array(dataset_numbers)
    cmap = plt.cm.viridis
    norm = plt.Normalize(vmin=nums.min(), vmax=nums.max())
    for (_, I), num in zip(columns_data, dataset_numbers):
        ax.scatter(q, I, c=[cmap(norm(num))], s=0.5, alpha=0.7, label=str(num))
    if plot_kind == "loglog":
        ax.set_xscale("log")
        ax.set_yscale("log")
    elif plot_kind == "log_linear":
        ax.set_yscale("log")
    # else "linear": both linear
    ax.set_xlabel("q")
    ax.set_ylabel("I")
    ax.set_title(title)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    plt.colorbar(sm, ax=ax, label="Dataset number")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()


PLOT_KINDS: List[PlotKind] = ["loglog", "log_linear", "linear"]


def save_multi_saxs_dat(path: str, q: np.ndarray, columns_data: List[Tuple[str, np.ndarray]]) -> None:
    """Save q and intensity columns in same format as input (tab, comma decimal)."""
    data = {"Common": q}
    for name, I in columns_data:
        data[name] = I
    df = pd.DataFrame(data)
    df.to_csv(path, sep="\t", decimal=",", index=False)


def process_file(
    dat_path: str,
    out_dir: Optional[Path] = None,
    aligned_dat_path: Optional[str] = None,
    q_range_abs: Optional[Tuple[float, float]] = None,
    q_range_rel: Tuple[Optional[float], Optional[float]] = (0.8, None),
    approach_factor: float = 0.98,
) -> None:
    dat_path = Path(dat_path)
    base_dir = out_dir if out_dir is not None else dat_path.parent
    if aligned_dat_path is None:
        aligned_dat_path = str(base_dir / (dat_path.stem + "_aligned.dat"))

    # Subdirectories for each plot kind
    for kind in PLOT_KINDS:
        (base_dir / kind).mkdir(parents=True, exist_ok=True)

    df, q, intensity_cols, dataset_numbers = load_multi_saxs_dat(str(dat_path))
    columns_data = [(c, df[c].to_numpy(dtype=float)) for c in intensity_cols]

    # Align tails (in region (4, 6) if q_range_abs given, else relative)
    aligned = align_tails(
        q,
        columns_data,
        q_range_abs=q_range_abs,
        q_range_rel=q_range_rel,
        approach_factor=approach_factor,
    )

    # Plot raw and aligned for each kind
    stem = dat_path.stem
    for kind in PLOT_KINDS:
        subdir = base_dir / kind
        raw_path = str(subdir / f"{stem}.raw.png")
        aligned_path = str(subdir / f"{stem}.aligned.png")
        plot_scatter_by_dataset(
            q, columns_data, dataset_numbers, raw_path,
            f"Raw: {dat_path.name}", plot_kind=kind,
        )
        plot_scatter_by_dataset(
            q, aligned, dataset_numbers, aligned_path,
            f"Tail-aligned: {dat_path.name}", plot_kind=kind,
        )

    save_multi_saxs_dat(aligned_dat_path, q, aligned)
    print(f"Processed {dat_path.name} -> {base_dir}/{{loglog,log_linear,linear}}/*.png, {aligned_dat_path}")


def main():
    parser = argparse.ArgumentParser(description="Process multi-dataset SAXS .dat files.")
    parser.add_argument(
        "files",
        nargs="+",
        help="Paths to .dat files (e.g. data/260206_from_pankin/*.dat)",
    )
    parser.add_argument("--out-dir", default=None, help="Base dir for plot subdirs (default: input file dir)")
    parser.add_argument("--aligned-dat", default=None, help="Output path for aligned .dat (default: <out_dir>/<file>_aligned.dat)")
    parser.add_argument("--q-tail-abs", type=float, nargs=2, default=[4.0, 6.0], metavar=("Q0", "Q1"),
                        help="Tail q-range in absolute q (default: 4 6)")
    parser.add_argument("--q-tail-rel", type=float, nargs=2, default=None, metavar=("Q0", "Q1"),
                        help="Tail q-range as fraction of q_max (if set, overrides --q-tail-abs)")
    parser.add_argument("--approach-factor", type=float, default=0.98, help="Scale factor for tail match (default: 0.98)")
    args = parser.parse_args()

    q_range_abs: Optional[Tuple[float, float]] = (args.q_tail_abs[0], args.q_tail_abs[1])
    q_range_rel: Tuple[Optional[float], Optional[float]] = (0.8, None)
    if args.q_tail_rel is not None:
        q_range_abs = None
        q_range_rel = (args.q_tail_rel[0], args.q_tail_rel[1] if args.q_tail_rel[1] != 1.0 else None)
    out_dir = Path(args.out_dir) if args.out_dir else None
    for f in args.files:
        process_file(
            f,
            out_dir=out_dir,
            aligned_dat_path=args.aligned_dat,
            q_range_abs=q_range_abs,
            q_range_rel=q_range_rel,
            approach_factor=args.approach_factor,
        )


if __name__ == "__main__":
    main()
