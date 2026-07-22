"""Plot helpers for fit_sizes (fit-vs-exp and D(R) with σ band / ensemble)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.viewer import write_iq_fit_comparison_png

from ..deps import EventBus, EventType


def write_fit_vs_exp_png(
    *,
    best_gnom_out_path: str,
    output_dir: str,
    base: str,
    system: int,
    best: Dict[str, Any],
    event_bus: Optional[EventBus],
) -> Tuple[Optional[str], Optional[str]]:
    """Write I(q) fit-vs-experiment PNG. Returns (path, error)."""
    fit_vs_exp_png_path: Optional[str] = None
    fit_vs_exp_png_error: Optional[str] = None
    try:
        out_text_best = Path(best_gnom_out_path).read_text(errors="replace")
        parsed = parse_gnom_out(out_text_best)
        iq_table = parsed.get("iq_table")
        if iq_table is None:
            fit_vs_exp_png_error = "could not parse I(q) table from .out"
        else:
            q, I_exp, sigma_arr, I_fit = iq_table
            fit_vs_exp_png_path = os.path.join(output_dir, f"{base}_fits.png")
            te = best.get("total_estimate")
            if te is not None:
                title = f"GNOM fit (system={system}): Total Estimate={float(te):.3f}"
            else:
                title = f"GNOM fit (system={system})"
            write_iq_fit_comparison_png(
                fit_vs_exp_png_path,
                q,
                I_exp,
                [(I_fit, "fit")],
                sigma=sigma_arr,
                title=title,
            )
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): wrote fit-vs-exp PNG: {os.path.basename(fit_vs_exp_png_path)}"},
                )
    except Exception as e:
        fit_vs_exp_png_error = f"failed to write fit-vs-exp PNG: {e}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): fit-vs-exp PNG not created ({fit_vs_exp_png_error})."},
            )
    return fit_vs_exp_png_path, fit_vs_exp_png_error


def _overlay_dr_curve(
    ax,
    out_path: str,
    *,
    color: str,
    lw: float,
    alpha: float,
    label: Optional[str] = None,
) -> bool:
    """Plot a faint D(R) overlay from a GNOM .out path. Returns True if drawn."""
    if not out_path or not os.path.isfile(out_path):
        return False
    try:
        arr = distribution_arrays(
            parse_gnom_out(Path(out_path).read_text(errors="replace")).get("distribution")
        )
    except OSError:
        return False
    if arr is None:
        return False
    rr, dd, _ee = arr
    ax.plot(rr, dd, color=color, lw=lw, alpha=alpha, zorder=1, label=label)
    return True


def write_dr_png(
    *,
    best: Dict[str, Any],
    dr_quality: Dict[str, Any],
    system: int,
    close_fit_out_paths: List[str],
    force_zero_off_out_path: str = "",
    event_bus: Optional[EventBus],
) -> Tuple[Optional[str], Optional[str]]:
    """Write D(R) PNG with ±σ band and faint close-fits / force-zero-off overlays."""
    best_dr_png_path: Optional[str] = None
    best_dr_png_error: Optional[str] = None
    if not best.get("ok"):
        return best_dr_png_path, best_dr_png_error
    out_path = str(best.get("out_path") or "")
    if not out_path or not os.path.isfile(out_path):
        best_dr_png_error = f"best .out path missing: {out_path!r}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): D(R) PNG not created ({best_dr_png_error})."},
            )
        return best_dr_png_path, best_dr_png_error
    try:
        out_text = Path(out_path).read_text(errors="replace")
    except OSError:
        best_dr_png_error = f"failed to read best .out: {out_path!r}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): D(R) PNG not created ({best_dr_png_error})."},
            )
        return best_dr_png_path, best_dr_png_error
    arrays = distribution_arrays(parse_gnom_out(out_text).get("distribution"))
    if arrays is None:
        best_dr_png_error = f"could not parse D(R) table from: {os.path.basename(out_path)}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): D(R) PNG not created ({best_dr_png_error})."},
            )
        return best_dr_png_path, best_dr_png_error
    r, d, err = arrays
    png_path = os.path.splitext(out_path)[0] + ".png"
    try:
        fig, ax = plt.subplots(figsize=(7, 4))
        close_labeled = False
        for cf_path in close_fit_out_paths or []:
            label = "close fits (Rmax±10%)" if not close_labeled else None
            if _overlay_dr_curve(ax, cf_path, color="0.65", lw=0.9, alpha=0.55, label=label):
                close_labeled = True
        _overlay_dr_curve(
            ax,
            force_zero_off_out_path or "",
            color="k",
            lw=0.8,
            alpha=1.0,
            label="force-zero-off",
        )
        has_err = err is not None and np.any(np.isfinite(np.asarray(err, dtype=float)))
        if has_err:
            e = np.asarray(err, dtype=float)
            m = np.isfinite(r) & np.isfinite(d) & np.isfinite(e)
            ax.fill_between(
                r[m],
                d[m] - e[m],
                d[m] + e[m],
                color="C0",
                alpha=0.25,
                linewidth=0,
                zorder=2,
                label=r"$\pm\sigma$",
            )
        ax.plot(r, d, "C0-", lw=2, zorder=3, label="best")
        ax.set_xlabel("R (nm)")
        ax.set_ylabel("D(R)")
        te = best.get("total_estimate")
        title_parts = [f"GNOM D(R), system={system}"]
        if te is not None:
            title_parts.append(f"TE={float(te):.3f}")
        pdi = dr_quality.get("pdi")
        if pdi is not None and np.isfinite(float(pdi)):
            title_parts.append(f"PDI={float(pdi):.3f}")
        ax.set_title(", ".join(title_parts))
        ax.grid(True, alpha=0.25)
        handles, _labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        best_dr_png_path = png_path
        best_dr_png_error = None
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): wrote D(R) PNG: {os.path.basename(png_path)}"},
            )
    except Exception:
        best_dr_png_error = f"matplotlib failed to save PNG: {os.path.basename(png_path)}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): D(R) PNG not created ({best_dr_png_error})."},
            )
        try:
            plt.close(fig)  # type: ignore[name-defined]
        except Exception:
            pass
    return best_dr_png_path, best_dr_png_error
