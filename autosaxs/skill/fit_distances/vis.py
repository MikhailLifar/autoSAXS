"""Plot helpers for fit_distances (fit-vs-exp and p(r) with σ band / ensemble)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out

from ..deps import EventBus, EventType


def write_fit_vs_exp_png(
    *,
    out_text: str,
    output_dir: str,
    base: str,
    best: Dict[str, Any],
    event_bus: Optional[EventBus],
) -> Tuple[Optional[str], Optional[str]]:
    """Write I(q) fit-vs-experiment PNG. Returns (path, error)."""
    fit_vs_exp_png_path: Optional[str] = None
    fit_vs_exp_png_error: Optional[str] = None
    try:
        parsed = parse_gnom_out(out_text)
        iq_table = parsed.get("iq_table")
        if iq_table is None:
            fit_vs_exp_png_error = "could not parse I(q) table from .out"
        else:
            q, I_exp, sigma_arr, I_fit = iq_table
            fit_vs_exp_png_path = os.path.join(output_dir, f"{base}_fits.png")
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.plot(q, I_exp, lw=3, label="exp")
            ax.plot(q, I_fit, lw=2, label="fit")
            ax.set_xlabel("q (nm$^{-1}$)")
            ax.set_ylabel("I(q)")
            ax.set_yscale("log")
            te = best.get("total_estimate")
            rg_nm_v = best.get("rg_nm")
            if te is not None and rg_nm_v is not None:
                ax.set_title(
                    f"DATGNOM fit: Rg={float(rg_nm_v):.4f} nm, Total Estimate={float(te):.3f}"
                )
            elif rg_nm_v is not None:
                ax.set_title(f"DATGNOM fit: Rg={float(rg_nm_v):.4f} nm")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend()
            fig.tight_layout()
            fig.savefig(fit_vs_exp_png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {
                        "text": (
                            "DATGNOM (fit_distances): wrote fit-vs-exp PNG: "
                            f"{os.path.basename(fit_vs_exp_png_path)}"
                        ),
                    },
                )
    except Exception as e:
        fit_vs_exp_png_error = f"failed to write fit-vs-exp PNG: {e}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        "DATGNOM (fit_distances): fit-vs-exp PNG not created "
                        f"({fit_vs_exp_png_error})."
                    ),
                },
            )
    return fit_vs_exp_png_path, fit_vs_exp_png_error


def _overlay_pr_curve(
    ax,
    out_path: str,
    *,
    color: str,
    lw: float,
    alpha: float,
    label: Optional[str] = None,
) -> bool:
    """Plot a faint p(r) overlay from a GNOM .out path. Returns True if drawn."""
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
    rr, pp, _ee = arr
    ax.plot(rr, pp, color=color, lw=lw, alpha=alpha, zorder=1, label=label)
    return True


def write_pr_png(
    *,
    best: Dict[str, Any],
    pr_quality: Dict[str, Any],
    close_fit_out_paths: List[str],
    force_zero_off_out_path: str = "",
    event_bus: Optional[EventBus],
) -> Tuple[Optional[str], Optional[str]]:
    """Write p(r) PNG with ±σ band and faint close-fits / force-zero-off overlays."""
    best_pr_png_path: Optional[str] = None
    best_pr_png_error: Optional[str] = None
    if not best.get("ok"):
        return best_pr_png_path, best_pr_png_error
    out_path = str(best.get("out_path") or "")
    if not out_path or not os.path.isfile(out_path):
        best_pr_png_error = f"best .out path missing: {out_path!r}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
            )
        return best_pr_png_path, best_pr_png_error
    try:
        out_text_pr = Path(out_path).read_text(errors="replace")
    except OSError:
        best_pr_png_error = f"failed to read best .out: {out_path!r}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
            )
        return best_pr_png_path, best_pr_png_error
    arrays = distribution_arrays(parse_gnom_out(out_text_pr).get("distribution"))
    if arrays is None:
        best_pr_png_error = f"could not parse p(r) table from: {os.path.basename(out_path)}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
            )
        return best_pr_png_path, best_pr_png_error
    r, p, err = arrays
    png_path = os.path.splitext(out_path)[0] + ".png"
    try:
        fig, ax = plt.subplots(figsize=(7, 4))
        close_labeled = False
        for cf_path in close_fit_out_paths or []:
            label = "close fits (Dmax±10%)" if not close_labeled else None
            if _overlay_pr_curve(ax, cf_path, color="0.65", lw=0.9, alpha=0.55, label=label):
                close_labeled = True
        # Thin black — force-zero-off probe at extended Dmax.
        _overlay_pr_curve(
            ax,
            force_zero_off_out_path or "",
            color="k",
            lw=0.8,
            alpha=1.0,
            label="force-zero-off",
        )
        if err is not None:
            e = np.asarray(err, dtype=float)
            m = np.isfinite(r) & np.isfinite(p) & np.isfinite(e)
            ax.fill_between(
                r[m],
                p[m] - e[m],
                p[m] + e[m],
                color="C0",
                alpha=0.25,
                linewidth=0,
                zorder=2,
                label=r"$\pm\sigma$",
            )
        ax.plot(r, p, "C0-", lw=2, zorder=3, label="best")
        ax.set_xlabel("r (nm)")
        ax.set_ylabel("p(r)")
        rg_nm_v = best.get("rg_nm")
        te = best.get("total_estimate")
        title_parts = ["DATGNOM p(r)"]
        if rg_nm_v is not None:
            title_parts.append(f"Rg={float(rg_nm_v):.4f} nm")
        if te is not None:
            title_parts.append(f"TE={float(te):.3f}")
        drg = pr_quality.get("delta_rg_pct")
        if drg is not None and np.isfinite(float(drg)):
            title_parts.append(f"ΔRg={float(drg):.1f}%")
        ax.set_title(", ".join(title_parts))
        ax.grid(True, alpha=0.25)
        handles, _labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(fontsize=8, loc="best")
        fig.tight_layout()
        fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        best_pr_png_path = png_path
        best_pr_png_error = None
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): wrote p(r) PNG: {os.path.basename(png_path)}"},
            )
    except Exception:
        best_pr_png_error = f"matplotlib failed to save PNG: {os.path.basename(png_path)}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
            )
        try:
            plt.close(fig)  # type: ignore[name-defined]
        except Exception:
            pass
    return best_pr_png_path, best_pr_png_error
