from __future__ import annotations

"""
Smart defaults for path fields are driven by :class:`SessionPathHints` in ``session_state``:
global semantic keys (e.g. ``two_d_tif_dir``, ``one_d_profile_dir``) that any skill can consume or
refresh. Success handlers map each skill's inputs/outputs onto those keys; the form maps each
parameter to the hints it cares about. This replaces an implicit "chain" of skills with explicit
shared state.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import RunRequest
from .path_normalize import normalize_pathish
from .session_state import SessionPathHints

INTEGRATOR_MARKERS = ("detector_params.json", "ai_params.json")

ANALYSIS_SKILLS_WITH_PROFILE = frozenset(
    {
        "plot",
        "fit_guinier",
        "model_mixture",
        "fit_distances",
        "model_bodies",
        "model_dam",
        "model_density",
    }
)


def resolve_under_workdir(pathish: str, workdir: Path) -> Path:
    p = Path(normalize_pathish(pathish))
    if not p.is_absolute():
        p = workdir / p
    try:
        return p.resolve()
    except OSError:
        return p


def resolve_existing_path_str(pathish: str, workdir: Path) -> Optional[str]:
    """Return resolved path string if it exists (file or directory), else None."""
    p = resolve_under_workdir(pathish, workdir)
    if p.exists():
        return str(p)
    return None


def path_exists(pathish: str, workdir: Path) -> bool:
    return resolve_under_workdir(pathish, workdir).exists()


def paths_from_saved_path_state(state: dict) -> List[str]:
    dropped = list(state.get("dropped_paths") or [])
    if dropped:
        return [normalize_pathish(p) for p in dropped if normalize_pathish(p)]
    t = (state.get("text") or "").strip()
    if not t:
        return []
    parts = [normalize_pathish(p.strip()) for p in t.split(",") if p.strip()]
    return parts if parts else [normalize_pathish(t)]


def saved_path_state_all_exist(workdir: Path, state: dict) -> bool:
    parts = paths_from_saved_path_state(state)
    if not parts:
        return False
    return all(path_exists(p, workdir) for p in parts)


def path_value_from_saved_state(workdir: Path, state: dict, *, multiple: bool) -> Optional[str]:
    if not saved_path_state_all_exist(workdir, state):
        return None
    parts = paths_from_saved_path_state(state)
    if not parts:
        return None
    resolved = [str(resolve_under_workdir(p, workdir)) for p in parts]
    if multiple:
        return ", ".join(resolved)
    return resolved[0]


def first_path_segment(text: str) -> Optional[str]:
    s = (text or "").strip()
    if not s:
        return None
    first = s.split(",")[0].strip()
    return first or None


def anchor_dir_for_path_expression(text: str, workdir: Path) -> Optional[Path]:
    seg = first_path_segment(text)
    if not seg:
        return None
    p = resolve_under_workdir(seg, workdir)
    if not p.exists():
        return None
    if p.is_file():
        return p.parent
    if p.is_dir():
        return p
    return None


def path_expression_paths_fully_exist(paths: List[str], workdir: Path) -> bool:
    parts = [normalize_pathish(p) for p in paths if normalize_pathish(p)]
    if not parts:
        return False
    return all(path_exists(p, workdir) for p in parts)


def anchor_dir_from_resolved_path_list(paths: List[str], workdir: Path) -> Optional[Path]:
    for raw in paths:
        seg = normalize_pathish(raw)
        if not seg:
            continue
        p = resolve_under_workdir(seg, workdir)
        if not p.exists():
            continue
        if p.is_file():
            return p.parent
        if p.is_dir():
            return p
    return None


def common_parent_dir_if_all_files(paths: List[str], workdir: Path) -> Optional[Path]:
    resolved_parents: List[Path] = []
    for raw in paths:
        seg = normalize_pathish(raw)
        if not seg:
            return None
        p = resolve_under_workdir(seg, workdir)
        if not p.is_file():
            return None
        resolved_parents.append(p.parent.resolve())
    if not resolved_parents:
        return None
    first = resolved_parents[0]
    if all(x == first for x in resolved_parents):
        return first
    return None


def _is_valid_integrator_dir(d: Path) -> bool:
    return d.is_dir() and all((d / m).is_file() for m in INTEGRATOR_MARKERS)


def find_integrator_dir_near(base: Path) -> Optional[Path]:
    for candidate in (base / "integrator", base.parent / "integrator"):
        if _is_valid_integrator_dir(candidate):
            return candidate
    return None


def find_config_conf_near(base: Path) -> Optional[Path]:
    for directory in (base, base.parent):
        matches = sorted(directory.glob("config*.conf"))
        for m in matches:
            if m.is_file():
                return m
    return None


def find_mask_near(base: Path) -> Optional[Path]:
    """
    Pick a likely mask file near a dataset folder.

    Preference order (sorted within each glob) in `base` then `base.parent`:
    - mask*.txt, mask*.npy (NumPy-style), mask*.msk (Fit2D)
    - mask.msk (exact legacy name)
    - any *.msk (last resort)
    """

    def first_in(directory: Path) -> Optional[Path]:
        candidates: List[Path] = []
        candidates.extend(sorted(directory.glob("mask*.txt")))
        candidates.extend(sorted(directory.glob("mask*.npy")))
        candidates.extend(sorted(directory.glob("mask*.msk")))
        msk = directory / "mask.msk"
        if msk.is_file():
            candidates.append(msk)
        candidates.extend(sorted(directory.glob("*.msk")))
        for c in candidates:
            if c.is_file():
                return c
        return None

    for directory in (base, base.parent):
        hit = first_in(directory)
        if hit is not None:
            return hit
    return None


def browse_start_dir_for_resolved_paths(paths: List[str], workdir: Path) -> Optional[str]:
    """
    Directory for QFileDialog: parent of the first existing file, or the path itself if it is an existing directory.
    """
    for raw in paths:
        seg = normalize_pathish(raw)
        if not seg:
            continue
        p = resolve_under_workdir(seg, workdir)
        if not p.exists():
            continue
        if p.is_file():
            return str(p.parent.resolve())
        if p.is_dir():
            return str(p.resolve())
    return None


def path_parts_from_positional_arg(arg: str) -> List[str]:
    s = (arg or "").strip()
    if not s:
        return []
    parts = [normalize_pathish(p.strip()) for p in s.split(",") if p.strip()]
    return parts if parts else [normalize_pathish(s)]


def session_anchor_dir_str_from_resolved_paths(paths: List[str], workdir: Path) -> Optional[str]:
    """
    Single directory string to store on a hint (e.g. ``two_d_tif_dir``): common parent if multiple
    files share it, else anchor from the first resolved path (file -> parent, dir -> self).
    """
    if not paths:
        return None
    if len(paths) > 1:
        common = common_parent_dir_if_all_files(paths, workdir)
        if common is not None:
            return str(common.resolve())
    ad = anchor_dir_from_resolved_path_list(paths, workdir)
    if ad is None:
        return None
    return str(ad.resolve())


def session_anchor_dir_from_positional_arg(arg: str, workdir: Path) -> Optional[str]:
    """Parse CLI positional path expression (comma-separated) into a single anchor directory string."""
    return session_anchor_dir_str_from_resolved_paths(path_parts_from_positional_arg(arg), workdir)


def sample_dir_for_subtract(sample_text: str, workdir: Path) -> Optional[Path]:
    seg = first_path_segment(sample_text)
    if not seg:
        return None
    p = resolve_under_workdir(seg, workdir)
    if not p.exists():
        return None
    if p.is_file():
        return p.parent
    if p.is_dir():
        return p
    return None


def find_single_buffer_dat(sample_text: str, workdir: Path) -> Optional[Path]:
    d = sample_dir_for_subtract(sample_text, workdir)
    if d is None or not d.is_dir():
        return None
    matches = sorted(x for x in d.glob("*_buffer.dat") if x.is_file())
    if len(matches) != 1:
        return None
    return matches[0]


def list_dat_files_in_dir(dir_path: Path) -> List[Path]:
    return sorted(x for x in dir_path.glob("*.dat") if x.is_file())


def profile_guess_from_subtract_output(subtract_output_dir: str, workdir: Path) -> Optional[str]:
    p = resolve_under_workdir(subtract_output_dir, workdir)
    if not p.is_dir():
        return None
    dats = list_dat_files_in_dir(p)
    if not dats:
        return None
    return ", ".join(str(x) for x in dats)


def update_session_hints_from_success(
    hints: SessionPathHints,
    *,
    workdir: Path,
    skill_name: str,
    result: Dict[str, Any],
    request: RunRequest,
) -> None:
    """
    Merge this run's paths into the global session hints. Each branch only touches the hint keys
    that skill is responsible for; other keys are left as-is for other skills to use or update.
    """
    if not result:
        return
    opts = request.options or {}
    pos = request.positional

    if skill_name == "calibrate":
        v = result.get("integrator_dir")
        if isinstance(v, str):
            s = resolve_existing_path_str(v, workdir)
            if s and Path(s).is_dir():
                hints.integrator_dir = s
        if pos and isinstance(pos[0], str):
            td = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if td:
                hints.two_d_tif_dir = td
    elif skill_name == "integrate":
        if pos and isinstance(pos[0], str):
            td = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if td:
                hints.two_d_tif_dir = td
        integrated = result.get("integrated_1d")
        first: Optional[str] = None
        if isinstance(integrated, list) and integrated:
            first = integrated[0] if isinstance(integrated[0], str) else None
        elif isinstance(integrated, str):
            first = integrated
        filled_integrate_out = False
        if first:
            parent = resolve_under_workdir(first, workdir).parent
            if parent.is_dir():
                hints.integrate_output_dir = str(parent)
                hints.one_d_profile_dir = str(parent)
                filled_integrate_out = True
        if not filled_integrate_out:
            od = opts.get("output_dir", ".")
            p = resolve_under_workdir(str(od), workdir)
            if p.is_dir():
                hints.integrate_output_dir = str(p)
                hints.one_d_profile_dir = str(p)
    elif skill_name == "integrate_proxy":
        if pos and isinstance(pos[0], str):
            td = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if td:
                hints.two_d_tif_dir = td
    elif skill_name == "plot_2d":
        if pos and isinstance(pos[0], str):
            td = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if td:
                hints.two_d_tif_dir = td
    elif skill_name == "subtract":
        if pos and isinstance(pos[0], str):
            one_d = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if one_d:
                hints.one_d_profile_dir = one_d
        filled_sub_out = False
        sub = result.get("subtracted_1d")
        if isinstance(sub, str) and sub:
            parent = resolve_under_workdir(sub, workdir).parent
            if parent.is_dir():
                hints.subtract_output_dir = str(parent)
                hints.one_d_profile_dir = str(parent)
                filled_sub_out = True
        if not filled_sub_out:
            od = opts.get("output_dir", ".")
            p = resolve_under_workdir(str(od), workdir)
            if p.is_dir():
                hints.subtract_output_dir = str(p)
                hints.one_d_profile_dir = str(p)
    elif skill_name in ANALYSIS_SKILLS_WITH_PROFILE:
        if pos and isinstance(pos[0], str):
            one_d = session_anchor_dir_from_positional_arg(pos[0], workdir)
            if one_d:
                hints.one_d_profile_dir = one_d

    ingest_shared_mask_and_config_from_request(hints, request, skill_name, workdir)


def _dir_hint_if_exists(hint: Optional[str], workdir: Path) -> Optional[str]:
    if not hint or not path_exists(hint, workdir):
        return None
    p = resolve_under_workdir(hint, workdir)
    if p.is_dir():
        return str(p)
    return None


def _file_hint_if_exists(hint: Optional[str], workdir: Path) -> Optional[str]:
    if not hint or not path_exists(hint, workdir):
        return None
    p = resolve_under_workdir(hint, workdir)
    if p.is_file():
        return str(p)
    return None


def ingest_shared_mask_and_config_from_request(
    hints: SessionPathHints, request: RunRequest, skill_name: str, workdir: Path
) -> None:
    """Update shared mask/config file hints from the executed request (last successful path wins)."""
    opts = request.options or {}
    m = opts.get("mask")
    if isinstance(m, str) and m.strip():
        s = resolve_existing_path_str(m.strip(), workdir)
        if s and Path(s).is_file():
            hints.mask_file_path = s
    c = opts.get("config_path")
    if isinstance(c, str) and c.strip():
        s = resolve_existing_path_str(c.strip(), workdir)
        if s and Path(s).is_file():
            hints.config_file_path = s
    if skill_name == "calibrate":
        pos = request.positional
        if len(pos) > 1 and isinstance(pos[1], str) and pos[1].strip():
            s = resolve_existing_path_str(pos[1].strip(), workdir)
            if s and Path(s).is_file():
                hints.config_file_path = s


def session_hint_option_mask(hints: SessionPathHints, workdir: Path) -> Optional[str]:
    return _file_hint_if_exists(hints.mask_file_path, workdir)


def session_hint_option_config_path(hints: SessionPathHints, workdir: Path) -> Optional[str]:
    return _file_hint_if_exists(hints.config_file_path, workdir)


def session_hint_for_positional_path(
    skill_name: str, param_name: str, hints: SessionPathHints, workdir: Path
) -> Optional[str]:
    """Resolve a positional parameter default from global hints (skill-specific mapping)."""
    if skill_name == "calibrate" and param_name == "calib_image":
        return _dir_hint_if_exists(hints.two_d_tif_dir, workdir)
    if skill_name == "integrate" and param_name == "images":
        return _dir_hint_if_exists(hints.two_d_tif_dir, workdir)
    if skill_name == "integrate_proxy" and param_name == "image":
        return _dir_hint_if_exists(hints.two_d_tif_dir, workdir)
    if skill_name == "plot_2d" and param_name == "image":
        return _dir_hint_if_exists(hints.two_d_tif_dir, workdir)
    if skill_name == "integrate" and param_name == "integrator_dir":
        if hints.integrator_dir and path_exists(hints.integrator_dir, workdir):
            return str(resolve_under_workdir(hints.integrator_dir, workdir))
    if skill_name == "subtract" and param_name == "sample_1d":
        return _dir_hint_if_exists(hints.one_d_profile_dir, workdir)
    if skill_name == "subtract" and param_name == "buffer_1d":
        return _file_hint_if_exists(hints.last_integrated_dat_path, workdir)
    if skill_name in ANALYSIS_SKILLS_WITH_PROFILE and param_name == "profile":
        pf = _file_hint_if_exists(hints.preferred_profile_dat_path, workdir)
        if pf:
            return pf
        od = _dir_hint_if_exists(hints.one_d_profile_dir, workdir)
        if od:
            return od
        if hints.subtract_output_dir:
            return profile_guess_from_subtract_output(hints.subtract_output_dir, workdir)
    return None


def anchor_for_calibrate_config(saved_pos: List[Any], resolved_calib_text: str, workdir: Path) -> Optional[Path]:
    ad = anchor_dir_for_path_expression(resolved_calib_text, workdir)
    if ad is not None:
        return ad
    if len(saved_pos) > 0 and isinstance(saved_pos[0], dict):
        parts = paths_from_saved_path_state(saved_pos[0])
        if parts and path_exists(parts[0], workdir):
            return anchor_dir_for_path_expression(parts[0], workdir)
    return None


def anchor_for_model_mixture_config(profile_text: str, saved_profile: Any, workdir: Path) -> Optional[Path]:
    ad = anchor_dir_for_path_expression(profile_text, workdir)
    if ad is not None:
        return ad
    if isinstance(saved_profile, dict):
        parts = paths_from_saved_path_state(saved_profile)
        if parts and path_exists(parts[0], workdir):
            return anchor_dir_for_path_expression(parts[0], workdir)
    return None
