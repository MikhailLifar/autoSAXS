from __future__ import annotations

import inspect
from typing import Callable, Dict, List

from ..core.models import SkillMeta, SkillParam


def _public_skill_functions() -> Dict[str, Callable]:
    from autosaxs import skill as skill_mod

    funcs: Dict[str, Callable] = {}
    for name, obj in inspect.getmembers(skill_mod, inspect.isfunction):
        if name.startswith("_"):
            continue
        if getattr(obj, "__module__", None) != skill_mod.__name__:
            continue
        funcs[name] = obj
    return funcs


def _summary(doc: str) -> str:
    doc = (doc or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def discover_skills() -> List[SkillMeta]:
    metas: List[SkillMeta] = []
    for name, fn in sorted(_public_skill_functions().items(), key=lambda kv: kv[0]):
        # Exclude report skills from the GUI for now.
        if name in ("report_individual", "report_summary") or name.startswith("report_"):
            continue
        doc = inspect.getdoc(fn) or ""
        sig = inspect.signature(fn)

        positional: List[SkillParam] = []
        options: List[SkillParam] = []

        for param in sig.parameters.values():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            ann = None
            if param.annotation is not inspect._empty:
                ann = getattr(param.annotation, "__name__", str(param.annotation))

            if param.kind == inspect.Parameter.KEYWORD_ONLY:
                options.append(
                    SkillParam(
                        name=param.name,
                        kind="kwonly",
                        default=None if param.default is inspect._empty else param.default,
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

