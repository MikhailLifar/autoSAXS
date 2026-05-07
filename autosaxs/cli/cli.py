from __future__ import annotations

import argparse
import inspect
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, get_args, get_origin, get_type_hints

from ..core.path_expression import ConfigPathExpression, PathExpression, SingletonPathExpression


def _to_kebab(s: str) -> str:
    return s.replace("_", "-")


def _is_list_annotation(ann: Any) -> bool:
    origin = get_origin(ann)
    if origin in (list, List):
        return True
    return False


def _is_list_str_annotation(ann: Any) -> bool:
    """True for list[str] / List[str]."""
    origin = get_origin(ann)
    if origin not in (list, List):
        return False
    args = get_args(ann)
    return len(args) == 1 and args[0] is str


def _is_optional_list_str_annotation(ann: Any) -> bool:
    """True for Optional[list[str]] / Optional[List[str]]."""
    origin = get_origin(ann)
    if origin is not Union:
        return False
    args = get_args(ann)
    non_none = [a for a in args if a is not type(None)]  # noqa: E721
    return len(non_none) == 1 and _is_list_str_annotation(non_none[0])


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


def _parse_optional_int(value: str) -> Optional[int]:
    """Argparse type for Optional[int]: empty / whitespace -> None (e.g. ``--first=`` from YAML or shell)."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return int(s)


def _parse_optional_float(value: str) -> Optional[float]:
    """Argparse type for Optional[float]: empty / whitespace -> None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return float(s)


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
        # Prefer any concrete PathExpression subclass present in the union.
        for t in non_none:
            if isinstance(t, type) and issubclass(t, PathExpression):
                return t(str(value))
        if PathExpression in non_none:
            return PathExpression(str(value))
        if ConfigPathExpression in non_none:
            return ConfigPathExpression(str(value))
        if SingletonPathExpression in non_none:
            return SingletonPathExpression(str(value))
        return value

    if isinstance(ann, type) and issubclass(ann, PathExpression):
        return ann(str(value))
    if ann is PathExpression:
        return PathExpression(str(value))
    if ann is ConfigPathExpression:
        return ConfigPathExpression(str(value))
    if ann is SingletonPathExpression:
        return SingletonPathExpression(str(value))
    return value


def _skill_functions() -> Dict[str, Callable[..., Any]]:
    from .. import skill as skill_mod

    return dict(skill_mod.list_skills(include_reports=True))


def _read_ai_skill_template() -> str:
    """
    Load the SKILL.md template shipped with the autosaxs package.

    Uses importlib.resources when available so this works from installed wheels.
    """
    try:
        from importlib.resources import files  # py3.9+

        return (files("autosaxs.resources.ai_skills") / "template.md").read_text(encoding="utf-8")
    except Exception:
        # Fallback for non-standard environments (editable installs, etc.)
        return (Path(__file__).resolve().parents[1] / "resources" / "ai_skills" / "template.md").read_text(
            encoding="utf-8"
        )


def _read_ai_skills_readme_template() -> str:
    """
    Load the skills/README.md template shipped with the autosaxs package.
    """
    try:
        from importlib.resources import files  # py3.9+

        return (files("autosaxs.resources.ai_skills") / "readme_template.md").read_text(encoding="utf-8")
    except Exception:
        return (Path(__file__).resolve().parents[1] / "resources" / "ai_skills" / "readme_template.md").read_text(
            encoding="utf-8"
        )


def _extract_frontmatter_field(md: str, field: str) -> Optional[str]:
    """
    Best-effort YAML-frontmatter extraction for single-line `field: value` entries.
    """
    lines = (md or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, min(len(lines), 200)):
        if lines[i].strip() == "---":
            break
        s = lines[i].strip()
        if s.startswith(field + ":"):
            return s.split(":", 1)[1].strip()
    return None


def _fill_template(template: str, *, values: Dict[str, str]) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def _skill_to_agent_skill_md(*, name: str, fn: Callable[..., Any]) -> str:
    """
    Render a Cursor-style Agent Skill `SKILL.md` from an autosaxs skill docstring.

    The function docstring is treated as the single source of truth.
    """
    doc = inspect.getdoc(fn) or ""
    first_line = ""
    for line in doc.splitlines():
        s = line.strip()
        if s:
            first_line = s
            break
    description = first_line or f"autosaxs skill: {name}"
    template = _read_ai_skill_template()
    return _fill_template(
        template,
        values={
            "name": name,
            "description": description,
            "command": name,
            "python_name": name.replace("-", "_"),
            "docstring": doc.rstrip(),
        },
    ).rstrip() + "\n"


def _add_get_readme_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "get-readme",
        help="Generate autosaxs README.md",
    )
    p.set_defaults(_autosaxs_internal_cmd="get-readme")
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="Directory where README.md will be written (default: current directory)",
    )


def _add_get_skills_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "get-skills",
        help="Generate Cursor Agent Skills from autosaxs docstrings",
    )
    p.set_defaults(_autosaxs_internal_cmd="get-skills")
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="Directory where `skills/` will be written (default: current directory)",
    )


def _add_get_default_config_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "get-default-config",
        help="Copy bundled config_base.conf into a directory",
    )
    p.set_defaults(_autosaxs_internal_cmd="get-default-config")
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="Directory where config_base.conf will be written (default: current directory)",
    )


def _read_agent_quickstart_epilog() -> str:
    """
    Text appended to top-level ``autosaxs --help`` so agents see how to export README, skills, and config.
    """
    try:
        from importlib.resources import files

        return (files("autosaxs.resources") / "agent_quickstart.txt").read_text(encoding="utf-8").rstrip()
    except Exception:
        p = Path(__file__).resolve().parents[1] / "resources" / "agent_quickstart.txt"
        return p.read_text(encoding="utf-8").rstrip()


def _read_default_config_base_bytes() -> bytes:
    """
    Load resources/config_base.conf from the installed package or the source tree.
    """
    try:
        from importlib.resources import files

        return (files("autosaxs.resources") / "config_base.conf").read_bytes()
    except Exception:
        return (Path(__file__).resolve().parents[1] / "resources" / "config_base.conf").read_bytes()


def _add_skill_subparser(
    subparsers: argparse._SubParsersAction,
    name: str,
    fn: Callable[..., Any],
    *,
    aliases: Optional[List[str]] = None,
) -> None:
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    p = subparsers.add_parser(
        name,
        aliases=aliases or [],
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
                # Caching is opt-in; expose both flags explicitly.
                g = p.add_mutually_exclusive_group()
                g.add_argument("--cache", dest="use_cache", action="store_true", help="Enable caching")
                g.add_argument("--no-cache", dest="use_cache", action="store_false", help="Disable caching")
                p.set_defaults(use_cache=param.default)
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
                p.add_argument(
                    opt_name,
                    dest=param.name,
                    type=_parse_optional_int,
                    default=param.default,
                )
                continue
            if _is_optional_scalar_annotation(ann, float):
                p.add_argument(
                    opt_name,
                    dest=param.name,
                    type=_parse_optional_float,
                    default=param.default,
                )
                continue

            if _is_optional_list_str_annotation(ann):
                p.add_argument(
                    opt_name,
                    dest=param.name,
                    nargs="*",
                    default=None,
                    metavar="SHAPE",
                    help="BODIES model names to fit (default: all). Example: --shapes cylinder ellipsoid",
                )
                continue

            p.add_argument(opt_name, dest=param.name, default=param.default)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="autosaxs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "SAXS pipeline: processing skills plus helpers that write README, IDE skills, and "
            "default config into your workspace (see epilog)."
        ),
        epilog=_read_agent_quickstart_epilog(),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_get_readme_subparser(subparsers)
    _add_get_skills_subparser(subparsers)
    _add_get_default_config_subparser(subparsers)

    skills = _skill_functions()
    # Prefer kebab-case commands; keep snake_case as backward-compatible alias.
    skills_by_cmd: Dict[str, Callable[..., Any]] = {}
    for snake_name, fn in skills.items():
        kebab_name = _to_kebab(snake_name)
        skills_by_cmd[snake_name] = fn
        skills_by_cmd[kebab_name] = fn
        if kebab_name == snake_name:
            _add_skill_subparser(subparsers, kebab_name, fn)
        else:
            _add_skill_subparser(subparsers, kebab_name, fn, aliases=[snake_name])

    # Support: `autosaxs <subcommand> --description` without requiring positional args.
    # Argparse enforces required positionals before we can inspect flags, so handle this early.
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        cmd = argv[0]
        if cmd in skills_by_cmd and "--description" in argv[1:]:
            raw = getattr(skills_by_cmd[cmd], "__doc__", None) or ""
            sys.stdout.write(raw)
            if raw and not raw.endswith("\n"):
                sys.stdout.write("\n")
            return 0

    args = parser.parse_args(argv)

    internal_cmd = getattr(args, "_autosaxs_internal_cmd", None)
    if internal_cmd == "get-readme":
        from ..resources.readme.autosaxs_skills_explained import generate_readme

        out_path = generate_readme(output_dir=getattr(args, "output_dir", "."))
        print(str(out_path))
        return 0
    if internal_cmd == "get-default-config":
        out_dir = Path(getattr(args, "output_dir", ".")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "config_base.conf"
        dest.write_bytes(_read_default_config_base_bytes())
        print(str(dest))
        return 0
    if internal_cmd == "get-skills":
        out_dir = Path(getattr(args, "output_dir", "."))
        skills_dir = out_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        written: List[str] = []
        for skill_name, fn in _skill_functions().items():
            folder_name = _to_kebab(skill_name)
            skill_out_dir = skills_dir / folder_name

            # Rewrite reliably: remove only this per-skill folder (not the parent `skills/`).
            if skill_out_dir.exists():
                shutil.rmtree(skill_out_dir)
            skill_out_dir.mkdir(parents=True, exist_ok=True)

            md = _skill_to_agent_skill_md(name=folder_name, fn=fn)
            out_md = skill_out_dir / "SKILL.md"
            out_md.write_text(md, encoding="utf-8")
            written.append(str(out_md))

        # Generate top-level skills index README.md (includes any pre-existing skills too).
        try:
            import autosaxs as _autosaxs_mod

            autosaxs_version = getattr(_autosaxs_mod, "__version__", "unknown")
        except Exception:
            autosaxs_version = "unknown"

        skills_list_lines: List[str] = []
        for child in sorted(skills_dir.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            skill_md = child / "SKILL.md"
            if not skill_md.is_file():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
            except OSError:
                content = ""
            desc = _extract_frontmatter_field(content, "description") or ""
            if desc:
                skills_list_lines.append(f"- [`{child.name}`]({child.name}/SKILL.md): {desc}")
            else:
                skills_list_lines.append(f"- [`{child.name}`]({child.name}/SKILL.md)")

        readme_template = _read_ai_skills_readme_template()
        readme_text = _fill_template(
            readme_template,
            values={
                "autosaxs_version": str(autosaxs_version),
                "skills_list": "\n".join(skills_list_lines) if skills_list_lines else "_(no skills found)_",
            },
        )
        (skills_dir / "README.md").write_text(readme_text.rstrip() + "\n", encoding="utf-8")

        for p in sorted(written):
            print(p)
        return 0

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

