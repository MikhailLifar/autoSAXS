from __future__ import annotations

import os
from typing import Any, Dict, Optional

from .common import SingletonPathExpressionArg, coerce_singleton_path_expression


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
    from ..report import write_individual_report_pdf

    _ = use_cache  # report generation does not use caching; kept for CLI parity
    directory = coerce_singleton_path_expression(directory)
    directory_path = directory.unwrap()[0]
    if output_path is None:
        output_path = os.path.join(output_dir, f"{basename}_report.pdf")
    path = write_individual_report_pdf(directory_path, basename, output_path=output_path)
    return {"report_pdf_path": path}

