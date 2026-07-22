from __future__ import annotations

import os
from typing import Any, Dict, Optional, List

from .common import (
    ConfigPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_singleton_path_expression,
)
from ..core.utils import _strip_sub_int_prefix, _parse_descriptors_from_results
from ..core.report_fragments import assemble_summary_markdown


def collect_summary_data_from_directory(directory: str) -> Dict[str, Any]:
    """
    Build summary_data dict by discovering samples from subtracted/ and plots/.
    Returns {'samples': [{'basename', 'integrated_curve_path', 'subtracted_curve_path', 'descriptors', 'guinier_path', ...}, ...]}.
    """
    subtracted_dir = os.path.join(directory, "subtracted")
    plots_dir = os.path.join(directory, "plots")
    averaged_dir = os.path.join(directory, "averaged")
    descriptors_dir = os.path.join(directory, "descriptors")
    samples: List[Dict[str, Any]] = []
    seen_bases: set = set()

    if os.path.isdir(subtracted_dir):
        for name in os.listdir(subtracted_dir):
            if name.endswith(".dat") and name.startswith("sub_"):
                base = _strip_sub_int_prefix(name[:-4])
                if base in seen_bases:
                    continue
                seen_bases.add(base)
                entry: Dict[str, Any] = {"basename": base}
                sub_dat = os.path.join(subtracted_dir, name)
                if os.path.isfile(sub_dat):
                    entry["subtracted_curve_path"] = sub_dat
                for stem in (f"int_{base}", base, f"sub_{base}", f"sub_int_{base}"):
                    p = os.path.join(averaged_dir, f"{stem}.dat")
                    if os.path.isfile(p):
                        entry["integrated_curve_path"] = p
                        break
                res_path = os.path.join(descriptors_dir, f"{base}_results.txt")
                if not os.path.isfile(res_path):
                    for st in (f"sub_{base}", f"sub_int_{base}"):
                        res_path = os.path.join(descriptors_dir, f"{st}_results.txt")
                        if os.path.isfile(res_path):
                            break
                if os.path.isfile(res_path):
                    desc = _parse_descriptors_from_results(res_path)
                    if desc:
                        entry["descriptors"] = desc
                for stem in (base, f"sub_{base}", f"sub_int_{base}"):
                    guinier_p = os.path.join(plots_dir, f"guinier_{stem}.dat")
                    if os.path.isfile(guinier_p):
                        entry["guinier_path"] = guinier_p
                        entry["kratky_path"] = os.path.join(plots_dir, f"kratky_{stem}.dat")
                        entry["loglog_path"] = os.path.join(plots_dir, f"loglog_{stem}.dat")
                        break
                samples.append(entry)

    if not samples:
        for name in sorted(os.listdir(subtracted_dir) if os.path.isdir(subtracted_dir) else []):
            if name.endswith(".dat"):
                stem = name[:-4]
                base = _strip_sub_int_prefix(stem)
                if base not in seen_bases:
                    seen_bases.add(base)
                    entry = {"basename": base}
                    entry["subtracted_curve_path"] = os.path.join(subtracted_dir, name)
                    samples.append(entry)

    return {"samples": samples}


def write_summary_report_from_fragments(
    directory: str,
    *,
    output_md_path: Optional[str] = None,
    output_pdf_path: Optional[str] = None,
    write_pdf: bool = True,
) -> Dict[str, Any]:
    """Merge ``*_report_summary.yaml`` into Markdown and build PDF with ReportLab."""
    from ..core.report import build_pdf_from_assembled_markdown

    reports_dir = os.path.join(directory, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    if output_md_path is None:
        output_md_path = os.path.join(reports_dir, "summary_assembled_report.md")
    md_dir = os.path.dirname(os.path.abspath(output_md_path)) or "."
    md_text = assemble_summary_markdown(directory, markdown_output_dir=md_dir)
    os.makedirs(os.path.dirname(output_md_path) or ".", exist_ok=True)
    with open(output_md_path, "w", encoding="utf-8") as f:
        f.write(md_text)

    pdf_path_out: Optional[str] = None
    if write_pdf and output_pdf_path is not None:
        os.makedirs(os.path.dirname(output_pdf_path) or ".", exist_ok=True)
        build_pdf_from_assembled_markdown(
            md_text, output_pdf_path, markdown_base_dir=os.path.dirname(os.path.abspath(output_md_path))
        )
        pdf_path_out = output_pdf_path

    return {
        "assembled_summary_md_path": output_md_path,
        "report_pdf_path": pdf_path_out,
    }


def report_summary(
    directory: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    output_path: Optional[str] = None,
    output_md_path: Optional[str] = None,
    write_pdf: bool = True,
    use_cache: bool = False,
) -> Dict[str, Any]:
    """
    SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

    Merges decentralized ``*_report_summary.yaml`` files into Markdown under
    ``<directory>/reports/summary_assembled_report.md`` and renders the PDF with **ReportLab**
    from that Markdown.

    ### Arguments

    - `directory` (str): Path to the existing pipeline output directory.
    - `output_dir` (str, default `.`): Unused for default paths; outputs go under ``<directory>/reports/``.
    - `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/summary_report.pdf``.
    - `output_md_path` (str | None, default `None`): Output path for merged summary Markdown.
    - `write_pdf` (bool, default `True`): Whether to emit a PDF.
    - `use_cache` (bool, default `False`): Present for CLI parity; unused.

    ### Returns

    `dict[str, Any]` with:

    - `report_pdf_path`: Path to the generated PDF when requested.
    - `assembled_summary_md_path`: Merged Markdown path.

    ### Python usage

    ```python
    from autosaxs.skill import report_summary

    out = report_summary(
        directory="pipeline_out",
        output_dir="reports",
    )

    print(out["report_pdf_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs report-summary pipeline_out --output-dir reports
    ```
    """
    _ = use_cache, output_dir
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]

    reports_dir = os.path.join(directory_path, "reports")
    pdf_target = output_path if output_path is not None else os.path.join(reports_dir, "summary_report.pdf")
    md_target = output_md_path
    if md_target is None:
        md_target = os.path.join(reports_dir, "summary_assembled_report.md")
    return write_summary_report_from_fragments(
        directory_path,
        output_md_path=md_target,
        output_pdf_path=pdf_target if write_pdf else None,
        write_pdf=write_pdf,
    )
