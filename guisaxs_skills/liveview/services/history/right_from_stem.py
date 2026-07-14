"""
Load right-column analysis previews from disk using autosaxs per-sample subdirs (stem = TIFF basename).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from autosaxs.skill.gnom_fit_common import default_atsas_failure_message

from ...session.output_paths import (
    fit_sizes_dir,
    integrated_dat_path,
    mixture_dir,
    subtracted_dat_path,
    tiff_output_root,
)
from ...session.state import AnalysisMode, LiveviewWatchMode


def _mtime_key(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


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


def _read_atsas_failure_payload(subdir: Path, stem: str, *, skill_id: str) -> dict[str, Any] | None:
    if not subdir.is_dir():
        return None
    summary_name = f"{stem}_fit_distances_best.yml" if skill_id == "fit_distances" else f"{stem}_fit_sizes_best.yml"
    summary_path = subdir / summary_name
    if not summary_path.is_file():
        matches = sorted(subdir.glob(f"*_fit_{'distances' if skill_id == 'fit_distances' else 'sizes'}_best.yml"))
        summary_path = matches[0] if matches else summary_path
    message = ""
    failed = False
    if summary_path.is_file():
        try:
            data = yaml.safe_load(summary_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                if data.get("atsas_fit_ok") is False or data.get("gnom_failed") is True:
                    failed = True
                msg = data.get("failure_message")
                if isinstance(msg, str) and msg.strip():
                    message = msg.strip()
        except (OSError, TypeError, yaml.YAMLError):
            pass
    failure_txt = subdir / f"{stem}_atsas_fit_failure.txt"
    if not failure_txt.is_file():
        txts = sorted(subdir.glob("*_atsas_fit_failure.txt"), key=_mtime_key, reverse=True)
        failure_txt = txts[0] if txts else failure_txt
    if failure_txt.is_file():
        failed = True
        try:
            message = failure_txt.read_text(encoding="utf-8", errors="replace").strip() or message
        except OSError:
            pass
    if not failed:
        return None
    payload: dict[str, Any] = {
        "atsas_fit_ok": False,
        "gnom_failed": True,
        "failure_message": message or default_atsas_failure_message(skill_id),
    }
    if skill_id == "fit_sizes":
        payload["best_dr_png_path"] = ""
        payload["dr_csv_path"] = ""
    return payload


def apply_right_outputs_from_disk(
    right: Any,
    *,
    watchdir: Path,
    tiff_stem: str,
    mode: AnalysisMode,
    tiff_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> None:
    """Clear analysis previews, then load paths for ``tiff_stem`` under the TIFF output root."""
    right.clear_output_previews()
    if mode == AnalysisMode.OFF or not (tiff_stem or "").strip():
        return
    stem = tiff_stem.strip()
    root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)

    if mode == AnalysisMode.MONODISPERSE:
        sub = subtracted_dat_path(root=root, stem=stem)
        integ = integrated_dat_path(root=root, stem=stem, integrator_ready=True)
        prof = sub if sub.is_file() else integ
        profile_path = str(prof.resolve()) if prof.is_file() else ""
        if hasattr(right, "load_monodisperse_from_disk"):
            right.load_monodisperse_from_disk(
                profile_path=profile_path,
                stem=stem,
                tiff_path=tiff_path,
            )
        return

    if mode == AnalysisMode.POLYDISPERSE_DR:
        fs = fit_sizes_dir(root) / stem
        failed = _read_atsas_failure_payload(fs, stem, skill_id="fit_sizes")
        if failed is not None:
            right.ingest_skill_result(failed, skill_name="fit_sizes")
            return
        fp, dp = _discover_fit_sizes_pngs(fs, stem)
        right.ingest_skill_result({"fit_vs_exp_png_path": fp, "best_dr_png_path": dp}, skill_name="fit_sizes")
        return

    if mode == AnalysisMode.POLYDISPERSE_MIXTURE:
        mx = mixture_dir(root) / stem
        comp = mx / "mixture_comparison_I_vs_q.png"
        dist = mx / "mixture_distributions.png"
        right.ingest_skill_result(
            {
                "comparison_path": str(comp.resolve()) if comp.is_file() else "",
                "distributions_path": str(dist.resolve()) if dist.is_file() else "",
            },
            skill_name="fit_mixture",
        )
