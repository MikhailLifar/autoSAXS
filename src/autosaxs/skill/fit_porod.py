from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.profile_features import (
    fit_porod_asymptote,
    porod_debye_plateau,
    select_porod_window,
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


def fit_porod(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    rg_nm: Optional[float] = None,
    alpha_mode: str = "free",
    alpha_nominal: float = -4.0,
    alpha_tol: float = 0.5,
    fit_background: bool = True,
    qR_min: float = 2.0,
    n_min: int = 5,
    max_pts: int = 60,
    porod_volume_method: str = "none",
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str], float, bool, None]]:
    """
    SAXS / small-angle x-ray scattering: high-q Porod asymptote + Porod–Debye diagnostic.

    Fits a background-aware model ``I(q) ≈ B + A·q^α`` on a high-q window
    (Pauw 2013; Li 2016 Chem. Rev. letter H / PDF p.8 — sharp-interface solids → q⁻⁴
    when q ≫ π/Rg; Jacques & Trewhella 2010 PDF p.8 — blank errors dominate high q).
    Emits a Porod–Debye ``q⁴I`` vs ``q⁴`` plateau score (Rambo & Tainer 2011 Biopolymers
    PDF pp.3,5,16). Soft metrics only — never hard-gates modeling.

    **Primary fit:** ``alpha_mode=free`` (α floated near ``alpha_nominal``, default −4).
    A secondary fixed-α=−4 fit always reports classical ``porod_constant`` ≡ lim q⁴(I−B)
    as a diagnostic companion. ``porod_volume_nm3`` is **omitted by default**
    (``porod_volume_method=none``); raw truncated / regularized invariant paths are
    not enabled in v1 (Trewhella 2017; Rambo & Tainer 2013).

    Units: ``q`` in nm⁻¹, lengths in nm.

    ### Arguments

    - `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where outputs are written.
    - `config_path` (str | None, default `None`): Deprecated YAML/`fit_porod` section; ignored.
    - `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
    - `alpha_mode` (str, default `free`): Primary asymptote mode — `free` or `fixed_minus4`.
    - `alpha_nominal` (float, default `-4.0`): Target exponent for window search / free-mode seed.
    - `alpha_tol` (float, default `0.5`): Acceptance band around `alpha_nominal` for a porod-like window.
    - `fit_background` (bool, default `True`): Fit constant background ``B`` in ``B + A q^α``.
    - `qR_min` (float, default `2.0`): Theoretical gate ``q ≥ qR_min / Rg`` when Rg is known.
    - `n_min` / `max_pts` (int): Min / max points in the Porod window search.
    - `porod_volume_method` (str, default `none`): `none` (omit volume) | reserved `raw_truncated` /
      `regularized_from_pr` (not implemented in v1 — raises if requested).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict` with:

    - `results_path`: Text passport mirror.
    - `porod_plot_path`: Windowed log–log / model PNG.
    - `porod_debye_plot_path`: Porod–Debye ``q⁴I`` vs ``q⁴`` PNG.
    - `porod_fit_dat_path`: Tabular ``q, I, I_fit`` companion.
    - Passport fields: `q_min_nm`, `q_max_nm`, `n_points`, `alpha`, `alpha_mode`,
      `porod_amplitude`, `porod_constant`, `background`, `fit_r2`,
      `theoretical_range_used`, `theoretical_range_absent`, `porod_debye_plateau`,
      `porod_volume_method`, optional `blank_sensitivity_hint`.
      `porod_volume_nm3` is omitted when method is `none`.

    ### Python usage

    ```python
    from autosaxs.skill import fit_porod

    out = fit_porod(
        profile="subtracted/sub_sample_01.dat",
        output_dir="porod",
        rg_nm=3.2,
        use_cache=False,
    )
    print(out["alpha"], out["porod_debye_plateau"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit-porod subtracted/sub_sample_01.dat --output-dir porod
    autosaxs fit-porod subtracted/sub_sample_01.dat --rg-nm 3.2 --output-dir porod
    ```
    """
    _ = config_path
    method = str(porod_volume_method or "none").strip().lower()
    if method not in ("none", "raw_truncated", "regularized_from_pr"):
        raise ValueError(
            f"fit_porod: unknown porod_volume_method={porod_volume_method!r} "
            "(expected none|raw_truncated|regularized_from_pr)"
        )
    if method != "none":
        raise NotImplementedError(
            "fit_porod: porod_volume_nm3 paths are not enabled in v1 "
            "(default porod_volume_method=none; omit volume)."
        )

    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_porod input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _fit_porod_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        rg_nm=rg_nm,
        alpha_mode=alpha_mode,
        alpha_nominal=alpha_nominal,
        alpha_tol=alpha_tol,
        fit_background=fit_background,
        qR_min=qR_min,
        n_min=n_min,
        max_pts=max_pts,
        porod_volume_method=method,
    )


def _guinier_rg_from_profile(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
    *,
    event_bus: Optional[EventBus],
) -> tuple[float, Dict[str, Any]]:
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "fit_porod: running fit_guinier (in-process)…"})
    results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
    if results.get("chosen") is None:
        raise RuntimeError(
            "fit_porod: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive Rg for the theoretical high-q gate."
        )
    rg = results.get("chosen_Rg")
    if rg is None:
        raise RuntimeError("fit_porod: Guinier result missing Rg.")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "fit_porod: fit_guinier completed."})
    summary = {
        "source": results.get("chosen"),
        "rg_nm": float(rg),
        "q_min": (results.get("chosen_interval") or (None, None))[0],
        "q_max": (results.get("chosen_interval") or (None, None))[1],
    }
    return float(rg), summary


def _plot_porod_window(
    q: np.ndarray,
    I: np.ndarray,
    I_fit: np.ndarray,
    *,
    title: str,
    plot_path: str,
    alpha: float,
    background: float,
) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    m = np.isfinite(q) & np.isfinite(I) & (q > 0) & (I > 0)
    ax.loglog(q[m], I[m], "o", ms=4, color="#1f77b4", label="data")
    mf = np.isfinite(q) & np.isfinite(I_fit) & (q > 0) & (I_fit > 0)
    ax.loglog(
        q[mf],
        I_fit[mf],
        "-",
        color="#d62728",
        lw=1.8,
        label=f"B+A q^α (α={alpha:.2f}, B={background:.3g})",
    )
    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel("I (a.u.)")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


def _plot_porod_debye(
    q4: np.ndarray,
    q4I: np.ndarray,
    *,
    title: str,
    plot_path: str,
    label: str,
    score: float,
) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    m = np.isfinite(q4) & np.isfinite(q4I) & (q4 > 0)
    ax.plot(q4[m], q4I[m], "o-", ms=3, lw=1.0, color="#1f77b4", label="q⁴ I(q)")
    if m.any():
        # Soft plateau guide: median of last third.
        y = q4I[m]
        n = int(y.size)
        a = max(3, n // 3)
        plateau = float(np.median(y[-a:]))
        ax.axhline(plateau, color="#d62728", ls="--", lw=1.2, label=f"late median ({label})")
    ax.set_xlabel("q⁴ (nm⁻⁴)")
    ax.set_ylabel("q⁴ · I(q) (a.u.)")
    ax.set_title(f"{title} [{label}, score={score:.2f}]")
    ax.legend(loc="best", fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


@apply_batch(stem_from_keys="profile", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=[
        "rg_nm",
        "alpha_mode",
        "alpha_nominal",
        "alpha_tol",
        "fit_background",
        "qR_min",
        "n_min",
        "max_pts",
        "porod_volume_method",
    ],
    include_config_in_hash=False,
)
def _fit_porod_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    *,
    rg_nm: Optional[float] = None,
    alpha_mode: str = "free",
    alpha_nominal: float = -4.0,
    alpha_tol: float = 0.5,
    fit_background: bool = True,
    qR_min: float = 2.0,
    n_min: int = 5,
    max_pts: int = 60,
    porod_volume_method: str = "none",
) -> Dict[str, Union[str, List[str], float, bool, None]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_porod requires input_paths['profile']")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    results_path = os.path.join(output_dir, f"{base}_porod_results.txt")
    porod_plot_path = os.path.join(output_dir, f"porod_{base}.png")
    porod_debye_plot_path = os.path.join(output_dir, f"porod_debye_{base}.png")
    porod_fit_dat_path = os.path.join(output_dir, f"porod_fit_{base}.dat")
    params_yaml_path = os.path.join(output_dir, f"{base}_porod_params.yml")

    q_arr, I_arr, sigma_arr = load_saxs_1d_any(profile)
    q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)

    guinier_summary: Optional[Dict[str, Any]] = None
    rg_source = "user" if rg_nm is not None else "none"
    if rg_nm is None:
        atsas_tmp = os.path.join(output_dir, f".{base}_guinier_atsas.dat")
        write_saxs_atsas_format(atsas_tmp, q_arr, I_arr, sigma_arr)
        try:
            rg_nm, guinier_summary = _guinier_rg_from_profile(
                q_arr, I_arr, sigma_arr, atsas_tmp, event_bus=event_bus
            )
            rg_source = "fit_guinier"
        except Exception as exc:
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"fit_porod: Guinier Rg unavailable ({exc}); continuing without Rg gate."},
                )
            rg_nm = None
            rg_source = "unavailable"

    win = select_porod_window(
        q_arr,
        I_arr,
        sigma=sigma_arr,
        n_min=n_min,
        alpha_nominal=alpha_nominal,
        alpha_tol=alpha_tol,
        max_pts=max_pts,
        rg_nm=rg_nm,
        qR_min=qR_min,
    )
    theoretical_absent = bool(win.get("theoretical_range_absent"))
    theoretical_used = bool(win.get("theoretical_range_used"))
    porod_like = bool(win.get("porod_like"))

    primary: Optional[Dict[str, Any]] = None
    fixed: Optional[Dict[str, Any]] = None
    blank_hint: Optional[str] = None

    if not theoretical_absent and win.get("q_min_nm") is not None:
        q_lo = float(win["q_min_nm"])
        q_hi = float(win["q_max_nm"])
        mode = str(alpha_mode or "free").strip().lower()
        if mode in ("fixed", "fixed_minus4", "minus4", "-4"):
            mode = "fixed_minus4"
        else:
            mode = "free"
        primary = fit_porod_asymptote(
            q_arr,
            I_arr,
            sigma=sigma_arr,
            alpha_mode=mode,
            alpha_nominal=alpha_nominal,
            fit_background=fit_background,
            q_min=q_lo,
            q_max=q_hi,
        )
        # Secondary / diagnostic fixed−4 Porod constant (always attempt).
        fixed = fit_porod_asymptote(
            q_arr,
            I_arr,
            sigma=sigma_arr,
            alpha_mode="fixed_minus4",
            alpha_nominal=-4.0,
            fit_background=fit_background,
            q_min=q_lo,
            q_max=q_hi,
        )
        if primary is not None:
            B = float(primary.get("background") or 0.0)
            I_med = float(np.median(primary["I_data"])) if primary.get("I_data") is not None else 0.0
            if I_med > 0 and abs(B) > 0.3 * I_med:
                blank_hint = "large fitted background — check blanks / high-q subtraction"
            if not porod_like:
                blank_hint = blank_hint or "no slope≈−4 window — α may be fractal/flexible or blank-ruined"

    # Porod–Debye on usable high-q (window if known, else full positive-I curve).
    pd_qmin = float(win["q_min_nm"]) if win.get("q_min_nm") is not None else None
    pd_qmax = float(win["q_max_nm"]) if win.get("q_max_nm") is not None else None
    debye = porod_debye_plateau(q_arr, I_arr, q_min=pd_qmin, q_max=pd_qmax)
    pd_label = str(debye.get("label") or "unclear")
    pd_score = float(debye.get("score") or 0.0)

    # Artifacts
    if primary is not None:
        write_data(
            porod_fit_dat_path,
            pd.DataFrame(
                {
                    "q": primary["q_fit"],
                    "I": primary["I_data"],
                    "I_fit": primary["I_fit"],
                }
            ),
            metadata={
                "type": "porod_fit",
                "title": f"Porod asymptote: {base}",
                "xlabel": "q (nm-1)",
                "ylabel": "I (a.u.)",
                "parent": profile,
                "alpha": primary["alpha"],
                "alpha_mode": primary["alpha_mode"],
                "background": primary["background"],
            },
        )
        _plot_porod_window(
            primary["q_fit"],
            primary["I_data"],
            primary["I_fit"],
            title=f"Porod window: {base}",
            plot_path=porod_plot_path,
            alpha=float(primary["alpha"]),
            background=float(primary["background"]),
        )
    else:
        # Empty soft plots still useful when theoretical range absent.
        write_data(
            porod_fit_dat_path,
            pd.DataFrame({"q": [], "I": [], "I_fit": []}),
            metadata={"type": "porod_fit", "title": f"Porod asymptote: {base}", "parent": profile},
        )
        _plot_porod_window(
            np.asarray([1.0]),
            np.asarray([1.0]),
            np.asarray([1.0]),
            title=f"Porod window: {base} (no asymptote fit)",
            plot_path=porod_plot_path,
            alpha=float("nan"),
            background=0.0,
        )

    _plot_porod_debye(
        debye["q4"],
        debye["q4I"],
        title=f"Porod–Debye: {base}",
        plot_path=porod_debye_plot_path,
        label=pd_label,
        score=pd_score,
    )

    alpha_out = float(primary["alpha"]) if primary else float("nan")
    mode_out = str(primary["alpha_mode"]) if primary else str(alpha_mode)
    amp_out = float(primary["porod_amplitude"]) if primary else float("nan")
    bg_out = float(primary["background"]) if primary else float("nan")
    r2_out = float(primary["fit_r2"]) if primary else float("nan")
    # Prefer fixed−4 constant; fall back to primary's derived constant.
    porod_const: Optional[float] = None
    if fixed is not None and fixed.get("porod_constant") is not None:
        porod_const = float(fixed["porod_constant"])
    elif primary is not None and primary.get("porod_constant") is not None:
        porod_const = float(primary["porod_constant"])

    q_min_nm = float(win["q_min_nm"]) if win.get("q_min_nm") is not None else float("nan")
    q_max_nm = float(win["q_max_nm"]) if win.get("q_max_nm") is not None else float("nan")
    n_points = int(win["n_points"]) if win.get("n_points") is not None else 0

    params_doc: Dict[str, Any] = {
        "profile": profile,
        "rg_nm": float(rg_nm) if rg_nm is not None else None,
        "rg_source": rg_source,
        "alpha_mode": mode_out,
        "alpha": alpha_out,
        "alpha_nominal": float(alpha_nominal),
        "porod_amplitude": amp_out,
        "porod_constant": porod_const,
        "porod_constant_fixed_minus4": (
            float(fixed["porod_constant"]) if fixed and fixed.get("porod_constant") is not None else None
        ),
        "background": bg_out,
        "fit_r2": r2_out,
        "q_min_nm": q_min_nm,
        "q_max_nm": q_max_nm,
        "n_points": n_points,
        "theoretical_range_used": theoretical_used,
        "theoretical_range_absent": theoretical_absent,
        "porod_like": porod_like,
        "porod_debye_plateau": pd_label,
        "porod_debye_score": pd_score,
        "porod_volume_method": porod_volume_method,
        "blank_sensitivity_hint": blank_hint,
        "fit_background": bool(fit_background),
        "qR_min": float(qR_min),
    }
    if guinier_summary is not None:
        params_doc["fit_guinier"] = guinier_summary
    with open(params_yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(params_doc, f, sort_keys=False, allow_unicode=True)

    with open(results_path, "w", encoding="utf-8") as f:
        f.write("SAXS Porod Asymptote Analysis\n")
        f.write("=============================\n")
        f.write(f"Input file: {profile}\n")
        f.write(f"Analysis date: {time.ctime()}\n\n")
        if rg_nm is not None:
            f.write(f"Rg = {float(rg_nm):.4f} nm (source: {rg_source})\n")
        else:
            f.write(f"Rg = (unavailable; source: {rg_source})\n")
        f.write(f"theoretical_range_used = {theoretical_used}\n")
        f.write(f"theoretical_range_absent = {theoretical_absent}\n")
        f.write(f"porod_like window = {porod_like}\n")
        if np.isfinite(q_min_nm):
            f.write(f"Window: q = [{q_min_nm:.5g}, {q_max_nm:.5g}] nm^-1  (n={n_points})\n")
        f.write(f"\nPrimary alpha_mode = {mode_out}\n")
        f.write(f"alpha = {alpha_out:.4g}\n")
        f.write(f"porod_amplitude A = {amp_out:.4g}\n")
        f.write(f"background B = {bg_out:.4g}\n")
        f.write(f"fit_r2 = {r2_out:.4g}\n")
        if porod_const is not None:
            f.write(f"porod_constant P (fixed−4 / q^4(I−B)) = {porod_const:.4g}\n")
        f.write(f"\nPorod–Debye plateau: {pd_label} (score={pd_score:.3f})\n")
        f.write(f"porod_volume_method = {porod_volume_method} (volume omitted)\n")
        if blank_hint:
            f.write(f"\nBlank sensitivity hint: {blank_hint}\n")
        f.write(f"\nPorod plot: {porod_plot_path}\n")
        f.write(f"Porod–Debye plot: {porod_debye_plot_path}\n")
        f.write(f"Parameters YAML: {params_yaml_path}\n")

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_lines = [
        "### Porod analysis\n",
        f"α = **{alpha_out:.3g}** ({mode_out}); Porod–Debye: **{pd_label}**.\n",
    ]
    if os.path.isfile(porod_fit_dat_path):
        md_lines.append(f"![Porod fit]({os.path.basename(porod_fit_dat_path)})\n")
    if os.path.isfile(porod_debye_plot_path):
        md_lines.append(f"![Porod–Debye]({os.path.basename(porod_debye_plot_path)})\n")

    summary_refs = [
        {"role": "porod_results", "path": os.path.basename(results_path), "format": "text"},
        {"role": "porod_fit_dat", "path": os.path.basename(porod_fit_dat_path), "format": "dat"},
        {"role": "porod_plot", "path": os.path.basename(porod_plot_path), "format": "png"},
        {"role": "porod_debye_plot", "path": os.path.basename(porod_debye_plot_path), "format": "png"},
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_porod",
        "".join(md_lines),
        summary_references=summary_refs,
        summary_extra={
            "porod_alpha": alpha_out,
            "porod_debye_plateau": pd_label,
        },
    )

    out: Dict[str, Union[str, List[str], float, bool, None]] = {
        "results_path": results_path,
        "porod_plot_path": porod_plot_path,
        "porod_debye_plot_path": porod_debye_plot_path,
        "porod_fit_dat_path": porod_fit_dat_path,
        "q_min_nm": q_min_nm,
        "q_max_nm": q_max_nm,
        "n_points": n_points,
        "alpha": alpha_out,
        "alpha_mode": mode_out,
        "porod_amplitude": amp_out,
        "porod_constant": porod_const if porod_const is not None else float("nan"),
        "background": bg_out,
        "fit_r2": r2_out,
        "theoretical_range_used": theoretical_used,
        "theoretical_range_absent": theoretical_absent,
        "porod_debye_plateau": pd_label,
        "porod_volume_method": porod_volume_method,
        "blank_sensitivity_hint": blank_hint or "",
    }
    return out
