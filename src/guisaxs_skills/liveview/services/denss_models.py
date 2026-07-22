"""Catalog of DENSS / model_density maps for monodisperse 3D viewing."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class DenssModelEntry:
    key: str
    label: str
    mrc_path: str
    kind: str  # "density"
    fit_path: Optional[str] = None
    sigma_path: Optional[str] = None
    is_primary: bool = False


@dataclass
class DenssModelCatalog:
    entries: List[DenssModelEntry] = field(default_factory=list)
    best_key: str = "primary"
    output_subdir: str = ""

    def by_key(self, key: str) -> Optional[DenssModelEntry]:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def best(self) -> Optional[DenssModelEntry]:
        return self.by_key(self.best_key) or (self.entries[0] if self.entries else None)


def _first_file(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        if p.is_file():
            return p
    return None


def build_denss_model_catalog(subdir: Path) -> DenssModelCatalog:
    """
    Build selectable DENSS maps under a ``model_density`` sample directory.

    Prefers refined map when present, else average, else pilot single map.
    Attaches ``*_sigma.mrc`` when available (average/refined).
    """
    sd = Path(subdir).expanduser().resolve()
    entries: List[DenssModelEntry] = []

    # denss-all nest: <stem>/<stem>_avg.mrc ; pilot: <stem>.mrc next to reports
    nested_dirs = sorted(
        [p for p in sd.iterdir() if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if sd.is_dir() else []

    refined = _first_file(sorted(sd.glob("*_refined.mrc"), key=lambda p: p.stat().st_mtime, reverse=True))
    avg = None
    sigma = None
    for nest in nested_dirs:
        avgs = sorted(nest.glob("*_avg.mrc"))
        if avgs:
            avg = avgs[0]
            sigs = sorted(nest.glob("*_sigma.mrc"))
            sigma = sigs[0] if sigs else None
            break
    if avg is None:
        avg = _first_file(sorted(sd.glob("*_avg.mrc"), key=lambda p: p.stat().st_mtime, reverse=True))
        if avg is not None:
            sigs = sorted(sd.glob("*_sigma.mrc"))
            sigma = sigs[0] if sigs else None

    pilot = None
    for cand in sorted(sd.glob("*.mrc")):
        name = cand.name.lower()
        if name.endswith("_avg.mrc") or name.endswith("_sigma.mrc") or name.endswith("_refined.mrc"):
            continue
        if name.endswith("_support.mrc") or name.endswith("_current.mrc"):
            continue
        if "_aligned" in name:
            continue
        pilot = cand
        break

    fit = _first_file(
        sorted(sd.glob("*_refined_map.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
        + sorted(sd.glob("*_map.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
    )
    # Prefer nest fit when average lives in nest
    if avg is not None:
        nest_fit = _first_file(sorted(avg.parent.glob("*_map.fit")))
        if nest_fit is not None and refined is None:
            fit = nest_fit

    sigma_s = str(sigma.resolve()) if sigma is not None else None

    if refined is not None:
        entries.append(
            DenssModelEntry(
                key="primary",
                label=f"Refined density — {refined.name}"
                + (" (σ colored)" if sigma_s else ""),
                mrc_path=str(refined.resolve()),
                kind="density",
                fit_path=str(fit.resolve()) if fit is not None else None,
                sigma_path=sigma_s,
                is_primary=True,
            )
        )
        if avg is not None:
            entries.append(
                DenssModelEntry(
                    key="average",
                    label=f"Average density — {avg.name}"
                    + (" (σ colored)" if sigma_s else ""),
                    mrc_path=str(avg.resolve()),
                    kind="density",
                    fit_path=str(fit.resolve()) if fit is not None else None,
                    sigma_path=sigma_s,
                    is_primary=False,
                )
            )
    elif avg is not None:
        entries.append(
            DenssModelEntry(
                key="primary",
                label=f"Average density — {avg.name}"
                + (" (σ colored)" if sigma_s else ""),
                mrc_path=str(avg.resolve()),
                kind="density",
                fit_path=str(fit.resolve()) if fit is not None else None,
                sigma_path=sigma_s,
                is_primary=True,
            )
        )
    elif pilot is not None:
        entries.append(
            DenssModelEntry(
                key="primary",
                label=f"Pilot density — {pilot.name}",
                mrc_path=str(pilot.resolve()),
                kind="density",
                fit_path=str(fit.resolve()) if fit is not None else None,
                sigma_path=None,
                is_primary=True,
            )
        )

    return DenssModelCatalog(
        entries=entries,
        best_key="primary",
        output_subdir=str(sd),
    )
