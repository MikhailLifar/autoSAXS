# Wrappers and common functionality for skills.
# See docs/skills_paradigm.md.

from __future__ import annotations

import functools
import hashlib
import os
import re
import subprocess
import warnings
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union, overload

import yaml

from ..core.event_bus import EventBus, EventType

CACHE_FILENAME = ".cache"

RECOMMENDED_ATSAS_VERSION = "3.2.1"
ATSAS_DOWNLOAD_URL = "https://www.embl-hamburg.de/biosaxs/download.html"


def probe_atsas() -> Tuple[Optional[str], Optional[str]]:
    """
    Probe ATSAS via ``dammif -v``.

    Returns ``(version, error)``:
    - ``(version, None)`` when a version string was parsed
    - ``(None, None)`` when dammif is present but version could not be parsed
    - ``(None, message)`` when dammif is missing or the probe failed
    """
    try:
        result = subprocess.run(
            ["dammif", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (result.stdout or "") + (result.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, (
            "Apparently ATSAS package, on which some autosaxs skills rely, is not installed "
            f"(dammif not runnable: {exc}). Install ATSAS here: {ATSAS_DOWNLOAD_URL}"
        )
    match = re.search(r"ATSAS\s+(\d+\.\d+\.\d+)", out)
    if not match:
        return None, None
    return match.group(1), None


def warn_atsas_on_import() -> None:
    """Soft import-time check: warn on missing/mismatched ATSAS; never raise."""
    version, err = probe_atsas()
    if err:
        warnings.warn(err, RuntimeWarning, stacklevel=2)
        return
    if version is None:
        warnings.warn(
            "ATSAS appears to be installed (dammif found), but its version could not be parsed from "
            "`dammif -v` output. Some autosaxs functions may not work as expected.",
            RuntimeWarning,
            stacklevel=2,
        )
        print("ATSAS installed - autosaxs is ready for use!")
        return
    if version != RECOMMENDED_ATSAS_VERSION:
        warnings.warn(
            f"ATSAS version mismatch: autosaxs was developed/tested with ATSAS "
            f"{RECOMMENDED_ATSAS_VERSION}, but detected ATSAS {version}. "
            "Some autosaxs functions may not work due to the mismatch.",
            RuntimeWarning,
            stacklevel=2,
        )
    print(f"ATSAS {version} installed - autosaxs is ready for use!")


def ensure_atsas_installed() -> str:
    """Raise if ATSAS is not available; return parsed version or ``\"unknown\"``."""
    version, err = probe_atsas()
    if err:
        raise RuntimeError(err)
    return version or "unknown"


def require_atsas(skill_impl: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: raise immediately if ATSAS is not installed before running the skill body."""

    @functools.wraps(skill_impl)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        ensure_atsas_installed()
        return skill_impl(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Cache and batch wrappers
# ---------------------------------------------------------------------------


def _file_content_hash(path: str) -> bytes:
    """Hash file/directory contents for caching. Path can be a file or directory (then hash tree).
    Uses canonical path (realpath) so the same logical path yields the same hash regardless of cwd/symlinks."""
    path = os.path.abspath(path)
    if os.path.exists(path):
        path = os.path.realpath(path)
    if not os.path.exists(path):
        raise RuntimeError()
    h = hashlib.sha256()
    h.update(path.encode())
    if os.path.isfile(path):
        with open(path, "rb") as f:
            h.update(f.read())
    else:
        for root, _dirs, files in sorted(os.walk(path)):
            for f in sorted(files):
                p = os.path.join(root, f)
                with open(p, "rb") as fp:
                    h.update(fp.read())
    return h.digest()


def _hashable_repr(obj: Any) -> str:
    """Convert object to a deterministic string for hashing. Normalizes numeric types and dicts."""
    if obj is None:
        return "None"
    if isinstance(obj, (bool, int, float, str)):
        return repr(obj)
    if isinstance(obj, dict):
        return repr(sorted((k, _hashable_repr(v)) for k, v in obj.items()))
    if isinstance(obj, (list, tuple)):
        return repr(tuple(_hashable_repr(x) for x in obj))
    # numpy and other types: try to get a stable scalar
    try:
        if hasattr(obj, "item"):
            return repr(obj.item())
        if hasattr(obj, "tolist"):
            return _hashable_repr(obj.tolist())
    except (ValueError, TypeError):
        pass
    return repr(obj)


def compute_input_hash(
    input_paths: Dict[str, Union[str, List[str]]],
    path_keys: List[str],
    config: Optional[Dict] = None,
    kwargs_for_hash: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Compute a deterministic hash from selected input paths (file/dir contents), config, and kwargs.
    Used for per-sample cache key. path_keys: which keys of input_paths to include (values can be path or list of paths).
    Config and kwargs_for_hash are normalized via _hashable_repr so that numpy types etc. yield stable hashes.
    """
    kwargs_for_hash = kwargs_for_hash or {}
    h = hashlib.sha256()
    all_paths: List[str] = []
    for k in sorted(path_keys):
        if k not in input_paths:
            continue
        v = input_paths[k]
        paths = [v] if isinstance(v, str) else v
        for p in paths:
            if p and isinstance(p, str) and os.path.exists(p):
                all_paths.append(os.path.realpath(p))
    for p in sorted(all_paths):
        h.update(_file_content_hash(p))
    if config is not None:
        h.update(_hashable_repr(config).encode())
    h.update(_hashable_repr(dict(kwargs_for_hash)).encode())
    return h.hexdigest()


def read_cache(output_dir: str) -> Optional[Dict[str, Any]]:
    """
    Read .cache from output_dir. Returns dict with "records" (list of dicts),
    each record: {"hash", "finish_date", "output_paths"}. Returns None if missing or empty.
    """
    cache_path = os.path.join(output_dir, CACHE_FILENAME)
    if not os.path.isfile(cache_path):
        return None
    with open(cache_path, "r") as f:
        data = yaml.safe_load(f)
    if not data:
        return None
    if "records" in data and isinstance(data["records"], list):
        return data
    return None


def write_cache(output_dir: str, records: List[Dict[str, Any]]) -> None:
    """Write .cache (YAML) with a list of records; each record has hash, finish_date, output_paths."""
    os.makedirs(output_dir, exist_ok=True)
    cache_path = os.path.join(output_dir, CACHE_FILENAME)
    with open(cache_path, "w") as f:
        yaml.dump({"records": records}, f, default_flow_style=False)


# Allow output file mtime to be up to this many seconds after finish_date (clock/filesystem skew).
INTEGRITY_MTIME_TOLERANCE_SEC = 2.0


def check_output_integrity(
    paths: List[str],
    finish_date_iso: str,
    tolerance_sec: float = INTEGRITY_MTIME_TOLERANCE_SEC,
) -> bool:
    """
    True if all paths exist and their mtime is not later than finish_date + tolerance.
    Paths can be files or directories; both have mtime. finish_date_iso is ISO format string.
    tolerance_sec: allow mtime up to this many seconds after finish_date (avoids spurious
    failures from filesystem timestamp granularity or minor clock skew).
    """
    try:
        finish_ts = datetime.fromisoformat(finish_date_iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return False
    cutoff = finish_ts + tolerance_sec
    for p in paths:
        if not p or not os.path.exists(p):
            return False
        if os.path.getmtime(p) > cutoff:
            return False
    return True


def _flatten_output_paths(out: Dict[str, Union[str, List[str]]]) -> List[str]:
    """Collect all path strings from output dict (values can be path or list of paths)."""
    flat = []
    for v in out.values():
        if isinstance(v, str):
            flat.append(v)
        elif isinstance(v, list):
            flat.extend(x for x in v if isinstance(x, str))
    return flat


def _output_paths_to_absolute(out: Dict[str, Union[str, List[str]]]) -> Dict[str, Union[str, List[str]]]:
    """Return a copy of the output dict with every path string replaced by its absolute form.
    Ensures cache records store absolute paths so integrity check works on re-run from any cwd."""
    result = {}
    for k, v in out.items():
        if isinstance(v, str):
            result[k] = os.path.abspath(v) if v else v
        elif isinstance(v, list):
            result[k] = [os.path.abspath(x) if isinstance(x, str) and x else x for x in v]
        else:
            result[k] = v
    return result


def run_with_cache(
    path_keys_for_hash: List[str],
    kwargs_for_hash: Optional[Dict[str, Any]] = None,
    kwargs_for_hash_keys: Optional[List[str]] = None,
    include_config_in_hash: bool = True,
    warn_if_no_cache: bool = False,
) -> Callable[[Callable[..., Dict[str, Union[str, List[str]]]]], Callable[..., Dict[str, Union[str, List[str]]]]]:
    """
    Decorator for skills: adds cache logic. Cache is a list of records (hash, finish_date, output_paths).
    Look up by current input hash: (1) No record -> run, append record. (2) Record found but
    output integrity fails -> remove record, run, append. (3) Record found and integrity ok -> return cached.
    kwargs_for_hash: static dict included in hash. kwargs_for_hash_keys: keys taken from call kwargs into hash.
    include_config_in_hash: if False, only path contents (and kwargs) are hashed; in-memory config is ignored
    (use when config is derived from path_keys and hashing it would be redundant or unstable).
    Usage: @run_with_cache(path_keys_for_hash=[...], include_config_in_hash=False) def skill(...): ...
    """

    def decorator(
        skill_impl: Callable[..., Dict[str, Union[str, List[str]]]],
    ) -> Callable[..., Dict[str, Union[str, List[str]]]]:
        @functools.wraps(skill_impl)
        def wrapper(
            input_paths: Dict[str, Union[str, List[str]]],
            output_dir: str,
            config: Optional[Dict] = None,
            event_bus: Optional[EventBus] = None,
            use_cache: bool = False,
            **kwargs: Any,
        ) -> Dict[str, Union[str, List[str]]]:
            # When caching is disabled, do not pay the cost of hashing inputs (may read large files)
            # and do not read/write any cache state.
            if not use_cache:
                if warn_if_no_cache:
                    msg = (
                        f"{skill_impl.__name__}: running without cache; this may take a long time. "
                        "Re-run with caching enabled if you plan to iterate."
                    )
                    if event_bus:
                        event_bus.publish(EventType.MESSAGE, {"text": f"WARNING: {msg}"})
                    else:
                        print(f"[cache] WARNING: {msg}")
                return skill_impl(
                    input_paths,
                    output_dir,
                    config=config,
                    event_bus=event_bus,
                    use_cache=False,
                    **kwargs,
                )

            kwh = dict(kwargs_for_hash or {})
            for k in kwargs_for_hash_keys or []:
                kwh[k] = kwargs.get(k)
            config_for_hash = config if include_config_in_hash else None
            current_hash = compute_input_hash(input_paths, path_keys_for_hash, config_for_hash, kwh)

            records: List[Dict[str, Any]] = []
            cache = read_cache(output_dir)
            if cache and "records" in cache:
                records = list(cache["records"])

            def find_record_by_hash() -> Optional[int]:
                for idx, rec in enumerate(records):
                    if rec.get("hash") == current_hash:
                        return idx
                return None

            _debug = os.environ.get("AUTOSAXS_CACHE_DEBUG", "").strip().lower() in ("1", "true", "yes")
            idx = find_record_by_hash()
            if _debug:
                print(
                    f"[cache] hash={current_hash[:16]}... records={len(records)} found_idx={idx} output_dir={output_dir!r}"
                )
            if idx is not None:
                rec = records[idx]
                paths_to_check = _flatten_output_paths(rec.get("output_paths") or {})
                finish = rec.get("finish_date") or ""
                ok = bool(paths_to_check and finish and check_output_integrity(paths_to_check, finish))
                if _debug:
                    print(f"[cache] integrity={ok} paths={paths_to_check!r} finish={finish}")
                if ok:
                    if event_bus:
                        event_bus.publish(
                            EventType.MESSAGE,
                            {"text": f"{skill_impl.__name__}: cache hit, reusing previous results."},
                        )
                    out_cached = dict(rec["output_paths"])
                    out_cached["from_cache"] = True
                    return out_cached
                records.pop(idx)
            elif _debug and idx is None:
                print("[cache] miss (no matching record), running skill")
            if event_bus:
                event_bus.publish(EventType.MESSAGE, {"text": f"{skill_impl.__name__}: cache miss, running skill."})

            out = skill_impl(input_paths, output_dir, config=config, event_bus=event_bus, use_cache=False, **kwargs)
            # Store absolute paths so integrity check works on re-run from any cwd
            out_stored = _output_paths_to_absolute(out)
            records.append(
                {
                    "hash": current_hash,
                    "finish_date": datetime.now(timezone.utc).isoformat(),
                    "output_paths": out_stored,
                }
            )
            write_cache(output_dir, records)
            return out

        return wrapper

    return decorator


def _strip_sub_int_prefix(stem: str) -> str:
    """Strip leading 'sub_' or 'int_' from stem so output names are consistent (e.g. ihs27_sample not int_ihs27_sample)."""
    while stem.startswith("sub_") or stem.startswith("int_"):
        if stem.startswith("sub_"):
            stem = stem[4:]
        elif stem.startswith("int_"):
            stem = stem[4:]
    return stem


def _stem_from_input_paths(inp: Dict[str, Union[str, List[str]]]) -> Optional[str]:
    """Get file stem from the first path found in input_paths dict. E.g. /foo/bar/baz.dat -> baz."""
    for v in inp.values():
        if isinstance(v, str) and v:
            return os.path.splitext(os.path.basename(v))[0]
        if isinstance(v, list):
            for p in v:
                if isinstance(p, str) and p:
                    return os.path.splitext(os.path.basename(p))[0]
    return None


def _stem_from_keys(
    inp: Dict[str, Union[str, List[str]]],
    keys: Optional[Union[str, List[str]]],
) -> Optional[str]:
    """Get file stem from the first path found under the given key(s). keys can be a single key or list of keys (tried in order)."""
    if keys is None:
        return _stem_from_input_paths(inp)
    key_list: List[str] = [keys] if isinstance(keys, str) else list(keys)
    for k in key_list:
        if k not in inp:
            continue
        v = inp[k]
        path = (v[0] if v and isinstance(v, list) else v) if isinstance(v, list) else v
        if isinstance(path, str) and path:
            return os.path.splitext(os.path.basename(path))[0]
    return None


@overload
def apply_batch(
    skill_fn: Callable[..., Dict[str, Any]],
    *,
    single_output_dir: bool = False,
    stem_from_keys: Optional[Union[str, List[str]]] = None,
    per_sample_subdir: Literal["always", "never"] = "never",
) -> Callable[..., Dict[str, Any]]: ...


@overload
def apply_batch(
    skill_fn: None = None,
    *,
    single_output_dir: bool = False,
    stem_from_keys: Optional[Union[str, List[str]]] = None,
    per_sample_subdir: Literal["always", "never"] = "never",
) -> Callable[[Callable[..., Dict[str, Any]]], Callable[..., Dict[str, Any]]]: ...


def apply_batch(
    skill_fn: Optional[Callable[..., Dict[str, Any]]] = None,
    *,
    single_output_dir: bool = False,
    stem_from_keys: Optional[Union[str, List[str]]] = None,
    per_sample_subdir: Literal["always", "never"] = "never",
) -> Union[Callable[..., Dict[str, Any]], Callable[[Callable[..., Dict[str, Any]]], Callable[..., Dict[str, Any]]]]:
    """
    Decorator for skills: returns a batch function that applies the skill to each input.
    Usage: batch_plot = apply_batch(plot) or batch_plot = apply_batch(stem_from_keys="profile")(plot).
    The returned function has signature (input_paths, output_dir, ...). input_paths can be a single dict or a list of dicts.
    """

    def decorator(fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
        @functools.wraps(fn)
        def batch_fn(
            input_paths: Union[Dict[str, Union[str, List[str]]], List[Dict[str, Union[str, List[str]]]]],
            output_dir: str,
            config: Optional[Dict] = None,
            event_bus: Optional[EventBus] = None,
            use_cache: bool = False,
            single_output_dir_override: bool = single_output_dir,
            stem_from_keys_override: Optional[Union[str, List[str]]] = stem_from_keys,
            per_sample_subdir_override: Literal["always", "never"] = per_sample_subdir,
            **kwargs: Any,
        ) -> Dict[str, Any]:
            list_of_input_paths = input_paths if isinstance(input_paths, list) else [input_paths]
            single_call = isinstance(input_paths, dict)

            # Decide whether to create per-sample stem-named subdirectories under output_dir.
            #
            # per_sample_subdir policy:
            # - "always": always create a per-sample subdir (even for single_call)
            # - "never": never create a per-sample subdir (even for batch list input)
            if per_sample_subdir_override == "always":
                use_single_out = False
            else:
                use_single_out = True

            # A hard override: caller wants a single shared output dir no matter what.
            if single_output_dir_override:
                use_single_out = True

            merged: Dict[str, Any] = {}
            for i, inp in enumerate(list_of_input_paths):
                if use_single_out:
                    out_dir = output_dir
                else:
                    stem = _stem_from_keys(inp, stem_from_keys_override)
                    # Per-sample subdir: use stripped stem (no sub_/int_ prefix) so report and paths are consistent
                    subdir = _strip_sub_int_prefix(stem) if stem else f"sample_{i}"
                    out_dir = os.path.join(output_dir, subdir)
                out = fn(
                    inp,
                    out_dir,
                    config=config,
                    event_bus=event_bus,
                    use_cache=use_cache,
                    **kwargs,
                )
                for k, v in out.items():
                    if k not in merged:
                        merged[k] = [] if isinstance(v, list) else []
                    if isinstance(v, list):
                        merged[k].extend(v)
                    else:
                        merged[k].append(v)
            result: Dict[str, Union[str, List[str]]] = {}
            for k, v in merged.items():
                if isinstance(v, list) and v and not isinstance(v[0], list):
                    result[k] = v
                elif isinstance(v, list) and v:
                    result[k] = v[0] if len(v) == 1 else v
                else:
                    result[k] = v
            if single_call and result:
                # Return single-sample shape: scalar paths as str, not list of one
                result = {
                    k: (v[0] if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str) else v)
                    for k, v in result.items()
                }
            return result

        return batch_fn

    if skill_fn is not None:
        return decorator(skill_fn)
    return decorator

