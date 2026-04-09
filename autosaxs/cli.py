from __future__ import annotations

import argparse
import inspect
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple, get_args, get_origin, get_type_hints

from .path_expression import PathExpression, SingletonPathExpression


def _to_kebab(s: str) -> str:
    return s.replace("_", "-")


def _is_list_annotation(ann: Any) -> bool:
    origin = get_origin(ann)
    if origin in (list, List):
        return True
    return False


def _is_optional_tuple2_annotation(ann: Any) -> bool:
    # Accept tuple[float, float] or Optional[tuple[float, float]] (PEP 604 included)
    origin = get_origin(ann)
    args = get_args(ann)
    if origin is tuple and len(args) == 2:
        return True
    if origin is Optional:
        inner = args[0] if args else None
        return _is_optional_tuple2_annotation(inner)
    # Python 3.10+: Optional[T] may be represented as Union[T, NoneType]
    if origin is getattr(__import__("typing"), "Union", None) and args:
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        return len(non_none) == 1 and _is_optional_tuple2_annotation(non_none[0])
    return False


def _is_optional_scalar_annotation(ann: Any, scalar_type: Any) -> bool:
    """
    True for `scalar_type` and `Optional[scalar_type]` (including Union[scalar_type, NoneType]).
    """
    if ann is scalar_type:
        return True
    origin = get_origin(ann)
    if origin is getattr(__import__("typing"), "Union", None) and get_args(ann):
        args = get_args(ann)
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        return len(non_none) == 1 and non_none[0] is scalar_type
    return False


def _wrap_path_expression_value(value: Any, ann: Any) -> Any:
    """
    Wrap raw CLI strings into PathExpression / SingletonPathExpression instances based on annotation.
    """
    if value is None:
        return None

    origin = get_origin(ann)
    args = get_args(ann)
    if origin is Optional and args:
        return _wrap_path_expression_value(value, args[0])
    if origin is getattr(__import__("typing"), "Union", None) and args:
        # Support annotations like `PathExpression | str` and `SingletonPathExpression | str`,
        # as well as Optional[...] forms represented as Union[..., NoneType].
        non_none = [a for a in args if a is not type(None)]  # noqa: E721
        if len(non_none) == 1:
            return _wrap_path_expression_value(value, non_none[0])
        if PathExpression in non_none:
            return PathExpression(str(value))
        if SingletonPathExpression in non_none:
            return SingletonPathExpression(str(value))
        return value

    if ann is PathExpression:
        return PathExpression(str(value))
    if ann is SingletonPathExpression:
        return SingletonPathExpression(str(value))
    return value


def _skill_functions() -> Dict[str, Callable[..., Any]]:
    from . import skill as skill_mod

    return dict(skill_mod.list_skills(include_reports=True))


def _add_skill_subparser(subparsers: argparse._SubParsersAction, name: str, fn: Callable[..., Any]) -> None:
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    p = subparsers.add_parser(
        name,
        help=doc.splitlines()[0] if doc else None,
    )
    p.set_defaults(_autosaxs_fn=fn)
    # A separate flag prints the full docstring and exits (handled before argparse enforces required positionals).
    p.add_argument("--description", action="store_true", help="Print full command description and exit")

    # Resolve postponed annotations ("from __future__ import annotations"), so
    # `Optional[float]` etc. are real types and not strings.
    try:
        type_hints = get_type_hints(fn)
    except Exception:
        type_hints = {}

    # Positional args: parameters without defaults, excluding keyword-only.
    for param in sig.parameters.values():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise TypeError(
                f"Skill '{name}' is not CLI-compatible: varargs are not supported ({param.name})."
            )
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            continue
        if param.default is not inspect._empty:
            continue

        arg_name = param.name
        kwargs: Dict[str, Any] = {}
        ann = type_hints.get(param.name, param.annotation)
        if _is_list_annotation(ann):
            kwargs["nargs"] = "+"
        p.add_argument(arg_name, **kwargs)

    # Options: keyword-only and positional-or-keyword with defaults.
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.KEYWORD_ONLY or (
            param.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD and param.default is not inspect._empty
        ):
            opt_name = f"--{_to_kebab(param.name)}"

            if param.name == "use_cache" and isinstance(param.default, bool):
                # Spec: --no-cache disables caching (use_cache=False)
                p.add_argument("--no-cache", dest="use_cache", action="store_false", help="Disable caching")
                continue
            if param.name == "output_dir":
                p.add_argument("--output-dir", dest="output_dir", default=param.default, help="Output directory")
                continue

            ann = type_hints.get(param.name, param.annotation)

            if _is_optional_tuple2_annotation(ann) or param.name.endswith("_range_nm"):
                p.add_argument(opt_name, dest=param.name, nargs=2, type=float)
                continue

            if isinstance(param.default, bool):
                if param.default is False:
                    p.add_argument(opt_name, dest=param.name, action="store_true")
                else:
                    p.add_argument(opt_name, dest=param.name, action="store_false")
                continue

            if _is_optional_scalar_annotation(ann, int):
                p.add_argument(opt_name, dest=param.name, type=int, default=param.default)
                continue
            if _is_optional_scalar_annotation(ann, float):
                p.add_argument(opt_name, dest=param.name, type=float, default=param.default)
                continue

            p.add_argument(opt_name, dest=param.name, default=param.default)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="autosaxs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    skills = _skill_functions()
    for name in sorted(skills):
        _add_skill_subparser(subparsers, name, skills[name])

    # Support: `autosaxs <subcommand> --description` without requiring positional args.
    # Argparse enforces required positionals before we can inspect flags, so handle this early.
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        cmd = argv[0]
        if cmd in skills and "--description" in argv[1:]:
            raw = getattr(skills[cmd], "__doc__", None) or ""
            sys.stdout.write(raw)
            if raw and not raw.endswith("\n"):
                sys.stdout.write("\n")
            return 0

    args = parser.parse_args(argv)
    fn: Callable[..., Any] = getattr(args, "_autosaxs_fn")

    # If user provided all required args, allow `--description` too (consistent behavior).
    if getattr(args, "description", False):
        raw = getattr(fn, "__doc__", None) or ""
        sys.stdout.write(raw)
        if raw and not raw.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    sig = inspect.signature(fn)
    try:
        type_hints = get_type_hints(fn)
    except Exception:
        type_hints = {}
    kwargs: Dict[str, Any] = {}
    positional: List[Any] = []

    for param in sig.parameters.values():
        ann = type_hints.get(param.name, param.annotation)
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            if hasattr(args, param.name):
                kwargs[param.name] = _wrap_path_expression_value(getattr(args, param.name), ann)
        elif param.default is inspect._empty:
            positional.append(_wrap_path_expression_value(getattr(args, param.name), ann))
        else:
            if hasattr(args, param.name):
                kwargs[param.name] = _wrap_path_expression_value(getattr(args, param.name), ann)

    out = fn(*positional, **kwargs)
    if isinstance(out, dict):
        for k, v in out.items():
            print(f"{k}={v}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

