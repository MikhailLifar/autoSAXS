"""Artifact writers for model_dr_mc (CSV/YAML/handoff/report fragments)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.report_fragments import write_skill_report_fragments
from autosaxs.core.utils import _strip_sub_int_prefix


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def write_dr_csv(result: Dict[str, Any], path: str | Path) -> str:
    path = Path(path)
    df = pd.DataFrame(
        {
            "R_nm": np.asarray(result["r_nm"], dtype=float),
            "dR_nm": np.asarray(result["dr_nm"], dtype=float),
            "D": np.asarray(result["D"], dtype=float),
            "D_std": np.asarray(result["D_std"], dtype=float),
        }
    )
    df.to_csv(path, index=False)
    return str(path)


def write_stats_yml(result: Dict[str, Any], path: str | Path) -> str:
    path = Path(path)
    doc = {
        "n_rep": result.get("n_rep"),
        "n_contrib": result.get("n_contrib"),
        "conv_crit": result.get("conv_crit"),
        "n_cores": result.get("n_cores"),
        "nbins": result.get("nbins"),
        "n_bin": result.get("n_bin"),
        "max_iter": result.get("max_iter"),
        "sld": result.get("sld"),
        "sld_solvent": result.get("sld_solvent"),
        "q_min_nm": _safe_float(result.get("q_min_nm")),
        "q_max_nm": _safe_float(result.get("q_max_nm")),
        "r_min_nm": _safe_float(result.get("r_min_nm")),
        "r_max_nm": _safe_float(result.get("r_max_nm")),
        "gof_mean": _safe_float(result.get("gof_mean")),
        "gof_std": _safe_float(result.get("gof_std")),
        "mode_mean_nm": _safe_float(result.get("mode_mean_nm")),
        "mode_mean_std_nm": _safe_float(result.get("mode_mean_std_nm")),
        "mode_total": _safe_float(result.get("mode_total")),
        "peaks_nm": [float(x) for x in (result.get("peaks_nm") or [])],
        "n_components_suggested": int(result.get("n_components_suggested") or 1),
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
    return str(path)


def write_handoff_yml(result: Dict[str, Any], path: str | Path, *, base: str) -> str:
    path = Path(path)
    peaks = [float(x) for x in (result.get("peaks_nm") or [])]
    doc = {
        "skill": "model_dr_mc",
        "basename": base,
        "n_components_suggested": int(result.get("n_components_suggested") or 1),
        "peaks_nm": peaks,
        "mode_mean_nm": _safe_float(result.get("mode_mean_nm")),
        "mode_mean_std_nm": _safe_float(result.get("mode_mean_std_nm")),
        "r_min_nm": _safe_float(result.get("r_min_nm")),
        "r_max_nm": _safe_float(result.get("r_max_nm")),
        "model_mixture": {
            "n_components_suggested": int(result.get("n_components_suggested") or 1),
            "peaks_nm": peaks,
            "r_min_nm": _safe_float(result.get("r_min_nm")),
            "r_max_nm": _safe_float(result.get("r_max_nm")),
            "mixture_dist_hint": "Schultz",
        },
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
    return str(path)


def write_report_and_artifacts(
    *,
    result: Dict[str, Any],
    output_dir: str | Path,
    profile_path: str,
    plot_paths: Dict[str, str],
) -> Dict[str, str]:
    output_dir = Path(output_dir)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile_path))[0])

    dr_csv = write_dr_csv(result, output_dir / "mcsas_dr.csv")
    stats_yml = write_stats_yml(result, output_dir / "mcsas_stats.yml")
    handoff = write_handoff_yml(result, output_dir / f"{base}_model_dr_mc.yml", base=base)

    fit_png = plot_paths.get("fit_png_path") or ""
    dr_png = plot_paths.get("dr_png_path") or ""
    card_png = plot_paths.get("result_card_png_path") or ""

    md_parts: List[str] = [
        "### McSAS3 form-free D(R)\n",
        (
            f"n_rep={result.get('n_rep')}, n_contrib={result.get('n_contrib')}, "
            f"gof={_safe_float(result.get('gof_mean'))}\n"
        ),
    ]
    peaks = result.get("peaks_nm") or []
    if peaks:
        md_parts.append("Peaks (nm): " + ", ".join(f"{p:.3g}" for p in peaks) + "\n")
    if fit_png:
        md_parts.append(f"![McSAS fit]({os.path.basename(fit_png)})\n")
    if dr_png:
        md_parts.append(f"![McSAS D(R)]({os.path.basename(dr_png)})\n")
    if card_png:
        md_parts.append(f"![McSAS result card]({os.path.basename(card_png)})\n")

    summary_refs = [
        {"role": "mcsas_dr_csv", "path": os.path.basename(dr_csv), "format": "csv"},
        {"role": "mcsas_stats", "path": os.path.basename(stats_yml), "format": "text"},
    ]
    if fit_png:
        summary_refs.append({"role": "mcsas_fit_png", "path": os.path.basename(fit_png), "format": "png"})
    if dr_png:
        summary_refs.append({"role": "mcsas_dr_png", "path": os.path.basename(dr_png), "format": "png"})
    if card_png:
        summary_refs.append(
            {"role": "mcsas_result_card_png", "path": os.path.basename(card_png), "format": "png"}
        )

    write_skill_report_fragments(
        str(output_dir),
        base,
        "model_dr_mc",
        "".join(md_parts),
        summary_references=summary_refs,
        summary_extra={
            "n_rep": result.get("n_rep"),
            "gof_mean": _safe_float(result.get("gof_mean")),
            "peaks_nm": peaks,
            "n_components_suggested": result.get("n_components_suggested"),
        },
    )
    return {
        "dr_csv_path": dr_csv,
        "stats_path": stats_yml,
        "handoff_path": handoff,
    }
