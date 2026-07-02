from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, get_args, get_origin, get_type_hints

from ..core.models import SkillMeta, SkillParam
from autosaxs.core.path_expression import (
    ConfigPathExpression,
    DatPathExpression,
    PathExpression,
    SingletonDatPathExpression,
    SingletonMaskPathExpression,
    SingletonPathExpression,
    SingletonTiffPathExpression,
    TiffPathExpression,
)


def _public_skill_functions() -> Dict[str, Callable]:
    from autosaxs import skill as skill_mod

    return dict(skill_mod.list_skills(include_reports=True))


def _summary(doc: str) -> str:
    doc = (doc or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def _skill_order_from_source(*, module_file: Path) -> List[str]:
    """
    Derive public skill function ordering from autosaxs/skill.py source order.

    We do this because inspect.getmembers returns functions sorted by name, but the GUI
    should mirror the curated ordering in the source file.
    """
    try:
        text = module_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    out: List[str] = []
    # Top-level defs only (no indent) to avoid grabbing nested helpers.
    pat = re.compile(r"^def\s+(?P<name>[A-Za-z_]\w*)\s*\(", re.MULTILINE)
    for m in pat.finditer(text):
        name = m.group("name")
        if name.startswith("_"):
            continue
        # Exclude report skills from the GUI for now.
        if name in ("report_individual", "report_summary") or name.startswith("report_"):
            continue
        out.append(name)
    return out


def _is_union(ann: Any) -> bool:
    origin = get_origin(ann)
    return origin is getattr(__import__("typing"), "Union", None)


def _contains_type(ann: Any, needle: Any) -> bool:
    """
    True if ann is needle, or if ann is a Union/Optional containing needle.
    """
    if ann is needle:
        return True
    if isinstance(ann, type) and isinstance(needle, type):
        try:
            if issubclass(ann, needle):
                return True
        except Exception:
            pass
    if _is_union(ann):
        return any(_contains_type(a, needle) for a in get_args(ann))
    origin = get_origin(ann)
    if origin is not None:
        return any(_contains_type(a, needle) for a in get_args(ann))
    return False


def _annotation_label(fn: Callable, param: inspect.Parameter, type_hints: Dict[str, Any]) -> Optional[str]:
    """
    Normalize annotations for the GUI.

    The UI uses substring checks ("PathExpression"/"SingletonPathExpression") to decide
    whether to render a path picker and whether it allows multiple files.

    Since autosaxs.skill uses postponed annotations and sometimes type aliases/unions,
    we resolve type hints and then map them back to stable labels.
    """
    ann = type_hints.get(param.name, param.annotation)
    if ann is inspect._empty:
        return None

    if _contains_type(ann, ConfigPathExpression):
        return "ConfigPathExpression"
    if _contains_type(ann, SingletonMaskPathExpression):
        return "SingletonMaskPathExpression"
    if _contains_type(ann, TiffPathExpression):
        return "TiffPathExpression"
    if _contains_type(ann, SingletonTiffPathExpression):
        return "SingletonTiffPathExpression"
    if _contains_type(ann, DatPathExpression):
        return "DatPathExpression"
    if _contains_type(ann, SingletonDatPathExpression):
        return "SingletonDatPathExpression"
    if _contains_type(ann, SingletonPathExpression):
        return "SingletonPathExpression"
    if _contains_type(ann, PathExpression):
        return "PathExpression"

    # Fall back to a readable string for non-path types.
    try:
        return getattr(ann, "__name__", str(ann))
    except Exception:
        return str(ann)


def discover_skills() -> List[SkillMeta]:
    metas: List[SkillMeta] = []
    public = _public_skill_functions()
    # Preferred ordering: a curated order list if available, otherwise autosaxs/skill.py source order.
    order: List[str] = []
    try:
        from autosaxs import skill as skill_mod
        if hasattr(skill_mod, "SKILL_ORDER"):
            order = list(getattr(skill_mod, "SKILL_ORDER") or [])
        if not order:
            order = _skill_order_from_source(module_file=Path(skill_mod.__file__))
    except Exception:
        order = []
    order_index = {name: i for i, name in enumerate(order)}

    def _sort_key(item: tuple[str, Callable]) -> tuple[int, int, str]:
        name, _fn = item
        # (0, idx, name): ordered-by-source; (1, 0, name): unknown -> alphabetical after.
        if name in order_index:
            return (0, order_index[name], name)
        return (1, 0, name)

    for name, fn in sorted(public.items(), key=_sort_key):
        # Exclude report skills from the GUI for now.
        if name in ("report_individual", "report_summary") or name.startswith("report_"):
            continue
        doc = inspect.getdoc(fn) or ""
        sig = inspect.signature(fn)
        try:
            type_hints = get_type_hints(fn)
        except Exception:
            type_hints = {}

        positional: List[SkillParam] = []
        options: List[SkillParam] = []

        for param in sig.parameters.values():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            ann = _annotation_label(fn, param, type_hints)

            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                if param.default is inspect._empty:
                    options.append(
                        SkillParam(
                            name=param.name,
                            kind="required_kwonly",
                            default=None,
                            annotation=ann,
                        )
                    )
                else:
                    options.append(
                        SkillParam(
                            name=param.name,
                            kind="kwonly",
                            default=param.default,
                            annotation=ann,
                        )
                    )
            elif param.default is inspect._empty:
                positional.append(SkillParam(name=param.name, kind="positional", default=None, annotation=ann))
            else:
                options.append(
                    SkillParam(
                        name=param.name,
                        kind="optional",
                        default=param.default,
                        annotation=ann,
                    )
                )

        metas.append(
            SkillMeta(
                name=name,
                summary=_summary(doc),
                doc=doc,
                positional_params=positional,
                option_params=options,
            )
        )
    return metas

