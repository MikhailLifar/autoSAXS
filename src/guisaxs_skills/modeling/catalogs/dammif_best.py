"""DAMMIF best-CIF resolution (shared by modeling catalogs)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

def _newest_dammif_dummy_cif(subdir: Path) -> Optional[str]:
    cifs = sorted(
        subdir.glob("dammif-*-1.cif"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(cifs[0].resolve()) if cifs else None


def best_dammif_cif(subdir: Path) -> Optional[str]:
    # Prefer model_dam ``best.cif`` symlink when present.
    best_link = subdir / "best.cif"
    if best_link.is_file() or best_link.is_symlink():
        try:
            return str(best_link.resolve())
        except OSError:
            pass
    yml = subdir / "dammif_fits.yml"
    if not yml.is_file():
        return _newest_dammif_dummy_cif(subdir)
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict) or not data:
            return _newest_dammif_dummy_cif(subdir)
        best_k = None
        best_c = float("inf")
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            c = v.get("chi2")
            try:
                cf = float(c)
            except (TypeError, ValueError):
                continue
            if cf < best_c:
                best_c = cf
                best_k = k
        if not best_k or not isinstance(best_k, str):
            return _newest_dammif_dummy_cif(subdir)
        cif = subdir / f"{best_k}-1.cif"
        if cif.is_file():
            return str(cif.resolve())
        legacy_zero_based = any(isinstance(k, str) and k == "dammif-0" for k in data.keys())
        if legacy_zero_based and best_k.startswith("dammif-"):
            try:
                idx = int(best_k.split("-", 1)[1])
            except (ValueError, IndexError):
                idx = -1
            if idx >= 0:
                leg = subdir / f"dammif-{idx + 1}-1.cif"
                if leg.is_file():
                    return str(leg.resolve())
        return _newest_dammif_dummy_cif(subdir)
    except Exception:
        return _newest_dammif_dummy_cif(subdir)


