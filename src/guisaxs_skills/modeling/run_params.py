"""Resolve modeling output dirs (family vs per-sample stem) and run-params YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

_RUN_PARAMS_FILES = (
    "model_dam_run_params.yml",
    "model_density_run_params.yml",
    "model_bodies_run_params.yml",
    "model_mixture_run_params.yml",
)

_FAMILY_NAMES = frozenset({"dammif", "denss", "model_bodies", "fit_bodies", "mixture"})

# Shape / DR engine mode → liveview skill family folder under the analysis root.
_MODE_TO_FAMILY = {
    "dammif": "dammif",
    "bodies": "model_bodies",
    "denss": "denss",
    "mixture": "mixture",
}


def profile_sample_stem(profile_abs: str) -> str:
    """Match liveview ``profile_sample_stem`` (strip ``sub_`` / ``int_``)."""
    stem = Path(profile_abs or "").stem
    for prefix in ("sub_", "int_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem


def _has_dam_artifacts(sd: Path) -> bool:
    return sd.is_dir() and (
        any(sd.glob("dammif-*-1.cif"))
        or any(sd.glob("dammif-*.cif"))
        or (sd / "dammif_fits.yml").is_file()
        or (sd / "best.cif").exists()
        or (sd / "model_dam_run_params.yml").is_file()
    )


def _has_bodies_artifacts(sd: Path) -> bool:
    return sd.is_dir() and (
        (sd / "bodies_fits.yml").is_file()
        or any(sd.glob("bodies_fit-*.fir"))
        or any(sd.glob("*.fir"))
        or (sd / "model_bodies_run_params.yml").is_file()
    )


def _has_denss_artifacts(sd: Path) -> bool:
    if not sd.is_dir():
        return False
    if (sd / "model_density_run_params.yml").is_file():
        return True
    if any(sd.glob("*.mrc")) or any(sd.glob("*_map.fit")) or any(sd.glob("*_denss_input.dat")):
        return True
    return any(p.is_dir() and any(p.glob("*_avg.mrc")) for p in sd.iterdir() if p.is_dir())


def _has_mixture_artifacts(sd: Path) -> bool:
    return sd.is_dir() and (
        (sd / "mixture_results.csv").is_file() or (sd / "model_mixture_run_params.yml").is_file()
    )


def _has_any_modeling_artifacts(sd: Path) -> bool:
    return (
        _has_dam_artifacts(sd)
        or _has_bodies_artifacts(sd)
        or _has_denss_artifacts(sd)
        or _has_mixture_artifacts(sd)
    )


def resolve_sample_modeling_dir(
    family_or_sample: str | Path,
    *,
    profile_path: str = "",
    stem: str = "",
) -> Path:
    """
    Map a modeling family dir (``…/dammif``) or sample dir (``…/dammif/<stem>``)
    to the directory that actually holds skill artifacts.

    Skills use ``per_sample_subdir="always"`` under the family dir.
    """
    base = Path(family_or_sample).expanduser()
    st = (stem or profile_sample_stem(profile_path) or "").strip()

    def _prefer_stem_under(family: Path) -> Path:
        if not st:
            return family
        cand = family / st
        if cand.is_dir() and _has_any_modeling_artifacts(cand):
            return cand
        if family.is_dir() and _has_any_modeling_artifacts(family):
            return family
        return cand

    # Already at ``family/<stem>``.
    if st and base.name == st:
        if base.is_dir() and _has_any_modeling_artifacts(base):
            return base
        # Empty placeholder stem dir: fall back to sibling / flat family artifacts.
        if base.parent.is_dir() and base.parent.name in _FAMILY_NAMES:
            return _prefer_stem_under(base.parent)
        return base

    if st:
        return _prefer_stem_under(base)

    if base.is_dir() and _has_any_modeling_artifacts(base):
        return base
    # No stem: pick newest child sample dir with artifacts.
    if base.is_dir():
        children = sorted(
            (p for p in base.iterdir() if p.is_dir() and _has_any_modeling_artifacts(p)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if children:
            return children[0]
    return base


def skill_batch_output_dir(sample_or_family: str | Path, *, profile_path: str = "") -> Path:
    """
    Directory to pass as ``output_dir`` to modeling skills.

    ``apply_batch(..., per_sample_subdir=\"always\")`` appends the profile stem, so
    pass the **family** dir when ``sample_or_family`` is already ``family/stem``.
    """
    p = Path(sample_or_family).expanduser().resolve()
    st = profile_sample_stem(profile_path)
    if st and p.name == st:
        return p.parent
    if p.name in _FAMILY_NAMES:
        return p
    # If parent is a known family, treat p as sample dir.
    if p.parent.name in _FAMILY_NAMES:
        return p.parent
    return p


def sample_modeling_dir_for_family(
    family_dir: str | Path,
    *,
    profile_path: str,
) -> Path:
    """``family/<stem>`` for this profile (create-ready path, not necessarily existing)."""
    family = Path(family_dir).expanduser()
    st = profile_sample_stem(profile_path)
    if not st:
        return family
    return family / st


def analysis_root_from_modeling_path(path: str | Path) -> Optional[Path]:
    """
    Infer the analysis output root from a modeling family or sample path.

    ``…/dammif`` → ``…``; ``…/dammif/<stem>`` → ``…``; unknown layout → ``None``.
    """
    p = Path(path).expanduser()
    if not str(p):
        return None
    if p.name in _FAMILY_NAMES:
        return p.parent
    if p.parent.name in _FAMILY_NAMES:
        return p.parent.parent
    return None


def family_dir_for_mode(mode: str) -> Optional[str]:
    """Conventional skill family folder name for a modeling-app mode, or ``None``."""
    return _MODE_TO_FAMILY.get((mode or "").strip().lower())


def conventional_sample_modeling_dir(
    *,
    mode: str,
    current_outdir: str = "",
    profile_path: str = "",
    analysis_root: str | Path | None = None,
) -> Optional[Path]:
    """
    Suggested per-sample output dir for ``mode`` under the analysis root.

    Prefers ``analysis_root`` when given; otherwise derives it from ``current_outdir``
    when that path is already under a known family (``dammif`` / ``model_bodies`` / …).
    """
    family = family_dir_for_mode(mode)
    if not family:
        return None
    root: Optional[Path] = None
    if analysis_root is not None and str(analysis_root).strip():
        root = Path(analysis_root).expanduser()
    elif current_outdir.strip():
        root = analysis_root_from_modeling_path(current_outdir)
    if root is None:
        return None
    return sample_modeling_dir_for_family(root / family, profile_path=profile_path)


def read_run_params(output_dir: str | Path, *, profile_path: str = "") -> Dict[str, Any]:
    """Load ``*_run_params.yml`` from a sample dir (or resolve stem under a family dir)."""
    sd = resolve_sample_modeling_dir(output_dir, profile_path=profile_path)
    if not sd.is_dir():
        return {}
    try:
        import yaml
    except Exception:
        return {}
    for name in _RUN_PARAMS_FILES:
        path = sd / name
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict):
            return dict(data)
    return {}


def infer_shape_mode_from_disk(output_dir: str | Path, *, profile_path: str = "") -> Optional[str]:
    """Infer BODIES / DAMMIF / DENSS from artifacts or run-params."""
    sd = resolve_sample_modeling_dir(output_dir, profile_path=profile_path)
    if not sd.is_dir():
        return None
    params = read_run_params(sd, profile_path=profile_path)
    skill = str(params.get("skill") or "").strip().lower()
    if skill == "model_dam":
        return "dammif"
    if skill == "model_density":
        return "denss"
    if skill == "model_bodies":
        return "bodies"
    if _has_dam_artifacts(sd):
        return "dammif"
    if _has_bodies_artifacts(sd):
        return "bodies"
    if _has_denss_artifacts(sd):
        return "denss"
    return None


def infer_n_runs_from_disk(output_dir: str | Path, *, profile_path: str = "") -> Optional[int]:
    """``n_runs`` from run-params or by counting DAMMIF replica CIFs."""
    sd = resolve_sample_modeling_dir(output_dir, profile_path=profile_path)
    if not sd.is_dir():
        return None
    params = read_run_params(sd, profile_path=profile_path)
    if "n_runs" in params:
        try:
            return max(1, int(params["n_runs"]))
        except (TypeError, ValueError):
            pass
    n = len(list(sd.glob("dammif-*-1.cif")))
    return n if n >= 1 else None


def apply_disk_params_to_session_state(state: Any, *, output_dir: str | Path, profile_path: str = "") -> None:
    """
    Copy skill ``*_run_params.yml`` (or CIF-count fallback) into liveview session fields.

    Keeps slim-pane controls and the next modeling-app launch aligned with disk.
    """
    sd = resolve_sample_modeling_dir(output_dir, profile_path=profile_path)
    params = read_run_params(sd, profile_path=profile_path)
    mode = infer_shape_mode_from_disk(sd, profile_path=profile_path)

    if mode == "dammif" or (params and str(params.get("skill") or "") == "model_dam"):
        n = None
        if "n_runs" in params:
            try:
                n = max(1, int(params["n_runs"]))
            except (TypeError, ValueError):
                n = None
        if n is None:
            n = infer_n_runs_from_disk(sd, profile_path=profile_path)
        if n is not None:
            try:
                state.model_dam_n_runs = int(n)
            except Exception:
                pass

    if mode == "denss" or (params and str(params.get("skill") or "") == "model_density"):
        if "mode" in params:
            try:
                state.model_density_mode = str(params["mode"])
            except Exception:
                pass
        if "denss_mode" in params:
            try:
                state.model_density_denss_mode = str(params["denss_mode"]).lower()
            except Exception:
                pass
        if "n_maps" in params:
            try:
                state.model_density_n_maps = max(2, int(params["n_maps"]))
            except Exception:
                pass

    if mode == "bodies" or (params and str(params.get("skill") or "") == "model_bodies"):
        shapes = params.get("shapes")
        if isinstance(shapes, list) and shapes:
            try:
                state.model_bodies_shapes = [str(s) for s in shapes]
            except Exception:
                pass

    if params and str(params.get("skill") or "") == "model_mixture":
        opts = dict(getattr(state, "model_mixture_options", None) or {})
        for key in ("max_nph", "r_max_nm", "poly_max_nm", "q_min", "q_max", "r_min_nm", "poly_min_nm"):
            if key in params and params[key] is not None:
                opts[key] = params[key]
        try:
            state.model_mixture_options = opts
        except Exception:
            pass
