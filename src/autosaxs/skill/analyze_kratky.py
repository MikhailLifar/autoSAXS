from __future__ import annotations

import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.kratky import (
    KratkyThresholds,
    SPHERE_X_MAX_REF,
    SPHERE_Y_MAX_REF,
    analyze_dimensionless_kratky,
)
from autosaxs.core.viewer import PLTViewer

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


def analyze_kratky(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    rg_nm: Optional[float] = None,
    i0: Optional[float] = None,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    globular_x_min: float = 1.65,
    globular_x_max: float = 1.85,
    globular_y_min: float = 1.0,
    globular_y_max: float = 1.2,
    elongated_x_min: float = 1.85,
    elongated_x_max: float = 2.5,
    elongated_y_min: float = 1.15,
    coil_plateau_y: float = 2.0,
    coil_plateau_tol: float = 0.25,
    coil_high_x_min: float = 3.0,
    x_search_min: float = 0.5,
    x_search_max: float = 4.0,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str], float]]:
    """
    SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.

    Builds classical (I·q² vs q) and dimensionless ((q·Rg)²·I/I(0) vs q·Rg) Kratky plots,
    locates the global peak, and assigns a model-free conformation class (globular / elongated /
    coil / intermediate).

    Unless both ``rg_nm`` and ``i0`` are supplied, runs in-process Guinier analysis to obtain them.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where analysis outputs are written.
    - `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; unused by this skill.
    - `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
    - `i0` (float | None, default `None`): Forward scattering I(0). If omitted, taken from in-process Guinier.
    - `q_min`, `q_max` (float | None): Optional q-range (nm⁻¹) applied before analysis.
    - `globular_x_min`, `globular_x_max`, `globular_y_min`, `globular_y_max`: Globular peak bands (defaults from quality guide).
    - `elongated_x_min`, `elongated_x_max`, `elongated_y_min`: Elongated peak bands.
    - `coil_plateau_y`, `coil_plateau_tol`, `coil_high_x_min`: Coil / Debye-plateau detection.
    - `x_search_min`, `x_search_max`: Peak search window in q·Rg.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict` with:

    - `results_path`: Path to the text results file.
    - `kratky_plot_path`: Path to the classical Kratky PNG (I·q² vs q).
    - `kratky_dimensionless_plot_path`: Path to the dimensionless Kratky PNG.
    - `kratky_classical_dat_path`: Path to classical Kratky `.dat`.
    - `kratky_dimensionless_dat_path`: Path to dimensionless Kratky `.dat`.
    - `classification`: Assigned conformation label.
    - `x_max`, `y_max`: Dimensionless peak coordinates (q·Rg, Y).

    ### Python usage

    ```python
    from autosaxs.skill import analyze_kratky

    out = analyze_kratky(
        profile="subtracted/sub_sample_01.dat",
        output_dir="kratky",
        use_cache=False,
    )

    print(out["classification"])
    ```

    ### CLI usage

    ```bash
    autosaxs analyze-kratky subtracted/sub_sample_01.dat --output-dir kratky
    autosaxs analyze-kratky subtracted/sub_sample_01.dat --rg-nm 3.2 --i0 1.05 --output-dir kratky
    ```
    """
    _ = config_path
    if (rg_nm is None) ^ (i0 is None):
        raise ValueError("analyze_kratky: provide both rg_nm and i0, or omit both to run Guinier in-process")

    thresholds = KratkyThresholds(
        globular_x_min=globular_x_min,
        globular_x_max=globular_x_max,
        globular_y_min=globular_y_min,
        globular_y_max=globular_y_max,
        elongated_x_min=elongated_x_min,
        elongated_x_max=elongated_x_max,
        elongated_y_min=elongated_y_min,
        coil_plateau_y=coil_plateau_y,
        coil_plateau_tol=coil_plateau_tol,
        coil_high_x_min=coil_high_x_min,
        x_search_min=x_search_min,
        x_search_max=x_search_max,
    )

    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("analyze_kratky input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _analyze_kratky_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        rg_nm=rg_nm,
        i0=i0,
        q_min=q_min,
        q_max=q_max,
        thresholds=thresholds,
    )


def _guinier_rg_i0_from_profile(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
    *,
    event_bus: Optional[EventBus],
) -> tuple[float, float, Dict[str, Any]]:
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "analyze_kratky: running fit_guinier (in-process)…"})
    results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
    if results.get("chosen") is None:
        raise RuntimeError(
            "analyze_kratky: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive Rg and I(0)."
        )
    rg = results.get("chosen_Rg")
    i0_val = results.get("chosen_I0")
    if rg is None or i0_val is None:
        raise RuntimeError("analyze_kratky: Guinier result missing Rg or I(0).")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "analyze_kratky: fit_guinier completed."})
    summary = {
        "source": results.get("chosen"),
        "rg_nm": float(rg),
        "i0": float(i0_val),
        "q_min": (results.get("chosen_interval") or (None, None))[0],
        "q_max": (results.get("chosen_interval") or (None, None))[1],
    }
    return float(rg), float(i0_val), summary


def _apply_q_window(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    q_min: Optional[float],
    q_max: Optional[float],
) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    mask = np.isfinite(q) & np.isfinite(I)
    if q_min is not None:
        mask &= q >= float(q_min)
    if q_max is not None:
        mask &= q <= float(q_max)
    if mask.sum() < 5:
        raise ValueError("analyze_kratky: fewer than 5 points remain after q-range filtering")
    sig_out = sigma[mask] if sigma is not None else None
    return q[mask], I[mask], sig_out


def _plot_dimensionless_kratky(
    q_rg: np.ndarray,
    y: np.ndarray,
    *,
    x_peak: Optional[float],
    y_peak: Optional[float],
    classification: str,
    title: str,
    plot_path: str,
    sigma_y: Optional[np.ndarray] = None,
) -> None:
    import matplotlib.pyplot as plt

    q_rg = np.asarray(q_rg, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(q_rg) & np.isfinite(y) & (y > 0)

    fig, ax = plt.subplots(figsize=(8, 5))
    if sigma_y is not None:
        sigma_y = np.asarray(sigma_y, dtype=float)
        good = mask & np.isfinite(sigma_y) & (sigma_y > 0)
        ax.errorbar(
            q_rg[good],
            y[good],
            yerr=sigma_y[good],
            fmt="o",
            ms=4,
            capsize=2,
            label="data",
            color="#1f77b4",
            elinewidth=1,
        )
    else:
        ax.plot(q_rg[mask], y[mask], "o", ms=4, label="data", color="#1f77b4")

    xref = np.linspace(max(0.1, float(np.nanmin(q_rg[mask]))), float(np.nanmax(q_rg[mask])), 200)
    y_ref = (xref ** 2) * np.exp(-(xref ** 2) / 3.0)
    ax.plot(xref, y_ref, "--", color="#888888", lw=1.5, label="globule ref (Guinier)")

    ax.plot(
        [SPHERE_X_MAX_REF],
        [SPHERE_Y_MAX_REF],
        "k*",
        ms=10,
        label=f"sphere ref ({SPHERE_X_MAX_REF:.3f}, {SPHERE_Y_MAX_REF:.3f})",
    )

    if x_peak is not None and y_peak is not None:
        ax.plot([x_peak], [y_peak], "r*", ms=12, label=f"peak ({x_peak:.3f}, {y_peak:.3f})")

    ax.set_xlabel("q · Rg")
    ax.set_ylabel("(q · Rg)² · I(q) / I(0)")
    ax.set_title(f"{title} [{classification}]")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


@apply_batch(stem_from_keys="profile", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["rg_nm", "i0", "q_min", "q_max", "thresholds"],
    include_config_in_hash=False,
)
def _analyze_kratky_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    *,
    rg_nm: Optional[float] = None,
    i0: Optional[float] = None,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    thresholds: KratkyThresholds,
) -> Dict[str, Union[str, List[str], float]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("analyze_kratky requires input_paths['profile']")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    results_path = os.path.join(output_dir, f"{base}_kratky_results.txt")
    kratky_plot_path = os.path.join(output_dir, f"kratky_{base}.png")
    kratky_dimensionless_plot_path = os.path.join(output_dir, f"kratky_dimensionless_{base}.png")
    kratky_classical_dat_path = os.path.join(output_dir, f"kratky_{base}.dat")
    kratky_dimensionless_dat_path = os.path.join(output_dir, f"kratky_dimensionless_{base}.dat")
    params_yaml_path = os.path.join(output_dir, f"{base}_kratky_params.yml")

    q_arr, I_arr, sigma_arr = load_saxs_1d_any(profile)
    q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)
    q_arr, I_arr, sigma_arr = _apply_q_window(q_arr, I_arr, sigma_arr, q_min, q_max)

    guinier_summary: Optional[Dict[str, Any]] = None
    rg_source = "user"
    i0_source = "user"
    if rg_nm is None and i0 is None:
        atsas_tmp = os.path.join(output_dir, f".{base}_guinier_atsas.dat")
        write_saxs_atsas_format(atsas_tmp, q_arr, I_arr, sigma_arr)
        rg_nm, i0, guinier_summary = _guinier_rg_i0_from_profile(
            q_arr, I_arr, sigma_arr, atsas_tmp, event_bus=event_bus
        )
        rg_source = "fit_guinier"
        i0_source = "fit_guinier"
    else:
        assert rg_nm is not None and i0 is not None

    analysis = analyze_dimensionless_kratky(
        q_arr,
        I_arr,
        rg_nm=float(rg_nm),
        i0=float(i0),
        thresholds=thresholds,
    )
    q_rg = analysis["q_rg"]
    y_dim = analysis["y"]
    x_peak = analysis["x_max"]
    y_peak = analysis["y_max"]
    classification = str(analysis["classification"])

    classical_y = (q_arr ** 2) * I_arr
    write_data(
        kratky_classical_dat_path,
        pd.DataFrame({"q": q_arr, "I * q^2": classical_y}),
        metadata={"type": "kratky", "parent": profile},
    )
    write_data(
        kratky_dimensionless_dat_path,
        pd.DataFrame({"q * Rg": q_rg, "Y": y_dim}),
        metadata={"type": "kratky_dimensionless", "parent": profile, "Rg_nm": rg_nm, "I0": i0},
    )

    PLTViewer.view_curves(
        q_arr,
        classical_y,
        f"Kratky: {base}",
        xlabel="q (nm-1)",
        ylabel="I * q^2 (a.u.)",
        legend=True,
        plotFilePath=kratky_plot_path,
    )

    sigma_y: Optional[np.ndarray] = None
    if sigma_arr is not None and i0 and i0 > 0:
        sigma_y = (q_rg ** 2) * (np.asarray(sigma_arr, dtype=float) / float(i0))

    _plot_dimensionless_kratky(
        q_rg,
        y_dim,
        x_peak=x_peak,
        y_peak=y_peak,
        classification=classification,
        title=f"Dimensionless Kratky: {base}",
        plot_path=kratky_dimensionless_plot_path,
        sigma_y=sigma_y,
    )

    params_doc: Dict[str, Any] = {
        "profile": profile,
        "rg_nm": float(rg_nm),
        "i0": float(i0),
        "rg_source": rg_source,
        "i0_source": i0_source,
        "classification": classification,
        "x_max": x_peak,
        "y_max": y_peak,
        "tail_mean_y": analysis.get("tail_mean_y"),
        "sphere_x_max_ref": SPHERE_X_MAX_REF,
        "sphere_y_max_ref": SPHERE_Y_MAX_REF,
        "thresholds": asdict(thresholds),
        "rationale": analysis.get("rationale"),
    }
    if guinier_summary is not None:
        params_doc["fit_guinier"] = guinier_summary
    if q_min is not None or q_max is not None:
        params_doc["q_window"] = {"q_min": q_min, "q_max": q_max}

    with open(params_yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(params_doc, f, sort_keys=False, allow_unicode=True)

    with open(results_path, "w", encoding="utf-8") as f:
        f.write("SAXS Dimensionless Kratky Analysis\n")
        f.write("==================================\n")
        f.write(f"Input file: {profile}\n")
        f.write(f"Analysis date: {time.ctime()}\n\n")
        f.write(f"Rg = {float(rg_nm):.4f} nm (source: {rg_source})\n")
        f.write(f"I(0) = {float(i0):.4g} (source: {i0_source})\n\n")
        f.write("Dimensionless Kratky peak (search in q·Rg window):\n")
        if x_peak is not None and y_peak is not None:
            f.write(f"  x_max = q·Rg = {x_peak:.4f}\n")
            f.write(f"  y_max = (q·Rg)²·I/I(0) = {y_peak:.4f}\n")
        else:
            f.write("  (no peak found)\n")
        tail = analysis.get("tail_mean_y")
        if tail is not None:
            f.write(f"  high-x tail mean Y (q·Rg ≥ {thresholds.coil_high_x_min}) = {tail:.4f}\n")
        f.write(f"\nReference sphere peak: ({SPHERE_X_MAX_REF:.4f}, {SPHERE_Y_MAX_REF:.4f})\n")
        f.write(f"Classification: {classification}\n")
        for line in analysis.get("rationale") or []:
            f.write(f"  - {line}\n")
        f.write(f"\nClassical Kratky plot: {kratky_plot_path}\n")
        f.write(f"Dimensionless Kratky plot: {kratky_dimensionless_plot_path}\n")
        f.write(f"Parameters YAML: {params_yaml_path}\n")

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_lines = [
        "### Kratky analysis\n",
        f"Classification: **{classification}**",
    ]
    if x_peak is not None and y_peak is not None:
        md_lines.append(f" (peak at q·Rg = {x_peak:.3f}, Y = {y_peak:.3f})")
    md_lines.append(".\n")
    if os.path.isfile(kratky_dimensionless_plot_path):
        md_lines.append(f"![Dimensionless Kratky]({os.path.basename(kratky_dimensionless_plot_path)})\n")

    summary_refs = [
        {"role": "kratky_results", "path": os.path.basename(results_path), "format": "text"},
        {"role": "kratky_classification", "path": os.path.basename(results_path), "format": "text"},
    ]
    if os.path.isfile(kratky_dimensionless_plot_path):
        summary_refs.append(
            {
                "role": "kratky_dimensionless_plot",
                "path": os.path.basename(kratky_dimensionless_plot_path),
                "format": "png",
            }
        )
    write_skill_report_fragments(
        output_dir,
        base,
        "analyze_kratky",
        "".join(md_lines),
        summary_references=summary_refs,
        summary_extra={"kratky_classification": classification},
    )

    return {
        "results_path": results_path,
        "kratky_plot_path": kratky_plot_path,
        "kratky_dimensionless_plot_path": kratky_dimensionless_plot_path,
        "kratky_classical_dat_path": kratky_classical_dat_path,
        "kratky_dimensionless_dat_path": kratky_dimensionless_dat_path,
        "kratky_params_path": params_yaml_path,
        "classification": classification,
        "x_max": x_peak if x_peak is not None else float("nan"),
        "y_max": y_peak if y_peak is not None else float("nan"),
    }
