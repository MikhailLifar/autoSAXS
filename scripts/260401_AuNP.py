#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

try:
    from autosaxs.utils import load_saxs_1d_any
except ModuleNotFoundError:
    # Allow running from workspace root without installing the package.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from autosaxs.utils import load_saxs_1d_any


@dataclass(frozen=True)
class CurvePoint:
    dt: datetime
    mean_intensity: float
    basename: str


_FIVE_DIGIT_SUFFIX_RE = re.compile(r".*_(\d{5})$")

PCA_Q_MAX_DEFAULT = 5.5
LOG_MIN_INTENSITY_DEFAULT = 1e-4


def _curve_basename(curve_path: Path) -> str:
    """Map averaged curve filename to raw TIFF basename used in *_times.csv."""
    stem = curve_path.stem
    return stem[4:] if stem.startswith("int_") else stem


def _require_five_digit_suffix(basename: str, filename_for_msg: str) -> None:
    m = _FIVE_DIGIT_SUFFIX_RE.match(basename)
    if not m:
        raise ValueError(
            f"Filename does not end with _<5 digits>, skipping: {filename_for_msg}"
        )


def _parse_dt(s: str) -> datetime:
    s = s.strip()
    try:
        # expected: 2025-12-24T05:07:39
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
    if not path.is_file():
        raise FileNotFoundError(f"Times CSV not found: {path}")
    out: dict[str, datetime] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if (
            r.fieldnames is None
            or "basename" not in r.fieldnames
            or "datetime" not in r.fieldnames
        ):
            raise ValueError(f"Expected columns basename,datetime in {path}")
        for row in r:
            b = (row.get("basename") or "").strip()
            d = (row.get("datetime") or "").strip()
            if not b or not d:
                continue
            out[b] = _parse_dt(d)
    if not out:
        raise ValueError(f"No rows read from {path}")
    return out


def _resolve_times_csv(data_dir: Path) -> Path:
    """
    Required layout: data_dir/times.csv
    """
    p = data_dir / "times.csv"
    if p.is_file():
        return p
    raise FileNotFoundError(f"Times CSV not found (expected {p})")


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


def _plot_one_dir(
    data_dir: Path,
    output_path: Path,
    title: str,
    times_by_basename: dict[str, datetime],
    q_max: float,
) -> None:
    dat_files = sorted(p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".dat")
    if not dat_files:
        raise ValueError(f"No .dat files found in {data_dir}")

    points: list[CurvePoint] = []
    failures: list[tuple[str, str]] = []
    skipped_nonmatching = 0
    skipped_missing_time = 0
    for p in dat_files:
        try:
            basename = _curve_basename(p)
            _require_five_digit_suffix(basename, p.name)
            dt = times_by_basename.get(basename)
            if dt is None:
                skipped_missing_time += 1
                continue
            q, I, _ = load_saxs_1d_any(str(p))
            q_a = np.asarray(q, dtype=float)
            I_a = np.asarray(I, dtype=float)
            mask = np.isfinite(q_a) & np.isfinite(I_a) & (q_a >= 0.0) & (q_a <= q_max)
            if not np.any(mask):
                raise ValueError(f"No points in q-range [0, {q_max}]")
            mean_i = float(np.mean(I_a[mask]))
            points.append(CurvePoint(dt=dt, mean_intensity=mean_i, basename=basename))
        except Exception as e:  # keep going; report at end
            msg = str(e)
            if "end with _<5 digits>" in msg:
                skipped_nonmatching += 1
            else:
                failures.append((p.name, msg))

    if not points:
        msg = f"No curves were successfully processed in {data_dir.name}."
        if failures:
            msg += f" First failure: {failures[0][0]}: {failures[0][1]}"
        raise ValueError(msg)

    points.sort(key=lambda x: x.dt)
    xs = [p.dt for p in points]
    ys = [p.mean_intensity for p in points]

    plt.figure(figsize=(11, 4.5))
    plt.plot(xs, ys, marker="o", linewidth=1.5, markersize=3)
    plt.xlabel("Time")
    plt.ylabel("Mean intensity")
    plt.title(title)
    plt.grid(True, alpha=0.3)
    ax = plt.gca()
    ax.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(ax.xaxis.get_major_locator())
    )
    plt.gcf().autofmt_xdate()
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()

    if failures:
        print(
            f"[{data_dir.name}] Processed {len(points)} curves; {len(failures)} failed; "
            f"{skipped_nonmatching} skipped (non-matching name); {skipped_missing_time} skipped (no time in CSV)."
        )
        for name, err in failures[:10]:
            print(f"- {name}: {err}")
        if len(failures) > 10:
            print(f"... {len(failures) - 10} more failures not shown")
    else:
        print(
            f"[{data_dir.name}] Processed {len(points)} curves; {skipped_nonmatching} skipped (non-matching name); "
            f"{skipped_missing_time} skipped (no time in CSV)."
        )

    print(f"[{data_dir.name}] Saved: {output_path}")


def _build_screening_subset_dir(
    source_dir: Path,
    dest_dir: Path,
    times_by_basename: dict[str, datetime],
    start_hm: tuple[int, int],
    end_hm: tuple[int, int],
) -> int:
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Overwrite any existing files in dest_dir for deterministic reruns.
    for old in dest_dir.glob("*.dat"):
        try:
            old.unlink()
        except OSError:
            pass

    selected = 0
    for p in sorted(source_dir.glob("*.dat")):
        basename = _curve_basename(p)
        try:
            _require_five_digit_suffix(basename, p.name)
        except ValueError:
            continue
        dt = times_by_basename.get(basename)
        if dt is None:
            continue
        t = dt.time()
        if (t.hour, t.minute) < start_hm or (t.hour, t.minute) > end_hm:
            continue
        shutil.copy2(p, dest_dir / p.name)
        selected += 1
    return selected


def _fit_linear_trend_mean_intensity(
    data_dir: Path,
    times_by_basename: dict[str, datetime],
    *,
    q_max: float,
    window_start_hm: tuple[int, int],
    window_end_hm: tuple[int, int],
    min_intensity: float,
) -> tuple[float, float]:
    """
    Fit m(t) = a*t + b on averaged_screening within a time-of-day window.
    Here m(t) is mean intensity over q in [0, q_max].
    Uses t as seconds since epoch (datetime.timestamp()).
    """
    xs: list[float] = []
    ys: list[float] = []
    for p in sorted(data_dir.glob("*.dat")):
        basename = _curve_basename(p)
        try:
            _require_five_digit_suffix(basename, p.name)
        except ValueError:
            continue
        dt = times_by_basename.get(basename)
        if dt is None:
            continue
        t_hm = (dt.hour, dt.minute)
        if t_hm < window_start_hm or t_hm > window_end_hm:
            continue
        q, I, _ = load_saxs_1d_any(str(p))
        q_a = np.asarray(q, dtype=float)
        I_a = np.asarray(I, dtype=float)
        mask = np.isfinite(q_a) & np.isfinite(I_a) & (q_a >= 0.0) & (q_a <= q_max)
        if not np.any(mask):
            continue
        if np.any(I_a[mask] <= min_intensity):
            raise ValueError(f"Intensity <= {min_intensity} in selected q-range for trend fit: {p.name}")
        m = float(np.mean(I_a[mask]))
        xs.append(float(dt.timestamp()))
        ys.append(m)

    if len(xs) < 2:
        raise ValueError(f"Not enough points to fit trend in window {window_start_hm}-{window_end_hm}: got {len(xs)}")
    x = np.asarray(xs, dtype=float)
    y = np.asarray(ys, dtype=float)
    t0 = float(np.min(x))
    # Fit in a shifted frame for better conditioning: y = a*(t - t0) + b0
    a, b0 = np.polyfit(x - t0, y, 1)
    # Convert back to epoch-time form: y = a*t + b where b = b0 - a*t0
    b = float(b0 - a * t0)
    return float(a), float(b)


def _plot_pc_coefficients_vs_time(pca_out_dir: Path, title_suffix: str) -> None:
    """Plot PC1 and PC2 scores vs acquisition time from pca_scores.csv (written by pca_to_saxs_1d.py)."""
    scores_csv = pca_out_dir / "pca_scores.csv"
    if not scores_csv.is_file():
        print(f"[PCA] No {scores_csv.name} found in {pca_out_dir}; skipping PC vs time plots.")
        return

    rows: list[tuple[datetime, float, float]] = []
    with scores_csv.open("r", newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        if r.fieldnames is None or "pc1" not in r.fieldnames or "pc2" not in r.fieldnames:
            print(f"[PCA] Unexpected columns in {scores_csv}; skipping PC vs time plots.")
            return
        has_dt = "datetime" in r.fieldnames
        for row in r:
            dt_str = (row.get("datetime") or "").strip() if has_dt else ""
            if not dt_str:
                continue
            try:
                dt = _parse_dt(dt_str)
                pc1 = float(row["pc1"])
                pc2 = float(row["pc2"])
            except (ValueError, KeyError):
                continue
            rows.append((dt, pc1, pc2))

    if len(rows) < 1:
        print(f"[PCA] No rows with datetime in {scores_csv}; skipping PC vs time plots.")
        return

    rows.sort(key=lambda x: x[0])
    xs = [r[0] for r in rows]
    pc1s = [r[1] for r in rows]
    pc2s = [r[2] for r in rows]

    for yvals, label, fname in (
        (pc1s, "PC1", "pca_pc1_vs_time.png"),
        (pc2s, "PC2", "pca_pc2_vs_time.png"),
    ):
        plt.figure(figsize=(11, 4.5))
        plt.plot(xs, yvals, marker="o", linewidth=1.2, markersize=3)
        plt.xlabel("Time")
        plt.ylabel(f"{label} coefficient")
        plt.title(f"{label} vs acquisition time ({title_suffix})")
        plt.grid(True, alpha=0.3)
        ax = plt.gca()
        ax.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(ax.xaxis.get_major_locator())
        )
        plt.gcf().autofmt_xdate()
        plt.tight_layout()
        out_path = pca_out_dir / fname
        plt.savefig(out_path, dpi=200)
        plt.close()
        print(f"[PCA] Saved: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="AuNP utilities (mean intensity, selection, PCA).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mean = sub.add_parser("mean-intensity-plot", help="Plot mean intensity vs time for a data dir.")
    p_mean.add_argument("data_dir", type=Path)
    p_mean.add_argument("--q-max", type=float, default=PCA_Q_MAX_DEFAULT)

    p_sel = sub.add_parser("select-time-range", help="Copy curves in a time-of-day window to dest dir (and write times.csv).")
    p_sel.add_argument("data_dir", type=Path)
    p_sel.add_argument("dest_dir", type=Path)
    p_sel.add_argument("--start", required=True, help="HH:MM")
    p_sel.add_argument("--end", required=True, help="HH:MM")

    p_fit = sub.add_parser("fit-mean-trend", help="Fit m(t)=a*t+b on mean intensity in a time-of-day window.")
    p_fit.add_argument("data_dir", type=Path)
    p_fit.add_argument("--start", required=True, help="HH:MM")
    p_fit.add_argument("--end", required=True, help="HH:MM")
    p_fit.add_argument("--q-max", type=float, default=PCA_Q_MAX_DEFAULT)
    p_fit.add_argument("--min-intensity", type=float, default=LOG_MIN_INTENSITY_DEFAULT)

    p_pca_raw = sub.add_parser("pca-raw", help="Run PCA on raw curves in a data dir.")
    p_pca_raw.add_argument("data_dir", type=Path)
    p_pca_raw.add_argument("dest_dir", type=Path)
    p_pca_raw.add_argument("--q-max", type=float, default=PCA_Q_MAX_DEFAULT)
    p_pca_raw.add_argument("--filename-regex", default=None)

    p_pca_scaled = sub.add_parser("pca-scaled-log", help="Run PCA after trend scaling + ln transform + min intensity check.")
    p_pca_scaled.add_argument("data_dir", type=Path)
    p_pca_scaled.add_argument("dest_dir", type=Path)
    p_pca_scaled.add_argument("--trend-a", type=float, required=True)
    p_pca_scaled.add_argument("--trend-b", type=float, required=True)
    p_pca_scaled.add_argument("--q-max", type=float, default=PCA_Q_MAX_DEFAULT)
    p_pca_scaled.add_argument("--min-intensity", type=float, default=LOG_MIN_INTENSITY_DEFAULT)
    p_pca_scaled.add_argument("--filename-regex", default=None)

    args = parser.parse_args()

    if args.cmd == "mean-intensity-plot":
        data_dir: Path = args.data_dir
        q_max = float(args.q_max)
        times_csv = _resolve_times_csv(data_dir)
        times_by_basename = _load_times_csv(times_csv)
        out_path = data_dir.parent / f"mean_intensity_vs_time_{data_dir.name}.png"
        _plot_one_dir(
            data_dir=data_dir,
            output_path=out_path,
            title=f"Mean intensity vs acquisition time ({data_dir.name})",
            times_by_basename=times_by_basename,
            q_max=q_max,
        )
        return 0

    if args.cmd == "select-time-range":
        data_dir: Path = args.data_dir
        dest_dir: Path = args.dest_dir
        start_hm = _parse_hm(args.start)
        end_hm = _parse_hm(args.end)
        times_csv = _resolve_times_csv(data_dir)
        times_by_basename = _load_times_csv(times_csv)

        n = _build_screening_subset_dir(
            source_dir=data_dir,
            dest_dir=dest_dir,
            times_by_basename=times_by_basename,
            start_hm=start_hm,
            end_hm=end_hm,
        )

        # Write filtered times.csv into dest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        out_times = dest_dir / "times.csv"
        with times_csv.open("r", newline="", encoding="utf-8") as f_in, out_times.open(
            "w", newline="", encoding="utf-8"
        ) as f_out:
            r = csv.DictReader(f_in)
            w = csv.DictWriter(f_out, fieldnames=r.fieldnames)
            assert r.fieldnames is not None
            w.writeheader()
            for row in r:
                b = (row.get("basename") or "").strip()
                dt = times_by_basename.get(b)
                if dt is None:
                    continue
                hm = (dt.hour, dt.minute)
                if hm < start_hm or hm > end_hm:
                    continue
                w.writerow(row)

        print(f"Selected {n} curves into {dest_dir}")
        print(f"Wrote filtered times to {out_times}")
        return 0

    if args.cmd == "fit-mean-trend":
        data_dir: Path = args.data_dir
        q_max = float(args.q_max)
        start_hm = _parse_hm(args.start)
        end_hm = _parse_hm(args.end)
        times_csv = _resolve_times_csv(data_dir)
        times_by_basename = _load_times_csv(times_csv)
        a, b = _fit_linear_trend_mean_intensity(
            data_dir=data_dir,
            times_by_basename=times_by_basename,
            q_max=q_max,
            window_start_hm=start_hm,
            window_end_hm=end_hm,
            min_intensity=float(args.min_intensity),
        )
        print(f"a={a}")
        print(f"b={b}")
        return 0

    if args.cmd == "pca-raw":
        data_dir: Path = args.data_dir
        dest_dir: Path = args.dest_dir
        times_csv = _resolve_times_csv(data_dir)
        pca_script = Path(__file__).with_name("pca_to_saxs_1d.py")
        cmd = [
            sys.executable,
            str(pca_script),
            "--input-dir",
            str(data_dir),
            "--output-dir",
            str(dest_dir),
            "--times-csv",
            str(times_csv),
            "--name-strip-prefix",
            "int_",
            "--q-max",
            str(float(args.q_max)),
        ]
        if args.filename_regex:
            cmd += ["--filename-regex", args.filename_regex]
        subprocess.run(cmd, check=True)
        _plot_pc_coefficients_vs_time(dest_dir, title_suffix=data_dir.name)
        return 0

    if args.cmd == "pca-scaled-log":
        data_dir: Path = args.data_dir
        dest_dir: Path = args.dest_dir
        times_csv = _resolve_times_csv(data_dir)
        pca_script = Path(__file__).with_name("pca_to_saxs_1d.py")
        cmd = [
            sys.executable,
            str(pca_script),
            "--input-dir",
            str(data_dir),
            "--output-dir",
            str(dest_dir),
            "--times-csv",
            str(times_csv),
            "--name-strip-prefix",
            "int_",
            "--q-max",
            str(float(args.q_max)),
            "--trend-scale",
            f"--trend-a={float(args.trend_a)}",
            f"--trend-b={float(args.trend_b)}",
            "--min-intensity",
            str(float(args.min_intensity)),
            "--log-transform",
        ]
        if args.filename_regex:
            cmd += ["--filename-regex", args.filename_regex]
        subprocess.run(cmd, check=True)
        _plot_pc_coefficients_vs_time(dest_dir, title_suffix=f"{data_dir.name} scaled+log")
        return 0

    raise SystemExit("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())

