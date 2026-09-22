"""Artifact path enrichment helpers for liveview job results."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ..services.artifacts import merge_fit_distances_quality_fields
from .monodisperse_pipeline import FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP
from .jobs import Job


def coerce_opt_int(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def read_first_last_from_best_summary(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, yaml.YAMLError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    sel = data.get("selected")
    if not isinstance(sel, dict):
        return None, None
    return (
        coerce_opt_int(sel.get("first")),
        coerce_opt_int(sel.get("last")),
    )


def read_first_last_from_fit_params(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, yaml.YAMLError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    return (
        coerce_opt_int(data.get("first")),
        coerce_opt_int(data.get("last")),
    )


def enrich_fit_distances_result(result: Dict[str, Any], *, watchdir: Path) -> Dict[str, Any]:
    out = dict(result or {})
    bs = out.get("fit_distances_log_path")
    fp = out.get("fit_params_path")
    first_i: Optional[int] = None
    last_i: Optional[int] = None
    try:
        if isinstance(bs, str) and bs.strip():
            p = Path(bs.strip()).expanduser()
            p = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
            if p.is_file():
                first_i, last_i = read_first_last_from_best_summary(p)
    except Exception:
        first_i, last_i = None, None
    if first_i is None and last_i is None:
        try:
            if isinstance(fp, str) and fp.strip():
                p2 = Path(fp.strip()).expanduser()
                p2 = p2.resolve() if p2.is_absolute() else (watchdir / p2).resolve()
                if p2.is_file():
                    first_i, last_i = read_first_last_from_fit_params(p2)
        except Exception:
            first_i, last_i = None, None
    if first_i is not None:
        out["selected_first"] = int(first_i)
    if last_i is not None:
        out["selected_last"] = int(last_i)
    return merge_fit_distances_quality_fields(out, watchdir=watchdir)


def resolve_artifact_path(
    path_str: str,
    *,
    resolve_bases: Optional[Sequence[Path]] = None,
    watchdir: Path,
) -> Path:
    p = Path(path_str.strip()).expanduser()
    if p.is_absolute():
        return p.resolve()
    bases = [b.expanduser().resolve() for b in (resolve_bases or [])]
    if not bases:
        bases = [watchdir.expanduser().resolve()]
    for base in bases:
        cand = (base / p).resolve()
        if cand.is_file():
            return cand
    return (bases[0] / p).resolve()


def artifact_resolve_bases_for_job(job: Optional[Job], *, watchdir: Path) -> List[Path]:
    bases: List[Path] = []
    if job is None:
        return [watchdir.expanduser().resolve()]
    or_raw = job.context.get("output_root")
    if isinstance(or_raw, str) and or_raw.strip():
        bases.append(Path(or_raw.strip()).expanduser().resolve())
    for step in job.steps:
        if step.name not in (FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP):
            continue
        od = (step.request.options or {}).get("output_dir")
        if isinstance(od, str) and od.strip():
            bases.append(Path(od.strip()).expanduser().resolve())
    bases.append(watchdir.expanduser().resolve())
    seen: set[str] = set()
    out: List[Path] = []
    for b in bases:
        key = str(b)
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def enrich_fit_guinier_result(
    result: Dict[str, Any],
    *,
    watchdir: Path,
    resolve_bases: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    from autosaxs.core.guinier import parse_guinier_results_txt
    from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

    out = dict(result or {})
    raw = out.get("results_path")
    if isinstance(raw, list) and len(raw) == 1:
        raw = raw[0]
    if not isinstance(raw, str) or not raw.strip():
        return out
    try:
        p = resolve_artifact_path(raw.strip(), resolve_bases=resolve_bases, watchdir=watchdir)
        if not p.is_file():
            return out
        data = parse_guinier_results_txt(str(p))
    except (OSError, TypeError, ValueError):
        return out
    for k, v in data.items():
        if k == "methods":
            continue
        if v is not None and out.get(k) is None:
            out[k] = v
    try:
        fp, lp = guinier_point_range_1based(out)
    except Exception:
        fp, lp = None, None
    if fp is not None:
        out["first_point_1based"] = fp
    if lp is not None:
        out["last_point_1based"] = lp
    return out
