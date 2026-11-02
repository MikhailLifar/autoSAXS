"""Portable GUI↔CLI request/result contract tests (no beamline fixtures)."""

from __future__ import annotations

import argparse
import inspect
from typing import Any, Dict, List

import pytest

from autosaxs.cli import cli as cli_mod
from autosaxs.cli.request_codec import (
    RESULT_FRAME_BEGIN,
    RESULT_FRAME_END,
    build_result_envelope,
    format_result_frame,
    options_to_argv,
    parse_result_frame,
    science_ok_for_result,
)
from autosaxs.skill import get_skill, list_skills
from guisaxs_skills.core.models import RunRequest
from guisaxs_skills.logic.result_parser import parse_skill_result_stdout


def _parse_skill_argv(skill_name: str, argv: List[str]) -> argparse.Namespace:
    """Parse argv the same way the CLI builds skill subparsers."""
    skills = list_skills()
    assert skill_name in skills, skill_name
    parser = argparse.ArgumentParser(prog="autosaxs-test")
    sub = parser.add_subparsers(dest="command", required=True)
    cli_mod._add_skill_subparser(sub, skill_name, get_skill(skill_name))
    return parser.parse_args(argv)


def _ns_option(ns: argparse.Namespace, name: str) -> Any:
    assert hasattr(ns, name), f"missing option {name} on {ns}"
    return getattr(ns, name)


@pytest.mark.parametrize(
    "options,expect",
    [
        ({"use_cache": True}, {"use_cache": True}),
        ({"use_cache": False}, {"use_cache": False}),
        ({"use_cache": True, "validation_png": True}, {"use_cache": True, "validation_png": True}),
        ({"use_cache": False, "validation_png": False}, {"use_cache": False, "validation_png": False}),
        ({"output_dir": "/tmp/out", "use_cache": False}, {"output_dir": "/tmp/out", "use_cache": False}),
        ({"note": ""}, {}),  # empty string omitted
    ],
)
def test_options_to_argv_cache_and_bools(options: Dict[str, Any], expect: Dict[str, Any]) -> None:
    argv = options_to_argv(options)
    if "use_cache" in options:
        assert ("--cache" in argv) ^ ("--no-cache" in argv)
        assert ("--cache" in argv) is bool(options["use_cache"])
    for key, value in options.items():
        if key == "use_cache" or key == "output_dir":
            continue
        if isinstance(value, bool):
            flag = f"--{key.replace('_', '-')}"
            no_flag = f"--no-{key.replace('_', '-')}"
            assert (flag in argv) is value
            assert (no_flag in argv) is (not value)


def _required_positionals(skill_name: str) -> List[str]:
    """Dummy positional argv values matching the skill signature arity."""
    fn = get_skill(skill_name)
    names = [
        p.name
        for p in inspect.signature(fn).parameters.values()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD and p.default is inspect.Parameter.empty
    ]
    stubs = {
        "images": "frames.tif",
        "integrator_dir": "integrator",
        "directory": "report_dir",
        "basename": "sample",
        "data": "curve.dat",
        "dat": "curve.dat",
        "input_data": "curve.dat",
        "profiles": "curve.dat",
        "path": "curve.dat",
    }
    return [stubs.get(n, f"{n}.dat") for n in names]


def test_runrequest_emits_cache_on_and_default_true_false() -> None:
    # Cache-on must emit --cache (historical bug: True was omitted → CLI stayed False).
    pos = _required_positionals("integrate")
    req_cache = RunRequest("integrate", pos, {"use_cache": True, "validation_png": False})
    argv_cache = req_cache.cli_argv()
    assert argv_cache[0] == "integrate"
    assert argv_cache[1 : 1 + len(pos)] == pos
    assert "--cache" in argv_cache
    assert "--no-cache" not in argv_cache
    assert "--no-validation-png" in argv_cache

    # Default-True skill option forced False via --no-flag.
    pos_pdf = _required_positionals("report_individual")
    req_pdf = RunRequest("report_individual", pos_pdf, {"write_pdf": False, "use_cache": False})
    argv_pdf = req_pdf.cli_argv()
    assert "--no-write-pdf" in argv_pdf
    assert "--no-cache" in argv_pdf

    ns = _parse_skill_argv("report_individual", argv_pdf)
    assert _ns_option(ns, "write_pdf") is False
    assert _ns_option(ns, "use_cache") is False

    ns2 = _parse_skill_argv("integrate", argv_cache)
    assert _ns_option(ns2, "use_cache") is True
    assert _ns_option(ns2, "validation_png") is False


def test_roundtrip_list_option_and_empty_string_omit() -> None:
    pos = _required_positionals("model_bodies")
    req = RunRequest(
        "model_bodies",
        pos,
        {"shapes": ["cylinder", "ellipsoid"], "use_cache": False, "output_dir": ""},
    )
    argv = req.cli_argv()
    assert "--shapes" in argv
    idx = argv.index("--shapes")
    assert argv[idx + 1 : idx + 3] == ["cylinder", "ellipsoid"]
    # empty output_dir omitted by codec
    assert "--output-dir" not in argv

    ns = _parse_skill_argv("model_bodies", argv)
    assert list(_ns_option(ns, "shapes") or []) == ["cylinder", "ellipsoid"]
    assert _ns_option(ns, "use_cache") is False


@pytest.mark.parametrize(
    "skill_name,options",
    [
        ("integrate", {"use_cache": True, "validation_png": True}),
        ("integrate", {"use_cache": False, "validation_png": False}),
        ("model_density", {"visualize_all": False, "use_cache": True}),
        ("model_density", {"visualize_all": True, "use_cache": False}),
        ("report_summary", {"write_pdf": False, "use_cache": False}),
        ("fit_distances", {"minimal": True, "use_cache": False}),
    ],
)
def test_runrequest_argv_argparse_roundtrip(skill_name: str, options: Dict[str, Any]) -> None:
    pos_vals = _required_positionals(skill_name)
    req = RunRequest(skill_name, pos_vals, options)
    argv = req.cli_argv()
    ns = _parse_skill_argv(skill_name, argv)
    for key, value in options.items():
        assert _ns_option(ns, key) == value, (skill_name, key, argv)


def test_framed_result_ignores_human_logs() -> None:
    envelope = build_result_envelope(
        skill_name="fit_distances",
        result={"atsas_fit_ok": False, "failure_message": "no gnom", "best_gnom_out_path": ""},
        process_ok=True,
    )
    assert envelope["process_ok"] is True
    assert envelope["science_ok"] is False
    frame = format_result_frame(envelope)
    stdout = "\n".join(
        [
            "[cache] miss (no matching record), running skill",
            "noise=value_should_be_ignored",
            frame.rstrip("\n"),
            "trailing human line",
        ]
    )
    parsed = parse_result_frame(stdout)
    assert parsed is not None
    assert parsed["science_ok"] is False
    assert parsed["result"]["atsas_fit_ok"] is False

    result, process_ok, science_ok = parse_skill_result_stdout(stdout)
    assert process_ok is True
    assert science_ok is False
    assert result["atsas_fit_ok"] is False
    assert "noise" not in result
    assert RESULT_FRAME_BEGIN in frame and RESULT_FRAME_END in frame


def test_science_ok_for_success_skill_without_atsas_markers() -> None:
    assert science_ok_for_result("subtract", {"subtracted_1d": "sub.dat"}) is True
    assert science_ok_for_result("fit_distances", {"atsas_fit_ok": True, "best_gnom_out_path": "x.out"}) is True
    assert science_ok_for_result("fit_sizes", {"atsas_fit_ok": False}) is False


def test_key_value_scanning_removed() -> None:
    # Old key=value lines must not become the GUI parse target.
    text = "subtracted_1d=/tmp/sub.dat\nrg=3.2\n"
    result, process_ok, science_ok = parse_skill_result_stdout(text)
    assert result == {}
    assert process_ok is None
    assert science_ok is None
