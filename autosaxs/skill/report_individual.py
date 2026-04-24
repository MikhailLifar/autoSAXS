from __future__ import annotations

import os
from typing import Any, Dict, Optional, List, Tuple

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression
from ..core.utils import _strip_sub_int_prefix, _parse_descriptors_from_results, _first_existing


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

    # Integrated curve
    for stem in (f"int_{base}", base, f"sub_int_{base}", f"sub_{base}"):
        p = os.path.join(averaged_dir, f"{stem}.dat")
        if os.path.isfile(p):
            rd["integrated_curve_path"] = p
            break

    # Difference and subtracted plots (files use stripped base)
    diff_path = os.path.join(subtracted_dir, f"diff_{base}.png")
    sub_plot_path = os.path.join(subtracted_dir, f"sub_{base}.png")
    if os.path.isfile(diff_path):
        rd["difference_plot_path"] = diff_path
    if os.path.isfile(sub_plot_path):
        rd["subtracted_plot_path"] = sub_plot_path

    # Descriptors: try stems that might be used (profile path stem)
    for stem in (base, f"sub_{base}", f"int_{base}", f"sub_int_{base}"):
        res_path = os.path.join(descriptors_dir, f"{stem}_results.txt")
        if os.path.isfile(res_path):
            desc = _parse_descriptors_from_results(res_path)
            if desc:
                rd["descriptors_table"] = desc
            break

    # Plot figures (guinier, kratky, loglog) — plot step writes .png; use those for embedding (not .dat)
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

    # Mixture: per-sample subdir named by stripped base (apply_batch creates these)
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

    # Bodies: per-sample subdir (same naming as mixture)
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

    # Dammif: per-sample subdir (same naming as mixture)
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


def write_individual_report_pdf(
    directory: str,
    basename: str,
    output_path: Optional[str] = None,
) -> str:
    """
    Collect report data from directory for the given basename, build PDF, write to output_path.
    If output_path is None, uses directory/reports/<basename>_report.pdf.
    Returns the path to the written PDF.
    """
    from ..core.report import build_report_pdf

    reports_dir = os.path.join(directory, "reports")
    if output_path is None:
        output_path = os.path.join(reports_dir, f"{basename}_report.pdf")
    os.makedirs(reports_dir, exist_ok=True)
    rd = collect_report_data_from_directory(directory, basename)
    build_report_pdf(rd, output_path)
    return output_path


def report_individual(
    directory: SingletonPathExpressionArg,
    basename: str,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build a per-sample PDF report from an existing pipeline directory. The skill scans `directory` for paths matching the provided `basename` and then assembles the report sections.

    ### Arguments

    - `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
    - `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
    - `output_dir` (str, default `.`): Directory where the PDF report is written.
    - `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/<basename>_report.pdf`.
    - `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

    ### Returns

    `dict[str, Any]` with:

    - `report_pdf_path`: Path to the generated PDF.

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
    autosaxs report_individual pipeline_out sample_01 --output-dir reports
    ```
    """
    _ = use_cache  # report generation does not use caching; kept for CLI parity
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]
    if output_path is None:
        output_path = os.path.join(output_dir, f"{basename}_report.pdf")
    path = write_individual_report_pdf(directory_path, basename, output_path=output_path)
    return {"report_pdf_path": path}

