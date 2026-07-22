"""Catalog of DAMMIF / DAMAVER models for monodisperse 3D viewing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

from .artifacts import best_dammif_cif


@dataclass(frozen=True)
class DamModelEntry:
    key: str
    label: str
    cif_path: str
    kind: str  # "dam" | "occupancy" | "overlap"
    chi2: Optional[float] = None
    fir_path: Optional[str] = None
    is_most_probable: bool = False


# Distinct RGBA colors for overlapped isosurfaces (stable by run index).
OVERLAP_RGBA: Tuple[Tuple[float, float, float, float], ...] = (
    (0.12, 0.47, 0.71, 0.50),  # blue
    (1.00, 0.50, 0.05, 0.45),  # orange
    (0.17, 0.63, 0.17, 0.45),  # green
    (0.84, 0.15, 0.16, 0.45),  # red
    (0.58, 0.40, 0.74, 0.45),  # purple
    (0.55, 0.34, 0.29, 0.45),  # brown
    (0.89, 0.47, 0.76, 0.45),  # pink
    (0.50, 0.50, 0.50, 0.45),  # gray
    (0.74, 0.74, 0.13, 0.45),  # olive
    (0.09, 0.75, 0.81, 0.45),  # cyan
)


@dataclass
class DamModelCatalog:
    entries: List[DamModelEntry] = field(default_factory=list)
    best_key: str = "best"
    output_subdir: str = ""

    def by_key(self, key: str) -> Optional[DamModelEntry]:
        for e in self.entries:
            if e.key == key:
                return e
        return None

    def best(self) -> Optional[DamModelEntry]:
        return self.by_key(self.best_key) or (self.entries[0] if self.entries else None)

    def overlay_candidates(self) -> List[DamModelEntry]:
        """Per-run DAM models for overlap checkboxes (unique CIFs; excludes occupancy/overlap)."""
        return [e for e in self.entries if e.kind == "dam" and e.key.startswith("run-")]



def _chi2_by_rep(subdir: Path) -> Dict[str, float]:
    """
    Map ATSAS rep tags (``dammif-1``, ``dammif-2``, …) → χ².

    Older autosaxs wrote 0-based YAML keys (``dammif-0`` …); remap those to the
    on-disk prefixes used by ``dammif-N-1.cif``.
    """
    yml = subdir / "dammif_fits.yml"
    if not yml.is_file():
        return {}
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    raw: Dict[str, float] = {}
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        c = v.get("chi2")
        try:
            raw[k] = float(c)
        except (TypeError, ValueError):
            continue
    legacy_zero_based = "dammif-0" in raw
    out: Dict[str, float] = {}
    for k, chi2 in raw.items():
        if legacy_zero_based and k.startswith("dammif-"):
            try:
                idx = int(k.split("-", 1)[1])
            except (ValueError, IndexError):
                out[k] = chi2
                continue
            out[f"dammif-{idx + 1}"] = chi2
        else:
            out[k] = chi2
    return out


def _resolve_best_target(subdir: Path, best_link: Optional[str]) -> Optional[Path]:
    if not best_link:
        return None
    p = Path(best_link)
    try:
        return p.resolve() if p.exists() else None
    except OSError:
        return p if p.is_file() else None


def _frequency_map_path(subdir: Path) -> Optional[Path]:
    dam_dir = subdir / "damaver"
    search_roots = [dam_dir, subdir] if dam_dir.is_dir() else [subdir]
    candidates: List[Path] = []
    for root in search_roots:
        candidates.extend(sorted(root.glob("*damaver*.cif")))
        candidates.extend(sorted(root.glob("*damaver*.pdb")))
    preferred = [
        p
        for p in candidates
        if "damfilt" not in p.name.lower()
        and "damstart" not in p.name.lower()
        and "damaver" in p.name.lower()
    ]
    for p in preferred:
        if "global-damaver" in p.name.lower() or p.name.lower().endswith("damaver.cif"):
            return p
    return preferred[0] if preferred else None


def _fir_for_rep(subdir: Path, rep_tag: str) -> Optional[str]:
    fir = subdir / f"{rep_tag}.fir"
    return str(fir.resolve()) if fir.is_file() else None


def _fmt_chi2(chi2: Optional[float]) -> str:
    if chi2 is None:
        return ""
    return f" (χ²={chi2:.2f})"


def build_dam_model_catalog(subdir: Path) -> DamModelCatalog:
    """
    Build selectable models under a ``model_dam`` output directory.

    Entries: most probable (best.cif), each ``dammif-N-1.cif`` run, and occupancy map when present.
    """
    sd = Path(subdir)
    chi2_map = _chi2_by_rep(sd)
    best_link = best_dammif_cif(sd)
    best_target = _resolve_best_target(sd, best_link)
    best_name = best_target.name if best_target is not None else None

    entries: List[DamModelEntry] = []
    run_cifs = sorted(sd.glob("dammif-*-1.cif"), key=lambda p: p.name)

    # Most probable / best entry first when we have any CIF.
    if best_link and Path(best_link).exists():
        rep_match = re.match(r"(dammif-\d+)-1\.cif$", best_name or "", flags=re.I)
        rep_tag = rep_match.group(1) if rep_match else None
        chi2 = chi2_map.get(rep_tag) if rep_tag else None
        label = f"Most probable — {best_name or 'best.cif'}{_fmt_chi2(chi2)}"
        entries.append(
            DamModelEntry(
                key="best",
                label=label,
                cif_path=str(Path(best_link).resolve()),
                kind="dam",
                chi2=chi2,
                fir_path=_fir_for_rep(sd, rep_tag) if rep_tag else None,
                is_most_probable=True,
            )
        )

    for cif in run_cifs:
        m = re.match(r"(dammif-(\d+))-1\.cif$", cif.name, flags=re.I)
        if not m:
            continue
        rep_tag = m.group(1)
        run_idx = int(m.group(2))
        chi2 = chi2_map.get(rep_tag)
        is_mp = bool(best_name and cif.name == best_name)
        mp_tag = " [most probable]" if is_mp else ""
        label = f"Run {run_idx} — {cif.name}{_fmt_chi2(chi2)}{mp_tag}"
        entries.append(
            DamModelEntry(
                key=f"run-{run_idx}",
                label=label,
                cif_path=str(cif.resolve()),
                kind="dam",
                chi2=chi2,
                fir_path=_fir_for_rep(sd, rep_tag),
                is_most_probable=is_mp,
            )
        )

    freq = _frequency_map_path(sd)
    if freq is not None and freq.is_file():
        entries.append(
            DamModelEntry(
                key="occupancy",
                label=f"Occupancy map — {freq.name}",
                cif_path=str(freq.resolve()),
                kind="occupancy",
            )
        )

    # Overlap comparison view (aligned isosurfaces); needs at least one DAM model.
    if any(e.kind == "dam" and e.key.startswith("run-") for e in entries) or any(
        e.key == "best" for e in entries
    ):
        entries.append(
            DamModelEntry(
                key="overlap",
                label="Overlap — aligned comparison",
                cif_path="",
                kind="overlap",
            )
        )

    if not entries and best_link and Path(best_link).is_file():
        entries.append(
            DamModelEntry(
                key="best",
                label=f"Most probable — {Path(best_link).name}",
                cif_path=str(Path(best_link).resolve()),
                kind="dam",
                is_most_probable=True,
            )
        )
        entries.append(
            DamModelEntry(
                key="overlap",
                label="Overlap — aligned comparison",
                cif_path="",
                kind="overlap",
            )
        )

    return DamModelCatalog(entries=entries, best_key="best", output_subdir=str(sd.resolve()))


def ensure_aligned_cif(*, static: Path, movable: Path, out_dir: Path) -> Path:
    """
    Align ``movable`` onto ``static`` with ATSAS ``cifsup`` (NSD).

    When paths resolve to the same file, returns ``movable`` (already in the reference frame).
    Caches ``{stem}_aligned.cif`` under ``out_dir``.
    """
    import shutil
    import subprocess

    static_r = static.expanduser().resolve()
    movable_r = movable.expanduser().resolve()
    if not static_r.is_file():
        raise FileNotFoundError(f"Alignment template missing: {static_r}")
    if not movable_r.is_file():
        raise FileNotFoundError(f"Alignment movable missing: {movable_r}")
    if static_r == movable_r:
        return movable_r

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{movable_r.stem}_aligned.cif"
    try:
        if out.is_file() and out.stat().st_mtime >= movable_r.stat().st_mtime:
            return out.resolve()
    except OSError:
        pass

    cifsup = shutil.which("cifsup")
    if not cifsup:
        raise RuntimeError("ATSAS `cifsup` not found on PATH (needed for aligned overlap view).")

    proc = subprocess.run(
        [
            cifsup,
            "--method=nsd",
            "-o",
            str(out),
            str(static_r),
            str(movable_r),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out.is_file():
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"cifsup failed for {movable_r.name}: {err or 'no output'}")
    return out.resolve()


def prepare_overlap_items(
    catalog: DamModelCatalog,
    *,
    selected_keys: List[str],
) -> List[Dict[str, object]]:
    """
    Build worker items for overlap view: each selected run aligned to the most probable model.

    Returns list of dicts: ``key``, ``label``, ``path``, ``rgba``.
    """
    candidates = {e.key: e for e in catalog.overlay_candidates()}
    if not candidates:
        # Fall back to best-only catalog (single-run without run-* entries).
        best = catalog.best()
        if best is None or best.kind != "dam":
            return []
        return [
            {
                "key": best.key,
                "label": best.label,
                "path": best.cif_path,
                "rgba": OVERLAP_RGBA[0],
            }
        ]

    # Reference frame: most probable run CIF (or first candidate).
    ref = next((e for e in candidates.values() if e.is_most_probable), None)
    if ref is None:
        best = catalog.best()
        if best is not None and best.cif_path:
            best_path = str(Path(best.cif_path).resolve())
            ref = next(
                (e for e in candidates.values() if str(Path(e.cif_path).resolve()) == best_path),
                None,
            )
        if ref is None:
            ref = next(iter(candidates.values()))

    out_dir = (
        Path(catalog.output_subdir) / "aligned"
        if catalog.output_subdir
        else Path(ref.cif_path).parent / "aligned"
    )
    static = Path(ref.cif_path)

    items: List[Dict[str, object]] = []
    for i, key in enumerate(selected_keys):
        entry = candidates.get(key)
        if entry is None:
            continue
        movable = Path(entry.cif_path)
        aligned = ensure_aligned_cif(static=static, movable=movable, out_dir=out_dir)
        m = re.match(r"run-(\d+)$", key)
        color_idx = max(0, int(m.group(1)) - 1) if m else i
        rgba = OVERLAP_RGBA[color_idx % len(OVERLAP_RGBA)]
        items.append(
            {
                "key": entry.key,
                "label": entry.label,
                "path": str(aligned),
                "rgba": rgba,
            }
        )
    return items


def read_cif_xyz_occupancy(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """
    Parse an ATSAS mmCIF ``_atom_site`` loop into (N,3) positions and (N,) occupancy.

    Missing occupancy columns default to 1.0.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # Locate the _atom_site loop that contains Cartn_x.
    start = None
    for i, raw in enumerate(lines):
        if raw.strip().lower() == "loop_":
            # Look ahead for Cartn_x in this loop's column headers
            j = i + 1
            headers: List[str] = []
            while j < len(lines) and lines[j].strip().startswith("_"):
                headers.append(lines[j].strip().split()[0])
                j += 1
            if any(h.lower().endswith("cartn_x") for h in headers):
                start = i
                cols = headers
                data_start = j
                break
    if start is None:
        from autosaxs.core.utils import read_bodies_cif

        atoms = read_bodies_cif(path)
        pts = np.asarray(atoms.positions, dtype=np.float64)
        occ = np.ones(len(pts), dtype=np.float64)
        return pts, occ

    def _idx(*names: str) -> Optional[int]:
        for i, c in enumerate(cols):
            cl = c.lower()
            for n in names:
                if cl.endswith(n.lower()) or cl == n.lower():
                    return i
        return None

    ix = _idx("_atom_site.Cartn_x", "Cartn_x")
    iy = _idx("_atom_site.Cartn_y", "Cartn_y")
    iz = _idx("_atom_site.Cartn_z", "Cartn_z")
    io = _idx("_atom_site.occupancy", "occupancy")
    if ix is None or iy is None or iz is None:
        from autosaxs.core.utils import read_bodies_cif

        atoms = read_bodies_cif(path)
        pts = np.asarray(atoms.positions, dtype=np.float64)
        occ = np.ones(len(pts), dtype=np.float64)
        return pts, occ

    xyz: List[List[float]] = []
    occ_l: List[float] = []
    for raw in lines[data_start:]:
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("_") or line.lower() == "loop_" or line.startswith("data_"):
            if xyz:
                break
            continue
        parts = line.split()
        if len(parts) <= max(ix, iy, iz):
            continue
        try:
            xyz.append([float(parts[ix]), float(parts[iy]), float(parts[iz])])
            if io is not None and io < len(parts):
                occ_l.append(float(parts[io]))
            else:
                occ_l.append(1.0)
        except (ValueError, IndexError):
            continue
    if not xyz:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0,), dtype=np.float64)
    return np.asarray(xyz, dtype=np.float64), np.asarray(occ_l, dtype=np.float64)
