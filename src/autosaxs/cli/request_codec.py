"""Shared GUI ↔ CLI request/result contract (argv + framed machine JSON).

GUI builds argv via :func:`options_to_argv` / :class:`~guisaxs_skills.core.models.RunRequest`.
CLI argparse accepts the same ``--cache``/``--no-cache`` and ``--flag``/``--no-flag`` forms.
Skill returns are printed as a framed JSON envelope; GUI parses only that frame.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

RESULT_FRAME_BEGIN = "<<<AUTOSAXS_RESULT_JSON>>>"
RESULT_FRAME_END = "<<<END_AUTOSAXS_RESULT_JSON>>>"


def _to_kebab(name: str) -> str:
    return str(name).replace("_", "-")


def options_to_argv(options: Mapping[str, Any]) -> List[str]:
    """
    Serialize skill keyword options to CLI argv tokens (no skill name / positionals).

    Contract:
    - ``use_cache`` always emits ``--cache`` or ``--no-cache`` (never omitted).
    - Other bools always emit ``--flag`` or ``--no-flag`` (BooleanOptionalAction).
    - Empty strings are omitted (same as unset optional form fields).
    - ``None`` values are omitted.
    """
    argv: List[str] = []
    for key, value in options.items():
        if key == "use_cache":
            argv.append("--cache" if bool(value) else "--no-cache")
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            # Omit optional args saved as "" in YAML / form state (same as unset).
            continue
        if key == "output_dir":
            argv.extend(["--output-dir", str(value)])
            continue

        flag = f"--{_to_kebab(key)}"
        if isinstance(value, bool):
            argv.append(flag if value else f"--no-{_to_kebab(key)}")
            continue
        if isinstance(value, (list, tuple)):
            argv.append(flag)
            argv.extend([str(x) for x in value])
            continue
        argv.extend([flag, str(value)])
    return argv


def json_safe(value: Any) -> Any:
    """Convert skill return values into JSON-serializable form."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    return str(value)


def science_ok_for_result(skill_name: str, result: Any) -> bool:
    """
    Scientific success distinct from process exit.

    GNOM/DATGNOM skills (and any result carrying ``atsas_fit_ok`` / gnom failure
    markers) use :func:`autosaxs.skill.gnom_fit_common.is_atsas_fit_ok`.
    Other skills: scientific ok when a dict result was produced.
    """
    if not isinstance(result, dict):
        return False
    name = str(skill_name or "").strip()
    if name in ("fit_distances", "fit_sizes") or (
        "atsas_fit_ok" in result or "gnom_failed" in result or "best_gnom_out_path" in result
    ):
        from autosaxs.skill.gnom_fit_common import is_atsas_fit_ok

        return bool(is_atsas_fit_ok(result))
    return True


def build_result_envelope(
    *,
    skill_name: str,
    result: Any,
    process_ok: bool = True,
) -> Dict[str, Any]:
    """Machine-readable envelope printed inside the result frame."""
    if isinstance(result, dict):
        payload: Any = json_safe(result)
        science_ok = bool(process_ok) and science_ok_for_result(skill_name, result)
    else:
        payload = {"value": json_safe(result)}
        science_ok = bool(process_ok)
    return {
        "process_ok": bool(process_ok),
        "science_ok": bool(science_ok),
        "skill_name": str(skill_name or ""),
        "result": payload,
    }


def format_result_frame(envelope: Mapping[str, Any]) -> str:
    """Return framed JSON text (including trailing newline)."""
    body = json.dumps(json_safe(dict(envelope)), ensure_ascii=False, sort_keys=True)
    return f"{RESULT_FRAME_BEGIN}\n{body}\n{RESULT_FRAME_END}\n"


def parse_result_frame(text: str) -> Optional[Dict[str, Any]]:
    """
    Extract the framed JSON envelope from CLI stdout.

    Free-text human logs outside the frame are ignored. Returns ``None`` when no
    complete frame is present.
    """
    raw = text or ""
    begin = raw.find(RESULT_FRAME_BEGIN)
    if begin < 0:
        return None
    start = begin + len(RESULT_FRAME_BEGIN)
    end = raw.find(RESULT_FRAME_END, start)
    if end < 0:
        return None
    blob = raw[start:end].strip()
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def envelope_to_outcome_fields(envelope: Optional[Mapping[str, Any]]) -> Tuple[Dict[str, Any], Optional[bool], Optional[bool]]:
    """
    Map a parsed envelope to ``(result_dict, process_ok, science_ok)``.

    Missing envelope → empty result and unknown oks (``None``).
    """
    if not envelope:
        return {}, None, None
    result = envelope.get("result")
    if isinstance(result, dict):
        result_dict: Dict[str, Any] = dict(result)
    elif result is None:
        result_dict = {}
    else:
        result_dict = {"value": result}
    process_ok = envelope.get("process_ok")
    science_ok = envelope.get("science_ok")
    process_ok_b = bool(process_ok) if isinstance(process_ok, bool) else None
    science_ok_b = bool(science_ok) if isinstance(science_ok, bool) else None
    return result_dict, process_ok_b, science_ok_b
