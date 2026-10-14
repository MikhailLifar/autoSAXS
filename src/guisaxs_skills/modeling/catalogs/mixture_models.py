"""Catalog of MIXTURE ensemble fits (nph × distribution) for DR results navigation."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MixtureModelEntry:
    key: str
    label: str
    fit_path: Optional[str] = None
    row: Dict[str, Any] = field(default_factory=dict)
    bic_log: Optional[float] = None
    chi2: Optional[float] = None


@dataclass
class MixtureModelCatalog:
    entries: List[MixtureModelEntry] = field(default_factory=list)
    best_key: str = ""
    output_subdir: str = ""
    results_csv_path: str = ""

    def by_key(self, key: str) -> Optional[MixtureModelEntry]:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def best(self) -> Optional[MixtureModelEntry]:
        if self.best_key:
            hit = self.by_key(self.best_key)
            if hit is not None:
                return hit
        return self.entries[0] if self.entries else None


def _float_or_none(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _discover_fit_path(base: Path, label: str) -> Optional[str]:
    work = base / label
    if work.is_dir():
        fits = sorted(work.glob("*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
        if fits:
            return str(fits[0].resolve())
    # Fallback: label-prefixed fits in the sample root.
    cands = sorted(base.glob(f"{label}*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
    if cands:
        return str(cands[0].resolve())
    return None


def build_mixture_model_catalog(
    subdir: Path | str,
    *,
    best_label: str = "",
) -> MixtureModelCatalog:
    """
    Build a MIXTURE ensemble catalog from ``mixture_results.csv`` + per-label ``.fit`` files.

    Same discovery shape liveview's mixture pane uses via ``set_fit_models``.
    """
    sd = Path(subdir)
    cat = MixtureModelCatalog(output_subdir=str(sd.resolve()) if sd.is_dir() else str(sd))
    if not sd.is_dir():
        return cat
    csv_path = sd / "mixture_results.csv"
    if not csv_path.is_file():
        return cat
    cat.results_csv_path = str(csv_path.resolve())
    rows: List[Dict[str, str]] = []
    try:
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return cat
    entries: List[MixtureModelEntry] = []
    best_bic = float("inf")
    best_from_bic = ""
    for row in rows:
        lab = str(row.get("label") or "").strip()
        if not lab:
            continue
        bic = _float_or_none(row.get("BIC_log"))
        chi2 = _float_or_none(row.get("chi2"))
        if bic is not None and bic < best_bic:
            best_bic = bic
            best_from_bic = lab
        entries.append(
            MixtureModelEntry(
                key=lab,
                label=lab,
                fit_path=_discover_fit_path(sd, lab),
                row={str(k): row[k] for k in row},
                bic_log=bic,
                chi2=chi2,
            )
        )
    preferred = (best_label or "").strip() or best_from_bic
    cat.entries = entries
    cat.best_key = preferred if any(e.key == preferred for e in entries) else (
        entries[0].key if entries else ""
    )
    return cat
