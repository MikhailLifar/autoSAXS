from __future__ import annotations

import os
from typing import Any, Dict, Optional, List

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression
from ..core.utils import _strip_sub_int_prefix, _parse_descriptors_from_results


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

    # Discover basenames from subtracted .dat files (sub_<base>.dat)
    if os.path.isdir(subtracted_dir):
        for name in os.listdir(subtracted_dir):
            if name.endswith(".dat") and name.startswith("sub_"):
                base = _strip_sub_int_prefix(name[:-4])  # stem without .dat, then strip sub_/int_
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
        # Fallback: any .dat in subtracted
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


def write_summary_report_pdf(
    directory: str,
    output_path: Optional[str] = None,
) -> str:
    """
    Collect summary data from directory, build summary PDF, write to output_path.
    If output_path is None, uses directory/reports/summary_report.pdf.
    Returns the path to the written PDF.
    """
    from ..core.report import build_summary_report_pdf

    reports_dir = os.path.join(directory, "reports")
    if output_path is None:
        output_path = os.path.join(reports_dir, "summary_report.pdf")
    os.makedirs(reports_dir, exist_ok=True)
    summary_data = collect_summary_data_from_directory(directory)
    build_summary_report_pdf(summary_data, output_path)
    return output_path


def report_summary(
    directory: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build a summary PDF report for all samples found inside an existing pipeline directory. The skill discovers samples and combines plots/tables where data exists.

    ### Arguments

    - `directory` (str): Path to the existing pipeline output directory.
    - `output_dir` (str, default `.`): Directory where the summary PDF is written.
    - `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/summary_report.pdf`.
    - `use_cache` (bool, default `True`): Present for CLI parity; report generation does not use caching.

    ### Returns

    `dict[str, Any]` with:

    - `report_pdf_path`: Path to the generated summary PDF.

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
    autosaxs report_summary pipeline_out --output-dir reports
    ```
    """
    _ = use_cache  # report generation does not use caching; kept for CLI parity
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]
    if output_path is None:
        output_path = os.path.join(output_dir, "summary_report.pdf")
    path = write_summary_report_pdf(directory_path, output_path=output_path)
    return {"report_pdf_path": path}

