from __future__ import annotations

import inspect
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

# Per-process cache: skill name -> fully introspected SkillMeta (one module import each).
_meta_cache: Dict[str, SkillMeta] = {}


def list_catalog_skill_names() -> List[str]:
    """
    Ordered skill names for the console catalog without importing skill bodies.
    """
    from autosaxs.skill import SKILL_ORDER, list_skill_names

    names = list_skill_names(include_reports=False)
    order_index = {n: i for i, n in enumerate(SKILL_ORDER)}
    return sorted(names, key=lambda n: (0, order_index[n], n) if n in order_index else (1, 0, n))


def _summary(doc: str) -> str:
    doc = (doc or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


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


def _meta_from_callable(name: str, fn: Callable[..., Any]) -> SkillMeta:
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

    return SkillMeta(
        name=name,
        summary=_summary(doc),
        doc=doc,
        positional_params=positional,
        option_params=options,
    )


def skill_meta_for(name: str) -> SkillMeta:
    """
    Load metadata for one skill (imports that skill module once; cached).
    """
    cached = _meta_cache.get(name)
    if cached is not None:
        return cached
    from autosaxs.skill import get_skill

    meta = _meta_from_callable(name, get_skill(name))
    _meta_cache[name] = meta
    return meta


def discover_skills() -> List[SkillMeta]:
    """
    Full catalog metadata (imports each skill module once).

    Prefer ``list_catalog_skill_names`` + ``skill_meta_for`` for console cold start
    so the UI can list names without importing every implementation up front.
    """
    return [skill_meta_for(name) for name in list_catalog_skill_names()]
