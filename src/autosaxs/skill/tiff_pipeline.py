"""
YAML-driven TIFF → subtracted-1D front-end for meta-skills / ``process_directory``.

Config schema, classification, and pairing rules: see Project doc
``docs/tiff-to-report-config.md`` (agent store) and skill docstrings on
``process_directory``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.utils import _strip_sub_int_prefix, map_sample_files_to_buffer_files

from .average import average
from .calibrate import calibrate
from .common import ConfigPathExpressionArg, coerce_config_path_expression
from .config import resolve_optional_config_path
from .integrate import integrate
from .subtract import subtract

DEFAULT_CALIBRANT_GLOB = "*AgBh*.tif"
DEFAULT_BUFFER_RULES: Dict[str, str] = {"*_sample*.tif": "*_buffer*.tif"}
DEFAULT_CONFIG_BASENAME = "config.conf"
ANALYSIS_VALUES = frozenset({"mono", "poly"})

# Trailing frame index on a stem (after int_/sub_ strip): _001, _f01, _F12
_FRAME_SUFFIX_RE = re.compile(r"_(?:f)?\d+$", re.IGNORECASE)

# Top-level keys that belong to the TIFF pipeline (not leaf skill sections).
_PIPELINE_KEYS = frozenset({"calibrant", "buffer_rules", "analysis"})


@dataclass(frozen=True)
class TiffPipelineConfig:
    """Parsed TIFF→report routing keys (+ path to the YAML file for leaf merges)."""

    calibrant: str = DEFAULT_CALIBRANT_GLOB
    buffer_rules: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_BUFFER_RULES))
    analysis: Optional[str] = None
    config_path: Optional[str] = None


@dataclass(frozen=True)
class ClassifiedFrames:
    calibrant_path: str
    sample_paths: Tuple[str, ...]
    buffer_paths: Tuple[str, ...]
    ignored_paths: Tuple[str, ...]


def default_config_path_for_frames_dir(frames_dir: str) -> str:
    return os.path.join(os.path.abspath(frames_dir), DEFAULT_CONFIG_BASENAME)


def resolve_tiff_config_path(
    frames_dir: str,
    config_path: Optional[ConfigPathExpressionArg] = None,
) -> str:
    """
    Resolve the YAML config path for a frames directory.

    Explicit ``config_path`` wins; otherwise ``<frames_dir>/config.conf``.
    """
    resolved = resolve_optional_config_path(config_path)
    if resolved:
        return resolved
    candidate = default_config_path_for_frames_dir(frames_dir)
    if not os.path.isfile(candidate):
        raise FileNotFoundError(
            f"TIFF pipeline config not found: {candidate!r} "
            f"(pass --conf / config_path, or place {DEFAULT_CONFIG_BASENAME} in the frames directory)"
        )
    return candidate


def load_tiff_pipeline_config(config_path: str) -> TiffPipelineConfig:
    """
    Load and validate TIFF→report routing keys from a YAML/``.conf`` file.

    Matching rule: **fnmatch globs only** (basename). No regex.
    """
    path = str(coerce_config_path_expression(config_path).unwrap()[0])
    if not os.path.isfile(path):
        raise FileNotFoundError(f"config_path not found: {path!r}")
    with open(path, "r", encoding="utf-8") as fp:
        raw = yaml.safe_load(fp) or {}
    if not isinstance(raw, dict):
        raise TypeError(f"TIFF pipeline config must be a mapping, got {type(raw).__name__}")

    from . import SKILL_ORDER
    from .config import _SKILL_SECTION_ALIASES

    known_skill_sections = frozenset(SKILL_ORDER)
    legacy_aliases = frozenset(
        alias for aliases in _SKILL_SECTION_ALIASES.values() for alias in aliases
    )
    for key in raw:
        if key in _PIPELINE_KEYS or key in known_skill_sections or key in legacy_aliases:
            continue
        warnings.warn(
            f"TIFF pipeline config: ignoring unknown top-level key {key!r} in {path}",
            UserWarning,
            stacklevel=2,
        )

    calibrant = raw.get("calibrant", DEFAULT_CALIBRANT_GLOB)
    if calibrant is None or not str(calibrant).strip():
        calibrant = DEFAULT_CALIBRANT_GLOB
    else:
        calibrant = str(calibrant).strip()

    buffer_rules_raw = raw.get("buffer_rules", None)
    if buffer_rules_raw is None:
        buffer_rules: Dict[str, str] = dict(DEFAULT_BUFFER_RULES)
    else:
        if not isinstance(buffer_rules_raw, dict) or not buffer_rules_raw:
            raise TypeError("buffer_rules must be a non-empty mapping of sample_glob → buffer_glob")
        buffer_rules = {}
        for sample_glob, buffer_glob in buffer_rules_raw.items():
            sg = str(sample_glob).strip()
            bg = str(buffer_glob).strip()
            if not sg or not bg:
                raise ValueError("buffer_rules entries must be non-empty globs")
            buffer_rules[sg] = bg

    analysis_raw = raw.get("analysis", None)
    analysis: Optional[str] = None
    if analysis_raw is not None and str(analysis_raw).strip():
        analysis = str(analysis_raw).strip().lower()
        if analysis not in ANALYSIS_VALUES:
            raise ValueError(
                f"analysis must be one of {sorted(ANALYSIS_VALUES)}, got {analysis_raw!r}"
            )

    return TiffPipelineConfig(
        calibrant=calibrant,
        buffer_rules=buffer_rules,
        analysis=analysis,
        config_path=path,
    )


def list_tiff_files(frames_dir: str) -> List[str]:
    """Non-recursive ``*.tif`` / ``*.tiff`` under ``frames_dir``, sorted by basename."""
    root = os.path.abspath(frames_dir)
    if not os.path.isdir(root):
        raise NotADirectoryError(f"frames_dir is not a directory: {root!r}")
    out: List[str] = []
    for name in os.listdir(root):
        lower = name.lower()
        if lower.endswith(".tif") or lower.endswith(".tiff"):
            path = os.path.join(root, name)
            if os.path.isfile(path):
                out.append(path)
    out.sort(key=lambda p: os.path.basename(p).lower())
    return out


def _basename_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(os.path.basename(path), pattern)


def classify_tiffs(frames_dir: str, config: TiffPipelineConfig) -> ClassifiedFrames:
    """
    Classify TIFFs into calibrant / samples / buffers / ignored using fnmatch globs.
    """
    files = list_tiff_files(frames_dir)
    if not files:
        raise FileNotFoundError(f"No TIFF files found in {os.path.abspath(frames_dir)!r}")

    calib_hits = [p for p in files if _basename_matches(p, config.calibrant)]
    if not calib_hits:
        raise FileNotFoundError(
            f"No calibrant TIFF matched glob {config.calibrant!r} under {frames_dir!r}"
        )
    # Lexicographic by basename (reproducible; see design doc vs liveview mtime).
    calib_hits_sorted = sorted(calib_hits, key=lambda p: os.path.basename(p).lower())
    calibrant_path = calib_hits_sorted[0]

    remaining = [p for p in files if os.path.normpath(p) != os.path.normpath(calibrant_path)]
    claimed: set[str] = set()
    samples: List[str] = []
    buffers: List[str] = []

    for sample_glob, buffer_glob in config.buffer_rules.items():
        for p in remaining:
            key = os.path.normpath(p)
            if key in claimed:
                continue
            if _basename_matches(p, sample_glob):
                samples.append(p)
                claimed.add(key)
        for p in remaining:
            key = os.path.normpath(p)
            if key in claimed:
                continue
            if _basename_matches(p, buffer_glob):
                buffers.append(p)
                claimed.add(key)

    ignored = tuple(p for p in remaining if os.path.normpath(p) not in claimed)
    if not samples:
        raise FileNotFoundError(
            f"No sample TIFFs matched buffer_rules under {frames_dir!r} "
            f"(rules={dict(config.buffer_rules)!r})"
        )
    if not buffers:
        raise FileNotFoundError(
            f"No buffer TIFFs matched buffer_rules under {frames_dir!r} "
            f"(rules={dict(config.buffer_rules)!r})"
        )

    return ClassifiedFrames(
        calibrant_path=calibrant_path,
        sample_paths=tuple(samples),
        buffer_paths=tuple(buffers),
        ignored_paths=ignored,
    )


def pair_sample_buffer_1d(
    sample_paths: Sequence[str],
    buffer_paths: Sequence[str],
) -> List[Tuple[str, str]]:
    """
    Pair sample/buffer 1D curves.

    Primary: ``map_sample_files_to_buffer_files`` (``_sample`` / ``_buffer`` convention).
    Fallback: if unpaired remain and there is exactly one buffer, share it with all
    unpaired samples (poly Pt_NPs / real-data light parity).
    """
    samples = list(sample_paths)
    buffers = list(buffer_paths)
    if not samples:
        raise ValueError("pair_sample_buffer_1d: no sample paths")
    if not buffers:
        raise ValueError("pair_sample_buffer_1d: no buffer paths")

    alignment = map_sample_files_to_buffer_files(samples, buffers)
    if alignment["overlapped"]:
        overlap_str = "\n".join(", ".join(p) for p in alignment["overlapped"])
        raise RuntimeError(
            "Buffer-sample alignment ambiguous (multiple buffers matched a sample).\n"
            f"Overlapped:\n{overlap_str}"
        )

    pairs: List[Tuple[str, str]] = list(alignment["aligned_pairs"])
    not_paired: List[str] = list(alignment["not_paired"])

    if not_paired and len(buffers) == 1:
        shared = buffers[0]
        for s in not_paired:
            pairs.append((s, shared))
        not_paired = []

    if not_paired:
        raise RuntimeError(
            "Buffer-sample alignment failed; unpaired samples:\n"
            + "\n".join(not_paired)
        )
    return pairs


def _logical_frame_stem(path: str) -> str:
    """Stem for multi-frame grouping: strip int_/sub_ and trailing _NNN / _fNNN."""
    stem = _strip_sub_int_prefix(Path(path).stem)
    stripped = _FRAME_SUFFIX_RE.sub("", stem)
    return stripped if stripped else stem


def _is_sample_1d(path: str, sample_tif_basenames: set[str]) -> bool:
    """Heuristic: integrated curve came from a sample TIFF (basename without ext)."""
    stem = _strip_sub_int_prefix(Path(path).stem)
    # int_<tiff_stem>.dat → compare tiff stem
    for base in sample_tif_basenames:
        if stem == base or stem.startswith(base) or base in stem:
            return True
    return "_sample" in stem.lower()


def _is_buffer_1d(path: str, buffer_tif_basenames: set[str]) -> bool:
    stem = _strip_sub_int_prefix(Path(path).stem)
    for base in buffer_tif_basenames:
        if stem == base or stem.startswith(base) or base in stem:
            return True
    return "_buffer" in stem.lower()


def _maybe_average_groups(
    paths: List[str],
    *,
    output_dir: str,
    use_cache: bool,
    bus: Optional[EventBus],
) -> List[str]:
    """Average when ≥2 curves share a logical stem; otherwise pass through."""
    groups: Dict[str, List[str]] = {}
    for p in paths:
        groups.setdefault(_logical_frame_stem(p), []).append(p)

    out: List[str] = []
    for stem, group in sorted(groups.items()):
        group_sorted = sorted(group)
        if len(group_sorted) < 2:
            out.extend(group_sorted)
            continue
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {"text": f"tiff_pipeline: average {len(group_sorted)} frames for stem {stem!r}…"},
            )
        avg_out = average(
            group_sorted,
            output_dir,
            use_cache=use_cache,
        )
        averaged_1d = avg_out.get("averaged_1d")
        if isinstance(averaged_1d, list):
            out.extend(str(x) for x in averaged_1d if x)
        elif averaged_1d:
            out.append(str(averaged_1d))
        else:
            out.extend(group_sorted)
    return out


def run_tiff_to_subtracted(
    frames_dir: str,
    output_dir: str,
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    use_cache: bool = False,
    event_bus: Optional[EventBus] = None,
) -> Dict[str, Any]:
    """
    Calibrate → integrate → average (as needed) → subtract.

    Returns a dict with ``subtracted_paths``, layout dirs, and classification metadata.
    """
    bus = event_bus or EventBus()
    if event_bus is None:
        bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    conf_path = resolve_tiff_config_path(frames_dir, config_path)
    cfg = load_tiff_pipeline_config(conf_path)
    classified = classify_tiffs(frames_dir, cfg)

    if classified.ignored_paths and bus:
        names = ", ".join(os.path.basename(p) for p in classified.ignored_paths)
        bus.publish(
            EventType.MESSAGE,
            {"text": f"tiff_pipeline: ignoring unmatched TIFFs: {names}"},
        )

    os.makedirs(output_dir, exist_ok=True)
    averaged_dir = os.path.join(output_dir, "averaged")
    subtracted_dir = os.path.join(output_dir, "subtracted")
    os.makedirs(averaged_dir, exist_ok=True)
    os.makedirs(subtracted_dir, exist_ok=True)

    if bus:
        bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"tiff_pipeline: calibrate {os.path.basename(classified.calibrant_path)}…"
                )
            },
        )
    out_cal = calibrate(
        classified.calibrant_path,
        output_dir,
        config_path=conf_path,
        use_cache=use_cache,
    )
    integrator_dir = str(out_cal["integrator_dir"])

    all_frames = list(classified.buffer_paths) + list(classified.sample_paths)
    if bus:
        bus.publish(
            EventType.MESSAGE,
            {"text": f"tiff_pipeline: integrate {len(all_frames)} TIFF(s)…"},
        )
    out_int = integrate(
        all_frames,
        integrator_dir,
        averaged_dir,
        config_path=conf_path,
        use_cache=use_cache,
    )
    integrated = out_int.get("integrated_1d") or []
    if isinstance(integrated, str):
        integrated_paths = [integrated]
    else:
        integrated_paths = [str(p) for p in integrated]

    sample_tif_bases = {Path(p).stem for p in classified.sample_paths}
    buffer_tif_bases = {Path(p).stem for p in classified.buffer_paths}

    sample_1d = [p for p in integrated_paths if _is_sample_1d(p, sample_tif_bases)]
    buffer_1d = [p for p in integrated_paths if _is_buffer_1d(p, buffer_tif_bases)]
    # Fallback: anything not classified as buffer → sample (and vice versa)
    if not sample_1d or not buffer_1d:
        sample_1d = []
        buffer_1d = []
        for p in integrated_paths:
            stem = _strip_sub_int_prefix(Path(p).stem).lower()
            if "_buffer" in stem:
                buffer_1d.append(p)
            else:
                sample_1d.append(p)

    sample_1d = _maybe_average_groups(
        sample_1d, output_dir=averaged_dir, use_cache=use_cache, bus=bus
    )
    buffer_1d = _maybe_average_groups(
        buffer_1d, output_dir=averaged_dir, use_cache=use_cache, bus=bus
    )

    pairs = pair_sample_buffer_1d(sample_1d, buffer_1d)
    subtracted_paths: List[str] = []
    for sample_path, buffer_path in pairs:
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        "tiff_pipeline: subtract "
                        f"{os.path.basename(sample_path)} − {os.path.basename(buffer_path)}…"
                    )
                },
            )
        out_sub = subtract(
            sample_path,
            buffer_path,
            subtracted_dir,
            config_path=conf_path,
            use_cache=use_cache,
        )
        sub = out_sub.get("subtracted_1d")
        if isinstance(sub, list):
            subtracted_paths.extend(str(x) for x in sub if x)
        elif sub:
            subtracted_paths.append(str(sub))

    if not subtracted_paths:
        raise RuntimeError("tiff_pipeline: subtract produced no curves")

    return {
        "config_path": conf_path,
        "analysis": cfg.analysis,
        "frames_dir": os.path.abspath(frames_dir),
        "pipeline_dir": os.path.abspath(output_dir),
        "integrator_dir": integrator_dir,
        "averaged_dir": averaged_dir,
        "subtracted_dir": subtracted_dir,
        "subtracted_paths": subtracted_paths,
        "calibrant_path": classified.calibrant_path,
        "sample_tiff_paths": list(classified.sample_paths),
        "buffer_tiff_paths": list(classified.buffer_paths),
        "ignored_tiff_paths": list(classified.ignored_paths),
        "calibrate": out_cal,
    }
