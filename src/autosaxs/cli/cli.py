from __future__ import annotations

import argparse
import inspect
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, get_args, get_origin, get_type_hints

from ..core.path_expression import ConfigPathExpression, PathExpression, SingletonPathExpression


class AutosaxsHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Compact top-level usage (no enumerated list of every subcommand)."""

    def _format_usage(self, usage, actions, groups, prefix):
        if usage is not None:
            pfx = prefix if prefix else "usage: "
            return pfx + usage + "\n\n"
        pfx = prefix if prefix else "usage: "
        return f"{pfx}autosaxs [-h] [-v] [-U [--force]] COMMAND ...\n\n"

def _autosaxs_version() -> str:
    try:
        import autosaxs

        return str(getattr(autosaxs, "__version__", "unknown"))
    except Exception:
        return "unknown"


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


def _read_subskill_ai_skill_template() -> str:
    """Leaf procedure template written as ``saxs-processing/<kebab>/<kebab>.md`` (not a Cursor ``SKILL.md``)."""
    try:
        from importlib.resources import files

        return (files("autosaxs.resources.ai_skills") / "subskill_template.md").read_text(encoding="utf-8")
    except Exception:
        return (
            Path(__file__).resolve().parents[1] / "resources" / "ai_skills" / "subskill_template.md"
        ).read_text(encoding="utf-8")


def _read_router_skill_template() -> str:
    """Top-level router SKILL.md for the ``saxs-processing`` bundle."""
    try:
        from importlib.resources import files

        return (files("autosaxs.resources.ai_skills") / "router_template.md").read_text(encoding="utf-8")
    except Exception:
        return (
            Path(__file__).resolve().parents[1] / "resources" / "ai_skills" / "router_template.md"
        ).read_text(encoding="utf-8")


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


def _doc_first_non_empty_line(fn: Callable[..., Any]) -> str:
    doc = inspect.getdoc(fn) or ""
    for line in doc.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _sanitize_router_hint(s: str) -> str:
    """Keep router bullets readable when docstrings contain Markdown."""
    s = s.replace("`", "'")
    s = s.replace("**", "")
    s = s.replace("*", "")
    return s


def _routing_summary_line(fn: Callable[..., Any], *, max_len: int = 280) -> str:
    s = _doc_first_non_empty_line(fn)
    if not s:
        return "this autosaxs skill"
    if len(s) > max_len:
        s = s[: max_len - 3].rstrip() + "..."
    return _sanitize_router_hint(s)


_ROUTER_IO_HELPERS: Tuple[str, ...] = (
    "read_saxs",
    "load_saxs_1d_any",
    "write_saxs",
    "write_saxs_atsas_format",
    "parse_gnom_out",
    "read_data",
    "write_data",
)


def _format_type_annotation(ann: Any) -> str:
    """Compact display name for signatures in generated router SKILL.md."""
    if ann is inspect.Parameter.empty:
        return ""
    if ann is type(None):
        return "None"
    if isinstance(ann, str):
        return ann
    origin = get_origin(ann)
    if origin is Union:
        args = get_args(ann)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return f"Optional[{_format_type_annotation(non_none[0])}]"
        return "Union[" + ", ".join(_format_type_annotation(a) for a in args) + "]"
    if origin in (tuple, Tuple):
        args = get_args(ann)
        if args:
            inner = ", ".join(_format_type_annotation(a) for a in args)
            return f"Tuple[{inner}]"
        return "tuple"
    if origin in (dict, Dict):
        key_t, val_t = get_args(ann)
        return f"Dict[{_format_type_annotation(key_t)}, {_format_type_annotation(val_t)}]"
    if origin in (list, List):
        (item_t,) = get_args(ann)
        return f"List[{_format_type_annotation(item_t)}]"
    mod = getattr(ann, "__module__", "") or ""
    name = getattr(ann, "__name__", str(ann))
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    if name in ("Any", "Optional", "Union", "Tuple", "List", "Dict"):
        return name
    if name == "DataFrame" or "pandas" in mod:
        return "pd.DataFrame"
    if mod == "numpy" or name == "ndarray":
        return "np.ndarray"
    if mod.startswith("typing."):
        return name
    if mod in ("builtins", ""):
        return name
    return f"{mod.split('.')[-1]}.{name}"


def _returns_hint_from_docstring(doc: str) -> Optional[str]:
    """Best-effort return description from a function docstring."""
    lines = (doc or "").splitlines()
    in_returns = False
    for line in lines:
        if re.match(r"^\s*Returns?:\s*$", line, flags=re.IGNORECASE):
            in_returns = True
            continue
        m = re.match(r"^\s*Returns?:\s*(.+)$", line, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
        if in_returns:
            s = line.strip()
            if not s:
                continue
            m2 = re.match(r"^(tuple|dict|list):\s*(.+)$", s, flags=re.IGNORECASE)
            if m2:
                kind = m2.group(1).lower()
                payload = m2.group(2).strip()
                if kind == "tuple":
                    return f"({payload.strip('()')})"
                return payload
            if not s[0].isupper() or "(" in s:
                return s
            break
    return None


def _io_helper_signature_line(fn: Callable[..., Any]) -> str:
    """One-line callable signature for router documentation."""
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}
    sig = inspect.signature(fn)
    parts: List[str] = []
    for param in sig.parameters.values():
        ann = hints.get(param.name, param.annotation)
        if ann is not inspect.Parameter.empty:
            segment = f"{param.name}: {_format_type_annotation(ann)}"
        else:
            segment = param.name
        if param.default is not inspect.Parameter.empty:
            if param.default is None:
                segment += " = None"
            elif isinstance(param.default, str):
                segment += f' = "{param.default}"'
            else:
                segment += f" = {param.default!r}"
        parts.append(segment)
    param_str = ", ".join(parts)
    ret_ann = hints.get("return", sig.return_annotation)
    if ret_ann is not inspect.Signature.empty and ret_ann is not None:
        ret = _format_type_annotation(ret_ann)
    else:
        ret = _returns_hint_from_docstring(inspect.getdoc(fn) or "")
    if ret:
        return f"{fn.__name__}({param_str}) -> {ret}"
    return f"{fn.__name__}({param_str})"


def _io_helpers_catalog() -> str:
    """Markdown bullets for autosaxs.core.utils I/O helpers (signatures from inspect)."""
    from ..core import utils as core_utils

    lines: List[str] = []
    for name in _ROUTER_IO_HELPERS:
        fn = getattr(core_utils, name, None)
        if fn is None or not callable(fn):
            continue
        sig_line = _io_helper_signature_line(fn)
        summary = _sanitize_router_hint(_doc_first_non_empty_line(fn) or name)
        lines.append(f"- `{sig_line}` — {summary}")
    return "\n".join(lines)


def _router_skill_md(skill_fns: Dict[str, Callable[..., Any]], *, autosaxs_version: str) -> str:
    """
    Build ``<output-dir>/saxs-processing/SKILL.md`` — SAXS orchestrator over
    ``saxs-processing/<kebab>/<kebab>.md`` procedure docs (ordinary markdown, not nested ``SKILL.md``).
    """
    catalog_lines: List[str] = []
    for skill_name, fn in skill_fns.items():
        kebab = _to_kebab(skill_name)
        teaser = _routing_summary_line(fn)
        rel = f"{kebab}/{kebab}.md"
        catalog_lines.append(f"- [`{rel}`]({rel}) (`autosaxs {kebab}`) — {teaser}")
    template = _read_router_skill_template()
    return _fill_template(
        template,
        values={
            "subskill_catalog": "\n".join(catalog_lines),
            "io_helpers_catalog": _io_helpers_catalog(),
            "autosaxs_version": str(autosaxs_version),
        },
    ).rstrip() + "\n"


def _skill_to_agent_skill_md(*, name: str, fn: Callable[..., Any]) -> str:
    """
    Render a subskill procedure markdown file from an autosaxs skill docstring.

    Leaves are ordinary ``<name>.md`` docs (not Cursor ``SKILL.md``); the docstring is the source of truth.
    """
    doc = inspect.getdoc(fn) or ""
    template = _read_subskill_ai_skill_template()
    return _fill_template(
        template,
        values={
            "command": name,
            "python_name": name.replace("-", "_"),
            "docstring": doc.rstrip(),
        },
    ).rstrip() + "\n"


def _add_get_docs_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "get-docs",
        help="Generate README.md (short) and autosaxs-docs/skills_reference.md (detailed skills)",
    )
    p.set_defaults(_autosaxs_internal_cmd="get-docs")
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="Directory where README.md and autosaxs-docs/ will be written (default: current directory)",
    )


def _add_get_skills_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "get-skills",
        help="Generate saxs-processing/ (orchestrator SKILL.md + nested <name>/<name>.md procedure docs)",
    )
    p.set_defaults(_autosaxs_internal_cmd="get-skills")
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default=".",
        help="Parent directory for `saxs-processing/`; that folder is replaced entirely each run (default: current directory)",
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


def _parse_short_parameter_list(doc: str) -> Dict[str, str]:
    """
    Parse ``### Short parameter list`` bullets from a skill docstring.

    Each line must be ``- param_name: help text`` (optional backticks around ``param_name``).
    """
    lines = (doc or "").splitlines()
    in_section = False
    result: Dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if re.match(r"^###\s+Short parameter list\s*$", stripped, flags=re.IGNORECASE):
            in_section = True
            continue
        if not in_section:
            continue
        if stripped.startswith("###"):
            break
        m = re.match(r"^-\s*`?([a-zA-Z_][a-zA-Z0-9_]*)`?\s*:\s*(.+?)\s*$", stripped)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


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
    p.add_argument("--description", action="store_true", help="Print detailed command description")
    short_params = _parse_short_parameter_list(doc)

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
                p.add_argument(
                    "-o",
                    "--output-dir",
                    dest="output_dir",
                    default=param.default,
                    help="Output directory",
                )
                continue
            if param.name == "config_path":
                p.add_argument(
                    "--config-path",
                    "--conf",
                    dest="config_path",
                    default=param.default,
                    help="Deprecated: YAML config file (skill-keyed sections)",
                )
                continue
            if param.name == "wavelength":
                p.add_argument(
                    opt_name,
                    dest=param.name,
                    type=_parse_optional_float,
                    default=param.default,
                    help=short_params.get("wavelength", "X-ray wavelength in Ångström"),
                )
                continue
            if param.name == "dist_guess":
                p.add_argument(
                    opt_name,
                    dest=param.name,
                    type=_parse_optional_float,
                    default=param.default,
                    help=short_params.get(
                        "dist_guess",
                        "Optional: initial sample-detector distance in metres (algorithm works good even if this is not set)",
                    ),
                )
                continue

            ann = type_hints.get(param.name, param.annotation)

            if _is_optional_tuple2_annotation(ann) or param.name.endswith("_range_nm"):
                p.add_argument(opt_name, dest=param.name, nargs=2, type=float)
                continue

            if isinstance(param.default, bool):
                bool_kw: Dict[str, Any] = {
                    "dest": param.name,
                    "default": param.default,
                }
                bool_help = short_params.get(param.name)
                if bool_help:
                    bool_kw["help"] = bool_help
                if param.default is False:
                    bool_kw["action"] = "store_true"
                else:
                    # True default: keep --flag semantics and expose --no-flag.
                    bool_kw["action"] = argparse.BooleanOptionalAction
                p.add_argument(opt_name, **bool_kw)
                continue

            if _is_optional_scalar_annotation(ann, int):
                int_kw: Dict[str, Any] = {
                    "dest": param.name,
                    "type": _parse_optional_int,
                    "default": param.default,
                }
                int_help = short_params.get(param.name)
                if int_help:
                    int_kw["help"] = int_help
                p.add_argument(opt_name, **int_kw)
                continue
            if _is_optional_scalar_annotation(ann, float):
                float_kw: Dict[str, Any] = {
                    "dest": param.name,
                    "type": _parse_optional_float,
                    "default": param.default,
                }
                float_help = short_params.get(param.name)
                if float_help:
                    float_kw["help"] = float_help
                p.add_argument(opt_name, **float_kw)
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

            if param.default is inspect._empty:
                if ann is float:
                    req_float_kw: Dict[str, Any] = {"dest": param.name, "type": float, "required": True}
                    req_float_help = short_params.get(param.name)
                    if req_float_help:
                        req_float_kw["help"] = req_float_help
                    p.add_argument(opt_name, **req_float_kw)
                    continue
                if ann is int:
                    req_int_kw: Dict[str, Any] = {"dest": param.name, "type": int, "required": True}
                    req_int_help = short_params.get(param.name)
                    if req_int_help:
                        req_int_kw["help"] = req_int_help
                    p.add_argument(opt_name, **req_int_kw)
                    continue

            opt_kw: Dict[str, Any] = {"dest": param.name, "default": param.default}
            opt_help = short_params.get(param.name)
            if opt_help:
                opt_kw["help"] = opt_help
            p.add_argument(opt_name, **opt_kw)


def main(argv: Optional[List[str]] = None) -> int:
    version = _autosaxs_version()
    parser = argparse.ArgumentParser(
        prog="autosaxs",
        formatter_class=AutosaxsHelpFormatter,
        description=(
            "SAXS pipeline: processing skills plus helpers that write README, IDE skills, and "
            "default config into your workspace (see epilog)."
        ),
        epilog=_read_agent_quickstart_epilog(),
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"autosaxs {version}",
        help="Show the installed autosaxs version and exit.",
    )
    parser.add_argument(
        "-U",
        "--update",
        action="store_true",
        help="Upgrade autosaxs[gui] to the latest version from git main.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With -U/--update, reinstall with pip --force-reinstall (default: upgrade only).",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=False,
        metavar="COMMAND",
        title="commands",
        description="Run a skill or helper (autosaxs COMMAND --help for details).",
    )

    _add_get_docs_subparser(subparsers)
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

    # Deprecated fit-* aliases → model_* (not listed in list_skills).
    if "model_dam" in skills:
        model_dam_fn = skills["model_dam"]
        skills_by_cmd["fit-dammif"] = model_dam_fn
        skills_by_cmd["fit_dammif"] = model_dam_fn
        _add_skill_subparser(
            subparsers,
            "fit-dammif",
            model_dam_fn,
            aliases=["fit_dammif"],
        )
    if "model_bodies" in skills:
        model_bodies_fn = skills["model_bodies"]
        skills_by_cmd["fit-bodies"] = model_bodies_fn
        skills_by_cmd["fit_bodies"] = model_bodies_fn
        _add_skill_subparser(
            subparsers,
            "fit-bodies",
            model_bodies_fn,
            aliases=["fit_bodies"],
        )
    if "model_mixture" in skills:
        model_mixture_fn = skills["model_mixture"]
        skills_by_cmd["fit-mixture"] = model_mixture_fn
        skills_by_cmd["fit_mixture"] = model_mixture_fn
        _add_skill_subparser(
            subparsers,
            "fit-mixture",
            model_mixture_fn,
            aliases=["fit_mixture"],
        )

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

    if getattr(args, "update", False):
        from .package_update import run_pip_upgrade

        return run_pip_upgrade(force=bool(getattr(args, "force", False)))

    if not getattr(args, "command", None):
        parser.error("the following arguments are required: command")

    internal_cmd = getattr(args, "_autosaxs_internal_cmd", None)
    if internal_cmd == "get-docs":
        from ..resources.readme.autosaxs_skills_explained import generate_docs

        paths = generate_docs(output_dir=getattr(args, "output_dir", "."))
        print(str(paths["readme"]))
        print(str(paths["skills_reference"]))
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
        bundle = out_dir / "saxs-processing"
        if bundle.exists():
            shutil.rmtree(bundle)
        bundle.mkdir(parents=True, exist_ok=True)

        skill_fns = _skill_functions()

        written: List[str] = []
        for skill_name, fn in skill_fns.items():
            folder_name = _to_kebab(skill_name)
            skill_out_dir = bundle / folder_name
            skill_out_dir.mkdir(parents=True, exist_ok=True)
            md = _skill_to_agent_skill_md(name=folder_name, fn=fn)
            out_md = skill_out_dir / f"{folder_name}.md"
            out_md.write_text(md, encoding="utf-8")
            written.append(str(out_md))

        try:
            import autosaxs as _autosaxs_mod

            autosaxs_version = getattr(_autosaxs_mod, "__version__", "unknown")
        except Exception:
            autosaxs_version = "unknown"

        router_md_path = bundle / "SKILL.md"
        router_md_path.write_text(_router_skill_md(skill_fns, autosaxs_version=autosaxs_version), encoding="utf-8")
        written.append(str(router_md_path))

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

