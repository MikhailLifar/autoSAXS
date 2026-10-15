"""Catalog of MIXTURE model runs for the distribution modeling advanced viewer."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MixtureModelEntry:
    key: str
    label: str
    """Display label for combo (e.g. ``Gauss_nph2``)."""
    csv_label: str
    """On-disk / CSV label (e.g. ``nph2_Gauss``)."""
    row: Dict[str, Any]
    fit_path: Optional[str] = None
    bic_chi2: Optional[float] = None
    n_phases: int = 0
    dist: str = ""
    is_best: bool = False


@dataclass
class MixtureModelCatalog:
    entries: List[MixtureModelEntry] = field(default_factory=list)
    best_key: str = ""
    output_subdir: str = ""
    r_min_ang: float = 5.0
    r_max_ang: float = 120.0

    def by_key(self, key: str) -> Optional[MixtureModelEntry]:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def best(self) -> Optional[MixtureModelEntry]:
        return self.by_key(self.best_key) or (self.entries[0] if self.entries else None)


def _fmt_bic(bic: Optional[float]) -> str:
    if bic is None:
        return ""
    try:
        v = float(bic)
    except (TypeError, ValueError):
        return ""
    if v != v:  # NaN
        return ""
    return f" (BIC_χ²={v:.3g})"


def _display_label(csv_label: str, dist: str, n_phases: int) -> str:
    """Prefer ``Gauss_nph2`` style; fall back to CSV label."""
    d = (dist or "").strip()
    if d and n_phases > 0:
        return f"{d}_nph{int(n_phases)}"
    # Parse nph2_Gauss → Gauss_nph2
    parts = str(csv_label or "").split("_", 1)
    if len(parts) == 2 and parts[0].startswith("nph") and parts[1]:
        return f"{parts[1]}_{parts[0]}"
    return str(csv_label or "model")


def _float_or_none(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def build_mixture_model_catalog(
    subdir: Path | str,
    *,
    best_label: str = "",
    r_min_ang: float = 5.0,
    r_max_ang: float = 120.0,
) -> MixtureModelCatalog:
    """
    Build selectable MIXTURE models from ``mixture_results.csv`` + per-label ``*.fit``.
    """
    sd = Path(subdir)
    cat = MixtureModelCatalog(
        output_subdir=str(sd.resolve()) if sd.is_dir() else str(sd),
        r_min_ang=float(r_min_ang),
        r_max_ang=float(r_max_ang),
    )
    csv_path = sd / "mixture_results.csv"
    if not csv_path.is_file():
        return cat
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return cat
    if not rows:
        return cat

    from autosaxs.skill.model_mixture.mixture import select_best_mixture_row

    best_row = select_best_mixture_row(rows)
    resolved_best = (best_label or "").strip() or str((best_row or {}).get("label") or "").strip()

    entries: List[MixtureModelEntry] = []
    for row in rows:
        csv_lab = str(row.get("label") or "").strip()
        if not csv_lab:
            continue
        try:
            n_phases = int(float(row.get("n_phases") or 0))
        except (TypeError, ValueError):
            n_phases = 0
        dist = str(row.get("dist") or "").strip()
        bic = _float_or_none(row.get("BIC_chi2"))
        disp = _display_label(csv_lab, dist, n_phases)
        is_best = csv_lab == resolved_best
        fit_path = None
        work = sd / csv_lab
        if work.is_dir():
            fits = sorted(work.glob("*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
            if fits:
                fit_path = str(fits[0].resolve())
        key = csv_lab
        label = f"{disp}{_fmt_bic(bic)}" + (" ★" if is_best else "")
        entries.append(
            MixtureModelEntry(
                key=key,
                label=label,
                csv_label=csv_lab,
                row=dict(row),
                fit_path=fit_path,
                bic_chi2=bic,
                n_phases=n_phases,
                dist=dist,
                is_best=is_best,
            )
        )
    # Stable order: best first, then by BIC_chi2, then label.
    def _rank(e: MixtureModelEntry) -> tuple:
        bic = e.bic_chi2 if e.bic_chi2 is not None else float("inf")
        return (0 if e.is_best else 1, bic, e.csv_label)

    entries.sort(key=_rank)
    cat.entries = entries
    if resolved_best:
        cat.best_key = resolved_best
    elif entries:
        cat.best_key = entries[0].key
    return cat
