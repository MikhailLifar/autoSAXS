from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Dict


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    return str(value)


def parse_key_value_stdout(text: str) -> Dict[str, Any]:
    """
    Parse autosaxs CLI output (lines `key=value`) into a dict.
    Values may be strings or Python-literals (lists/dicts); we parse via literal_eval when safe.
    """
    out: Dict[str, Any] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        parsed: Any = v
        if (v.startswith("[") and v.endswith("]")) or (v.startswith("{") and v.endswith("}")):
            try:
                parsed = ast.literal_eval(v)
            except Exception:
                parsed = v
        out[k] = _json_safe(parsed)
    return out

