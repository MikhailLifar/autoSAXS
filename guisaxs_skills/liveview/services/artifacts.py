from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml


def norm_artifact_path(val: object) -> str:
    if isinstance(val, list) and len(val) == 1:
        val = val[0]
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if not s or s.lower() == "none":
        return ""
    return s


def resolve_artifact_path(val: object, *, bases: list[Path]) -> str:
    """Resolve a skill artifact path (absolute or relative to watch/output roots)."""
    s = norm_artifact_path(val)
    if not s:
        return ""
    p = Path(s).expanduser()
    if p.is_absolute() and p.is_file():
        return str(p.resolve())
    for base in bases:
        try:
            cand = (base.expanduser().resolve() / p).resolve()
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    if p.is_absolute():
        return str(p.resolve())
    if bases:
        return str((bases[0].expanduser().resolve() / p).resolve())
    return s


def _newest_dammif_dummy_cif(subdir: Path) -> Optional[str]:
    cifs = sorted(
        subdir.glob("dammif-*-1.cif"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(cifs[0].resolve()) if cifs else None


def best_dammif_cif(subdir: Path) -> Optional[str]:
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


def bodies_best_fit(subdir: Path) -> tuple[Optional[str], dict[str, float], Optional[str]]:
    """Lowest-chi2 row from ``bodies_fits.yml`` plus CSV path if present."""
    yml = subdir / "bodies_fits.yml"
    csv_path = subdir / "bodies_fits.csv"
    csv_str = str(csv_path.resolve()) if csv_path.is_file() else None
    if not yml.is_file():
        return None, {}, csv_str
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict) or not data:
            return None, {}, csv_str
        best_shape: Optional[str] = None
        best_c = float("inf")
        best_row: Optional[dict[str, Any]] = None
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
                best_shape = k if isinstance(k, str) else None
                best_row = v
        if not best_shape or best_row is None:
            return None, {}, csv_str
        params: dict[str, float] = {}
        for pk, pv in best_row.items():
            if pk == "chi2" or isinstance(pv, bool):
                continue
            try:
                params[str(pk)] = float(pv)
            except (TypeError, ValueError):
                continue
        return best_shape, params, csv_str
    except Exception:
        return None, {}, csv_str


_FIT_DISTANCES_QUALITY_KEYS = (
    "dmax_nm",
    "rg_pr_nm",
    "i0_pr",
    "rg_guinier_nm",
    "q_min_fit_nm",
    "total_estimate",
    "delta_rg_pct",
    "shannon_s_min",
    "shannon_class",
    "shannon_ok",
    "shannon_tip",
    "pr_quality_class",
    "overall_status",
    "quality_passport_path",
    "best_summary_path",
    "fit_params_path",
)


def _resolve_under_watchdir(path_str: str, watchdir: Path) -> Path:
    p = Path(path_str.strip()).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (watchdir / p).resolve()


def _read_fit_distances_quality_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _profile_sample_stem(profile_abs: str) -> str:
    stem = Path(profile_abs).stem
    for prefix in ("sub_", "int_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem


def gnom_out_usable_for_dammif(path: str, *, min_iq_points: int = 5) -> bool:
    """True when path is a GNOM/DATGNOM .out with enough I(q) points for DAMMIF."""
    if not path or not str(path).lower().endswith(".out"):
        return False
    p = Path(path).expanduser()
    if not p.is_file():
        return False
    try:
        from autosaxs.core.gnom import parse_gnom_out

        parsed = parse_gnom_out(str(p.resolve()))
        iq = parsed.get("iq_table")
        if not iq or len(iq) != 4:
            return False
        return len(iq[0]) >= int(min_iq_points)
    except Exception:
        return False


def discover_gnom_out_path(
    *,
    profile_abs: str,
    output_root: Path,
    watchdir: Path,
    hint: str = "",
) -> str:
    """Resolve the best DATGNOM .out for DAMMIF (newest usable under fit_distances/<stem>/)."""
    from ..session.output_paths import fit_distances_dir

    bases = [output_root.expanduser().resolve(), watchdir.expanduser().resolve()]
    seen: set[str] = set()
    candidates: list[Path] = []

    hint_s = norm_artifact_path(hint)
    if hint_s:
        candidates.append(Path(hint_s))

    stem = _profile_sample_stem(profile_abs)
    fd = fit_distances_dir(output_root) / stem
    if fd.is_dir():
        for patt in ("datgnom_rg_*.out", "*_gnom.out", "*.out"):
            candidates.extend(sorted(fd.glob(patt), key=lambda p: p.stat().st_mtime, reverse=True))

    for cand in candidates:
        resolved = resolve_artifact_path(str(cand), bases=bases)
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        if gnom_out_usable_for_dammif(resolved):
            return str(Path(resolved).expanduser().resolve())
    return ""


def merge_fit_distances_quality_fields(result: dict, *, watchdir: Path) -> dict[str, Any]:
    """Fill GNOM / p(r) diagnostics missing from skill stdout (key=value lines)."""
    out = dict(result or {})
    candidates: list[Path] = []
    qp = norm_artifact_path(out.get("quality_passport_path"))
    if qp:
        candidates.append(_resolve_under_watchdir(qp, watchdir))
    sub = norm_artifact_path(out.get("output_subdir"))
    if sub:
        sd = _resolve_under_watchdir(sub, watchdir)
        if sd.is_dir():
            candidates.extend(
                sorted(sd.glob("*_fit_distances_quality.yml"), key=lambda p: p.stat().st_mtime, reverse=True)
            )
    bs = norm_artifact_path(out.get("best_summary_path"))
    if bs:
        candidates.append(_resolve_under_watchdir(bs, watchdir))
    seen: set[str] = set()
    quality: dict[str, Any] = {}
    for cand in candidates:
        key = str(cand)
        if key in seen or not cand.is_file():
            continue
        seen.add(key)
        if cand.name.endswith("_fit_distances_quality.yml"):
            quality.update(_read_fit_distances_quality_yaml(cand))
            out.setdefault("quality_passport_path", str(cand.resolve()))
            continue
        if cand.name.endswith("_fit_distances_best.yml"):
            summary = _read_fit_distances_quality_yaml(cand)
            sel = summary.get("selected")
            if isinstance(sel, dict):
                for k in ("first", "last"):
                    if sel.get(k) is not None and out.get(f"selected_{k}") is None:
                        out[f"selected_{k}"] = sel[k]
            out.setdefault("best_summary_path", str(cand.resolve()))
    for key in _FIT_DISTANCES_QUALITY_KEYS:
        if key in quality and quality[key] is not None and out.get(key) in (None, "", []):
            val = quality[key]
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            out[key] = val
    return out


_FIT_SIZES_QUALITY_KEYS = (
    "d_avg_nm",
    "d_std_nm",
    "pdi",
    "dr_peak_positions_nm",
    "dr_n_peaks",
    "modality_class",
    "rg_guinier_nm",
    "dmax_nm",
    "q_min_fit_nm",
    "total_estimate",
    "shannon_s_min",
    "shannon_class",
    "shannon_ok",
    "shannon_tip",
    "sizes_quality_class",
    "overall_status",
    "quality_passport_path",
    "best_summary_path",
    "fit_params_path",
    "dr_csv_path",
    "best_gnom_out_path",
)


def merge_fit_sizes_quality_fields(result: dict, *, watchdir: Path) -> dict[str, Any]:
    """Fill D(R) / Shannon diagnostics missing from skill stdout (key=value lines)."""
    out = dict(result or {})
    candidates: list[Path] = []
    qp = norm_artifact_path(out.get("quality_passport_path"))
    if qp:
        candidates.append(_resolve_under_watchdir(qp, watchdir))
    sub = norm_artifact_path(out.get("output_subdir"))
    if sub:
        sd = _resolve_under_watchdir(sub, watchdir)
        if sd.is_dir():
            candidates.extend(
                sorted(sd.glob("*_fit_sizes_quality.yml"), key=lambda p: p.stat().st_mtime, reverse=True)
            )
    bs = norm_artifact_path(out.get("best_summary_path"))
    if bs:
        candidates.append(_resolve_under_watchdir(bs, watchdir))
    seen: set[str] = set()
    quality: dict[str, Any] = {}
    for cand in candidates:
        key = str(cand)
        if key in seen or not cand.is_file():
            continue
        seen.add(key)
        if cand.name.endswith("_fit_sizes_quality.yml"):
            quality.update(_read_fit_distances_quality_yaml(cand))
            out.setdefault("quality_passport_path", str(cand.resolve()))
            continue
        if cand.name.endswith("_fit_sizes_best.yml"):
            summary = _read_fit_distances_quality_yaml(cand)
            sel = summary.get("selected")
            if isinstance(sel, dict):
                for k in ("first", "last"):
                    if sel.get(k) is not None and out.get(f"selected_{k}") is None:
                        out[f"selected_{k}"] = sel[k]
                if sel.get("out_path") and out.get("best_gnom_out_path") in (None, "", []):
                    out["best_gnom_out_path"] = sel["out_path"]
            qdoc = summary.get("quality")
            if isinstance(qdoc, dict):
                quality.update(qdoc)
            out.setdefault("best_summary_path", str(cand.resolve()))
            for path_key in ("dr_csv_path", "fit_params_path", "best_gnom_out_path"):
                if summary.get(path_key) and out.get(path_key) in (None, "", []):
                    out[path_key] = summary[path_key]
    for key in _FIT_SIZES_QUALITY_KEYS:
        if key in quality and quality[key] is not None and out.get(key) in (None, "", []):
            val = quality[key]
            if isinstance(val, list) and len(val) == 1:
                val = val[0]
            out[key] = val
    return out
