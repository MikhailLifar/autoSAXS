#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from sklearn.decomposition import PCA

try:
    from autosaxs.utils import load_saxs_1d_any
except ModuleNotFoundError:
    # Allow running from workspace root without installing the package.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from autosaxs.utils import load_saxs_1d_any


@dataclass(frozen=True)
class Curve1D:
    name: str  # basename-like identifier (no extension)
    path: Path
    q: np.ndarray
    I: np.ndarray
    sigma: np.ndarray | None
    dt: datetime | None


def _parse_dt(s: str) -> datetime:
    s = s.strip()
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unparseable datetime: {s!r}")


def _load_times_csv(path: Path) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None or "basename" not in r.fieldnames or "datetime" not in r.fieldnames:
            raise ValueError(f"Expected columns basename,datetime in {path}")
        for row in r:
            b = (row.get("basename") or "").strip()
            d = (row.get("datetime") or "").strip()
            if not b or not d:
                continue
            out[b] = _parse_dt(d)
    return out


def _time_of_day_hm(dt: datetime) -> tuple[int, int]:
    t = dt.time()
    return (t.hour, t.minute)


def _parse_hm(s: str) -> tuple[int, int]:
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", s)
    if not m:
        raise ValueError(f"Expected HH:MM, got {s!r}")
    hh = int(m.group(1))
    mm = int(m.group(2))
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Invalid HH:MM: {s!r}")
    return hh, mm


def _mean_intensity_q_range(q: np.ndarray, I: np.ndarray, q_min: float, q_max: float) -> float:
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    mask = np.isfinite(q) & np.isfinite(I) & (q >= q_min) & (q <= q_max)
    if not np.any(mask):
        raise ValueError(f"No points in q-range [{q_min}, {q_max}]")
    return float(np.mean(I[mask]))


def _check_q_values_identical(curves: list[Curve1D], rtol: float = 1e-5) -> bool:
    if len(curves) <= 1:
        return True
    q0 = curves[0].q
    for c in curves[1:]:
        if q0.shape != c.q.shape:
            return False
        if not np.allclose(q0, c.q, rtol=rtol):
            return False
    return True


def _interpolate_to_common_grid(curves: list[Curve1D], n_points: int = 1000) -> tuple[np.ndarray, np.ndarray]:
    q_min = max(float(np.min(c.q)) for c in curves)
    q_max = min(float(np.max(c.q)) for c in curves)
    if not np.isfinite(q_min) or not np.isfinite(q_max) or q_max <= q_min:
        raise ValueError(f"Invalid common q-range: q_min={q_min}, q_max={q_max}")
    q_common = np.linspace(q_min, q_max, n_points)
    I_matrix = np.vstack([np.interp(q_common, c.q, c.I) for c in curves])
    return q_common, I_matrix


def _apply_q_range(q: np.ndarray, I_matrix: np.ndarray, q_min: float | None, q_max: float | None) -> tuple[np.ndarray, np.ndarray]:
    if q_min is None and q_max is None:
        return q, I_matrix
    q_min_v = float(q_min) if q_min is not None else float(np.min(q))
    q_max_v = float(q_max) if q_max is not None else float(np.max(q))
    mask = (q >= q_min_v) & (q <= q_max_v)
    if not np.any(mask):
        raise ValueError(f"No points remain after q-range filter [{q_min_v}, {q_max_v}]")
    return q[mask], I_matrix[:, mask]


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _save_scores_csv(out_csv: Path, names: list[str], scores: np.ndarray, times: dict[str, datetime] | None) -> None:
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        header = ["name", "pc1", "pc2"]
        if times is not None:
            header.append("datetime")
        w.writerow(header)
        for i, name in enumerate(names):
            row = [name, float(scores[i, 0]), float(scores[i, 1])]
            if times is not None:
                dt = times.get(name)
                row.append(dt.isoformat(timespec="seconds") if dt else "")
            w.writerow(row)


def _plot_components(out_dir: Path, q: np.ndarray, components: np.ndarray, explained: np.ndarray) -> None:
    plt.figure()
    plt.plot(q, components[0], "b-", linewidth=2, label="1st PCA component")
    plt.xlabel("q (nm⁻¹)")
    plt.ylabel("Component (a.u.)")
    plt.title("First PCA Component")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "pca_component_1.png", dpi=150)
    plt.close()

    if components.shape[0] > 1:
        plt.figure()
        plt.plot(q, components[1], "r-", linewidth=2, label="2nd PCA component")
        plt.xlabel("q (nm⁻¹)")
        plt.ylabel("Component (a.u.)")
        plt.title("Second PCA Component")
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()
        plt.savefig(out_dir / "pca_component_2.png", dpi=150)
        plt.close()

    n_show = int(min(10, explained.shape[0]))
    plt.figure()
    idx = np.arange(1, n_show + 1)
    bars = plt.bar(idx, explained[:n_show] * 100, color="steelblue", alpha=0.7, edgecolor="black", linewidth=0.5)
    for bar, pct in zip(bars, explained[:n_show] * 100):
        plt.text(bar.get_x() + bar.get_width() / 2.0, bar.get_height(), f"{pct:.1f}%", ha="center", va="bottom", fontsize=9)
    plt.xlabel("Principal Component")
    plt.ylabel("Explained Variance (%)")
    plt.title("Explained Variance by Principal Component")
    plt.xticks(idx)
    plt.grid(True, alpha=0.3, axis="y")
    plt.tight_layout()
    plt.savefig(out_dir / "pca_explained_variance.png", dpi=150)
    plt.close()


def _plot_scatter_pc1_pc2(out_dir: Path, scores: np.ndarray, explained: np.ndarray, times: list[datetime] | None) -> None:
    plt.figure()
    if times is None:
        c = np.arange(scores.shape[0])
        scatter = plt.scatter(scores[:, 0], scores[:, 1], c=c, cmap="viridis", s=40, alpha=0.7)
        plt.colorbar(scatter, label="File index")
    else:
        t_num = mdates.date2num(times)
        scatter = plt.scatter(scores[:, 0], scores[:, 1], c=t_num, cmap="viridis", s=40, alpha=0.7)
        cbar = plt.colorbar(scatter)
        cbar.ax.yaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        cbar.set_label("Time")
    plt.xlabel(f"PC1 ({explained[0]*100:.1f}% variance)")
    plt.ylabel(f"PC2 ({explained[1]*100:.1f}% variance)")
    plt.title("All Curves in PC1-PC2 Space")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_dir / "pca_scatter_pc1_pc2.png", dpi=150)
    plt.close()


def _plot_all_curves_colored(out_dir: Path, curves: list[Curve1D], scores: np.ndarray) -> None:
    fig, ax = plt.subplots()
    order = np.argsort(scores[:, 0])
    pc1_sorted = scores[order, 0]
    cmap = plt.cm.get_cmap("RdYlBu_r")
    norm = mcolors.Normalize(vmin=float(np.min(pc1_sorted)), vmax=float(np.max(pc1_sorted)))
    for rank, idx in enumerate(order):
        color = cmap(norm(float(pc1_sorted[rank])))
        c = curves[idx]
        ax.plot(c.q, c.I, color=color, alpha=0.25, linewidth=1.0)
    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel("I (a.u.)")
    ax.set_title("All Curves (colored by PC1 score)")
    ax.grid(True, alpha=0.3)
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = plt.colorbar(sm, ax=ax)
    cbar.set_label("PC1 score", rotation=270, labelpad=20)
    plt.tight_layout()
    plt.savefig(out_dir / "pca_all_curves_colored.png", dpi=150)
    plt.close()

def _plot_extreme_pc1_curves(
    out_dir: Path,
    q: np.ndarray,
    I_matrix: np.ndarray,
    names: list[str],
    scores: np.ndarray,
) -> None:
    if scores.shape[0] < 2:
        return
    idx_low = int(np.argmin(scores[:, 0]))
    idx_high = int(np.argmax(scores[:, 0]))

    plt.figure()
    plt.plot(
        q,
        I_matrix[idx_low],
        "o-",
        markersize=2,
        linewidth=1.2,
        alpha=0.8,
        label=f"Lowest PC1 ({names[idx_low]}, PC1={scores[idx_low, 0]:.3g})",
    )
    plt.plot(
        q,
        I_matrix[idx_high],
        "o-",
        markersize=2,
        linewidth=1.2,
        alpha=0.8,
        label=f"Highest PC1 ({names[idx_high]}, PC1={scores[idx_high, 0]:.3g})",
    )
    plt.xlabel("q (nm⁻¹)")
    plt.ylabel("I (a.u.)")
    plt.title("Curves with Extreme PC1 Scores")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "pca_extreme_curves.png", dpi=150)
    plt.close()


def _plot_extreme_pc2_curves(
    out_dir: Path,
    q: np.ndarray,
    I_matrix: np.ndarray,
    names: list[str],
    scores: np.ndarray,
) -> None:
    if scores.shape[0] < 2 or scores.shape[1] < 2:
        return
    idx_low = int(np.argmin(scores[:, 1]))
    idx_high = int(np.argmax(scores[:, 1]))

    plt.figure()
    plt.plot(
        q,
        I_matrix[idx_low],
        "o-",
        markersize=2,
        linewidth=1.2,
        alpha=0.8,
        label=f"Lowest PC2 ({names[idx_low]}, PC2={scores[idx_low, 1]:.3g})",
    )
    plt.plot(
        q,
        I_matrix[idx_high],
        "o-",
        markersize=2,
        linewidth=1.2,
        alpha=0.8,
        label=f"Highest PC2 ({names[idx_high]}, PC2={scores[idx_high, 1]:.3g})",
    )
    plt.xlabel("q (nm⁻¹)")
    plt.ylabel("I (a.u.)")
    plt.title("Curves with Extreme PC2 Scores")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "pca_extreme_curves_pc2.png", dpi=150)
    plt.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="PCA analysis on SAXS 1D curves (.dat).")
    ap.add_argument("--input-dir", type=Path, required=True, help="Directory containing .dat curves.")
    ap.add_argument("--output-dir", type=Path, required=True, help="Directory to write PCA outputs.")
    ap.add_argument("--glob", default="*.dat", help='Glob within input-dir (default: "*.dat").')
    ap.add_argument(
        "--filename-regex",
        default=None,
        help="Optional regex filter applied to the filename (e.g. '.*_\\\\d{5}\\\\.dat$').",
    )
    ap.add_argument("--q-min", type=float, default=None, help="Optional q-min filter (nm^-1).")
    ap.add_argument("--q-max", type=float, default=None, help="Optional q-max filter (nm^-1).")
    ap.add_argument("--n-points", type=int, default=1000, help="Interpolation grid size when needed.")
    ap.add_argument("--times-csv", type=Path, default=None, help="Optional CSV with columns basename,datetime for coloring/exports.")
    ap.add_argument("--name-strip-prefix", default="", help='If set, strip this prefix from each curve stem when forming "name".')
    ap.add_argument("--log-transform", action="store_true", help="Apply natural log to intensity before PCA.")
    ap.add_argument(
        "--min-intensity",
        type=float,
        default=None,
        help="If set, raise if any intensity <= min-intensity in selected q-range (after scaling).",
    )
    ap.add_argument(
        "--trend-scale",
        action="store_true",
        help="Scale each curve by a linear trend m(t)=a*t+b (so m(t_ref) maps to scale 1.0). Requires --trend-a/--trend-b/--trend-ref-hm and datetimes.",
    )
    ap.add_argument("--trend-a", type=float, default=None, help="Trend slope a (intensity / second).")
    ap.add_argument("--trend-b", type=float, default=None, help="Trend intercept b (intensity).")
    ap.add_argument(
        "--trend-ref-hm",
        default=None,
        help="Optional reference time-of-day HH:MM where scaling factor is 1.0. If omitted, uses the earliest timestamp among curves.",
    )
    args = ap.parse_args()

    in_dir = args.input_dir
    if not in_dir.is_dir():
        raise SystemExit(f"Input directory not found: {in_dir}")

    paths = sorted(in_dir.glob(args.glob))
    paths = [p for p in paths if p.is_file() and p.suffix.lower() == ".dat"]
    if args.filename_regex:
        rx = re.compile(args.filename_regex)
        paths = [p for p in paths if rx.match(p.name)]
    if not paths:
        raise SystemExit(f"No .dat files matched in {in_dir} with glob {args.glob!r}")

    times_map: dict[str, datetime] | None = None
    if args.times_csv is not None:
        if not args.times_csv.is_file():
            raise SystemExit(f"Times CSV not found: {args.times_csv}")
        times_map = _load_times_csv(args.times_csv)

    curves: list[Curve1D] = []
    failures: list[tuple[str, str]] = []
    for p in paths:
        try:
            q, I, sigma = load_saxs_1d_any(str(p))
            q_a = np.asarray(q, dtype=float)
            I_a = np.asarray(I, dtype=float)
            mask = np.isfinite(q_a) & np.isfinite(I_a)
            q_a = q_a[mask]
            I_a = I_a[mask]
            if q_a.size < 3:
                raise ValueError("Too few finite points")
            name = p.stem
            if args.name_strip_prefix and name.startswith(args.name_strip_prefix):
                name = name[len(args.name_strip_prefix):]
            dt = times_map.get(name) if times_map is not None else None
            curves.append(
                Curve1D(
                    name=name,
                    path=p,
                    q=q_a,
                    I=I_a,
                    sigma=None if sigma is None else np.asarray(sigma),
                    dt=dt,
                )
            )
        except Exception as e:
            failures.append((p.name, str(e)))

    if len(curves) < 3:
        msg = f"Too few curves loaded for PCA: {len(curves)}"
        if failures:
            msg += f" (first failure: {failures[0][0]}: {failures[0][1]})"
        raise SystemExit(msg)

    _ensure_dir(args.output_dir)

    # Optional trend scaling and/or log transform is applied per-curve before matrix building.
    if args.trend_scale:
        if args.trend_a is None or args.trend_b is None or args.trend_ref_hm is None:
            # trend_ref_hm is optional; earliest timestamp is used if omitted
            if args.trend_a is None or args.trend_b is None:
                raise SystemExit("--trend-scale requires --trend-a and --trend-b")
        if any(c.dt is None for c in curves):
            raise SystemExit("--trend-scale requires datetimes for all curves (provide --times-csv matching names)")
        a = float(args.trend_a)
        b = float(args.trend_b)

        # Reference timestamp:
        # - If trend_ref_hm provided: use date of earliest curve at that HH:MM (seconds=0).
        # - Else: use earliest curve timestamp exactly.
        dts = [c.dt for c in curves if c.dt is not None]
        dt_earliest = min(dts)
        if args.trend_ref_hm is not None:
            ref_hm = _parse_hm(args.trend_ref_hm)
            dt_ref = dt_earliest.replace(
                hour=ref_hm[0], minute=ref_hm[1], second=0, microsecond=0
            )
        else:
            dt_ref = dt_earliest
        t_ref = dt_ref.timestamp()
        m_ref = a * t_ref + b
        if not np.isfinite(m_ref) or m_ref == 0:
            raise SystemExit(f"Invalid reference trend value at {dt_ref.isoformat()}: {m_ref}")

        scaled_curves: list[Curve1D] = []
        for c in curves:
            assert c.dt is not None
            t = c.dt.timestamp()
            m_t = a * t + b
            if not np.isfinite(m_t) or m_t == 0:
                raise SystemExit(f"Invalid trend value for {c.name} at {c.dt.isoformat()}: {m_t}")
            s = m_ref / m_t
            scaled_curves.append(
                Curve1D(
                    name=c.name,
                    path=c.path,
                    q=c.q,
                    I=c.I * float(s),
                    sigma=c.sigma,
                    dt=c.dt,
                )
            )
        curves = scaled_curves

    if args.log_transform or args.min_intensity is not None:
        q_min_for_check = float(args.q_min) if args.q_min is not None else -np.inf
        q_max_for_check = float(args.q_max) if args.q_max is not None else np.inf
        min_I = float(args.min_intensity) if args.min_intensity is not None else None
        transformed: list[Curve1D] = []
        for c in curves:
            q_a = c.q
            I_a = np.asarray(c.I, dtype=float)
            if min_I is not None:
                mask = np.isfinite(q_a) & np.isfinite(I_a) & (q_a >= q_min_for_check) & (q_a <= q_max_for_check)
                if np.any(mask) and np.any(I_a[mask] <= min_I):
                    raise SystemExit(
                        f"Intensity <= {min_I} encountered in selected q-range for {c.name} (needed for log/PCA)."
                    )
            if args.log_transform:
                I_a = np.log(I_a)
            transformed.append(
                Curve1D(name=c.name, path=c.path, q=c.q, I=I_a, sigma=c.sigma, dt=c.dt)
            )
        curves = transformed

    # Build matrix
    if _check_q_values_identical(curves):
        q_common = curves[0].q
        I_matrix = np.vstack([c.I for c in curves])
    else:
        q_common, I_matrix = _interpolate_to_common_grid(curves, n_points=args.n_points)

    q_common, I_matrix = _apply_q_range(q_common, I_matrix, args.q_min, args.q_max)

    # Center and PCA
    I_mean = np.mean(I_matrix, axis=0)
    I_centered = I_matrix - I_mean
    pca = PCA()
    pca.fit(I_centered)
    components = pca.components_
    scores = pca.transform(I_centered)
    explained = pca.explained_variance_ratio_

    names = [c.name for c in curves]
    times_list: list[datetime] | None = None
    if times_map is not None:
        # only include times that exist (missing -> NaT-like: keep None and don't color)
        if all((n in times_map) for n in names):
            times_list = [times_map[n] for n in names]

    # Plots
    _plot_components(args.output_dir, q_common, components, explained)
    _plot_scatter_pc1_pc2(args.output_dir, scores, explained, times_list)
    _plot_all_curves_colored(args.output_dir, curves, scores)
    _plot_extreme_pc1_curves(args.output_dir, q_common, I_matrix, names, scores)
    _plot_extreme_pc2_curves(args.output_dir, q_common, I_matrix, names, scores)

    # Save results
    np.savez(
        args.output_dir / "pca_results.npz",
        q_common=q_common,
        mean_curve=I_mean,
        components=components[:10],
        scores=scores,
        explained_variance=explained[:10],
        file_names=np.array(names),
    )
    _save_scores_csv(args.output_dir / "pca_scores.csv", names, scores, times_map)

    print(f"Processed {len(curves)} curves. Outputs in {args.output_dir}")
    if failures:
        print(f"{len(failures)} files failed to load.")
        for name, err in failures[:10]:
            print(f"- {name}: {err}")
        if len(failures) > 10:
            print(f"... {len(failures) - 10} more failures not shown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

