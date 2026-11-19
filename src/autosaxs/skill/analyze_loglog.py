from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.profile_features import (
    dominant_mid_q_tag,
    low_q_plateau_hint,
    powerlaw_segments,
)

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    run_guinier_analysis,
    run_with_cache,
    write_data,
    write_saxs_atsas_format,
)


# Regime enum strings (SSOT for playbooks / passport).
REGIME_TAGS = (
    "rod_like",
    "disk_like",
    "coil_like",
    "compact_porod_like",
    "fractal_like",
    "unclear",
)


def analyze_loglog(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    rg_nm: Optional[float] = None,
    n_min: int = 8,
    max_segments: int = 6,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str], float, int]]:
    """
    SAXS / small-angle x-ray scattering: whole-curve log–log regime analysis.

    Provides the initial log[I] vs log[q] inspection after buffer subtraction
    (Jacques & Trewhella 2010 PDF p.8) and soft-tags dimensionality / Porod-exponent
    regimes −1 / −2 / −4 (rod / disk / sphere; Li 2016 Chem. Rev. letter H PDF pp.8–9)
    plus polymer / fractal-like bands (Putnam 2007; Jeffries primer). Fits slopes on
    original I(q) then annotates the log–log figure (Pauw 2013 linearization caution).

    Soft metrics only — never hard-gates modeling. Replaces the former ``plot`` skill's
    dumb ``loglog_*.png`` with an annotated whole-curve artifact + passport.

    **Regime tags** (``regimes[*].tag`` / ``dominant_mid_q_tag``):
    ``rod_like`` | ``disk_like`` | ``coil_like`` | ``compact_porod_like`` |
    ``fractal_like`` | ``unclear``.

    Units: ``q`` in nm⁻¹, lengths in nm. Shared slope math lives in
    ``autosaxs.core.profile_features`` (same SSOT as ``powerlaw_exponent`` / ``fit_porod``).

    ### Arguments

    - `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where outputs are written.
    - `config_path` (str | None, default `None`): Deprecated YAML/`analyze_loglog` section; ignored.
    - `rg_nm` (float | None, default `None`): Optional Rg (nm) for Guinier-band annotation only.
      If omitted, a light in-process Guinier is attempted; failure is soft (no band).
    - `n_min` (int, default `8`): Minimum points per slope window.
    - `max_segments` (int, default `6`): Cap on retained regime segments.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict` with:

    - `results_path`: Text passport mirror (`regimes`, tags).
    - `loglog_plot_path`: Annotated whole-curve log–log PNG.
    - `loglog_dat_path`: Tabular ``q, I`` companion (optional segment id).
    - Passport fields: `regimes` (JSON string of segment list), `dominant_mid_q_tag`,
      `n_regimes`, `n_dropped_nonpositive`, `low_q_plateau_hint`,
      optional `blank_sensitivity_hint`, `rg_nm_context`.

    ### Python usage

    ```python
    from autosaxs.skill import analyze_loglog

    out = analyze_loglog(
        profile="subtracted/sub_sample_01.dat",
        output_dir="loglog",
        use_cache=False,
    )
    print(out["dominant_mid_q_tag"], out["n_regimes"])
    ```

    ### CLI usage

    ```bash
    autosaxs analyze-loglog subtracted/sub_sample_01.dat --output-dir loglog
    autosaxs analyze-loglog subtracted/sub_sample_01.dat --rg-nm 3.2 --output-dir loglog
    ```
    """
    _ = config_path
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("analyze_loglog input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _analyze_loglog_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        rg_nm=rg_nm,
        n_min=n_min,
        max_segments=max_segments,
    )


def _optional_guinier_rg(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
    *,
    event_bus: Optional[EventBus],
) -> Optional[float]:
    try:
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": "analyze_loglog: running fit_guinier (in-process, plot banding only)…"},
            )
        results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
        rg = results.get("chosen_Rg")
        if rg is None:
            return None
        return float(rg)
    except Exception as exc:
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"analyze_loglog: Guinier Rg unavailable ({exc}); continuing without band."},
            )
        return None


def _plot_annotated_loglog(
    q: np.ndarray,
    I: np.ndarray,
    regimes: List[Dict[str, Any]],
    *,
    title: str,
    plot_path: str,
    rg_nm: Optional[float],
    dominant_tag: str,
) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    m = np.isfinite(q) & np.isfinite(I) & (q > 0) & (I > 0)
    ax.loglog(q[m], I[m], "o", ms=3.5, color="#1f77b4", label="data", zorder=3)

    colors = {
        "rod_like": "#2ca02c",
        "disk_like": "#ff7f0e",
        "coil_like": "#9467bd",
        "compact_porod_like": "#d62728",
        "fractal_like": "#8c564b",
        "unclear": "#7f7f7f",
    }
    for seg in regimes:
        q0 = float(seg["q_min_nm"])
        q1 = float(seg["q_max_nm"])
        alpha = float(seg["alpha"])
        tag = str(seg.get("tag") or "unclear")
        color = colors.get(tag, "#7f7f7f")
        ax.axvspan(q0, q1, color=color, alpha=0.15, zorder=1)
        # Guide line through segment median intensity.
        mid_mask = m & (q >= q0) & (q <= q1)
        if int(mid_mask.sum()) >= 2:
            q_mid = float(np.exp(np.mean(np.log(q[mid_mask]))))
            I_mid = float(np.exp(np.mean(np.log(I[mid_mask]))))
            A = I_mid / (q_mid ** alpha) if q_mid > 0 else 1.0
            qq = np.logspace(np.log10(q0), np.log10(q1), 40)
            ax.loglog(qq, A * (qq ** alpha), "-", color=color, lw=1.6, zorder=2, label=f"{tag} (α={alpha:.2f})")

    # Reference slope ticks at a high-q anchor.
    if m.any():
        q_hi = float(np.percentile(q[m], 85))
        I_hi = float(np.interp(q_hi, q[m], I[m]))
        for alpha_ref, lab in ((-1.0, "−1"), (-2.0, "−2"), (-4.0, "−4")):
            A = I_hi / (q_hi ** alpha_ref)
            qq = np.logspace(np.log10(max(q_hi * 0.5, float(np.min(q[m])))), np.log10(float(np.max(q[m]))), 20)
            ax.loglog(qq, A * (qq ** alpha_ref), ":", color="#aaaaaa", lw=1.0, alpha=0.8)
            ax.text(qq[-1], A * (qq[-1] ** alpha_ref), lab, fontsize=7, color="#888888")

    if rg_nm is not None and rg_nm > 0 and m.any():
        # Approximate Guinier validity band q·Rg ≲ 1.3
        q_g = 1.3 / float(rg_nm)
        ax.axvline(q_g, color="#17becf", ls="--", lw=1.0, label=f"q·Rg≈1.3 (Rg={rg_nm:.2f} nm)")

    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel("I (a.u.)")
    ax.set_title(f"{title} [{dominant_tag}]")
    # Deduplicate legend labels.
    handles, labels = ax.get_legend_handles_labels()
    seen = set()
    uniq_h, uniq_l = [], []
    for h, lab in zip(handles, labels):
        if lab in seen:
            continue
        seen.add(lab)
        uniq_h.append(h)
        uniq_l.append(lab)
    ax.legend(uniq_h, uniq_l, loc="best", fontsize=7)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


@apply_batch(stem_from_keys="profile", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["rg_nm", "n_min", "max_segments"],
    include_config_in_hash=False,
)
def _analyze_loglog_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    *,
    rg_nm: Optional[float] = None,
    n_min: int = 8,
    max_segments: int = 6,
) -> Dict[str, Union[str, List[str], float, int]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("analyze_loglog requires input_paths['profile']")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    results_path = os.path.join(output_dir, f"{base}_loglog_results.txt")
    loglog_plot_path = os.path.join(output_dir, f"loglog_{base}.png")
    loglog_dat_path = os.path.join(output_dir, f"loglog_{base}.dat")
    params_yaml_path = os.path.join(output_dir, f"{base}_loglog_params.yml")

    q_arr, I_arr, sigma_arr = load_saxs_1d_any(profile)
    q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)

    if rg_nm is None:
        atsas_tmp = os.path.join(output_dir, f".{base}_guinier_atsas.dat")
        write_saxs_atsas_format(atsas_tmp, q_arr, I_arr, sigma_arr)
        rg_nm = _optional_guinier_rg(
            q_arr, I_arr, sigma_arr, atsas_tmp, event_bus=event_bus
        )

    seg_doc = powerlaw_segments(
        q_arr,
        I_arr,
        sigma=sigma_arr,
        n_min=n_min,
        max_segments=max_segments,
    )
    regimes: List[Dict[str, Any]] = list(seg_doc.get("segments") or [])
    n_dropped = int(seg_doc.get("n_dropped_nonpositive") or 0)
    dominant = dominant_mid_q_tag(regimes)
    if dominant not in REGIME_TAGS:
        dominant = "unclear"

    lq = low_q_plateau_hint(q_arr, I_arr)
    low_q_hint = str(lq.get("hint") or "absent")
    blank_hint = ""
    if n_dropped > max(3, int(0.05 * max(int(q_arr.size), 1))):
        blank_hint = "many non-positive I points dropped — check blanks at high q"

    # Tabular companion with optional segment id.
    seg_id = np.full(q_arr.shape, -1, dtype=int)
    for i, seg in enumerate(regimes):
        mask = (q_arr >= float(seg["q_min_nm"])) & (q_arr <= float(seg["q_max_nm"]))
        seg_id[mask] = i
    write_data(
        loglog_dat_path,
        pd.DataFrame({"q": q_arr, "I": I_arr, "segment_id": seg_id}),
        metadata={
            "type": "loglog",
            "title": f"Log-log: {base}",
            "xlabel": "q (nm-1)",
            "ylabel": "I (a.u.)",
            "parent": profile,
            "dominant_mid_q_tag": dominant,
            "n_regimes": len(regimes),
        },
    )

    _plot_annotated_loglog(
        q_arr,
        I_arr,
        regimes,
        title=f"Log–log regimes: {base}",
        plot_path=loglog_plot_path,
        rg_nm=rg_nm,
        dominant_tag=dominant,
    )

    params_doc: Dict[str, Any] = {
        "profile": profile,
        "rg_nm_context": float(rg_nm) if rg_nm is not None else None,
        "regimes": regimes,
        "dominant_mid_q_tag": dominant,
        "n_regimes": len(regimes),
        "n_dropped_nonpositive": n_dropped,
        "low_q_plateau_hint": low_q_hint,
        "blank_sensitivity_hint": blank_hint or None,
        "regime_tags": list(REGIME_TAGS),
    }
    with open(params_yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(params_doc, f, sort_keys=False, allow_unicode=True)

    with open(results_path, "w", encoding="utf-8") as f:
        f.write("SAXS Log–log Regime Analysis\n")
        f.write("============================\n")
        f.write(f"Input file: {profile}\n")
        f.write(f"Analysis date: {time.ctime()}\n\n")
        if rg_nm is not None:
            f.write(f"Rg context = {float(rg_nm):.4f} nm (plot banding only)\n")
        f.write(f"n_dropped_nonpositive = {n_dropped}\n")
        f.write(f"low_q_plateau_hint = {low_q_hint}\n")
        f.write(f"dominant_mid_q_tag = {dominant}\n")
        f.write(f"n_regimes = {len(regimes)}\n\n")
        for i, seg in enumerate(regimes):
            f.write(
                f"  [{i}] q=[{seg['q_min_nm']:.5g},{seg['q_max_nm']:.5g}] nm^-1  "
                f"α={seg['alpha']:.3g}  n={seg['n_points']}  tag={seg['tag']}  "
                f"conf={float(seg.get('confidence') or 0):.2f}\n"
            )
        if blank_hint:
            f.write(f"\nBlank sensitivity hint: {blank_hint}\n")
        f.write(f"\nLog-log plot: {loglog_plot_path}\n")
        f.write(f"Parameters YAML: {params_yaml_path}\n")

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_lines = [
        "### Log–log regime analysis\n",
        f"Dominant mid-q tag: **{dominant}** ({len(regimes)} segments).\n",
    ]
    if os.path.isfile(loglog_dat_path):
        md_lines.append(f"![Log-log]({os.path.basename(loglog_dat_path)})\n")

    summary_refs = [
        {"role": "loglog_results", "path": os.path.basename(results_path), "format": "text"},
        {"role": "loglog_dat", "path": os.path.basename(loglog_dat_path), "format": "dat"},
        {"role": "loglog_plot", "path": os.path.basename(loglog_plot_path), "format": "png"},
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "analyze_loglog",
        "".join(md_lines),
        summary_references=summary_refs,
        summary_extra={"dominant_mid_q_tag": dominant},
    )

    return {
        "results_path": results_path,
        "loglog_plot_path": loglog_plot_path,
        "loglog_dat_path": loglog_dat_path,
        "regimes": json.dumps(regimes),
        "dominant_mid_q_tag": dominant,
        "n_regimes": len(regimes),
        "n_dropped_nonpositive": n_dropped,
        "low_q_plateau_hint": low_q_hint,
        "blank_sensitivity_hint": blank_hint,
        "rg_nm_context": float(rg_nm) if rg_nm is not None else float("nan"),
    }
