"""
Load right-column analysis previews from disk using autosaxs per-sample subdirs (stem = TIFF basename).
Matches ``apply_batch(..., per_sample_subdir="always")`` layout under the watch directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..state import AnalysisMode


def _mtime_key(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _discover_fit_distances_pngs(subdir: Path, stem: str) -> tuple[str, str]:
    """GNOM / fit_distances: ``{stem}_fits.png`` and a companion p(r) PNG (not ``*_fits.png``)."""
    if not subdir.is_dir():
        return "", ""
    fit: Path | None = subdir / f"{stem}_fits.png"
    if not fit.is_file():
        cands = sorted(
            (p for p in subdir.glob("*_fits.png") if "fit_sizes" not in p.name),
            key=_mtime_key,
            reverse=True,
        )
        fit = cands[0] if cands else None
    fp = str(fit.resolve()) if fit is not None and fit.is_file() else ""
    fp_res = Path(fp).resolve() if fp else None

    pr_cands: list[Path] = []
    for p in subdir.glob("*.png"):
        if fp_res is not None and p.resolve() == fp_res:
            continue
        if p.name.endswith("_fits.png"):
            continue
        if "fit_sizes" in p.name or p.name.endswith("_DR.png"):
            continue
        pr_cands.append(p)
    pr_cands.sort(key=_mtime_key, reverse=True)
    pp = str(pr_cands[0].resolve()) if pr_cands else ""
    return fp, pp


def _discover_fit_sizes_pngs(subdir: Path, stem: str) -> tuple[str, str]:
    if not subdir.is_dir():
        return "", ""
    fp: Path | None = subdir / f"{stem}_fit_sizes_fits.png"
    if not fp.is_file():
        cands = sorted(subdir.glob("*_fit_sizes_fits.png"), key=_mtime_key, reverse=True)
        fp = cands[0] if cands else None
    fps = str(fp.resolve()) if fp is not None and fp.is_file() else ""
    drs = sorted(subdir.glob("*_DR.png"), key=_mtime_key, reverse=True)
    dps = str(drs[0].resolve()) if drs else ""
    return fps, dps


def apply_right_outputs_from_disk(
    right: Any,
    *,
    watchdir: Path,
    tiff_stem: str,
    mode: AnalysisMode,
) -> None:
    """Clear analysis previews, then load paths under ``watchdir`` for ``tiff_stem`` and ``mode``."""
    right.clear_output_previews()
    if mode == AnalysisMode.OFF or not (tiff_stem or "").strip():
        return
    stem = tiff_stem.strip()
    wd = watchdir.expanduser().resolve()

    if mode == AnalysisMode.MONODISPERSE_PR:
        fd = wd / "fit_distances" / stem
        fp, pp = _discover_fit_distances_pngs(fd, stem)
        right.ingest_skill_result({"fit_vs_exp_png_path": fp, "best_pr_png_path": pp})
        return

    if mode == AnalysisMode.MONODISPERSE_DAM:
        fd = wd / "fit_distances" / stem
        fp, pp = _discover_fit_distances_pngs(fd, stem)
        right.ingest_skill_result({"fit_vs_exp_png_path": fp, "best_pr_png_path": pp})
        dam = wd / "dammif" / stem
        if dam.is_dir():
            right.ingest_skill_result({"output_subdir": str(dam.resolve())})
        return

    if mode == AnalysisMode.MONODISPERSE_BODIES:
        fb = wd / "fit_bodies" / stem
        if fb.is_dir():
            right.ingest_skill_result({"output_subdir": str(fb.resolve())})
        return

    if mode == AnalysisMode.POLYDISPERSE_DR:
        fs = wd / "fit_sizes" / stem
        fp, dp = _discover_fit_sizes_pngs(fs, stem)
        right.ingest_skill_result({"fit_vs_exp_png_path": fp, "best_dr_png_path": dp})
        return

    if mode == AnalysisMode.POLYDISPERSE_MIXTURE:
        mx = wd / "mixture" / stem
        comp = mx / "mixture_comparison_I_vs_q.png"
        dist = mx / "mixture_distributions.png"
        right.ingest_skill_result(
            {
                "comparison_path": str(comp.resolve()) if comp.is_file() else "",
                "distributions_path": str(dist.resolve()) if dist.is_file() else "",
            }
        )
