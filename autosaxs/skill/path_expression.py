from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Set
from urllib.parse import unquote, urlparse


def _normalize_pathish(value: str) -> str:
    """
    Normalize path-like strings.

    - Accept plain paths (returned stripped).
    - Accept file URIs (e.g. file:///home/user/x.conf) and convert to /home/user/x.conf.
    """
    s = (value or "").strip()
    if s.startswith("file://"):
        p = urlparse(s)
        if p.scheme == "file":
            return unquote(p.path)
    return s


def _stable_dedup(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _looks_like_glob(s: str) -> bool:
    return any(ch in s for ch in ("*", "?", "["))


@dataclass(frozen=True)
class PathExpression:
    """
    A wrapper around a user-provided path expression string.

    Supported forms:
    - file path
    - directory path
    - glob expression (including ** with recursive=True)
    - comma-separated list (split by /,\\s*/)

    Regex expressions are intentionally not supported.
    """

    expr: str

    def unwrap(self) -> List[str]:
        raw = _normalize_pathish(self.expr)
        if not raw:
            raise FileNotFoundError("Empty path expression")

        parts = [p.strip() for p in re.split(r",\s*", raw) if p.strip()]
        if not parts:
            raise FileNotFoundError("Empty path expression")

        expanded: List[str] = []
        for part in parts:
            part = _normalize_pathish(part)
            if not part:
                continue
            part = os.path.expanduser(part)

            # Prefer direct existence checks before globbing (faster, avoids surprising
            # behavior for literal paths containing glob metacharacters).
            if os.path.isfile(part) or os.path.isdir(part):
                expanded.append(str(Path(part).resolve()))
                continue

            if _looks_like_glob(part):
                matches = glob.glob(part, recursive=True)
                for m in matches:
                    if os.path.exists(m):
                        expanded.append(str(Path(m).resolve()))
                continue

        expanded = _stable_dedup(expanded)
        if not expanded:
            raise FileNotFoundError(f"No existing paths matched: {self.expr!r}")
        return expanded


@dataclass(frozen=True)
class SingletonPathExpression(PathExpression):
    """
    A PathExpression that must resolve to exactly one existing path.
    """

    def unwrap(self) -> List[str]:
        items = super().unwrap()
        if len(items) != 1:
            raise ValueError(f"Expected exactly one existing path, got {len(items)} for: {self.expr!r}")
        return items

