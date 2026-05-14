from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression
from ..core.utils import _strip_sub_int_prefix, _parse_descriptors_from_results, _first_existing
from ..core.report_fragments import (
    assemble_individual_markdown,
    discover_individual_fragment_paths,
)


def collect_report_data_from_directory(directory: str, basename: str) -> Dict[str, Any]:
    """
    Build report_data dict by scanning pipeline directory for files matching basename.
    basename is the canonical sample name (e.g. ihs27_sample). Tries common prefixes (int_, sub_, sub_int_)
    for integrated/descriptor paths. Output plot names use stripped base, so diff_<base>.png, sub_<base>.png.
    """
    rd: Dict[str, Any] = {"basename": basename}
    base = _strip_sub_int_prefix(basename)
    averaged_dir = os.path.join(directory, "averaged")
    subtracted_dir = os.path.join(directory, "subtracted")
    plots_dir = os.path.join(directory, "plots")
    descriptors_dir = os.path.join(directory, "descriptors")
    mixture_dir = os.path.join(directory, "mixture")
    bodies_dir = os.path.join(directory, "bodies")
    dammif_dir = os.path.join(directory, "dammif")

    for stem in (f"int_{base}", base, f"sub_int_{base}", f"sub_{base}"):
        p = os.path.join(averaged_dir, f"{stem}.dat")
        if os.path.isfile(p):
            rd["integrated_curve_path"] = p
            break

    diff_path = os.path.join(subtracted_dir, f"diff_{base}.png")
    sub_plot_path = os.path.join(subtracted_dir, f"sub_{base}.png")
    if os.path.isfile(diff_path):
        rd["difference_plot_path"] = diff_path
    if os.path.isfile(sub_plot_path):
        rd["subtracted_plot_path"] = sub_plot_path

    for stem in (base, f"sub_{base}", f"int_{base}", f"sub_int_{base}"):
        res_path = os.path.join(descriptors_dir, f"{stem}_results.txt")
        if os.path.isfile(res_path):
            desc = _parse_descriptors_from_results(res_path)
            if desc:
                rd["descriptors_table"] = desc
            break

    guinier_png = os.path.join(plots_dir, f"guinier_{base}.png")
    kratky_png = os.path.join(plots_dir, f"kratky_{base}.png")
    loglog_png = os.path.join(plots_dir, f"loglog_{base}.png")
    if os.path.isfile(guinier_png) or os.path.isfile(kratky_png) or os.path.isfile(loglog_png):
        rd["plot_figures"] = {}
        if os.path.isfile(guinier_png):
            rd["plot_figures"]["guinier"] = guinier_png
        if os.path.isfile(kratky_png):
            rd["plot_figures"]["kratky"] = kratky_png
        if os.path.isfile(loglog_png):
            rd["plot_figures"]["loglog"] = loglog_png

    sample_mixture = os.path.join(mixture_dir, base)
    if os.path.isdir(sample_mixture):
        comp = _first_existing([
            os.path.join(sample_mixture, "mixture_comparison_I_vs_q.png"),
            os.path.join(sample_mixture, "comparison.png"),
            os.path.join(sample_mixture, "mixture_comparison.png"),
        ])
        dist = _first_existing([
            os.path.join(sample_mixture, "mixture_distributions.png"),
            os.path.join(sample_mixture, "distributions.png"),
        ])
        csv_path = _first_existing([
            os.path.join(sample_mixture, "mixture_results.csv"),
            os.path.join(sample_mixture, "results.csv"),
        ])
        if comp and os.path.isfile(comp):
            rd["mixture_comparison_figure_path"] = comp
        if dist and os.path.isfile(dist):
            rd["mixture_distributions_figure_path"] = dist
        if csv_path and os.path.isfile(csv_path):
            rd["mixture_results_csv_path"] = csv_path

    sample_bodies = os.path.join(bodies_dir, base)
    if os.path.isdir(sample_bodies):
        b_yml = os.path.join(sample_bodies, "bodies_fits.yml")
        b_csv = os.path.join(sample_bodies, "bodies_fits.csv")
        b_fits = os.path.join(sample_bodies, f"{base}_fits.png")
        if os.path.isfile(b_yml):
            rd["bodies_fits_yml_path"] = b_yml
        if os.path.isfile(b_csv):
            rd["bodies_fits_csv_path"] = b_csv
        if os.path.isfile(b_fits):
            fits = rd.get("fits_comparison_figure_path") or []
            if isinstance(fits, list):
                fits = list(fits)
            else:
                fits = [fits] if fits else []
            fits.append((b_fits, "bodies"))
            rd["fits_comparison_figure_path"] = fits

    sample_dammif = os.path.join(dammif_dir, base)
    if os.path.isdir(sample_dammif):
        d_yml = os.path.join(sample_dammif, "dammif_fits.yml")
        d_csv = os.path.join(sample_dammif, "dammif_fits.csv")
        d_fits = os.path.join(sample_dammif, f"{base}_fits.png")
        if os.path.isfile(d_yml):
            rd["dammif_fits_yml_path"] = d_yml
        if os.path.isfile(d_csv):
            rd["dammif_fits_csv_path"] = d_csv
        if os.path.isfile(d_fits):
            fits = rd.get("fits_comparison_figure_path") or []
            if isinstance(fits, list):
                fits = list(fits)
            else:
                fits = [fits] if fits else []
            fits.append((d_fits, "dammif"))
            rd["fits_comparison_figure_path"] = fits

    return rd


def write_individual_report_from_fragments(
    directory: str,
    basename: str,
    *,
    output_md_path: Optional[str] = None,
    output_pdf_path: Optional[str] = None,
    write_pdf: bool = True,
) -> Dict[str, Any]:
    """Assemble ``*_report_individual.md`` fragments; write merged Markdown and PDF (ReportLab)."""
    from ..core.report import build_pdf_from_assembled_markdown

    reports_dir = os.path.join(directory, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    if output_md_path is None:
        output_md_path = os.path.join(reports_dir, f"{basename}_assembled_report.md")
    frag_paths = discover_individual_fragment_paths(directory, basename)
    md_dir = os.path.dirname(os.path.abspath(output_md_path)) or "."
    md_text = assemble_individual_markdown(frag_paths, assembly_md_dir=md_dir)
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
        "assembled_report_md_path": output_md_path,
        "report_pdf_path": pdf_path_out,
        "fragments_found": len(frag_paths),
    }


def report_individual(
    directory: SingletonPathExpressionArg,
    basename: str,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    output_md_path: Optional[str] = None,
    write_pdf: bool = True,
    use_cache: bool = False,
) -> Dict[str, Any]:
    """
    SAXS / small-angle x-ray scattering: build a per-sample report from an existing pipeline directory.

    Assembles decentralized ``*_report_individual.md`` fragments, writes
    ``<pipeline>/reports/<basename>_assembled_report.md``, and builds the PDF with **ReportLab**
    from that Markdown (headings, text, images, simple tables).

    ### Arguments

    - `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
    - `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
    - `output_dir` (str, default `.`): Unused for default paths; PDF/MD default to ``<directory>/reports/``.
    - `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/<basename>_report.pdf``.
    - `output_md_path` (str | None, default `None`): Optional path for merged Markdown.
    - `write_pdf` (bool, default `True`): Whether to emit a PDF.
    - `use_cache` (bool, default `False`): Present for CLI parity; unused.

    ### Returns

    `dict[str, Any]` with:

    - `report_pdf_path`: Path to the generated PDF when ``write_pdf`` is True.
    - `assembled_report_md_path`: Path to merged Markdown.
    - `fragments_found`: Number of fragment files merged.

    ### Python usage

    ```python
    from autosaxs.skill import report_individual

    out = report_individual(
        directory="pipeline_out",
        basename="sample_01",
        output_dir="reports",
    )

    print(out["report_pdf_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs report-individual pipeline_out sample_01 --output-dir reports
    ```
    """
    _ = use_cache, output_dir
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]

    reports_dir = os.path.join(directory_path, "reports")
    pdf_target = output_path if output_path is not None else os.path.join(reports_dir, f"{basename}_report.pdf")
    md_target = output_md_path
    if md_target is None:
        md_target = os.path.join(reports_dir, f"{basename}_assembled_report.md")
    return write_individual_report_from_fragments(
        directory_path,
        basename,
        output_md_path=md_target,
        output_pdf_path=pdf_target if write_pdf else None,
        write_pdf=write_pdf,
    )
